"""The switchboard: a manifest's exposed tools served to external agents at
POST /mcp/{namespace}/{name}, each caller seeing only what their own
permissions allow."""

import contextlib
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.runtime import switchboard as switchboard_endpoint
from app.core.auth import create_api_key
from app.models.function import Function
from app.models.manifest import Manifest
from app.models.user import Role, RolePermission, User, UserRole
from app.schemas.spec.manifest import ManifestSpec
from app.services import switchboard
from tests.conftest import auth_headers

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _same_session(db, monkeypatch):
    """The switchboard opens its own sessions; the fixture's rows live in a
    rolled-back transaction those can't see, so hand it the fixture's."""

    @contextlib.asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(switchboard_endpoint, "AsyncSessionLocal", _session)


async def _function(db, owner, namespace, name, **extra) -> Function:
    row = Function(
        user_id=owner.id, namespace=namespace, name=name, description=extra.pop("description", f"does {name}"),
        code="def main(input):\n    return input\n",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        output_schema={"type": "object", "properties": {"y": {"type": "integer"}}},
        **extra,
    )
    db.add(row)
    await db.flush()
    return row


async def _manifest(db, owner, exposed, namespace="gw", name=None) -> Manifest:
    row = Manifest(namespace=namespace, name=name or f"hub-{_uid()}", user_id=owner.id, exposed_tools=exposed)
    db.add(row)
    await db.flush()
    return row


async def _user_with(db, *perms) -> User:
    user = User(email=f"ext-{_uid()}@example.com")
    db.add(user)
    await db.flush()
    role = Role(name=f"ext-{_uid()}", description="external agent")
    db.add(role)
    await db.flush()
    for key in perms:
        db.add(RolePermission(role_id=role.id, permission_key=key, permission_value=True))
    db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
    await db.flush()
    return user


def _rpc(method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


async def _call(client, path, headers, method, params=None):
    r = await client.post(path, headers={**MCP_HEADERS, **headers}, json=_rpc(method, params))
    assert r.status_code == 200, (r.status_code, r.text)
    payload = r.json()
    assert payload.get("jsonrpc") == "2.0", payload
    return payload


# ---------------------------------------------------------------- catalog


@pytest.mark.asyncio
async def test_catalog_is_the_manifests_exposed_tools_the_caller_may_run(client, db: AsyncSession, admin_user):
    allowed = await _function(db, admin_user, "tools", f"allowed-{_uid()}")
    hidden = await _function(db, admin_user, "tools", f"hidden-{_uid()}")
    inactive = await _function(db, admin_user, "tools", f"off-{_uid()}", is_active=False)
    unexposed = await _function(db, admin_user, "tools", f"private-{_uid()}")
    manifest = await _manifest(db, admin_user, [
        {"type": "function", "namespace": "tools", "name": allowed.name, "tool_name": "do_allowed", "description": "Allowed thing"},
        {"type": "function", "namespace": "tools", "name": hidden.name},
        {"type": "function", "namespace": "tools", "name": inactive.name},
        {"type": "function", "namespace": "tools", "name": "nope-missing"},
    ])
    caller = await _user_with(
        db,
        f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all",
        f"sinas.functions/tools/{allowed.name}.execute:all",
        f"sinas.functions/tools/{unexposed.name}.execute:all",  # may run it, but it is not exposed
        f"sinas.functions/tools/{inactive.name}.execute:all",
    )
    path = f"/mcp/{manifest.namespace}/{manifest.name}"

    listed = await _call(client, path, auth_headers(caller), "tools/list")
    tools = listed["result"]["tools"]
    assert [t["name"] for t in tools] == ["do_allowed"]
    assert tools[0]["description"] == "Allowed thing"
    assert tools[0]["inputSchema"]["properties"]["x"]["type"] == "integer"
    assert tools[0]["outputSchema"]["properties"]["y"]["type"] == "integer"

    # The owner (admin) sees every exposed, existing, active tool — and the
    # unnamed one under its default name.
    listed = await _call(client, path, auth_headers(admin_user), "tools/list")
    assert sorted(t["name"] for t in listed["result"]["tools"]) == sorted(["do_allowed", f"tools__{hidden.name}"])


@pytest.mark.asyncio
async def test_default_tool_names_are_mcp_safe_and_distinct_across_types(db: AsyncSession, admin_user):
    from app.models.database_connection import DatabaseConnection
    from app.models.query import Query

    await _function(db, admin_user, "my.ns", "do-it")
    conn = DatabaseConnection(name=f"db-{_uid()}", connection_type="postgresql", host="localhost", port=5432,
                              database="x", username="u")
    db.add(conn)
    await db.flush()
    q = Query(user_id=admin_user.id, namespace="my.ns", name="do-it", database_connection_id=conn.id,
              operation="read", sql="select 1", input_schema={"type": "object", "properties": {}}, output_schema={})
    db.add(q)
    await db.flush()
    manifest = await _manifest(db, admin_user, [
        {"type": "function", "namespace": "my.ns", "name": "do-it"},
        {"type": "query", "namespace": "my.ns", "name": "do-it"},
    ])
    from app.core.auth import get_user_permissions

    perms = await get_user_permissions(db, str(admin_user.id))
    tools = await switchboard.list_tools(db, manifest, str(admin_user.id), perms)
    assert [t.name for t in tools] == ["my_ns__do-it", "query_my_ns__do-it"]
    assert {t.type for t in tools} == {"function", "query"}


# ------------------------------------------------------------------- calls


@pytest.mark.asyncio
async def test_call_runs_the_resource_as_the_caller_and_records_it(client, db: AsyncSession, admin_user, monkeypatch):
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"sum-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "sum"}])
    caller = await _user_with(
        db, f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all",
        f"sinas.functions/tools/{fn.name}.execute:all",
    )
    seen = {}

    async def fake_execute(self, db_, tool_name, arguments, user_id, user_token, **kwargs):
        seen.update(tool_name=tool_name, arguments=arguments, user_id=user_id, token=user_token, kwargs=kwargs)
        return {"y": arguments["x"] * 2}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    headers = auth_headers(caller)
    out = await _call(client, f"/mcp/{manifest.namespace}/{manifest.name}", headers, "tools/call",
                      {"name": "sum", "arguments": {"x": 21}})
    result = out["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {"y": 42}
    assert result["structuredContent"] == {"y": 42}
    assert seen["tool_name"] == f"tools/{fn.name}" and seen["user_id"] == str(caller.id)
    assert seen["token"] == headers["Authorization"].split(" ", 1)[1]
    assert seen["kwargs"]["enabled_functions"] == [f"tools/{fn.name}"]

    from sqlalchemy import select

    from app.models.switchboard_call import SwitchboardCall

    (row,) = (await db.execute(select(SwitchboardCall).where(SwitchboardCall.user_id == caller.id))).scalars().all()
    assert (row.manifest_id, row.manifest_namespace, row.manifest_name) == (manifest.id, manifest.namespace, manifest.name)
    assert (row.tool_name, row.resource_type, row.resource_ref) == ("sum", "function", f"tools/{fn.name}")
    assert row.arguments == {"x": 21} and row.result == {"y": 42} and row.is_error is False
    assert row.api_key_id is None and row.duration_ms is not None


@pytest.mark.asyncio
async def test_a_tool_the_caller_may_not_run_is_unknown_to_them(client, db: AsyncSession, admin_user):
    fn = await _function(db, admin_user, "tools", f"secret-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "secret"}])
    caller = await _user_with(db, f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all")
    out = await _call(client, f"/mcp/{manifest.namespace}/{manifest.name}", auth_headers(caller), "tools/call",
                      {"name": "secret", "arguments": {}})
    assert "error" in out and "Unknown tool" in out["error"]["message"]


@pytest.mark.asyncio
async def test_a_failed_call_is_an_error_result_not_a_protocol_error(client, db: AsyncSession, admin_user, monkeypatch):
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"boom-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "boom"}])

    async def fake_execute(self, *a, **k):
        return {"error": "Permission denied", "message": "nope"}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    out = await _call(client, f"/mcp/{manifest.namespace}/{manifest.name}", auth_headers(admin_user), "tools/call",
                      {"name": "boom", "arguments": {}})
    assert out["result"]["isError"] is True
    assert "Permission denied" in out["result"]["content"][0]["text"]
    assert out["result"].get("structuredContent") is None


# -------------------------------------------------------------------- access


@pytest.mark.asyncio
async def test_switchboard_requires_a_credential_and_the_manifest(client, db: AsyncSession, admin_user):
    manifest = await _manifest(db, admin_user, [])
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    r = await client.post(path, headers=MCP_HEADERS, json=_rpc("tools/list"))
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    r = await client.post(path, headers={**MCP_HEADERS, "Authorization": "Bearer not-a-key"}, json=_rpc("tools/list"))
    assert r.status_code == 401
    r = await client.post("/mcp/gw/does-not-exist", headers={**MCP_HEADERS, **auth_headers(admin_user)}, json=_rpc("tools/list"))
    assert r.status_code == 404
    r = await client.post("/mcp/only-one-segment", headers={**MCP_HEADERS, **auth_headers(admin_user)}, json=_rpc("tools/list"))
    assert r.status_code == 404

    stranger = await _user_with(db, "sinas.functions/*/*.execute:all")  # may run things, may not use this switchboard
    r = await client.post(path, headers={**MCP_HEADERS, **auth_headers(stranger)}, json=_rpc("tools/list"))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_an_api_key_works_and_narrows_the_catalog_to_its_own_permissions(client, db: AsyncSession, admin_user):
    a = await _function(db, admin_user, "tools", f"a-{_uid()}")
    b = await _function(db, admin_user, "tools", f"b-{_uid()}")
    manifest = await _manifest(db, admin_user, [
        {"type": "function", "namespace": "tools", "name": a.name, "tool_name": "a"},
        {"type": "function", "namespace": "tools", "name": b.name, "tool_name": "b"},
    ])
    _, plain = await create_api_key(db, admin_user, f"agent-{_uid()}", {
        f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all": True,
        f"sinas.functions/tools/{a.name}.execute:all": True,
    })
    await db.flush()
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    for headers in ({"Authorization": f"Bearer {plain}"}, {"X-API-Key": plain}):
        listed = await _call(client, path, headers, "tools/list")
        assert [t["name"] for t in listed["result"]["tools"]] == ["a"]


# --------------------------------------------------------------- declaring


def test_spec_validates_exposed_tools():
    ManifestSpec(name="ok", exposedTools=[{"type": "function", "namespace": "t", "name": "f", "toolName": "run_f"}])
    with pytest.raises(ValueError, match="type must be one of"):
        ManifestSpec(name="bad", exposedTools=[{"type": "agent", "namespace": "t", "name": "a"}])
    with pytest.raises(ValueError, match="would both be called"):
        ManifestSpec(name="dup", exposedTools=[
            {"type": "function", "namespace": "t", "name": "f", "toolName": "same"},
            {"type": "pipeline", "namespace": "t", "name": "p", "toolName": "same"},
        ])
    with pytest.raises(ValueError):
        ManifestSpec(name="bad-name", exposedTools=[{"type": "function", "namespace": "t", "name": "f", "toolName": "has space"}])
    # Export leaves the list out when empty and keeps the camelCase form otherwise.
    assert "exposedTools" not in ManifestSpec(name="plain").to_config()
    cfg = ManifestSpec(name="x", exposedTools=[{"type": "query", "namespace": "t", "name": "q"}]).to_config()
    assert cfg["exposedTools"] == [{"type": "query", "namespace": "t", "name": "q"}]


@pytest.mark.asyncio
async def test_rest_and_config_declare_exposed_tools_and_refuse_missing_resources(client, db: AsyncSession, admin_user):
    from app.schemas.config import SinasConfig
    from app.services.config_apply.service import ConfigApplyService

    fn = await _function(db, admin_user, "tools", f"real-{_uid()}")
    headers = auth_headers(admin_user)
    name = f"api-{_uid()}"
    r = await client.post("/api/v1/manifests", headers=headers, json={
        "namespace": "gw", "name": name,
        "exposed_tools": [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "real"}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["exposed_tools"] == [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "real", "description": None}]
    r = await client.post("/api/v1/manifests", headers=headers, json={
        "namespace": "gw", "name": f"missing-{_uid()}",
        "exposed_tools": [{"type": "function", "namespace": "tools", "name": "ghost"}],
    })
    assert r.status_code == 404 and "ghost" in r.json()["detail"]
    # /status counts an exposed tool's resource like a required one.
    r = await client.get(f"/manifests/gw/{name}/status", headers=headers)
    assert r.status_code == 200 and r.json()["ready"] is True
    assert [x["name"] for x in r.json()["resources"]["satisfied"]] == [fn.name]

    cfg_name = f"cfg-{_uid()}"
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
        "spec": {
            "functions": [{"namespace": "gw", "name": "declared", "code": "def main(i):\n    return i\n",
                           "inputSchema": {"type": "object", "properties": {}}, "outputSchema": {"type": "object", "properties": {}}}],
            "manifests": [{"namespace": "gw", "name": cfg_name, "exposedTools": [
                {"type": "function", "namespace": "gw", "name": "declared", "toolName": "declared"},
                {"type": "function", "namespace": "tools", "name": fn.name},
            ]}],
        },
    })
    svc = ConfigApplyService(db, "cfg", owner_user_id=str(admin_user.id), managed_by="config", auto_commit=False)
    result = await svc.apply_config(config)
    assert result.success, result.errors
    row = await Manifest.get_by_name(db, "gw", cfg_name)
    assert [t["tool_name"] for t in row.exposed_tools if "tool_name" in t] == ["declared"]
    assert row.exposed_tools[1] == {"type": "function", "namespace": "tools", "name": fn.name}


@pytest.mark.asyncio
async def test_a_real_mcp_client_can_list_and_call(app, db: AsyncSession, admin_user, monkeypatch):
    """Protocol conformance: the SDK's own client against the switchboard, over
    an in-process transport (initialize, tools/list, tools/call)."""
    import httpx
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"echo-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "echo"}])

    async def fake_execute(self, db_, tool_name, arguments, *a, **k):
        return {"echo": arguments}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)

    @contextlib.asynccontextmanager
    async def transport():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=auth_headers(admin_user))
        async with http:
            async with streamable_http_client(f"http://test/mcp/{manifest.namespace}/{manifest.name}", http_client=http) as streams:
                yield streams

    async with Client(transport()) as mcp_client:
        listed = await mcp_client.list_tools()
        assert [t.name for t in listed.tools] == ["echo"]
        result = await mcp_client.call_tool("echo", {"x": 1})
        assert not result.is_error
        assert json.loads(result.content[0].text) == {"echo": {"x": 1}}


# ------------------------------------------------------------- rate limits


@pytest_asyncio.fixture
async def _fresh_limits():
    """Rate-limit counters live in Redis across tests: clear the switchboard's."""
    from app.core.redis import get_redis

    redis = await get_redis()
    async for key in redis.scan_iter("sinas:ratelimit:switchboard:*"):
        await redis.delete(key)
    yield


@pytest.mark.asyncio
async def test_calls_past_the_manifests_limit_are_refused_with_a_retry_hint(client, db: AsyncSession, admin_user, monkeypatch, _fresh_limits):
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"lim-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "lim"}])
    manifest.switchboard_limits = {"max_calls": 2, "window_seconds": 60}
    await db.flush()

    async def fake_execute(self, *a, **k):
        return {"ok": True}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    headers = auth_headers(admin_user)
    for _ in range(2):
        out = await _call(client, path, headers, "tools/call", {"name": "lim", "arguments": {}})
        assert out["result"]["isError"] is False
    out = await _call(client, path, headers, "tools/call", {"name": "lim", "arguments": {}})
    assert out["error"]["code"] == switchboard_endpoint.RATE_LIMITED
    assert "2 calls per 60s" in out["error"]["message"]
    assert 0 < out["error"]["data"]["retry_after_seconds"] <= 60
    # Listing is never counted.
    listed = await _call(client, path, headers, "tools/list")
    assert [t["name"] for t in listed["result"]["tools"]] == ["lim"]


@pytest.mark.asyncio
async def test_each_api_key_is_its_own_caller_and_zero_disables(client, db: AsyncSession, admin_user, monkeypatch, _fresh_limits):
    from app.core.config import settings
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"keys-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "k"}])
    monkeypatch.setattr(settings, "switchboard_rate_limit_max_calls", 1)  # the deployment default applies: no manifest override

    async def fake_execute(self, *a, **k):
        return {"ok": True}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    perms = {
        f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all": True,
        f"sinas.functions/tools/{fn.name}.execute:all": True,
    }
    _, key_a = await create_api_key(db, admin_user, f"a-{_uid()}", perms)
    _, key_b = await create_api_key(db, admin_user, f"b-{_uid()}", perms)
    await db.flush()
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    call = {"name": "k", "arguments": {}}
    assert (await _call(client, path, {"X-API-Key": key_a}, "tools/call", call))["result"]["isError"] is False
    assert (await _call(client, path, {"X-API-Key": key_a}, "tools/call", call))["error"]["code"] == switchboard_endpoint.RATE_LIMITED
    # Same user, other key: its own budget.
    assert (await _call(client, path, {"X-API-Key": key_b}, "tools/call", call))["result"]["isError"] is False

    manifest.switchboard_limits = {"max_calls": 0, "window_seconds": 60}
    await db.flush()
    for _ in range(3):
        assert (await _call(client, path, {"X-API-Key": key_a}, "tools/call", call))["result"]["isError"] is False


def test_limits_declare_and_export():
    spec = ManifestSpec(name="x", switchboardLimits={"maxCalls": 10})
    assert spec.switchboard_limits.window_seconds == 60
    assert ManifestSpec(name="x", switchboard_limits={"max_calls": 10, "window_seconds": 5}).to_config()["switchboardLimits"] == {"maxCalls": 10, "windowSeconds": 5}
    assert "switchboardLimits" not in ManifestSpec(name="plain").to_config()
    with pytest.raises(ValueError):
        ManifestSpec(name="bad", switchboardLimits={"maxCalls": -1})


@pytest.mark.asyncio
async def test_rest_and_config_carry_the_limits(client, db: AsyncSession, admin_user):
    from app.schemas.config import SinasConfig
    from app.services.config_apply.service import ConfigApplyService

    headers = auth_headers(admin_user)
    name = f"lim-{_uid()}"
    r = await client.post("/api/v1/manifests", headers=headers, json={"namespace": "gw", "name": name, "switchboard_limits": {"max_calls": 5}})
    assert r.status_code == 201, r.text
    assert r.json()["switchboard_limits"] == {"max_calls": 5, "window_seconds": 60}
    r = await client.put(f"/api/v1/manifests/gw/{name}", headers=headers, json={"switchboard_limits": {"max_calls": 7, "window_seconds": 10}})
    assert r.status_code == 200 and r.json()["switchboard_limits"] == {"max_calls": 7, "window_seconds": 10}

    # An explicit null clears the override (back to the deployment default); an omitted field keeps it.
    r = await client.put(f"/api/v1/manifests/gw/{name}", headers=headers, json={"description": "kept"})
    assert r.json()["switchboard_limits"] == {"max_calls": 7, "window_seconds": 10}
    r = await client.put(f"/api/v1/manifests/gw/{name}", headers=headers, json={"switchboard_limits": None})
    assert r.status_code == 200 and r.json()["switchboard_limits"] is None
    assert (await client.put(f"/api/v1/manifests/gw/{name}", headers=headers, json={"switchboard_limits": {}})).status_code == 422

    cfg_name = f"cfg-{_uid()}"
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
        "spec": {"manifests": [{"namespace": "gw", "name": cfg_name, "switchboardLimits": {"maxCalls": 0}}]},
    })
    svc = ConfigApplyService(db, "cfg", owner_user_id=str(admin_user.id), managed_by="config", auto_commit=False)
    result = await svc.apply_config(config)
    assert result.success, result.errors
    row = await Manifest.get_by_name(db, "gw", cfg_name)
    assert row.switchboard_limits == {"max_calls": 0, "window_seconds": 60}
    assert switchboard.effective_limits(row) == (0, 60)


# ------------------------------------------------------------------ audit


@pytest.mark.asyncio
async def test_audit_rows_are_redacted_clipped_and_keyed_by_api_key(client, db: AsyncSession, admin_user, monkeypatch):
    from sqlalchemy import select

    from app.core.config import settings
    from app.models.switchboard_call import SwitchboardCall
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"big-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "big"}])
    api_key, plain = await create_api_key(db, admin_user, f"agent-{_uid()}", {
        f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all": True,
        f"sinas.functions/tools/{fn.name}.execute:all": True,
    })
    await db.flush()
    monkeypatch.setattr(settings, "switchboard_audit_result_max_chars", 200)

    async def fake_execute(self, db_, tool_name, arguments, *a, **k):
        if arguments.get("fail"):
            return {"error": "boom"}
        return {"rows": ["x" * 50] * 20, "token": "secret-token"}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    await _call(client, path, {"X-API-Key": plain}, "tools/call", {"name": "big", "arguments": {"password": "hunter2", "q": 1}})
    await _call(client, path, {"X-API-Key": plain}, "tools/call", {"name": "big", "arguments": {"fail": True}})

    rows = (await db.execute(select(SwitchboardCall).where(SwitchboardCall.manifest_id == manifest.id).order_by(SwitchboardCall.created_at))).scalars().all()
    assert len(rows) == 2
    ok, failed = rows
    assert ok.api_key_id == api_key.id and ok.user_id == admin_user.id
    assert ok.arguments == {"password": "***REDACTED***", "q": 1}
    assert ok.result["_truncated"] is True and "secret-token" not in json.dumps(ok.result)
    assert failed.is_error is True and failed.result == {"error": "boom"}


@pytest.mark.asyncio
async def test_audit_listing_filters_and_scopes_by_permission(client, db: AsyncSession, admin_user, monkeypatch):
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"aud-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "aud"}])
    other_manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "aud2"}])
    member = await _user_with(
        db, "sinas.manifests/*/*.read:all", f"sinas.functions/tools/{fn.name}.execute:all", "sinas.switchboard_calls.read:own",
    )

    async def fake_execute(self, *a, **k):
        return {"ok": True}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    for user, m, tool in ((admin_user, manifest, "aud"), (member, manifest, "aud"), (member, other_manifest, "aud2")):
        await _call(client, f"/mcp/{m.namespace}/{m.name}", auth_headers(user), "tools/call", {"name": tool, "arguments": {}})

    # Admin: everything, with filters.
    r = await client.get("/api/v1/switchboard/calls", headers=auth_headers(admin_user), params={"manifest": f"{manifest.namespace}/{manifest.name}"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2 and {c["tool_name"] for c in r.json()["items"]} == {"aud"}
    r = await client.get("/api/v1/switchboard/calls", headers=auth_headers(admin_user), params={"user_id": str(member.id)})
    assert r.json()["total"] == 2 and {c["manifest_name"] for c in r.json()["items"]} == {manifest.name, other_manifest.name}
    r = await client.get("/api/v1/switchboard/calls", headers=auth_headers(admin_user), params={"tool": "aud2", "is_error": "false"})
    assert r.json()["total"] == 1 and r.json()["items"][0]["resource_ref"] == f"tools/{fn.name}"
    one = r.json()["items"][0]
    r = await client.get(f"/api/v1/switchboard/calls/{one['id']}", headers=auth_headers(admin_user))
    assert r.status_code == 200 and r.json()["id"] == one["id"]

    # Member: own calls only, whatever they ask for.
    r = await client.get("/api/v1/switchboard/calls", headers=auth_headers(member))
    assert r.status_code == 200 and r.json()["total"] == 2 and {c["user_id"] for c in r.json()["items"]} == {str(member.id)}
    r = await client.get("/api/v1/switchboard/calls", headers=auth_headers(member), params={"user_id": str(admin_user.id)})
    assert r.status_code == 403
    admin_call = (await client.get("/api/v1/switchboard/calls", headers=auth_headers(admin_user), params={"user_id": str(admin_user.id)})).json()["items"][0]
    r = await client.get(f"/api/v1/switchboard/calls/{admin_call['id']}", headers=auth_headers(member))
    assert r.status_code == 404
    # No permission at all: refused.
    nobody = await _user_with(db, "sinas.chats.read:own")
    assert (await client.get("/api/v1/switchboard/calls", headers=auth_headers(nobody))).status_code == 403
    # Malformed filters are a 422, not a server error.
    assert (await client.get("/api/v1/switchboard/calls", headers=auth_headers(admin_user), params={"user_id": "not-a-uuid"})).status_code == 422
    assert (await client.get("/api/v1/switchboard/calls", headers=auth_headers(admin_user), params={"api_key_id": "nope"})).status_code == 422


@pytest.mark.asyncio
async def test_a_failed_audit_write_never_fails_the_call(client, db: AsyncSession, admin_user, monkeypatch):
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"na-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "na"}])

    async def fake_execute(self, *a, **k):
        return {"ok": True}

    real_commit = type(db).commit

    async def failing_commit(self):
        if any(isinstance(o, __import__("app.models.switchboard_call", fromlist=["SwitchboardCall"]).SwitchboardCall) for o in self.new):
            self.expire_all()  # what a failed flush does to loaded rows
            raise RuntimeError("disk full")
        return await real_commit(self)

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    monkeypatch.setattr(type(db), "commit", failing_commit)
    out = await _call(client, f"/mcp/{manifest.namespace}/{manifest.name}", auth_headers(admin_user), "tools/call", {"name": "na", "arguments": {}})
    assert out["result"]["isError"] is False and json.loads(out["result"]["content"][0]["text"]) == {"ok": True}


@pytest.mark.asyncio
async def test_retention_drops_old_audit_rows(db: AsyncSession, admin_user, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.core.config import settings
    from app.models.switchboard_call import SwitchboardCall
    from app.scheduler.jobs import cleanup_switchboard_calls as job

    @contextlib.asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(job, "AsyncSessionLocal", _session)
    monkeypatch.setattr(settings, "switchboard_audit_retention_days", 30)
    manifest = await _manifest(db, admin_user, [])
    old = SwitchboardCall(manifest_id=manifest.id, manifest_namespace="gw", manifest_name=manifest.name, user_id=admin_user.id,
                      tool_name="t", resource_type="function", resource_ref="a/b", created_at=datetime.now(UTC) - timedelta(days=31))
    fresh = SwitchboardCall(manifest_id=manifest.id, manifest_namespace="gw", manifest_name=manifest.name, user_id=admin_user.id,
                        tool_name="t", resource_type="function", resource_ref="a/b")
    db.add_all([old, fresh])
    await db.flush()
    assert await job.cleanup_switchboard_calls() == 1
    left = (await db.execute(select(SwitchboardCall.id).where(SwitchboardCall.manifest_id == manifest.id))).scalars().all()
    assert left == [fresh.id]


# ------------------------------------------------------------------ OAuth


@pytest.mark.asyncio
async def test_metadata_names_the_identity_providers_and_the_challenge_points_at_it(client, db: AsyncSession, admin_user, monkeypatch):
    from tests.unit.test_oidc_sso import ISSUER, _provider

    manifest = await _manifest(db, admin_user, [])
    manifest.description = "Engineering agents"
    await _provider(db)
    await db.flush()
    r = await client.get(f"/.well-known/oauth-protected-resource/mcp/{manifest.namespace}/{manifest.name}")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["resource"].endswith(f"/mcp/{manifest.namespace}/{manifest.name}")
    assert doc["authorization_servers"] == [ISSUER.rstrip("/")]
    assert doc["bearer_methods_supported"] == ["header"] and doc["resource_name"] == "Engineering agents"
    assert (await client.get("/.well-known/oauth-protected-resource/mcp/gw/nope")).status_code == 404

    r = await client.post(f"/mcp/{manifest.namespace}/{manifest.name}", headers=MCP_HEADERS, json=_rpc("tools/list"))
    assert r.status_code == 401
    assert 'resource_metadata="' in r.headers["www-authenticate"]
    assert f"/.well-known/oauth-protected-resource/mcp/{manifest.namespace}/{manifest.name}" in r.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_an_identity_providers_access_token_signs_the_caller_in(client, db: AsyncSession, admin_user, monkeypatch):
    import time as _time

    import httpx

    from app.core.auth import get_user_by_email
    from app.services import sso, switchboard_auth
    from app.services.function_tools import FunctionToolConverter
    from tests.unit.test_oidc_sso import CLIENT_ID, ISSUER, FakeIdP, _provider, _role

    # The fake identity provider the SSO tests use, reached over a mock transport.
    fake = FakeIdP()
    real = httpx.AsyncClient
    monkeypatch.setattr(sso, "_client", lambda: real(transport=httpx.MockTransport(fake.handler)))
    sso.clear_caches()
    switchboard_auth.clear_cache()

    fn = await _function(db, admin_user, "tools", f"ext-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "ext"}])
    role = await _role(db)
    for key in (f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all", f"sinas.functions/tools/{fn.name}.execute:all"):
        db.add(RolePermission(role_id=role.id, permission_key=key, permission_value=True))
    provider = await _provider(db, auto_provision=True, default_role=role.name)
    await db.flush()

    async def fake_execute(self, *a, **k):
        return {"ok": True}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    now = int(_time.time())
    email = f"agent-{_uid()}@acme.example"
    resource = switchboard_auth.resource_url(manifest.namespace, manifest.name)

    def token(**over):
        claims = {"iss": ISSUER, "aud": resource, "sub": f"sub-{email}", "email": email, "email_verified": True,
                  "iat": now, "exp": now + 300, **over}
        return fake.key.sign(claims)

    # A token for this switchboard: the user is provisioned with the provider's default role and sees its tools.
    listed = await _call(client, path, {"Authorization": f"Bearer {token()}"}, "tools/list")
    assert [t["name"] for t in listed["result"]["tools"]] == ["ext"]
    user = await get_user_by_email(db, email)
    assert user is not None
    out = await _call(client, path, {"Authorization": f"Bearer {token()}"}, "tools/call", {"name": "ext", "arguments": {}})
    assert out["result"]["isError"] is False

    # The provider's own client id is an acceptable audience for an access
    # token (typ at+jwt, RFC 9068) — not for an ID token, which carries the
    # same audience but authorizes a login, not API calls.
    from jose import jwt as _jwt

    def access_token_for_client(**over):
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": f"client-{_uid()}", "email": f"c-{_uid()}@acme.example",
                  "email_verified": True, "iat": now, "exp": now + 300, **over}
        return _jwt.encode(claims, fake.key.pem, algorithm="RS256", headers={"kid": fake.key.kid, "typ": "at+jwt"})

    assert (await client.post(path, headers={**MCP_HEADERS, "Authorization": f"Bearer {access_token_for_client()}"}, json=_rpc("tools/list"))).status_code == 200
    r = await client.post(path, headers={**MCP_HEADERS, "Authorization": f"Bearer {token(aud=CLIENT_ID, sub='id-tok', nonce='n1', email=f'id-{_uid()}@acme.example')}"}, json=_rpc("tools/list"))
    assert r.status_code == 401 and "id token" in r.json()["detail"].lower()
    r = await client.post(path, headers={**MCP_HEADERS, "Authorization": f"Bearer {token(aud=CLIENT_ID, sub='plain-jwt', email=f'pj-{_uid()}@acme.example')}"}, json=_rpc("tools/list"))
    assert r.status_code == 401  # client-id audience without the access-token type: refused

    # Not for this switchboard, expired, or from an unknown issuer: 401 with the reason in the challenge.
    for bad, reason in (
        (token(aud="https://elsewhere.example/mcp/x/y", sub="s2", email=f"s2-{_uid()}@acme.example"), "audience"),
        (token(exp=now - 600, sub="s3", email=f"s3-{_uid()}@acme.example"), "expired"),
        (token(iss="https://unknown.example", sub="s4", email=f"s4-{_uid()}@acme.example"), "issuer"),
    ):
        r = await client.post(path, headers={**MCP_HEADERS, "Authorization": f"Bearer {bad}"}, json=_rpc("tools/list"))
        assert r.status_code == 401, r.text
        assert reason in r.headers["www-authenticate"].lower(), r.headers["www-authenticate"]

    # A provider that does not auto-provision refuses strangers.
    provider.auto_provision = False
    await db.flush()
    switchboard_auth.clear_cache()
    r = await client.post(path, headers={**MCP_HEADERS, "Authorization": f"Bearer {token(sub='s5', email=f'new-{_uid()}@acme.example')}"}, json=_rpc("tools/list"))
    assert r.status_code == 401 and "no Sinas account" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_token_for_one_switchboard_does_not_open_another(client, db: AsyncSession, admin_user, monkeypatch):
    """The resolution cache is per token AND switchboard: a token issued for A,
    accepted there, still fails B's audience check."""
    import time as _time

    import httpx

    from app.services import sso, switchboard_auth
    from tests.unit.test_oidc_sso import ISSUER, FakeIdP, _provider, _role

    fake = FakeIdP()
    real = httpx.AsyncClient
    monkeypatch.setattr(sso, "_client", lambda: real(transport=httpx.MockTransport(fake.handler)))
    sso.clear_caches()
    switchboard_auth.clear_cache()
    a = await _manifest(db, admin_user, [])
    b = await _manifest(db, admin_user, [])
    role = await _role(db)
    db.add(RolePermission(role_id=role.id, permission_key="sinas.manifests/*/*.read:all", permission_value=True))
    await _provider(db, auto_provision=True, default_role=role.name)
    await db.flush()
    now = int(_time.time())
    token = fake.key.sign({"iss": ISSUER, "aud": switchboard_auth.resource_url(a.namespace, a.name), "sub": "cross",
                           "email": f"cross-{_uid()}@acme.example", "email_verified": True, "iat": now, "exp": now + 300})
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {token}"}
    assert (await client.post(f"/mcp/{a.namespace}/{a.name}", headers=headers, json=_rpc("tools/list"))).status_code == 200
    r = await client.post(f"/mcp/{b.namespace}/{b.name}", headers=headers, json=_rpc("tools/list"))
    assert r.status_code == 401 and "audience" in r.headers["www-authenticate"].lower()


@pytest.mark.asyncio
async def test_an_unreachable_identity_provider_is_a_401_not_a_500(client, db: AsyncSession, admin_user, monkeypatch):
    import time as _time

    import httpx

    from app.services import sso, switchboard_auth
    from tests.unit.test_oidc_sso import ISSUER, SigningKey, _provider

    def down(request):
        return httpx.Response(503, text="maintenance")

    real = httpx.AsyncClient
    monkeypatch.setattr(sso, "_client", lambda: real(transport=httpx.MockTransport(down)))
    sso.clear_caches()
    switchboard_auth.clear_cache()
    manifest = await _manifest(db, admin_user, [])
    await _provider(db)
    await db.flush()
    now = int(_time.time())
    token = SigningKey("k9").sign({"iss": ISSUER, "aud": switchboard_auth.resource_url(manifest.namespace, manifest.name), "sub": "s",
                                  "email": "x@acme.example", "iat": now, "exp": now + 300})
    r = await client.post(f"/mcp/{manifest.namespace}/{manifest.name}", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}, json=_rpc("tools/list"))
    assert r.status_code == 401, r.text
    assert "unavailable" in r.headers["www-authenticate"].lower()


def test_deployment_limits_are_validated():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(switchboard_rate_limit_window_seconds=0)
    with pytest.raises(ValueError):
        Settings(switchboard_rate_limit_max_calls=-1)
    assert Settings(switchboard_rate_limit_max_calls=0).switchboard_rate_limit_max_calls == 0


@pytest.mark.asyncio
async def test_catalog_uses_the_runners_own_rules(db: AsyncSession, admin_user):
    """A tool in the catalog is a tool the call will run: pipelines need the
    `run` permission and `as_tool`; a function that requires approval is
    not served (the switchboard has no one to ask)."""
    from app.core.auth import get_user_permissions
    from app.models.pipeline import Pipeline

    gated = await _function(db, admin_user, "tools", f"gated-{_uid()}", requires_approval=True)
    plain = await _function(db, admin_user, "tools", f"plain-{_uid()}")
    as_tool = Pipeline(user_id=admin_user.id, namespace="ops", name=f"p-{_uid()}", description="runs", steps=[], as_tool=True,
                       input_schema={"type": "object", "properties": {}})
    not_tool = Pipeline(user_id=admin_user.id, namespace="ops", name=f"q-{_uid()}", description="internal", steps=[], as_tool=False,
                        input_schema={"type": "object", "properties": {}})
    db.add_all([as_tool, not_tool])
    await db.flush()
    manifest = await _manifest(db, admin_user, [
        {"type": "function", "namespace": "tools", "name": gated.name, "tool_name": "gated"},
        {"type": "function", "namespace": "tools", "name": plain.name, "tool_name": "plain"},
        {"type": "pipeline", "namespace": "ops", "name": as_tool.name, "tool_name": "run_p"},
        {"type": "pipeline", "namespace": "ops", "name": not_tool.name, "tool_name": "run_q"},
    ])
    admin_perms = await get_user_permissions(db, str(admin_user.id))
    assert sorted(t.name for t in await switchboard.list_tools(db, manifest, str(admin_user.id), admin_perms)) == ["plain", "run_p"]

    runner = await _user_with(db, f"sinas.pipelines/ops/{as_tool.name}.run:all")
    executor = await _user_with(db, f"sinas.pipelines/ops/{as_tool.name}.execute:all")  # the wrong verb for pipelines
    from app.core.auth import get_user_permissions as perms

    assert [t.name for t in await switchboard.list_tools(db, manifest, str(runner.id), await perms(db, str(runner.id)))] == ["run_p"]
    assert await switchboard.list_tools(db, manifest, str(executor.id), await perms(db, str(executor.id))) == []

    # /status counts a declared-but-unservable tool as missing.
    from app.services.manifest_validator import validate_manifest_status

    status = await validate_manifest_status(db, manifest, str(admin_user.id), admin_perms)
    assert status.ready is False
    assert sorted(r.name for r in status.resources["missing"]) == sorted([gated.name, not_tool.name])


@pytest.mark.asyncio
async def test_oauth_follow_ups(client, db: AsyncSession, admin_user, monkeypatch):
    """A cached token dies with its provider; a missing email_verified is
    fetched from userinfo so an existing account can be linked; downstream
    calls carry a Sinas token, not the external one; the challenge stays a
    valid header whatever the provider's display name."""
    import time as _time

    import httpx

    from app.core.auth import decode_access_token
    from app.services import sso, switchboard_auth
    from app.services.function_tools import FunctionToolConverter
    from tests.unit.test_oidc_sso import ISSUER, FakeIdP, _provider, _role

    fake = FakeIdP()
    real = httpx.AsyncClient
    monkeypatch.setattr(sso, "_client", lambda: real(transport=httpx.MockTransport(fake.handler)))
    sso.clear_caches()
    switchboard_auth.clear_cache()

    fn = await _function(db, admin_user, "tools", f"ds-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "ds"}])
    role = await _role(db)
    for key in ("sinas.manifests/*/*.read:all", f"sinas.functions/tools/{fn.name}.execute:all"):
        db.add(RolePermission(role_id=role.id, permission_key=key, permission_value=True))
    provider = await _provider(db, auto_provision=True, default_role=role.name, display_name='Acme "SSO"')
    await db.flush()
    seen = {}

    async def fake_execute(self, db_, tool_name, arguments, user_id, user_token, **k):
        seen["token"] = user_token
        return {"ok": True}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    now = int(_time.time())
    resource = switchboard_auth.resource_url(manifest.namespace, manifest.name)

    def token(**over):
        return fake.key.sign({"iss": ISSUER, "aud": resource, "iat": now, "exp": now + 300, **over})

    # Existing account, token without email_verified: userinfo supplies the proof and the account is linked.
    existing_email = f"linked-{_uid()}@acme.example"
    existing = User(email=existing_email)
    db.add(existing)
    await db.flush()
    db.add(UserRole(role_id=role.id, user_id=existing.id, active=True))
    await db.flush()
    fake.userinfo = {"sub": "link-me", "email": existing_email, "email_verified": True}
    t = token(sub="link-me", email=existing_email)
    out = await _call(client, path, {"Authorization": f"Bearer {t}"}, "tools/call", {"name": "ds", "arguments": {}})
    assert out["result"]["isError"] is False
    # Downstream, the tool got a Sinas access token for that user, not the external one.
    assert seen["token"] != t and decode_access_token(seen["token"])["sub"] == str(existing.id)

    # The provider is disabled: the cached token stops working at once.
    provider.is_active = False
    await db.flush()
    r = await client.post(path, headers={**MCP_HEADERS, "Authorization": f"Bearer {t}"}, json=_rpc("tools/list"))
    assert r.status_code == 401 and "identity provider" in r.json()["detail"]
    provider.is_active = True
    await db.flush()

    # A rejection message carrying the provider's quoted name still yields a parseable header.
    switchboard_auth.clear_cache()
    provider.auto_provision = False
    await db.flush()
    fake.userinfo = None
    r = await client.post(path, headers={**MCP_HEADERS, "Authorization": f"Bearer {token(sub='stranger', email=f'new-{_uid()}@acme.example', email_verified=True)}"}, json=_rpc("tools/list"))
    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert 'Acme "SSO"' in r.json()["detail"] or "no Sinas account" in r.json()["detail"]
    assert challenge.count('"') % 2 == 0 and 'Acme "SSO"' not in challenge


async def test_a_counter_without_expiry_is_repaired(_fresh_limits):
    """INCR without its EXPIRE (a dropped connection in between) used to
    leave a counter that never reset; now the expiry travels with the key's
    creation, and a stray counter is given one."""
    from fastapi import HTTPException

    from app.core.rate_limit import check_rate_limit
    from app.core.redis import get_redis

    redis = await get_redis()
    key = f"switchboard:test:{_uid()}"
    await redis.set(f"sinas:ratelimit:{key}", 5)  # at the cap, no TTL: the stuck shape
    assert await redis.ttl(f"sinas:ratelimit:{key}") == -1
    with pytest.raises(HTTPException) as refused:
        await check_rate_limit(key, 5, 30)
    assert 0 < await redis.ttl(f"sinas:ratelimit:{key}") <= 30  # repaired: it will reset
    assert 0 < int(refused.value.headers["Retry-After"]) <= 30
    fresh = f"switchboard:test:{_uid()}"
    await check_rate_limit(fresh, 5, 30)
    assert 0 < await redis.ttl(f"sinas:ratelimit:{fresh}") <= 30  # expiry from the first call on
