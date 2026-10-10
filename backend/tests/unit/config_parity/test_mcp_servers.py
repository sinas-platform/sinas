"""MCP servers: one applier for every write channel.

The REST API and config apply write identical rows; history never shows
header values or URL credentials; export round-trips; a re-apply keeps a
server someone disabled; agents bind servers in both the string and the
dict form.
"""

import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.config_revision import ConfigRevision
from app.models.mcp_server import McpServer
from app.schemas.config import SinasConfig
from app.schemas.spec.mcp_server import McpServerSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from app.services.resource_serializers import serialize_agent
from tests.conftest import auth_headers

FIELDS = (
    "description", "url", "transport", "auth", "headers", "tool_allow", "tool_deny",
    "timeout_seconds", "connect_timeout_seconds", "is_active",
)
API_KEY = "sk-live-do-not-show"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _yaml_server(name: str, **extra) -> dict:
    return {
        "namespace": "tools",
        "name": name,
        "description": "Issue tracker",
        "url": "https://mcp.example.com/mcp",
        "transport": "streamable_http",
        "auth": {"type": "bearer", "secret": "TRACKER_TOKEN"},
        "headers": {"X-Api-Key": API_KEY},
        "toolAllow": ["list_*", "create_issue"],
        "toolDeny": ["create_issue"],
        "timeoutSeconds": 45,
        "connectTimeoutSeconds": 5,
        **extra,
    }


def _rest_server(name: str, **extra) -> dict:
    return {
        "namespace": "tools",
        "name": name,
        "description": "Issue tracker",
        "url": "https://mcp.example.com/mcp",
        "transport": "streamable_http",
        "auth": {"type": "bearer", "secret": "TRACKER_TOKEN"},
        "headers": {"X-Api-Key": API_KEY},
        "tool_allow": ["list_*", "create_issue"],
        "tool_deny": ["create_issue"],
        "timeout_seconds": 45,
        "connect_timeout_seconds": 5,
        **extra,
    }


def _config(spec: dict) -> SinasConfig:
    return SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"}, "spec": spec,
    })


async def _apply(db, owner, spec: dict, managed_by="config", prune=False):
    svc = ConfigApplyService(
        db, "cfg", owner_user_id=str(owner.id), managed_by=managed_by, auto_commit=False,
        prune_missing=prune,
    )
    return svc, await svc.apply_config(_config(spec))


async def _row(db: AsyncSession, name: str) -> McpServer | None:
    return (
        await db.execute(select(McpServer).where(McpServer.namespace == "tools", McpServer.name == name))
    ).scalar_one_or_none()


async def _revisions(db: AsyncSession, name: str) -> list[ConfigRevision]:
    return list((await db.execute(
        select(ConfigRevision)
        .where(ConfigRevision.resource_kind == "mcp_servers", ConfigRevision.resource_key == f"tools/{name}")
        .order_by(ConfigRevision.id)
    )).scalars())


# ------------------------------------------------------------------ spec


class TestMcpServerSpec:
    def test_rest_and_config_shapes_are_the_same_spec(self):
        assert McpServerSpec.model_validate(_yaml_server("x")) == McpServerSpec.model_validate(_rest_server("x"))

    @pytest.mark.parametrize("bad", [
        {"transport": "stdio"},  # no process execution in the backend
        {"transport": "websocket"},
        {"url": "mcp.example.com/mcp"},  # no scheme
        {"auth": {"type": "oauth"}},
        {"auth": {"type": "bearer"}},  # a bearer with nothing to send
        {"auth": {"type": "header", "secret": "K"}},  # ...in which header?
        {"namespace": "team/tools"},
        {"namespace": "my.team"},  # would become an invalid function name
        {"name": "issue tracker"},
        {"timeoutSeconds": 0},
        {"connectTimeoutSeconds": 0},
        {"transprt": "sse"},  # a typo is an error, not silently ignored
    ])
    def test_refuses_what_cannot_work(self, bad):
        with pytest.raises(ValidationError):
            McpServerSpec.model_validate({**_yaml_server("x"), **bad})

    def test_config_and_rest_refuse_unknown_fields(self):
        """A misspelt key must fail the apply, not silently drop a filter."""
        for bad in (
            {"toolDney": ["delete_*"]},
            {"auth": {"type": "bearer", "secret": "S", "secrett": "S"}},
        ):
            with pytest.raises(ValidationError):
                _config({"mcpServers": [{**_yaml_server("x"), **bad}]})
        with pytest.raises(ValidationError):
            _config({"agents": [{
                "name": "a", "enabledMcpServers": [{"server": "tools/x", "tool": ["a"]}],
            }]})

    def test_defaults_and_export_form(self):
        spec = McpServerSpec.model_validate({"name": "min", "url": "http://localhost:8000/mcp"})
        assert (spec.namespace, spec.transport, spec.auth.type) == ("default", "streamable_http", "none")
        assert (spec.timeout_seconds, spec.connect_timeout_seconds, spec.is_active) == (60, 10, True)
        # Empty collections stay out of the config form.
        assert spec.to_config() == {
            "namespace": "default", "name": "min", "url": "http://localhost:8000/mcp",
            "transport": "streamable_http", "auth": {"type": "none"},
            "timeoutSeconds": 60, "connectTimeoutSeconds": 10, "isActive": True,
        }


# ------------------------------------------------------------ one write path


class TestOneWritePath:
    async def test_api_and_config_write_identical_rows(self, client, db: AsyncSession, admin_user):
        api, cfg = f"api-{_uid()}", f"cfg-{_uid()}"
        response = await client.post(
            "/api/v1/mcp-servers", json=_rest_server(api), headers=auth_headers(admin_user)
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["auth"] == {"type": "bearer", "secret": "TRACKER_TOKEN"} and body["managed_by"] is None

        _, result = await _apply(db, admin_user, {"mcpServers": [_yaml_server(cfg)]})
        assert result.success, result.errors

        api_row, cfg_row = await _row(db, api), await _row(db, cfg)
        await db.refresh(api_row)
        assert {f: getattr(api_row, f) for f in FIELDS} == {f: getattr(cfg_row, f) for f in FIELDS}
        assert cfg_row.managed_by == "config" and cfg_row.config_name == "cfg"
        assert None not in cfg_row.auth.values()

    async def test_rest_refuses_unknown_fields(self, client, admin_user):
        headers = auth_headers(admin_user)
        for bad in (
            {"tool_dney": ["x"]},
            {"auth": {"type": "bearer", "secret": "S", "secrett": "S"}},
        ):
            response = await client.post("/api/v1/mcp-servers", json={**_rest_server(f"x-{_uid()}"), **bad}, headers=headers)
            assert response.status_code == 422, response.text
        name = f"api-{_uid()}"
        assert (await client.post("/api/v1/mcp-servers", json=_rest_server(name), headers=headers)).status_code == 201
        response = await client.put(f"/api/v1/mcp-servers/tools/{name}", json={"timeout_secs": 5}, headers=headers)
        assert response.status_code == 422
        response = await client.post("/api/v1/agents", json={
            "namespace": f"ns{_uid()}", "name": "a", "system_prompt": "x",
            "enabled_mcp_servers": [{"server": f"tools/{name}", "tool": ["a"]}],
        }, headers=headers)
        assert response.status_code == 422

    async def test_create_honours_is_active(self, client, db: AsyncSession, admin_user):
        name = f"api-{_uid()}"
        response = await client.post(
            "/api/v1/mcp-servers", json=_rest_server(name, is_active=False), headers=auth_headers(admin_user)
        )
        assert response.status_code == 201, response.text
        assert response.json()["is_active"] is False
        assert (await _row(db, name)).is_active is False

    async def test_update_patches_and_renames_safely(self, client, db: AsyncSession, admin_user):
        first, second, headers = f"a-{_uid()}", f"b-{_uid()}", auth_headers(admin_user)
        for name in (first, second):
            await client.post("/api/v1/mcp-servers", json=_rest_server(name), headers=headers)

        response = await client.put(
            f"/api/v1/mcp-servers/tools/{first}", json={"timeout_seconds": 5, "is_active": False},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert (response.json()["timeout_seconds"], response.json()["is_active"]) == (5, False)
        assert response.json()["auth"]["secret"] == "TRACKER_TOKEN"  # untouched fields stay

        response = await client.put(f"/api/v1/mcp-servers/tools/{first}", json={"name": second}, headers=headers)
        assert response.status_code == 400
        assert response.json()["detail"] == f"MCP server 'tools/{second}' already exists"

        response = await client.put(
            f"/api/v1/mcp-servers/tools/{first}", json={"auth": {"type": "bearer"}}, headers=headers
        )
        assert response.status_code == 422

    async def test_a_manual_edit_detaches_a_config_managed_server(self, client, db, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, {"mcpServers": [_yaml_server(name)]})
        response = await client.put(
            f"/api/v1/mcp-servers/tools/{name}", json={"timeout_seconds": 5}, headers=auth_headers(admin_user)
        )
        assert response.status_code == 200, response.text
        row = await _row(db, name)
        await db.refresh(row)
        assert row.managed_by is None

    async def test_a_reapply_keeps_a_disabled_server_disabled(self, db: AsyncSession, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, {"mcpServers": [_yaml_server(name)]})
        row = await _row(db, name)
        row.is_active = False
        await db.flush()
        _, result = await _apply(db, admin_user, {"mcpServers": [_yaml_server(name)]})
        assert result.success and (await _row(db, name)).is_active is False
        _, result = await _apply(db, admin_user, {"mcpServers": [_yaml_server(name, isActive=True)]})
        assert result.success and (await _row(db, name)).is_active is True

    async def test_prune_removes_what_the_config_no_longer_declares(self, db: AsyncSession, admin_user):
        keep, drop = f"keep-{_uid()}", f"drop-{_uid()}"
        await _apply(db, admin_user, {"mcpServers": [_yaml_server(keep), _yaml_server(drop)]})
        _, result = await _apply(db, admin_user, {"mcpServers": [_yaml_server(keep)]}, prune=True)
        assert result.success, result.errors
        assert await _row(db, keep) is not None and await _row(db, drop) is None
        assert [r.action for r in await _revisions(db, drop)] == ["create", "delete"]

    async def test_delete_endpoint(self, client, db: AsyncSession, admin_user):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/mcp-servers", json=_rest_server(name), headers=headers)
        assert (await client.delete(f"/api/v1/mcp-servers/tools/{name}", headers=headers)).status_code == 204
        assert (await client.get(f"/api/v1/mcp-servers/tools/{name}", headers=headers)).status_code == 404
        assert await _row(db, name) is None


# ------------------------------------------------------------ history + export


class TestHistoryAndExport:
    async def test_header_values_and_url_credentials_are_redacted(self, client, db, admin_user):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        response = await client.post(
            "/api/v1/mcp-servers",
            json=_rest_server(name, url="https://user:hunter2@mcp.example.com/mcp?key=abc"),
            headers=headers,
        )
        assert response.status_code == 201, response.text
        [created] = await _revisions(db, name)
        shown = json.dumps(created.spec) + json.dumps(created.changes)
        for secret in (API_KEY, "hunter2", "key=abc"):
            assert secret not in shown, secret
        assert created.spec["auth"]["secret"] == "TRACKER_TOKEN"  # a name, not a value
        assert created.spec["headers"].keys() == {"X-Api-Key"}

        listed = await client.get(f"/api/v1/config/history/{created.id}", headers=headers)
        assert API_KEY not in listed.text and "hunter2" not in listed.text

    async def test_a_deleted_server_restores_with_its_real_values(self, client, db, admin_user):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        url = "https://user:hunter2@mcp.example.com/mcp"
        await client.post("/api/v1/mcp-servers", json=_rest_server(name, url=url), headers=headers)
        await client.delete(f"/api/v1/mcp-servers/tools/{name}", headers=headers)
        [deleted] = [r for r in await _revisions(db, name) if r.action == "delete"]
        response = await client.post(f"/api/v1/config/history/{deleted.id}/restore", headers=headers)
        assert response.status_code == 200, response.text
        row = await _row(db, name)
        assert row.url == url and row.headers == {"X-Api-Key": API_KEY}

    async def test_export_round_trips_without_secret_values(self, db: AsyncSession, admin_user):
        name = f"cfg-{_uid()}"
        declared = _yaml_server(name)
        await _apply(db, admin_user, {"mcpServers": [declared]})
        exported = ConfigExportService(db, managed_only=True, managed_by="config")
        config = SinasConfig.model_validate(__import__("yaml").safe_load(await exported.export_config()))
        [item] = [s for s in config.spec.mcpServers if s.name == name]
        assert item.model_dump(exclude_none=True) == {**declared, "isActive": True}
        # Re-applying the export is a no-op.
        _, result = await _apply(db, admin_user, {"mcpServers": [item.model_dump(exclude_none=True)]})
        assert result.success and not result.summary.updated


# ------------------------------------------------------------ agent binding


class TestAgentBinding:
    async def test_config_accepts_string_and_dict_forms(self, db: AsyncSession, admin_user):
        ns = f"ns{_uid()}"
        _, result = await _apply(db, admin_user, {
            "agents": [{
                "namespace": ns, "name": "helper", "systemPrompt": "x",
                "enabledMcpServers": ["tools/tracker", {"server": "tools/docs", "tools": ["search_*"]}],
            }],
        })
        assert result.success, result.errors
        agent = (await db.execute(select(Agent).where(Agent.namespace == ns))).scalar_one()
        assert agent.enabled_mcp_servers == [
            {"server": "tools/docs", "tools": ["search_*"]},
            {"server": "tools/tracker", "tools": []},
        ]
        assert serialize_agent(agent)["enabledMcpServers"] == agent.enabled_mcp_servers

        # Changing only the binding is a change (it is part of the hash).
        _, result = await _apply(db, admin_user, {
            "agents": [{
                "namespace": ns, "name": "helper", "systemPrompt": "x",
                "enabledMcpServers": ["tools/tracker"],
            }],
        })
        assert result.success and result.summary.updated == {"agents": 1}
        stored = (
            await db.execute(select(Agent.enabled_mcp_servers).where(Agent.id == agent.id))
        ).scalar_one()
        assert stored == [{"server": "tools/tracker", "tools": []}]

    async def test_agents_without_a_binding_are_unchanged(self, db: AsyncSession, admin_user):
        ns = f"ns{_uid()}"
        spec = {"agents": [{"namespace": ns, "name": "plain", "systemPrompt": "x"}]}
        await _apply(db, admin_user, spec)
        _, result = await _apply(db, admin_user, spec)
        assert result.success and not result.summary.updated
        agent = (await db.execute(select(Agent).where(Agent.namespace == ns))).scalar_one()
        assert agent.enabled_mcp_servers == []
        assert "enabledMcpServers" not in serialize_agent(agent)

    async def test_rest_create_and_update(self, client, admin_user):
        ns, headers = f"ns{_uid()}", auth_headers(admin_user)
        server = f"tracker-{_uid()}"
        assert (await client.post("/api/v1/mcp-servers", json=_rest_server(server), headers=headers)).status_code == 201
        ref = f"tools/{server}"

        response = await client.post("/api/v1/agents", json={
            "namespace": ns, "name": "api", "system_prompt": "x",
            "enabled_mcp_servers": [{"server": ref}],
        }, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["enabled_mcp_servers"] == [{"server": ref, "tools": []}]

        response = await client.put(f"/api/v1/agents/{ns}/api", json={
            "enabled_mcp_servers": [{"server": ref, "tools": ["list_*"]}],
        }, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["enabled_mcp_servers"] == [{"server": ref, "tools": ["list_*"]}]

        response = await client.put(f"/api/v1/agents/{ns}/api", json={"description": "d"}, headers=headers)
        assert response.json()["enabled_mcp_servers"] == [{"server": ref, "tools": ["list_*"]}]

        # A binding to a server that doesn't exist is refused on the way in.
        response = await client.post("/api/v1/agents", json={
            "namespace": ns, "name": "b", "system_prompt": "x",
            "enabled_mcp_servers": [{"server": "tools/nope"}],
        }, headers=headers)
        assert response.status_code == 404, response.text
