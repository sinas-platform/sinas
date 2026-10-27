"""A full Redis shows up, rather than losing state silently.

The in-cluster Redis now runs maxmemory-policy noeviction: at maxmemory it
refuses writes. So the System page shows Redis memory against maxmemory and
warns before it's full, on a policy that evicts, and on evictions or refused
writes; and a request whose write Redis refused answers 503 saying why
instead of an anonymous 500.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.endpoints import system as system_endpoint
from app.core.redis import get_redis
from tests.conftest import auth_headers


def _mem(**overrides):
    return {
        "used_bytes": 10 * 2**20,
        "maxmemory_bytes": 256 * 2**20,
        "percent": 3.9,
        "policy": "noeviction",
        "evicted_keys": 0,
        "oom_errors": 0,
        **overrides,
    }


class TestWarnings:
    def test_healthy_redis_says_nothing(self):
        assert system_endpoint._redis_warnings(_mem()) == []
        assert system_endpoint._redis_warnings(None) == []
        # no maxmemory: nothing to measure against
        assert system_endpoint._redis_warnings(_mem(maxmemory_bytes=None, percent=None)) == []

    @pytest.mark.parametrize("percent,level", [(74.9, None), (75, "warning"), (89.9, "warning"), (90, "critical")])
    def test_memory_thresholds(self, percent, level):
        warnings = system_endpoint._redis_warnings(_mem(percent=percent))
        if level is None:
            assert warnings == []
        else:
            (w,) = warnings
            assert w["level"] == level
            assert f"{percent}%" in w["message"] and "writes fail when it is full" in w["message"]

    def test_evicting_policy_is_flagged(self):
        messages = [w["message"] for w in system_endpoint._redis_warnings(_mem(policy="allkeys-lru"))]
        assert any("allkeys-lru" in m and "noeviction" in m for m in messages)

    def test_refused_writes_and_evictions(self):
        warnings = system_endpoint._redis_warnings(_mem(oom_errors=3, evicted_keys=12))
        levels = {w["level"]: w["message"] for w in warnings}
        assert "refused 3 write" in levels["critical"]
        assert "evicted 12 key" in levels["warning"]

    def test_dropped_dead_letters_are_flagged(self):
        stats = {"dlq": {"size": 10000, "dropped": 7, "max_entries": 10000}}
        warnings = system_endpoint._generate_warnings([], [{"queue": "functions"}, {"queue": "agents"}], 10000, stats, {})
        assert any("7 dead-lettered job(s) were dropped" in w["message"] for w in warnings)


async def test_memory_read_from_the_real_redis():
    redis = await get_redis()
    info = await redis.info("memory")
    mem = await system_endpoint._redis_memory()

    assert mem["used_bytes"] > 0
    assert mem["policy"] == info["maxmemory_policy"]
    assert mem["evicted_keys"] >= 0
    if mem["maxmemory_bytes"]:
        assert 0 < mem["percent"] <= 100 * mem["used_bytes"] / mem["maxmemory_bytes"] + 0.1
    else:
        assert mem["percent"] is None


async def test_health_endpoint_carries_redis_memory(client, admin_user, monkeypatch):
    async def no_services(include_stats):
        return [], None

    monkeypatch.setattr(system_endpoint, "_docker_services", no_services)
    resp = await client.get("/api/v1/system/health", headers=auth_headers(admin_user))
    assert resp.status_code == 200, resp.text
    redis_memory = resp.json()["redis"]
    assert redis_memory["used_bytes"] > 0 and redis_memory["policy"]


async def test_unreadable_redis_degrades_to_none(monkeypatch):
    class Down:
        async def info(self, section=None):
            raise ConnectionError("down")

    async def get_down():
        return Down()

    monkeypatch.setattr("app.core.redis.get_redis", get_down)
    assert await system_endpoint._redis_memory() is None


_SCRIPT_OOM = (  # how Redis 6.2 reports a write refused inside a Lua script
    "Error running script (call to f_0eb3): @user_script:2: @user_script: 2: "
    "-OOM command not allowed when used memory > 'maxmemory'."
)


def test_oom_detection():
    from redis.exceptions import OutOfMemoryError, ResponseError

    from app.core.redis import is_redis_oom

    assert is_redis_oom(OutOfMemoryError("command not allowed when used memory > 'maxmemory'."))
    assert is_redis_oom(ResponseError(_SCRIPT_OOM))
    assert not is_redis_oom(ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value"))
    assert not is_redis_oom(ValueError("OOM command not allowed"))


@pytest.mark.parametrize("openai_format", [False, True], ids=["sinas", "openai"])
async def test_handler_answers_oom_and_leaves_other_redis_errors(openai_format):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from redis.exceptions import OutOfMemoryError, ResponseError

    from app.core.redis import install_redis_oom_handler

    app = FastAPI()
    install_redis_oom_handler(app, openai_format=openai_format)

    @app.get("/oom")
    async def oom():
        raise OutOfMemoryError("command not allowed when used memory > 'maxmemory'.")

    @app.get("/script-oom")
    async def script_oom():
        raise ResponseError(_SCRIPT_OOM)

    @app.get("/other")
    async def other():
        raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        for path in ("/oom", "/script-oom"):
            resp = await c.get(path)
            assert resp.status_code == 503
            body = resp.json()
            code = body["error"]["code"] if openai_format else body["code"]
            assert code == "redis_out_of_memory"
        assert (await c.get("/other")).status_code == 500


@pytest.fixture
async def full_redis():
    """The test Redis at its maxmemory under noeviction (restored after)."""
    redis = await get_redis()
    before = {  # one name per CONFIG GET: several need Redis 7
        **await redis.config_get("maxmemory"),
        **await redis.config_get("maxmemory-policy"),
    }
    await redis.config_set("maxmemory-policy", "noeviction")
    await redis.config_set("maxmemory", 1)  # 1 byte: every write is refused
    try:
        yield redis
    finally:
        await redis.config_set("maxmemory", before["maxmemory"])
        await redis.config_set("maxmemory-policy", before["maxmemory-policy"])


async def test_a_refused_write_answers_503(client, test_user, full_redis):
    """Login counts the request in Redis first (the rate limiter); a full
    Redis refuses that write. The caller gets 503 and what to do."""
    with patch("app.api.runtime.endpoints.authentication.create_otp_session") as create:
        create.return_value = AsyncMock(id=uuid.uuid4())
        resp = await client.post("/auth/login", json={"email": test_user.email})

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == "redis_out_of_memory"
    assert "maxmemory" in body["detail"]
    assert resp.headers["Retry-After"] == "30"

    mem = await system_endpoint._redis_memory()
    assert mem["policy"] == "noeviction" and mem["percent"] > 100
    if mem["oom_errors"] is not None:  # errorstats: Redis 6.2+
        assert mem["oom_errors"] >= 1
