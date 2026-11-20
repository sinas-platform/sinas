"""The MCP gateway: a manifest's exposed tools served to external agents at
POST /mcp/{namespace}/{name}, each caller seeing only what their own
permissions allow."""

import contextlib
import json
import uuid

import pytest
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
    # The result store's table is partitioned by day and the test database
    # carries no partitions: capture the record instead of reading it back.
    from app.services import tool_result_store

    recorded = []

    async def fake_save(db_, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(tool_result_store, "save_tool_result", fake_save)
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

    (row,) = recorded
    assert row["source"] == "gateway" and row["tool_name"] == "sum" and row["user_id"] == str(caller.id)
    assert row["arguments"]["manifest"] == f"{manifest.namespace}/{manifest.name}"
    assert row["arguments"]["resource"] == f"function:tools/{fn.name}" and row["arguments"]["x"] == 21
    assert row["result"] == {"y": 42} and row["tool_call_id"].startswith("gw_")


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
