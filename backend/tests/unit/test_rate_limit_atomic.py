"""The auth rate limiter counts and sets the window in one atomic step.

It used to INCR and then EXPIRE in a second command: a process dying
between the two left a counter with no TTL, a permanent lockout for that
IP or account. These run against the real Redis (Lua via EVAL, Redis 6.2+).
"""

import pytest
from fastapi import HTTPException

from app.core.rate_limit import check_rate_limit
from app.core.redis import get_redis


async def test_counts_and_limits_within_the_window():
    for _ in range(3):
        await check_rate_limit("t:limit", max_requests=3, window_seconds=60)
    with pytest.raises(HTTPException) as exc:
        await check_rate_limit("t:limit", max_requests=3, window_seconds=60)

    assert exc.value.status_code == 429
    assert 0 < int(exc.value.headers["Retry-After"]) <= 60
    redis = await get_redis()
    assert await redis.get("sinas:ratelimit:t:limit") == "4"


async def test_the_first_request_opens_a_window_with_a_ttl():
    await check_rate_limit("t:ttl", max_requests=5, window_seconds=120)
    redis = await get_redis()
    assert 0 < await redis.ttl("sinas:ratelimit:t:ttl") <= 120


async def test_later_requests_do_not_extend_the_window():
    redis = await get_redis()
    await check_rate_limit("t:fixed", max_requests=5, window_seconds=120)
    await redis.expire("sinas:ratelimit:t:fixed", 30)  # 90s into the window
    await check_rate_limit("t:fixed", max_requests=5, window_seconds=120)
    assert await redis.ttl("sinas:ratelimit:t:fixed") <= 30


async def test_a_counter_left_without_a_ttl_gets_one_back():
    """What the old INCR-then-EXPIRE left behind after a crash: over the
    limit and never expiring. The next request gives it its window."""
    redis = await get_redis()
    await redis.set("sinas:ratelimit:t:stuck", 50)  # no TTL
    assert await redis.ttl("sinas:ratelimit:t:stuck") == -1

    with pytest.raises(HTTPException) as exc:
        await check_rate_limit("t:stuck", max_requests=5, window_seconds=90)

    assert 0 < await redis.ttl("sinas:ratelimit:t:stuck") <= 90
    assert 0 < int(exc.value.headers["Retry-After"]) <= 90


async def test_one_round_trip_no_separate_expire(monkeypatch):
    """Nothing but the script touches the key: no INCR/EXPIRE pair to be
    split by a crash."""
    redis = await get_redis()
    calls: list[str] = []
    real_eval = redis.eval

    async def eval_(*args, **kwargs):
        calls.append("eval")
        return await real_eval(*args, **kwargs)

    for name in ("incr", "expire", "ttl"):
        async def forbidden(*a, _name=name, **k):
            raise AssertionError(f"rate limiter called {_name} outside the script")

        monkeypatch.setattr(redis, name, forbidden)
    monkeypatch.setattr(redis, "eval", eval_)

    await check_rate_limit("t:one", max_requests=5, window_seconds=60)
    assert calls == ["eval"]
