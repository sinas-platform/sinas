"""Fill the test Redis to its maxmemory (noeviction), and put it back.

Tests that check what Sinas does when Redis is full set `maxmemory` with
CONFIG SET, which changes the whole server. So `fill_redis` refuses (skips)
unless REDIS_URL points at a throwaway Redis: a local host on a port other
than 6379, the default every real install uses (CI maps its Redis to 6390
for this). The previous config is restored by a finalizer with its own
synchronous client, so it runs even when the test fails or is interrupted.
"""
from urllib.parse import urlparse

import pytest
import redis as redis_sync

from app.core.config import settings

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def throwaway_redis_url() -> str:
    """REDIS_URL if it is safe to reconfigure, else skip the test."""
    url = settings.redis_url
    parsed = urlparse(url)
    if parsed.scheme != "redis" or parsed.hostname not in _LOCAL_HOSTS or (parsed.port or 6379) == 6379:
        pytest.skip(
            f"refusing to CONFIG SET maxmemory on {parsed.hostname}:{parsed.port or 6379}: "
            "point REDIS_URL at a throwaway local Redis on a port other than 6379"
        )
    return url


class RedisFiller:
    def __init__(self, url: str):
        self.client = redis_sync.Redis.from_url(url)
        self.before = {
            # one name per CONFIG GET: several need Redis 7
            **self.client.config_get("maxmemory"),
            **self.client.config_get("maxmemory-policy"),
        }

    def fill(self, below_used: int = 0) -> None:
        """Every write that can grow memory is refused from now on. With
        `below_used`, maxmemory goes that many bytes under current use
        instead of to 1 byte: Redis is full, but deletes and expiries free
        enough now and then for a few writes to get through, as in a real
        Redis at its limit."""
        self.client.config_set("maxmemory-policy", "noeviction")
        if below_used:
            used = int(self.client.info("memory")["used_memory"])
            self.client.config_set("maxmemory", max(1, used - below_used))
        else:
            self.client.config_set("maxmemory", 1)

    def release(self) -> None:
        self.client.config_set("maxmemory", self.before["maxmemory"])
        self.client.config_set("maxmemory-policy", self.before["maxmemory-policy"])


def make_filler(request) -> RedisFiller:
    """The `fill_redis` fixture (tests/conftest.py)."""
    filler = RedisFiller(throwaway_redis_url())

    def restore():
        try:
            filler.release()
        finally:
            filler.client.close()

    request.addfinalizer(restore)
    return filler
