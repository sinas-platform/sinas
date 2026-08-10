"""Operations metering: hot-path counting, snapshots, cumulative push.

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
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


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


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setattr(settings, "metering_endpoint", "https://ops.example.com/v1/usage")
    monkeypatch.setattr(settings, "metering_api_key", "sekrit")
    # No real sleeping in retry backoff
    async def _no_sleep(_):
        pass

    monkeypatch.setattr("asyncio.sleep", _no_sleep)


def _patch_client(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr("app.services.metering.httpx.AsyncClient", lambda **kw: client)
    return client


class TestPush:
    async def test_pushes_cumulative_total_with_idempotency_key(
        self, db, metering_on, endpoint, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        client = _patch_client(monkeypatch, [_Resp(200)])

        pushed = await metering.push(db)
        assert pushed == 1

        post = client.posts[0]
        assert post["headers"]["Authorization"] == "Bearer sekrit"
        assert post["headers"]["Idempotency-Key"] == f"{INSTANCE}:{period_id()}:1"
        payload = post["json"]
        assert payload["schema"] == SCHEMA
        assert payload["total"] == 2  # cumulative, not a delta
        assert payload["by_kind"] == {"function": 2}
        assert payload["snapshot_seq"] == 1

        row = (
            await db.execute(
                select(UsagePeriod).where(UsagePeriod.instance_id == INSTANCE)
            )
        ).scalar_one()
        assert row.reported_at is not None

    async def test_no_progress_means_no_push(self, db, metering_on, endpoint, monkeypatch):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        _patch_client(monkeypatch, [_Resp(200)])
        assert await metering.push(db) == 1

        _patch_client(monkeypatch, [])
        assert await metering.push(db) == 0  # nothing changed since report

    async def test_new_activity_pushes_again_with_next_seq(
        self, db, metering_on, endpoint, monkeypatch
    ):
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        _patch_client(monkeypatch, [_Resp(200)])
        await metering.push(db)

        await record(OperationKind.FUNCTION)
        await snapshot(db)
        # In-test transaction timestamps don't advance, so simulate the
        # updated_at bump a later transaction would produce
        row = (
            await db.execute(
                select(UsagePeriod).where(UsagePeriod.instance_id == INSTANCE)
            )
        ).scalar_one()
        row.updated_at = datetime.now(UTC) + timedelta(seconds=1)
        await db.flush()

        client = _patch_client(monkeypatch, [_Resp(200)])
        assert await metering.push(db) == 1
        payload = client.posts[0]["json"]
        assert payload["total"] == 2  # still cumulative
        assert payload["snapshot_seq"] == 2
        assert client.posts[0]["headers"]["Idempotency-Key"].endswith(":2")

    async def test_failed_push_retries_next_cycle_no_data_loss(
        self, db, metering_on, endpoint, monkeypatch
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
        client = _patch_client(monkeypatch, [_Resp(200)])
        assert await metering.push(db) == 1
        assert client.posts[0]["json"]["total"] == 1

    async def test_empty_endpoint_sends_nothing(self, db, metering_on, monkeypatch):
        monkeypatch.setattr(settings, "metering_endpoint", "")
        await record(OperationKind.FUNCTION)
        await snapshot(db)
        assert await metering.push(db) == 0


# ---------------------------------------------------------------------------
# Rollover
# ---------------------------------------------------------------------------


class TestRollover:
    async def test_previous_period_finalized_and_keys_expired(
        self, db, metering_on, endpoint, monkeypatch
    ):
        prev = metering._previous_period_id(period_id())
        # Leftover counters from last month + fresh activity this month
        metering_on.store[_k("total", prev)] = 40
        metering_on.store[_k("kind:function", prev)] = 40
        await record(OperationKind.AGENT)

        await snapshot(db)
        rows = (
            (
                await db.execute(
                    select(UsagePeriod)
                    .where(UsagePeriod.instance_id == INSTANCE)
                    .order_by(UsagePeriod.period_start)
                )
            )
            .scalars()
            .all()
        )
        assert [r.period_id for r in rows] == [prev, period_id()]
        assert rows[0].total == 40
        assert rows[1].total == 1

        client = _patch_client(monkeypatch, [_Resp(200), _Resp(200)])
        assert await metering.push(db) == 2
        # Old period got its final push; its Redis keys are set to expire
        assert _k("total", prev) in metering_on.expired


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
