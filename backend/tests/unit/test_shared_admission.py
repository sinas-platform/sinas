"""Shared-pool admission: crash-safe slot claims + scaled reserve.

Field incident: a worker restart orphaned 8 slot claims (the finally-zrem
never ran) and the old 1-hour age-out let those ghosts pin the entire pool —
13.5K queued jobs drained single-file. Claims now heartbeat while running
and go stale in ~1 minute.
"""

import asyncio
import time

import pytest

from app.core.config import settings
from app.services import shared_admission
from app.services.shared_admission import (
    _INFLIGHT_ZSET,
    _STALE_AFTER_SECONDS,
    SharedPoolSaturated,
    effective_reserve,
    shared_pool_admission,
)


class _FakeZRedis:
    """Just enough of Redis sorted sets for the admission gate."""

    def __init__(self):
        self.z: dict[str, float] = {}

    async def zremrangebyscore(self, key, lo, hi):
        dead = [m for m, s in self.z.items() if lo <= s <= hi]
        for m in dead:
            del self.z[m]
        return len(dead)

    async def zcard(self, key):
        return len(self.z)

    async def zadd(self, key, mapping, xx=False):
        for m, s in mapping.items():
            if xx and m not in self.z:
                continue
            self.z[m] = s
        return len(mapping)

    async def zrem(self, key, member):
        return 1 if self.z.pop(member, None) is not None else 0


@pytest.fixture
def zredis(monkeypatch):
    r = _FakeZRedis()

    async def _get():
        return r

    monkeypatch.setattr("app.services.shared_admission.get_redis", _get)
    monkeypatch.setattr(settings, "default_worker_count", 12)
    monkeypatch.setattr(settings, "shared_pool_reserve", 4)
    return r


class TestEffectiveReserve:
    def test_auto_scales_with_pool(self, monkeypatch):
        monkeypatch.setattr(settings, "shared_pool_reserve", -1)
        for count, expected in ((4, 1), (12, 4), (24, 8), (2, 1)):
            monkeypatch.setattr(settings, "default_worker_count", count)
            assert effective_reserve() == expected

    def test_explicit_value_clamped_to_leave_a_slot(self, monkeypatch):
        monkeypatch.setattr(settings, "default_worker_count", 4)
        monkeypatch.setattr(settings, "shared_pool_reserve", 4)
        # Old behavior: cap = max(1, 4-4) = ... reserve swallowed the pool.
        assert effective_reserve() == 3  # at least one top-level slot

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setattr(settings, "shared_pool_reserve", 0)
        assert effective_reserve() == 0


class TestCrashSafety:
    async def test_ghost_claims_pruned_after_stale_window(self, zredis):
        """A restart-orphaned claim must free its slot in ~a minute, not an
        hour — this is the regression that serialized 13.5K jobs."""
        now = time.time()
        for i in range(8):  # fill the cap (12-4=8) with restart ghosts
            zredis.z[f"ghost-{i}"] = now - _STALE_AFTER_SECONDS - 5

        async with shared_pool_admission(0, "fresh-exec"):
            assert "fresh-exec" in zredis.z
        assert all(not m.startswith("ghost") for m in zredis.z)

    async def test_live_claims_are_not_pruned(self, zredis):
        now = time.time()
        for i in range(8):
            zredis.z[f"live-{i}"] = now - 5  # recent heartbeats

        with pytest.raises(SharedPoolSaturated):
            async with shared_pool_admission(0, "x"):
                pass  # pragma: no cover
        assert len(zredis.z) == 8

    async def test_heartbeat_refreshes_score(self, zredis, monkeypatch):
        monkeypatch.setattr(shared_admission, "_HEARTBEAT_SECONDS", 0.05)
        async with shared_pool_admission(0, "beating"):
            first = zredis.z["beating"]
            await asyncio.sleep(0.2)
            assert zredis.z["beating"] > first  # refreshed while running
        assert "beating" not in zredis.z  # removed on exit

    async def test_claim_removed_even_on_execution_error(self, zredis):
        with pytest.raises(RuntimeError):
            async with shared_pool_admission(0, "err-exec"):
                raise RuntimeError("function blew up")
        assert "err-exec" not in zredis.z

    async def test_nested_calls_bypass(self, zredis):
        now = time.time()
        for i in range(12):
            zredis.z[f"live-{i}"] = now
        async with shared_pool_admission(depth=1, execution_id="nested"):
            pass  # admitted despite a full pool
        assert "nested" not in zredis.z  # bypass never claims
