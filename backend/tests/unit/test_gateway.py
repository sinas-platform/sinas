"""The MCP gateway: a manifest's exposed tools served to external agents at
POST /mcp/{namespace}/{name}, each caller seeing only what their own
permissions allow."""

import contextlib
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.runtime import gateway as gateway_endpoint
from app.core.auth import create_api_key
from app.models.function import Function
from app.models.manifest import Manifest
from app.models.user import Role, RolePermission, User, UserRole
from app.schemas.spec.manifest import ManifestSpec
from app.services import gateway
from tests.conftest import auth_headers

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def _same_session(db, monkeypatch):
    """The gateway opens its own sessions; the fixture's rows live in a
    rolled-back transaction those can't see, so hand it the fixture's."""

    @contextlib.asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(gateway_endpoint, "AsyncSessionLocal", _session)


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
    tools = await gateway.list_tools(db, manifest, str(admin_user.id), perms)
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

    from app.models.gateway_call import GatewayCall

    (row,) = (await db.execute(select(GatewayCall).where(GatewayCall.user_id == caller.id))).scalars().all()
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
async def test_gateway_requires_a_credential_and_the_manifest(client, db: AsyncSession, admin_user):
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

    stranger = await _user_with(db, "sinas.functions/*/*.execute:all")  # may run things, may not use this gateway
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
    """Protocol conformance: the SDK's own client against the gateway, over
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
    """Rate-limit counters live in Redis across tests: clear the gateway's."""
    from app.core.redis import get_redis

    redis = await get_redis()
    async for key in redis.scan_iter("sinas:ratelimit:gateway:*"):
        await redis.delete(key)
    yield


@pytest.mark.asyncio
async def test_calls_past_the_manifests_limit_are_refused_with_a_retry_hint(client, db: AsyncSession, admin_user, monkeypatch, _fresh_limits):
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"lim-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "lim"}])
    manifest.gateway_limits = {"max_calls": 2, "window_seconds": 60}
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
    assert out["error"]["code"] == gateway_endpoint.RATE_LIMITED
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
    monkeypatch.setattr(settings, "gateway_rate_limit_max_calls", 1)  # the deployment default applies: no manifest override

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
    assert (await _call(client, path, {"X-API-Key": key_a}, "tools/call", call))["error"]["code"] == gateway_endpoint.RATE_LIMITED
    # Same user, other key: its own budget.
    assert (await _call(client, path, {"X-API-Key": key_b}, "tools/call", call))["result"]["isError"] is False

    manifest.gateway_limits = {"max_calls": 0, "window_seconds": 60}
    await db.flush()
    for _ in range(3):
        assert (await _call(client, path, {"X-API-Key": key_a}, "tools/call", call))["result"]["isError"] is False


def test_limits_declare_and_export():
    spec = ManifestSpec(name="x", gatewayLimits={"maxCalls": 10})
    assert spec.gateway_limits.window_seconds == 60
    assert ManifestSpec(name="x", gateway_limits={"max_calls": 10, "window_seconds": 5}).to_config()["gatewayLimits"] == {"maxCalls": 10, "windowSeconds": 5}
    assert "gatewayLimits" not in ManifestSpec(name="plain").to_config()
    with pytest.raises(ValueError):
        ManifestSpec(name="bad", gatewayLimits={"maxCalls": -1})


@pytest.mark.asyncio
async def test_rest_and_config_carry_the_limits(client, db: AsyncSession, admin_user):
    from app.schemas.config import SinasConfig
    from app.services.config_apply.service import ConfigApplyService

    headers = auth_headers(admin_user)
    name = f"lim-{_uid()}"
    r = await client.post("/api/v1/manifests", headers=headers, json={"namespace": "gw", "name": name, "gateway_limits": {"max_calls": 5}})
    assert r.status_code == 201, r.text
    assert r.json()["gateway_limits"] == {"max_calls": 5, "window_seconds": 60}
    r = await client.put(f"/api/v1/manifests/gw/{name}", headers=headers, json={"gateway_limits": {"max_calls": 7, "window_seconds": 10}})
    assert r.status_code == 200 and r.json()["gateway_limits"] == {"max_calls": 7, "window_seconds": 10}

    cfg_name = f"cfg-{_uid()}"
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
        "spec": {"manifests": [{"namespace": "gw", "name": cfg_name, "gatewayLimits": {"maxCalls": 0}}]},
    })
    svc = ConfigApplyService(db, "cfg", owner_user_id=str(admin_user.id), managed_by="config", auto_commit=False)
    result = await svc.apply_config(config)
    assert result.success, result.errors
    row = await Manifest.get_by_name(db, "gw", cfg_name)
    assert row.gateway_limits == {"max_calls": 0, "window_seconds": 60}
    assert gateway.effective_limits(row) == (0, 60)


# ------------------------------------------------------------------ audit


@pytest.mark.asyncio
async def test_audit_rows_are_redacted_clipped_and_keyed_by_api_key(client, db: AsyncSession, admin_user, monkeypatch):
    from sqlalchemy import select

    from app.core.config import settings
    from app.models.gateway_call import GatewayCall
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"big-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "big"}])
    api_key, plain = await create_api_key(db, admin_user, f"agent-{_uid()}", {
        f"sinas.manifests/{manifest.namespace}/{manifest.name}.read:all": True,
        f"sinas.functions/tools/{fn.name}.execute:all": True,
    })
    await db.flush()
    monkeypatch.setattr(settings, "gateway_audit_result_max_chars", 200)

    async def fake_execute(self, db_, tool_name, arguments, *a, **k):
        if arguments.get("fail"):
            return {"error": "boom"}
        return {"rows": ["x" * 50] * 20, "token": "secret-token"}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    path = f"/mcp/{manifest.namespace}/{manifest.name}"
    await _call(client, path, {"X-API-Key": plain}, "tools/call", {"name": "big", "arguments": {"password": "hunter2", "q": 1}})
    await _call(client, path, {"X-API-Key": plain}, "tools/call", {"name": "big", "arguments": {"fail": True}})

    rows = (await db.execute(select(GatewayCall).where(GatewayCall.manifest_id == manifest.id).order_by(GatewayCall.created_at))).scalars().all()
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
        db, "sinas.manifests/*/*.read:all", f"sinas.functions/tools/{fn.name}.execute:all", "sinas.gateway_calls.read:own",
    )

    async def fake_execute(self, *a, **k):
        return {"ok": True}

    monkeypatch.setattr(FunctionToolConverter, "execute_function_tool", fake_execute)
    for user, m, tool in ((admin_user, manifest, "aud"), (member, manifest, "aud"), (member, other_manifest, "aud2")):
        await _call(client, f"/mcp/{m.namespace}/{m.name}", auth_headers(user), "tools/call", {"name": tool, "arguments": {}})

    # Admin: everything, with filters.
    r = await client.get("/api/v1/gateway/calls", headers=auth_headers(admin_user), params={"manifest": f"{manifest.namespace}/{manifest.name}"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2 and {c["tool_name"] for c in r.json()["items"]} == {"aud"}
    r = await client.get("/api/v1/gateway/calls", headers=auth_headers(admin_user), params={"user_id": str(member.id)})
    assert r.json()["total"] == 2 and {c["manifest_name"] for c in r.json()["items"]} == {manifest.name, other_manifest.name}
    r = await client.get("/api/v1/gateway/calls", headers=auth_headers(admin_user), params={"tool": "aud2", "is_error": "false"})
    assert r.json()["total"] == 1 and r.json()["items"][0]["resource_ref"] == f"tools/{fn.name}"
    one = r.json()["items"][0]
    r = await client.get(f"/api/v1/gateway/calls/{one['id']}", headers=auth_headers(admin_user))
    assert r.status_code == 200 and r.json()["id"] == one["id"]

    # Member: own calls only, whatever they ask for.
    r = await client.get("/api/v1/gateway/calls", headers=auth_headers(member))
    assert r.status_code == 200 and r.json()["total"] == 2 and {c["user_id"] for c in r.json()["items"]} == {str(member.id)}
    r = await client.get("/api/v1/gateway/calls", headers=auth_headers(member), params={"user_id": str(admin_user.id)})
    assert r.status_code == 403
    admin_call = (await client.get("/api/v1/gateway/calls", headers=auth_headers(admin_user), params={"user_id": str(admin_user.id)})).json()["items"][0]
    r = await client.get(f"/api/v1/gateway/calls/{admin_call['id']}", headers=auth_headers(member))
    assert r.status_code == 404
    # No permission at all: refused.
    nobody = await _user_with(db, "sinas.chats.read:own")
    assert (await client.get("/api/v1/gateway/calls", headers=auth_headers(nobody))).status_code == 403
    # Malformed filters are a 422, not a server error.
    assert (await client.get("/api/v1/gateway/calls", headers=auth_headers(admin_user), params={"user_id": "not-a-uuid"})).status_code == 422
    assert (await client.get("/api/v1/gateway/calls", headers=auth_headers(admin_user), params={"api_key_id": "nope"})).status_code == 422


@pytest.mark.asyncio
async def test_a_failed_audit_write_never_fails_the_call(client, db: AsyncSession, admin_user, monkeypatch):
    from app.services.function_tools import FunctionToolConverter

    fn = await _function(db, admin_user, "tools", f"na-{_uid()}")
    manifest = await _manifest(db, admin_user, [{"type": "function", "namespace": "tools", "name": fn.name, "tool_name": "na"}])

    async def fake_execute(self, *a, **k):
        return {"ok": True}

    real_commit = type(db).commit

    async def failing_commit(self):
        if any(isinstance(o, __import__("app.models.gateway_call", fromlist=["GatewayCall"]).GatewayCall) for o in self.new):
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
    from app.models.gateway_call import GatewayCall
    from app.scheduler.jobs import cleanup_gateway_calls as job

    @contextlib.asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(job, "AsyncSessionLocal", _session)
    monkeypatch.setattr(settings, "gateway_audit_retention_days", 30)
    manifest = await _manifest(db, admin_user, [])
    old = GatewayCall(manifest_id=manifest.id, manifest_namespace="gw", manifest_name=manifest.name, user_id=admin_user.id,
                      tool_name="t", resource_type="function", resource_ref="a/b", created_at=datetime.now(UTC) - timedelta(days=31))
    fresh = GatewayCall(manifest_id=manifest.id, manifest_namespace="gw", manifest_name=manifest.name, user_id=admin_user.id,
                        tool_name="t", resource_type="function", resource_ref="a/b")
    db.add_all([old, fresh])
    await db.flush()
    assert await job.cleanup_gateway_calls() == 1
    left = (await db.execute(select(GatewayCall.id).where(GatewayCall.manifest_id == manifest.id))).scalars().all()
    assert left == [fresh.id]
