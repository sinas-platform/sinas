"""Streaming responses (SSE) that log what ended them.

An error raised after a streaming response has started can't become an
error response any more. Starlette then reports a handled exception type
(one with an exception handler, such as Redis' ResponseError for a full
Redis) only as "RuntimeError: Caught handled exception, but response
already started", which hides the real error. So the generators behind the
SSE endpoints log the original error before it escapes.
"""
import logging
from collections.abc import AsyncIterator
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def log_stream_error(what: str, exc: BaseException) -> None:
    """Log the error that ends a started stream; a full Redis by name
    (without the refused command's arguments)."""
    from app.core.redis import is_redis_oom, redis_oom_summary

    if is_redis_oom(exc):
        logger.error(f"{what} ended mid-response: Redis is full: {redis_oom_summary(exc)}")
    else:
        logger.error(f"{what} ended mid-response: {type(exc).__name__}: {exc}", exc_info=exc)


async def logged_stream(stream: AsyncIterator[T], what: str) -> AsyncIterator[T]:
    """Pass `stream` through; log an exception that ends it, then re-raise."""
    try:
        async for item in stream:
            yield item
    except Exception as e:
        log_stream_error(what, e)
        raise
