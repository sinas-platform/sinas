"""Operations metering: count operations, snapshot durably, push cumulative
usage to the central platform. Pure emission — nothing is enforced on the
instance, no limits or status are pulled down, and the heartbeat response
body is ignored (2xx = ack).

Design (Confluence: "Operations metering & usage-based billing — design"):

  hot path   metering.record(kind) → atomic Redis INCRBY, fire-and-forget
  durability scheduler snapshot job → usage_periods row per period (upsert);
             Redis is re-seeded from it on startup so restarts don't lose
             the running count
  emission   scheduler push job → POST the CUMULATIVE period-to-date total
             (never deltas). A missed beat needs no catch-up: the next push
             carries the current total and the platform takes max(total) per
             (instance_id, period_id). Idempotency-Key dedupes retries.
             Per-instance jitter keeps a fleet from pushing in sync.

Meter integrity (§6): no process that executes client-supplied code may hold
write access to the meter. Sandbox and shared executor containers receive no
Redis/DB credentials (verified: shared_worker_manager passes only
WORKER_MODE/WORKER_ID), so record() only ever runs in platform code.
TRUSTED_EXECUTOR=inprocess runs client code inside a credential-bearing
process — the invariant is unenforceable there, and enabling metering in
that mode logs a loud warning at scheduler startup. The platform-side
backstop is in the contract: totals are cumulative and snapshot_seq is
monotonic, so the receiver flags any decrease as tampering.
"""

import enum
import hashlib
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app._version import __version__
from app.core.config import settings

logger = logging.getLogger(__name__)

SCHEMA = "sinas.metering/v2"

# First-report sentinel: sent while no Platform period is cached. The
# Platform applies an "init" report to the then-current billing period and
# returns that period in the ack; Core then adopts it WITHOUT resetting —
# the applied counts are the period's baseline, and a reset would make the
# next report's counters regress (tripping the Platform's counter_regression
# check). Reset happens only on a subsequent period CHANGE.
INIT_PERIOD_ID = "init"

# Old-period Redis keys are kept briefly after their final push, then expire.
_ROLLED_OVER_KEY_TTL = int(timedelta(days=7).total_seconds())

_PUSH_ATTEMPTS = 3


class OperationKind(str, enum.Enum):
    FUNCTION = "function"  # function execution (any trigger)
    CODE = "code"  # agent codeExecution tool
    QUERY = "query"  # query execution (agent tool or runtime API)
    AGENT = "agent"  # agent invocation (one LLM turn)
    UPLOAD = "upload"  # file upload
    TOOL = "tool"  # other tool calls (skill/state/component/connector/...)


def instance_id() -> str:
    return (
        settings.metering_instance_id.strip()
        or (settings.domain or "").strip()
        or "unknown"
    )


def period_id(now: Optional[datetime] = None) -> str:
    """Monthly period, UTC. Fallback until the platform supplies
    subscription-anchored periods — same field, so that slots in later."""
    now = now or datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


def period_bounds(pid: str) -> tuple[datetime, datetime]:
    year, month = int(pid[:4]), int(pid[5:7])
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC) if month == 12 else datetime(year, month + 1, 1, tzinfo=UTC)
    return start, end


def _previous_period_id(pid: str) -> str:
    start, _ = period_bounds(pid)
    prev_last_day = start - timedelta(days=1)
    return f"{prev_last_day.year:04d}-{prev_last_day.month:02d}"


def _key(pid: str, suffix: str) -> str:
    return f"usage:{instance_id()}:{pid}:{suffix}"


def _platform_pid_key() -> str:
    return f"usage:{instance_id()}:platform_period"


async def current_pid(redis) -> str:
    """Period id live counters are keyed by: the Platform-issued period once
    the POST handshake has supplied one, the local monthly fallback before
    that (and always, in offline/self-hosted mode)."""
    pid = await redis.get(_platform_pid_key())
    if isinstance(pid, bytes):
        pid = pid.decode()
    return pid or period_id()


def push_jitter_seconds(interval_minutes: int) -> int:
    """Deterministic per-instance offset so fleet pushes spread across the
    window instead of aligning to the wall clock."""
    digest = hashlib.sha256(instance_id().encode()).digest()
    return int.from_bytes(digest[:4], "big") % max(1, interval_minutes * 60)


# ---------------------------------------------------------------------------
# Hot path
# ---------------------------------------------------------------------------


async def record(kind: OperationKind, n: int = 1) -> None:
    """Count an operation. Fire-and-forget: never raises, never blocks the
    request on metering problems (posture matches the ClickHouse logging)."""
    if not settings.metering_enabled:
        return
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        pid = await current_pid(redis)
        pipe = redis.pipeline(transaction=False)
        pipe.incrby(_key(pid, "total"), n)
        pipe.incrby(_key(pid, f"kind:{kind.value}"), n)
        pipe.set(f"usage:{instance_id()}:last_op_at", datetime.now(UTC).isoformat())
        await pipe.execute()
    except Exception as e:
        # Under-counting during a Redis blip is acceptable; failing the
        # customer's request is not.
        logger.warning(f"metering.record failed (op not counted): {e}")


# ---------------------------------------------------------------------------
# Snapshot: Redis -> usage_periods (durable record of record)
# ---------------------------------------------------------------------------


async def _read_redis_counters(redis, pid: str) -> Optional[dict[str, Any]]:
    total = await redis.get(_key(pid, "total"))
    if total is None:
        return None
    by_kind: dict[str, int] = {}
    for kind in OperationKind:
        v = await redis.get(_key(pid, f"kind:{kind.value}"))
        if v is not None:
            by_kind[kind.value] = int(v)
    last_op = await redis.get(f"usage:{instance_id()}:last_op_at")
    if isinstance(last_op, bytes):
        last_op = last_op.decode()
    return {"total": int(total), "by_kind": by_kind, "last_op_at": last_op}


async def snapshot(db: AsyncSession) -> None:
    """Upsert usage_periods from Redis for the current period — and for the
    previous one while its keys still exist, which is what finalizes a period
    after rollover without a special case."""
    from app.core.redis import get_redis
    from app.models import UsagePeriod

    redis = await get_redis()
    state = await _load_platform_state(db)
    if state is not None:
        # Platform-period mode: one current period, bounds from the handshake.
        # No previous-period finalization — rollover discards (MVP contract).
        pids = (state.period_id,)
    else:
        current = period_id()
        pids = (current, _previous_period_id(current))

    for pid in pids:
        counters = await _read_redis_counters(redis, pid)
        if counters is None:
            continue

        row = (
            await db.execute(
                select(UsagePeriod).where(
                    UsagePeriod.instance_id == instance_id(),
                    UsagePeriod.period_id == pid,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            if state is not None and pid == state.period_id:
                start, end = state.period_start, state.period_end
            else:
                start, end = period_bounds(pid)
            row = UsagePeriod(
                instance_id=instance_id(),
                period_id=pid,
                period_start=start,
                period_end=end,
            )
            db.add(row)

        # The count only ever moves up; never let a stale Redis (e.g. one
        # restarted mid-seed) regress the durable row.
        row.total = max(row.total or 0, counters["total"])
        merged = dict(row.by_kind or {})
        for k, v in counters["by_kind"].items():
            merged[k] = max(merged.get(k, 0), v)
        row.by_kind = merged
        if counters["last_op_at"]:
            row.last_op_at = datetime.fromisoformat(counters["last_op_at"])

    await db.commit()


async def _load_platform_state(db: AsyncSession):
    """Current Platform-issued period for this instance, or None before the
    first successful POST handshake (and always in offline mode)."""
    from app.models import MeteringPlatformPeriod

    return (
        await db.execute(
            select(MeteringPlatformPeriod).where(
                MeteringPlatformPeriod.instance_id == instance_id()
            )
        )
    ).scalar_one_or_none()


async def seed_redis_from_db(db: AsyncSession) -> None:
    """On startup: restore the current period's count into Redis, taking the
    max of both sides so neither a Redis restart nor a stale snapshot can
    move the count backwards."""
    from app.core.redis import get_redis

    state = await _load_platform_state(db)
    pid = state.period_id if state else period_id()
    if state is not None:
        # Restore the shared pointer record()/current_pid() key by
        try:
            redis = await get_redis()
            await redis.set(_platform_pid_key(), state.period_id)
        except Exception:
            pass

    from app.core.redis import get_redis
    from app.models import UsagePeriod

    row = (
        await db.execute(
            select(UsagePeriod).where(
                UsagePeriod.instance_id == instance_id(),
                UsagePeriod.period_id == pid,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return

    redis = await get_redis()
    current_total = await redis.get(_key(row.period_id, "total"))
    if current_total is None or int(current_total) < (row.total or 0):
        await redis.set(_key(row.period_id, "total"), row.total or 0)
        for kind, value in (row.by_kind or {}).items():
            existing = await redis.get(_key(row.period_id, f"kind:{kind}"))
            if existing is None or int(existing) < value:
                await redis.set(_key(row.period_id, f"kind:{kind}"), value)
        logger.info(
            f"Metering: re-seeded Redis for period {row.period_id} "
            f"from durable snapshot (total={row.total})"
        )


# ---------------------------------------------------------------------------
# Push: cumulative heartbeat to the Platform (sinas.metering/v2)
#
# The POST is also the period handshake — there is no context endpoint.
# Every response carries the current Platform period; Core persists it and
# keys its counters by it. Three situations:
#
#   bootstrap  no cached period → report canonical_period_id "init". The
#              Platform applies it to the then-current period and returns
#              that period. Core ADOPTS AND MIGRATES its local counters to
#              the new id (no reset — the applied counts are the baseline;
#              a reset would regress the next report's counters).
#   steady     ack period == cached period → mark reported.
#   rollover   ack period != cached period (200 grace-window apply), or a
#              409 period_mismatch / period_context_required → adopt the
#              new period and RESET counters + snapshot_seq to zero. Ops
#              accumulated in between are discarded (accepted MVP gap).
# ---------------------------------------------------------------------------


def _build_payload(row, canonical_pid: str) -> dict[str, Any]:
    by_kind = {k.value: int((row.by_kind or {}).get(k.value, 0)) for k in OperationKind}
    # The Platform validates total == sum(by_kind) with an explicit "other";
    # counters can drift apart across a Redis blip, so compute the remainder.
    other = max(0, int(row.total or 0) - sum(by_kind.values()))
    return {
        "schema": SCHEMA,
        "instance_id": row.instance_id,
        "platform_version": __version__,
        "canonical_period_id": canonical_pid,
        # Decimal strings throughout: the Platform is TypeScript + Postgres
        # BigInt, and IEEE doubles corrupt large counters.
        "cumulative": {
            "total": str(int(row.total or 0)),
            "by_kind": {**{k: str(v) for k, v in by_kind.items()}, "other": str(other)},
        },
        "snapshot_seq": str(int(row.snapshot_seq)),
        "last_op_at": row.last_op_at.isoformat() if row.last_op_at else None,
        "sent_at": datetime.now(UTC).isoformat(),
    }


def _parse_period(body: Any) -> Optional[dict[str, Any]]:
    """{"id", "start", "end"} from a response body, or None if absent/bad."""
    try:
        period = (body or {}).get("period") or {}
        pid = str(period["id"])
        start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
        return {"id": pid, "start": start, "end": end}
    except Exception:
        return None


async def _persist_period(db: AsyncSession, period: dict[str, Any]) -> None:
    from app.models import MeteringPlatformPeriod

    state = await _load_platform_state(db)
    if state is None:
        state = MeteringPlatformPeriod(instance_id=instance_id())
        db.add(state)
    state.period_id = period["id"]
    state.period_start = period["start"]
    state.period_end = period["end"]


async def _adopt_migrate(db: AsyncSession, redis, old_pid: str, period: dict[str, Any], row) -> None:
    """Bootstrap adopt: the "init" report was applied to `period`, so local
    counters move under the new id with their values intact."""
    from app.models import UsagePeriod

    await _persist_period(db, period)
    new_pid = period["id"]

    # Live counters: copy, then let the old keys age out. A record() racing
    # this in another process may land on the old key and be lost — bounded
    # by one op and covered by the accepted bootstrap gap.
    counters = await _read_redis_counters(redis, old_pid)
    if counters is not None:
        pipe = redis.pipeline(transaction=False)
        pipe.incrby(_key(new_pid, "total"), counters["total"])
        for k, v in counters["by_kind"].items():
            pipe.incrby(_key(new_pid, f"kind:{k}"), v)
        await pipe.execute()
        for k in OperationKind:
            await redis.expire(_key(old_pid, f"kind:{k.value}"), _ROLLED_OVER_KEY_TTL)
        await redis.expire(_key(old_pid, "total"), _ROLLED_OVER_KEY_TTL)
    await redis.set(_platform_pid_key(), new_pid)

    # Durable row: re-key under the Platform period, values and seq intact
    # (seq continuity is what keeps the monotonic contract unbroken).
    existing = (
        await db.execute(
            select(UsagePeriod).where(
                UsagePeriod.instance_id == instance_id(),
                UsagePeriod.period_id == new_pid,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        row.period_id = new_pid
        row.period_start = period["start"]
        row.period_end = period["end"]


async def _adopt_reset(db: AsyncSession, redis, old_pid: str, period: dict[str, Any]) -> None:
    """Rollover adopt: new period starts from zero; the old period's tail is
    discarded (accepted MVP gap — no previous-period finalization)."""
    from app.models import UsagePeriod

    await _persist_period(db, period)
    await redis.set(_platform_pid_key(), period["id"])
    for k in OperationKind:
        await redis.expire(_key(old_pid, f"kind:{k.value}"), _ROLLED_OVER_KEY_TTL)
    await redis.expire(_key(old_pid, "total"), _ROLLED_OVER_KEY_TTL)
    # Create the new period's row at zero IMMEDIATELY. Without it, an idle
    # instance adopted the period but then had nothing to send until its
    # next operation — showing "Not yet reported" on the Platform for as
    # long as it stayed idle. A zero row means the very next cycle reports
    # cumulative "0" under the new period (seq 1), closing the gap.
    existing = (
        await db.execute(
            select(UsagePeriod).where(
                UsagePeriod.instance_id == instance_id(),
                UsagePeriod.period_id == period["id"],
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            UsagePeriod(
                instance_id=instance_id(),
                period_id=period["id"],
                period_start=period["start"],
                period_end=period["end"],
            )
        )


async def push(db: AsyncSession) -> int:
    """POST the current period's cumulative report and process the period
    handshake in the response. Returns reports accepted.

    Failures are logged and abandoned until the next cycle — cumulative
    totals mean there is nothing to replay.
    """
    from app.core.redis import get_redis
    from app.models import UsagePeriod

    url = settings.platform_report_url.strip()
    if not url:
        logger.warning("METERING_ENABLED but PLATFORM_REPORT_URL is empty — nothing sent")
        return 0

    redis = await get_redis()
    state = await _load_platform_state(db)
    pid = state.period_id if state else period_id()
    canonical = state.period_id if state else INIT_PERIOD_ID

    row = (
        await db.execute(
            select(UsagePeriod).where(
                UsagePeriod.instance_id == instance_id(),
                UsagePeriod.period_id == pid,
            )
        )
    ).scalar_one_or_none()
    if row is None or (row.reported_at is not None and row.updated_at <= row.reported_at):
        return 0

    headers = {}
    if settings.platform_api_key:
        # Never log this header or the settings value (SIN-649).
        headers["Authorization"] = f"Bearer {settings.platform_api_key}"

    row.snapshot_seq += 1
    payload = _build_payload(row, canonical)
    key = f"{row.instance_id}:{row.period_id}:{row.snapshot_seq}"

    pushed = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(_PUSH_ATTEMPTS):
            try:
                resp = await client.post(
                    url, json=payload, headers={**headers, "Idempotency-Key": key}
                )
            except Exception as e:
                logger.warning(
                    f"Metering push {key} failed "
                    f"(attempt {attempt + 1}/{_PUSH_ATTEMPTS}): {e}"
                )
                resp = None

            if resp is not None:
                try:
                    body = resp.json()
                except Exception:
                    body = {}

                if 200 <= resp.status_code < 300:
                    row.reported_at = datetime.now(UTC)
                    pushed = 1
                    period = _parse_period(body)
                    if period and period["id"] != canonical:
                        if canonical == INIT_PERIOD_ID:
                            await _adopt_migrate(db, redis, pid, period, row)
                        else:
                            await _adopt_reset(db, redis, pid, period)
                    break

                if resp.status_code == 409:
                    disposition = (body or {}).get("disposition")
                    period = _parse_period(body)
                    if (
                        disposition in ("period_mismatch", "period_context_required")
                        and period is not None
                    ):
                        # Rollover detected the hard way: adopt + reset. The
                        # rejected counters are the discarded tail.
                        await _adopt_reset(db, redis, pid, period)
                        logger.info(
                            f"Metering: period changed to {period['id']} "
                            f"({disposition}); counters reset"
                        )
                    else:
                        # sequence_reused / counter_regression: retrying the
                        # same payload cannot help — surface loudly. Include
                        # what we could(n't) parse: a contract-shape mismatch
                        # on the Platform's 409 body lands here too, and this
                        # line is the difference between a one-look diagnosis
                        # and an instance silently 409ing every cycle.
                        logger.error(
                            f"Metering push {key} rejected: 409 "
                            f"disposition={disposition!r} "
                            f"period_parseable={_parse_period(body) is not None} "
                            f"body~{str(body)[:200]}"
                        )
                    break

                logger.warning(
                    f"Metering push {key}: HTTP {resp.status_code} "
                    f"(attempt {attempt + 1}/{_PUSH_ATTEMPTS})"
                )

            if attempt < _PUSH_ATTEMPTS - 1:
                import asyncio

                # Backoff with jitter so a fleet retries out of phase
                await asyncio.sleep((2**attempt) + random.uniform(0, 1))

    await db.commit()
    if pushed:
        logger.info("Metering: pushed 1 period report")
    return pushed


# ---------------------------------------------------------------------------
# Scheduler entrypoints
# ---------------------------------------------------------------------------


async def run_snapshot_cycle() -> None:
    from app.core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await snapshot(db)
    except Exception:
        logger.exception("Metering snapshot cycle failed")


async def run_push_cycle() -> None:
    from app.core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await snapshot(db)  # push fresh numbers, not last cycle's
            await push(db)
    except Exception:
        logger.exception("Metering push cycle failed")
