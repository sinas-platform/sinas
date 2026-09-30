"""Depth-aware admission control for the shared (trusted) worker pool.

Prevents the nested-execution deadlock: a parent `shared_pool` function blocked
while synchronously waiting on a child can starve the pool when every worker is
held by a parent. We reserve slots that only NESTED executions (depth > 0) may
use, so a child can always find capacity.

Opt-in: `shared_pool_reserve = 0` disables it entirely. `-1` scales the
reserve with the pool (a third of the workers, at least 1), so resizing
DEFAULT_WORKER_COUNT doesn't silently strand top-level capacity behind a
stale fixed reserve. An explicit positive value is clamped to leave at least
one top-level slot.

The in-flight set is a Redis sorted-set keyed by execution_id. Each admitted
execution HEARTBEATS its entry (score refreshed every few seconds) while it
runs; entries whose heartbeat goes stale are pruned. This makes slot claims
crash-safe: a worker killed mid-execution (deploy restart, OOM) frees its
slots within ~1 minute. The previous design used a 1-hour age-out, which
after one restart pinned the whole pool behind ghost claims — 13.5K queued
registrations drained single-file for an hour.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

_INFLIGHT_ZSET = "sinas:shared:toplevel_inflight"
# Heartbeat cadence for live entries, and how stale an entry may be before it
# is considered a crashed worker's ghost. Stale window = several missed beats,
# generous for event-loop hiccups but ~1 minute to reclaim after a crash.
_HEARTBEAT_SECONDS = 10
_STALE_AFTER_SECONDS = 60


def effective_reserve() -> int:
    """Resolve the nested-call reserve against the current pool size.

    -1 = auto (a third of the pool, at least 1). Positive values are clamped
    so at least one top-level slot always remains.
    """
    reserve = settings.shared_pool_reserve
    count = settings.default_worker_count
    if reserve == -1:
        return max(1, count // 3)
    if reserve <= 0:
        return 0
    return min(reserve, count - 1)


class SharedPoolSaturated(Exception):
    """Raised when top-level shared-pool admission is at capacity."""


async def _heartbeat(redis, execution_id: str) -> None:
    while True:
        await asyncio.sleep(_HEARTBEAT_SECONDS)
        try:
            await redis.zadd(_INFLIGHT_ZSET, {execution_id: time.time()}, xx=True)
        except Exception as e:  # never let the heartbeat kill the execution
            logger.warning(f"Admission heartbeat failed for {execution_id}: {e}")


@contextlib.asynccontextmanager
async def shared_pool_admission(depth: int, execution_id: str):
    """Admit a shared-pool execution, reserving slots for nested calls.

    Nested executions (depth > 0) bypass the gate — that is the whole point of
    the reserve. Top-level (depth 0) executions are capped at
    `default_worker_count - effective_reserve()`. Raises `SharedPoolSaturated`
    (fail-fast, retryable) when the top-level cap is reached.
    """
    reserve = effective_reserve()
    if reserve <= 0 or depth > 0:
        # Disabled, or a nested call that must always be allowed through.
        yield
        return

    cap = max(1, settings.default_worker_count - reserve)
    redis = await get_redis()
    now = time.time()

    # Prune entries whose heartbeat went stale (crashed worker), then count
    # live top-level executions.
    await redis.zremrangebyscore(_INFLIGHT_ZSET, 0, now - _STALE_AFTER_SECONDS)
    live = await redis.zcard(_INFLIGHT_ZSET)
    if live >= cap:
        raise SharedPoolSaturated(
            f"Shared worker pool saturated: {live}/{cap} top-level slots in use "
            f"({reserve} reserved for nested calls). Retry shortly or scale workers."
        )

    await redis.zadd(_INFLIGHT_ZSET, {execution_id: now})
    beat = asyncio.create_task(_heartbeat(redis, execution_id))
    try:
        yield
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
        with contextlib.suppress(Exception):
            await redis.zrem(_INFLIGHT_ZSET, execution_id)
