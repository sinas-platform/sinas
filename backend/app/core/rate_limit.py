"""Redis-based rate limiting for auth endpoints."""

import logging

from fastapi import HTTPException, Request, status

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


async def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> None:
    """
    Enforce a sliding-window rate limit using Redis INCR + EXPIRE.

    Raises HTTP 429 if the limit is exceeded.
    """
    redis = await get_redis()
    redis_key = f"sinas:ratelimit:{key}"

    # The window's expiry is set with the key's creation (SET NX EX), never
    # after the count: an increment that happens without its EXPIRE (a
    # dropped connection in between) used to leave a counter that never
    # reset, refusing its caller forever once at the cap. A counter found
    # without an expiry (from before this change) is repaired the same way.
    await redis.set(redis_key, 0, ex=window_seconds, nx=True)
    count = await redis.incr(redis_key)
    ttl = await redis.ttl(redis_key)
    if ttl is None or ttl < 0:
        await redis.expire(redis_key, window_seconds)
        ttl = window_seconds

    if count > max_requests:
        # Retry-After lets automation back off intelligently instead of
        # hammering into the same window. TTL of the window key = seconds left.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(ttl if ttl and ttl > 0 else window_seconds)},
        )


async def rate_limit_by_ip(request: Request, action: str, max_requests: int = 10, window_seconds: int = 900) -> None:
    """Rate limit by client IP + action."""
    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"{action}:ip:{client_ip}", max_requests, window_seconds)


async def rate_limit_by_value(value: str, action: str, max_requests: int = 5, window_seconds: int = 900) -> None:
    """Rate limit by an arbitrary value (e.g. email, session_id) + action."""
    await check_rate_limit(f"{action}:{value}", max_requests, window_seconds)
