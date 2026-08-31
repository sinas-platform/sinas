"""Operations metering: hot-path counting, snapshots, cumulative push.

v2 contract fixtures for Platform contract tests live in
tests/fixtures/metering/. Contract decisions (POST-only, "init" sentinel
applied-not-rejected with adopt-and-migrate, both rollover paths reset)
are recorded on SIN-640/SIN-641.

Redis is faked (local test env has no reachable Redis, and the fake lets us
assert exact keys); the DB is real. metering.py imports get_redis inside each
function, so patching app.core.redis.get_redis covers everything.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import UsagePeriod
from app.services import metering
from app.services.metering import (
    SCHEMA,
    OperationKind,
    period_bounds,
    period_id,
    push_jitter_seconds,
    record,
    seed_redis_from_db,
    snapshot,
)

INSTANCE = "test-instance"


class _FakePipeline:
    def __init__(self, store):
        self._store = store
        self._ops = []

    def incrby(self, key, n):
        self._ops.append(("incrby", key, n))

    def set(self, key, value):
        self._ops.append(("set", key, value))

    async def execute(self):
        for op, key, arg in self._ops:
            if op == "incrby":
                self._store[key] = int(self._store.get(key, 0)) + arg
            else:
                self._store[key] = arg


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}
        self.expired: dict = {}

    def pipeline(self, transaction=True):
        return _FakePipeline(self.store)

    async def get(self, key):
        v = self.store.get(key)
        return None if v is None else str(v)

    async def set(self, key, value):
        self.store[key] = value

    async def expire(self, key, ttl):
        self.expired[key] = ttl


@pytest.fixture
def fake_redis(monkeypatch):
    redis = _FakeRedis()

    async def _get_redis():
        return redis

    monkeypatch.setattr("app.core.redis.get_redis", _get_redis)
    return redis


@pytest.fixture
def metering_on(monkeypatch, fake_redis):
    monkeypatch.setattr(settings, "metering_enabled", True)
    monkeypatch.setattr(settings, "metering_instance_id", INSTANCE)
    return fake_redis


def _k(suffix, pid=None):
    return f"usage:{INSTANCE}:{pid or period_id()}:{suffix}"


# ---------------------------------------------------------------------------
# record(): hot path
# ---------------------------------------------------------------------------


class TestRecord:
    async def test_disabled_is_noop(self, fake_redis, monkeypatch):
        monkeypatch.setattr(settings, "metering_enabled", False)
        await record(OperationKind.FUNCTION)
        assert fake_redis.store == {}

    async def test_increments_total_and_kind(self, metering_on):
        await record(OperationKind.FUNCTION)
        await record(OperationKind.FUNCTION)
        await record(OperationKind.QUERY)

        assert metering_on.store[_k("total")] == 3
        assert metering_on.store[_k("kind:function")] == 2
        assert metering_on.store[_k("kind:query")] == 1
        assert f"usage:{INSTANCE}:last_op_at" in metering_on.store

    async def test_never_raises_on_redis_failure(self, monkeypatch):
        monkeypatch.setattr(settings, "metering_enabled", True)

        async def _boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr("app.core.redis.get_redis", _boom)
        # A metering outage must never fail the customer's operation
        await record(OperationKind.AGENT)


# ---------------------------------------------------------------------------
# snapshot / seed: Redis <-> usage_periods
# ---------------------------------------------------------------------------


class TestSnapshot:
    async def test_creates_and_updates_row(self, db: AsyncSession, metering_on):
        await record(OperationKind.FUNCTION)
        await record(OperationKind.AGENT)
        await snapshot(db)

        row = (
            await db.execute(
                select(UsagePeriod).where(UsagePeriod.instance_id == INSTANCE)
            )
        ).scalar_one()
        assert row.period_id == period_id()
        assert row.total == 2
        assert row.by_kind == {"function": 1, "agent": 1}
        start, end = period_bounds(row.period_id)
        assert (row.period_start, row.period_end) == (start, end)

        await record(OperationKind.FUNCTION)
        await snapshot(db)
        await db.refresh(row)
        assert row.total == 3
        assert row.by_kind["function"] == 2

    async def test_snapshot_never_regresses_the_row(self, db, metering_on):
        """A wiped Redis (count lower than the durable row) must not pull the
        durable count down — the meter only moves up."""
        await record(OperationKind.FUNCTION)
        await record(OperationKind.FUNCTION)
        await snapshot(db)

        metering_on.store[_k("total")] = 1
        metering_on.store[_k("kind:function")] = 1
        await snapshot(db)

        row = (
            await db.execute(
                select(UsagePeriod).where(UsagePeriod.instance_id == INSTANCE)
            )
        ).scalar_one()
        assert row.total == 2
        assert row.by_kind["function"] == 2

    async def test_seed_restores_redis_after_restart(self, db, metering_on):
        await record(OperationKind.FUNCTION)
        await record(OperationKind.QUERY)
        await snapshot(db)

        metering_on.store.clear()  # Redis restart
        await seed_redis_from_db(db)
        assert metering_on.store[_k("total")] == 2
        assert metering_on.store[_k("kind:query")] == 1

    async def test_seed_keeps_higher_redis_value(self, db, metering_on):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        # Redis moved on since the last snapshot — seeding must not regress it
        await record(OperationKind.FUNCTION)
        await seed_redis_from_db(db)
        assert metering_on.store[_k("total")] == 2


# ---------------------------------------------------------------------------
# push: cumulative heartbeat
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


P1 = {"id": "01J5PERIODONE", "start": "2026-07-14T00:00:00Z", "end": "2026-08-14T00:00:00Z"}
P2 = {"id": "01J5PERIODTWO", "start": "2026-08-14T00:00:00Z", "end": "2026-09-14T00:00:00Z"}


def _ack(period, disposition="applied"):
    return {
        "schema": "opensaas.metering-response/v1",
        "accepted": True,
        "disposition": disposition,
        "period": period,
    }


@pytest.fixture
def platform(monkeypatch):
    monkeypatch.setattr(settings, "platform_report_url", "https://platform.example.com/api/sinas/metering/v1/reports")
    monkeypatch.setattr(settings, "platform_api_key", "sekrit")
    # No real sleeping in retry backoff
    async def _no_sleep(_):
        pass

    monkeypatch.setattr("asyncio.sleep", _no_sleep)


def _patch_client(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr("app.services.metering.httpx.AsyncClient", lambda **kw: client)
    return client


async def _state(db):
    from app.models import MeteringPlatformPeriod

    return (
        await db.execute(
            select(MeteringPlatformPeriod).where(
                MeteringPlatformPeriod.instance_id == INSTANCE
            )
        )
    ).scalar_one_or_none()


class TestPushV2:
    async def test_init_bootstrap_adopts_and_migrates_without_reset(
        self, db, metering_on, platform, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        client = _patch_client(monkeypatch, [_Resp(200, _ack(P1))])

        assert await metering.push(db) == 1

        post = client.posts[0]
        assert post["headers"]["Authorization"] == "Bearer sekrit"
        assert post["headers"]["Idempotency-Key"] == f"{INSTANCE}:{period_id()}:1"
        payload = post["json"]
        assert payload["schema"] == "sinas.metering/v2"
        assert payload["canonical_period_id"] == "init"
        # Decimal strings, nested cumulative, explicit other
        assert payload["cumulative"]["total"] == "2"
        assert payload["cumulative"]["by_kind"]["function"] == "2"
        assert payload["cumulative"]["by_kind"]["other"] == "0"
        assert payload["snapshot_seq"] == "1"

        # Adopted: state persisted, live counters MIGRATED (not reset)
        state = await _state(db)
        assert state is not None and state.period_id == P1["id"]
        assert metering_on.store.get(_k("total", P1["id"])) == 2
        row = (
            await db.execute(
                select(UsagePeriod).where(
                    UsagePeriod.instance_id == INSTANCE,
                    UsagePeriod.period_id == P1["id"],
                )
            )
        ).scalar_one()
        assert row.total == 2  # baseline preserved — no counter regression
        assert row.snapshot_seq == 1  # seq continuity
        assert row.reported_at is not None

    async def test_steady_state_reports_under_cached_period(
        self, db, metering_on, platform, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        _patch_client(monkeypatch, [_Resp(200, _ack(P1))])
        await metering.push(db)

        await record(OperationKind.AGENT)
        await snapshot(db)
        row = (
            await db.execute(
                select(UsagePeriod).where(
                    UsagePeriod.instance_id == INSTANCE,
                    UsagePeriod.period_id == P1["id"],
                )
            )
        ).scalar_one()
        row.updated_at = datetime.now(UTC) + timedelta(seconds=1)
        await db.flush()

        client = _patch_client(monkeypatch, [_Resp(200, _ack(P1))])
        assert await metering.push(db) == 1
        payload = client.posts[0]["json"]
        assert payload["canonical_period_id"] == P1["id"]
        assert payload["cumulative"]["total"] == "2"  # still cumulative
        assert payload["snapshot_seq"] == "2"

    async def test_rollover_in_ack_adopts_and_resets(
        self, db, metering_on, platform, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        _patch_client(monkeypatch, [_Resp(200, _ack(P1))])
        await metering.push(db)

        await record(OperationKind.FUNCTION)
        await snapshot(db)
        row = (
            await db.execute(
                select(UsagePeriod).where(
                    UsagePeriod.instance_id == INSTANCE,
                    UsagePeriod.period_id == P1["id"],
                )
            )
        ).scalar_one()
        row.updated_at = datetime.now(UTC) + timedelta(seconds=1)
        await db.flush()

        # Grace-window apply: 200 whose period moved on
        _patch_client(monkeypatch, [_Resp(200, _ack(P2))])
        assert await metering.push(db) == 1

        state = await _state(db)
        assert state.period_id == P2["id"]
        assert metering_on.store.get(metering._platform_pid_key()) == P2["id"]
        # Old period's live keys age out; new period starts empty (reset)
        assert _k("total", P1["id"]) in metering_on.expired
        assert metering_on.store.get(_k("total", P2["id"])) is None

    async def test_409_period_mismatch_resets_without_marking_reported(
        self, db, metering_on, platform, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        _patch_client(monkeypatch, [_Resp(200, _ack(P1))])
        await metering.push(db)

        await record(OperationKind.FUNCTION)
        await snapshot(db)
        row = (
            await db.execute(
                select(UsagePeriod).where(
                    UsagePeriod.instance_id == INSTANCE,
                    UsagePeriod.period_id == P1["id"],
                )
            )
        ).scalar_one()
        row.updated_at = datetime.now(UTC) + timedelta(seconds=1)
        reported_before = row.reported_at
        await db.flush()

        client = _patch_client(
            monkeypatch,
            [_Resp(409, {"disposition": "period_mismatch", "period": P2})],
        )
        assert await metering.push(db) == 0
        assert len(client.posts) == 1  # no retry on contract 409s
        state = await _state(db)
        assert state.period_id == P2["id"]
        assert row.reported_at == reported_before  # rejected report not marked

    async def test_409_counter_regression_is_terminal_no_adopt(
        self, db, metering_on, platform, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        client = _patch_client(
            monkeypatch, [_Resp(409, {"disposition": "counter_regression"})]
        )
        assert await metering.push(db) == 0
        assert len(client.posts) == 1
        assert await _state(db) is None

    async def test_duplicate_disposition_counts_as_acknowledged(
        self, db, metering_on, platform, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        _patch_client(monkeypatch, [_Resp(200, _ack(P1, disposition="duplicate"))])
        assert await metering.push(db) == 1
        row = (
            await db.execute(
                select(UsagePeriod).where(UsagePeriod.instance_id == INSTANCE)
            )
        ).scalar_one()
        assert row.reported_at is not None

    async def test_no_progress_means_no_push(self, db, metering_on, platform, monkeypatch):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        _patch_client(monkeypatch, [_Resp(200, _ack(P1))])
        assert await metering.push(db) == 1

        _patch_client(monkeypatch, [])
        assert await metering.push(db) == 0  # nothing changed since report

    async def test_failed_push_retries_next_cycle_no_data_loss(
        self, db, metering_on, platform, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await snapshot(db)

        _patch_client(monkeypatch, [_Resp(503), _Resp(503), ConnectionError("dns")])
        assert await metering.push(db) == 0  # all attempts failed, no raise

        row = (
            await db.execute(
                select(UsagePeriod).where(UsagePeriod.instance_id == INSTANCE)
            )
        ).scalar_one()
        assert row.reported_at is None  # still owed

        # Next cycle succeeds; total is cumulative so nothing was lost
        client = _patch_client(monkeypatch, [_Resp(200, _ack(P1))])
        assert await metering.push(db) == 1
        assert client.posts[0]["json"]["cumulative"]["total"] == "1"

    async def test_empty_url_sends_nothing(self, db, metering_on, monkeypatch):
        monkeypatch.setattr(settings, "platform_report_url", "")
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        assert await metering.push(db) == 0


class TestPayloadV2:
    def test_other_absorbs_counter_drift(self):
        from types import SimpleNamespace

        row = SimpleNamespace(
            instance_id=INSTANCE,
            total=10,
            by_kind={"function": 3, "agent": 4},
            snapshot_seq=7,
            last_op_at=None,
        )
        payload = metering._build_payload(row, "init")
        assert payload["cumulative"]["by_kind"]["other"] == "3"
        assert payload["cumulative"]["total"] == "10"
        assert payload["snapshot_seq"] == "7"


# ---------------------------------------------------------------------------
# Jitter
# ---------------------------------------------------------------------------


class TestJitter:
    def test_deterministic_and_bounded(self, monkeypatch):
        monkeypatch.setattr(settings, "metering_instance_id", "acme-prod")
        a = push_jitter_seconds(15)
        b = push_jitter_seconds(15)
        assert a == b
        assert 0 <= a < 15 * 60

    def test_spreads_instances(self, monkeypatch):
        offsets = set()
        for name in ("inst-a", "inst-b", "inst-c", "inst-d", "inst-e"):
            monkeypatch.setattr(settings, "metering_instance_id", name)
            offsets.add(push_jitter_seconds(15))
        assert len(offsets) > 1
