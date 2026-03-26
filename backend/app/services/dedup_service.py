"""Webhook deduplication service using Redis."""
import hashlib
import json
import logging
import re
from typing import Any, Optional

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

DEDUP_PREFIX = "sinas:dedup:"


def _extract_key_value(
    key_expr: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> Optional[str]:
    """Extract dedup key value from request body (JSONPath) or headers."""
    if key_expr.startswith("header:"):
        header_name = key_expr[7:]
        return headers.get(header_name) or headers.get(header_name.lower())

    # Simple JSONPath: $.field.subfield
    if key_expr.startswith("$."):
        path = key_expr[2:].split(".")
        node = body
        for part in path:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        return str(node) if node is not None else None

    return None


async def check_and_mark(
    webhook_id: str,
    body: dict[str, Any],
    headers: dict[str, str],
    dedup_config: dict[str, Any],
) -> tuple[bool, Optional[str]]:
    """Check if request is a duplicate.

    Returns:
        (is_duplicate, cached_result) — cached_result is the JSON string of the
        previous result if available (sync mode only).
    """
    key_expr = dedup_config.get("key", "")
    ttl = dedup_config.get("ttl_seconds", 300)

    key_value = _extract_key_value(key_expr, body, headers)
    if not key_value:
        return False, None

    key_hash = hashlib.sha256(key_value.encode()).hexdigest()[:16]
    redis_key = f"{DEDUP_PREFIX}{webhook_id}:{key_hash}"

    redis = await get_redis()

    # SETNX: set if not exists
    was_set = await redis.set(redis_key, "", nx=True, ex=ttl)
    if was_set:
        # First time seeing this key
        return False, None

    # Duplicate — check for cached result
    cached = await redis.get(f"{redis_key}:result")
    return True, cached


async def store_result(
    webhook_id: str,
    body: dict[str, Any],
    headers: dict[str, str],
    dedup_config: dict[str, Any],
    result: str,
) -> None:
    """Cache the result of a sync webhook for dedup responses."""
    key_expr = dedup_config.get("key", "")
    ttl = dedup_config.get("ttl_seconds", 300)

    key_value = _extract_key_value(key_expr, body, headers)
    if not key_value:
        return

    key_hash = hashlib.sha256(key_value.encode()).hexdigest()[:16]
    redis_key = f"{DEDUP_PREFIX}{webhook_id}:{key_hash}:result"

    redis = await get_redis()
    await redis.set(redis_key, result, ex=ttl)
