"""Redis-based rate limiting for auth endpoints."""

import logging

from fastapi import HTTPException, Request, status

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Count a request and make sure its window expires, in one atomic step.
# INCR then a separate EXPIRE left a counter without a TTL whenever the
# process died (or the connection dropped) between the two: a permanent
# lockout for that IP or account. The script also gives such a counter
# (left by an older version) its TTL back. Redis 6.2 compatible: no
# EXPIRE NX (7.0). Returns {count, seconds left in the window}.
_INCR_WITH_WINDOW = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


async def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> None:
    """
    Enforce a fixed-window rate limit: at most `max_requests` per
    `window_seconds`, counted in Redis.

    Raises HTTP 429 if the limit is exceeded.
    """
    redis = await get_redis()
    redis_key = f"sinas:ratelimit:{key}"

    count, ttl = await redis.eval(_INCR_WITH_WINDOW, 1, redis_key, int(window_seconds))
    count, ttl = int(count), int(ttl)

    if count > max_requests:
        # Retry-After lets automation back off intelligently instead of
        # hammering into the same window. TTL of the window key = seconds left.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(ttl if ttl > 0 else window_seconds)},
        )


async def rate_limit_by_ip(request: Request, action: str, max_requests: int = 10, window_seconds: int = 900) -> None:
    """Rate limit by client IP + action."""
    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"{action}:ip:{client_ip}", max_requests, window_seconds)


async def rate_limit_by_value(value: str, action: str, max_requests: int = 5, window_seconds: int = 900) -> None:
    """Rate limit by an arbitrary value (e.g. email, session_id) + action."""
    await check_rate_limit(f"{action}:{value}", max_requests, window_seconds)
