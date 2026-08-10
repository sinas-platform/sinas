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

SCHEMA = "sinas.metering/v1"

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
        pid = period_id()
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
    current = period_id()

    for pid in (current, _previous_period_id(current)):
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


async def seed_redis_from_db(db: AsyncSession) -> None:
    """On startup: restore the current period's count into Redis, taking the
    max of both sides so neither a Redis restart nor a stale snapshot can
    move the count backwards."""
    from app.core.redis import get_redis
    from app.models import UsagePeriod

    row = (
        await db.execute(
            select(UsagePeriod).where(
                UsagePeriod.instance_id == instance_id(),
                UsagePeriod.period_id == period_id(),
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
# Push: cumulative heartbeat to the platform
# ---------------------------------------------------------------------------


def _build_payload(row) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "instance_id": row.instance_id,
        "platform_version": __version__,
        "period_id": row.period_id,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        # Cumulative period-to-date — never a delta. The platform takes
        # max(total) per (instance_id, period_id); a missed or duplicated
        # beat self-heals.
        "total": row.total,
        "by_kind": row.by_kind or {},
        "last_op_at": row.last_op_at.isoformat() if row.last_op_at else None,
        "snapshot_seq": row.snapshot_seq,
        "sent_at": datetime.now(UTC).isoformat(),
    }


async def push(db: AsyncSession) -> int:
    """POST every period row with unreported progress. Returns rows pushed.

    Failures are logged and abandoned until the next cycle — cumulative
    totals mean there is nothing to replay.
    """
    from app.core.redis import get_redis
    from app.models import UsagePeriod

    endpoint = settings.metering_endpoint.strip()
    if not endpoint:
        logger.warning("METERING_ENABLED but METERING_ENDPOINT is empty — nothing sent")
        return 0

    rows = (
        (
            await db.execute(
                select(UsagePeriod)
                .where(UsagePeriod.instance_id == instance_id())
                .order_by(UsagePeriod.period_start)
            )
        )
        .scalars()
        .all()
    )
    to_send = [
        r for r in rows if r.reported_at is None or r.updated_at > r.reported_at
    ]
    if not to_send:
        return 0

    headers = {}
    if settings.metering_api_key:
        headers["Authorization"] = f"Bearer {settings.metering_api_key}"

    pushed = 0
    current = period_id()
    async with httpx.AsyncClient(timeout=15.0) as client:
        for row in to_send:
            row.snapshot_seq += 1
            payload = _build_payload(row)
            key = f"{row.instance_id}:{row.period_id}:{row.snapshot_seq}"

            ok = False
            for attempt in range(_PUSH_ATTEMPTS):
                try:
                    resp = await client.post(
                        endpoint,
                        json=payload,
                        headers={**headers, "Idempotency-Key": key},
                    )
                    if 200 <= resp.status_code < 300:
                        ok = True
                        break
                    logger.warning(
                        f"Metering push {key}: HTTP {resp.status_code} "
                        f"(attempt {attempt + 1}/{_PUSH_ATTEMPTS})"
                    )
                except Exception as e:
                    logger.warning(
                        f"Metering push {key} failed "
                        f"(attempt {attempt + 1}/{_PUSH_ATTEMPTS}): {e}"
                    )
                if attempt < _PUSH_ATTEMPTS - 1:
                    import asyncio

                    # Backoff with jitter so a fleet retries out of phase
                    await asyncio.sleep((2**attempt) + random.uniform(0, 1))

            if ok:
                row.reported_at = datetime.now(UTC)
                pushed += 1
                if row.period_id != current:
                    # Rolled-over period got its final push; let its Redis
                    # keys age out instead of lingering forever.
                    try:
                        redis = await get_redis()
                        for kind in OperationKind:
                            await redis.expire(
                                _key(row.period_id, f"kind:{kind.value}"),
                                _ROLLED_OVER_KEY_TTL,
                            )
                        await redis.expire(
                            _key(row.period_id, "total"), _ROLLED_OVER_KEY_TTL
                        )
                    except Exception:
                        pass
            await db.commit()

    if pushed:
        logger.info(f"Metering: pushed {pushed} period report(s)")
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
