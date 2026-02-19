"""Redis connection management for queue and caching."""
import logging
from urllib.parse import urlparse

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Redis | None = None
_arq_pool: ArqRedis | None = None


def get_redis_settings() -> RedisSettings:
    """Parse redis_url into arq RedisSettings."""
    parsed = urlparse(settings.redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "0"),
        password=parsed.password,
    )


async def get_redis() -> Redis:
    """Get async Redis client (lazy-init with connection pool)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
    return _redis_client


async def get_arq_pool() -> ArqRedis:
    """Get arq Redis pool for enqueuing jobs."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(get_redis_settings())
    return _arq_pool


async def close_redis() -> None:
    """Cleanup Redis connections on shutdown."""
    global _redis_client, _arq_pool
    if _arq_pool is not None:
        await _arq_pool.aclose()
        _arq_pool = None
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
    logger.info("Redis connections closed")
