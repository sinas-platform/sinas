"""MCP servers as a tool source (ADR 2026-10-09-mcp-client).

A fake MCP server runs in-process behind the real Streamable HTTP client
transport (ASGI, no sockets), so listing, calling, auth headers and content
mapping are exercised end to end. Contracts under test: tools convert to
OpenAI-format definitions with mcp metadata; server and agent filters
apply; a server that doesn't answer contributes nothing (and isn't
hammered); results map text / structured / binary content; credentials
come from Secrets and a missing one refuses the call; MCP tools go through
the generic dispatch (workbench references, approval rules) unchanged.
"""
import base64
import contextlib
import json
import uuid

import httpx2
import pytest
import pytest_asyncio
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ImageContent, TextContent
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, async_engine
from app.core.encryption import encryption_service
from app.models.agent import Agent
from app.models.chat import Chat, Message
from app.models.file import Collection, File
from app.models.mcp_server import McpServer
from app.models.pending_approval import PendingToolApproval
from app.models.secret import Secret
from app.models.user import Role, RolePermission, User, UserRole
from app.services.mcp_client import blob_prefix_for, safe_url
from app.services import mcp_client
from app.services.mcp_tools import McpToolConverter, parse_mcp_tool_name
from app.services.tool_execution import (
    build_tool_status,
    check_approval_requirements,
    execute_single_tool,
    tool_name_to_status_key,
)
from app.services.workbench import WorkbenchTools
from tests.conftest import auth_headers

PNG = base64.b64encode(b"\x89PNG fake").decode()


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ------------------------------------------------------------ fake server


class FakeMcp:
    """An in-process MCP server + the transport that reaches it."""

    def __init__(self):
        self.server = MCPServer("fake")
        self.seen_headers: list[dict[str, str]] = []
        self.transport_opens = 0

        @self.server.tool(description="Echo the text back")
        def echo(text: str) -> str:
            return text

        @self.server.tool(description="Add two numbers")
        def add(a: int, b: int) -> int:
            return a + b

        @self.server.tool(description="A picture with a caption")
        def picture() -> list:
            return [
                TextContent(type="text", text="caption"),
                ImageContent(type="image", data=PNG, mime_type="image/png"),
            ]

        @self.server.tool(description="Always fails")
        def fail() -> str:
            raise ValueError("nope")

        @self.server.tool(name="weird.name/v2", description="Odd characters")
        def weird(x: str) -> str:
            return f"weird:{x}"

        self.app = self.server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    def open_transport(self, server: McpServer, headers: dict[str, str]):
        fake = self

        @contextlib.asynccontextmanager
        async def _transport():
            fake.transport_opens += 1
            if "down.test" in server.url:
                raise httpx2.ConnectError("connection refused")
            fake.seen_headers.append(dict(headers))
            http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=fake.app), headers=headers)
            async with http:
                async with streamable_http_client(server.url, http_client=http) as streams:
                    yield streams

        return _transport()


@pytest_asyncio.fixture
async def fake_mcp(monkeypatch):
    fake = FakeMcp()
    mcp_client.clear_tool_cache()
    monkeypatch.setattr(mcp_client, "open_transport", fake.open_transport)
    # The server's lifespan owns an anyio task group, which must be entered
    # and exited by the same task; pytest-asyncio tears fixtures down from
    # another one, so the lifespan runs in a task of its own.
    import asyncio

    started, stop = asyncio.Event(), asyncio.Event()

    async def run_lifespan():
        async with fake.app.router.lifespan_context(fake.app):
            started.set()
            await stop.wait()

    runner = asyncio.create_task(run_lifespan())
    await started.wait()
    try:
        yield fake
    finally:
        stop.set()
        await runner
        mcp_client.clear_tool_cache()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_per_test():
    """The module-level engine binds its pool to the first event loop that
    used it; each test runs in a fresh loop (as in test_ask_user)."""
    yield
    await async_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _test_role_can_read_own_servers(db: AsyncSession, test_role: Role):
    """Using a server requires read access to it (its own permission model);
    the shared test role grants nothing on mcp_servers, so give it :own."""
    db.add(RolePermission(
        role_id=test_role.id, permission_key="sinas.mcp_servers/*/*.read:own", permission_value=True
    ))
    await db.flush()


@pytest.fixture(autouse=True)
def _tmp_file_storage(tmp_path, monkeypatch):
    import app.services.file_storage as fs

    monkeypatch.setenv("FILE_STORAGE_PATH", str(tmp_path / "files"))
    fs._storage = None
    yield
    fs._storage = None


def _server(owner, **extra) -> McpServer:
    fields = dict(
        user_id=owner.id,
        namespace="tools",
        name=f"fake-{_uid()}",
        url="http://fake.test/mcp",
        transport="streamable_http",
        auth={"type": "none"},
        headers={},
        tool_allow=[],
        tool_deny=[],
        timeout_seconds=30,
        connect_timeout_seconds=5,
        is_active=True,
    )
    fields.update(extra)
    return McpServer(**fields)


@pytest_asyncio.fixture
async def server(db: AsyncSession, test_user: User) -> McpServer:
    row = _server(test_user)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


def _binding(server: McpServer, tools=None) -> list[dict]:
    return [{"server": f"{server.namespace}/{server.name}", "tools": tools or []}]


def _tool_def(server: McpServer, mcp_tool: str, name: str | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name or f"mcp_{server.namespace}__{server.name}__{mcp_tool}",
            "description": "x",
            "parameters": {"type": "object", "properties": {}},
            "_metadata": {
                "tool_type": "mcp",
                "server_namespace": server.namespace,
                "server_name": server.name,
                "mcp_tool": mcp_tool,
            },
        },
    }


# ------------------------------------------------------------ discovery


class TestDiscovery:
    @pytest.mark.asyncio
    async def test_lists_and_converts_tools(self, db, fake_mcp, server, test_user):
        tools = await McpToolConverter().get_available_tools(db, _binding(server), str(test_user.id))
        by_name = {t["function"]["name"]: t["function"] for t in tools}
        prefix = f"mcp_{server.namespace}__{server.name}__"
        assert set(by_name) == {prefix + n for n in ("echo", "add", "picture", "fail", "weird_name_v2")}

        add = by_name[prefix + "add"]
        assert add["description"] == f"[{server.namespace}/{server.name}] Add two numbers"
        assert add["parameters"]["properties"]["a"]["type"] == "integer"
        assert add["parameters"]["required"] == ["a", "b"]
        assert add["_metadata"] == {
            "tool_type": "mcp",
            "server_namespace": server.namespace,
            "server_name": server.name,
            "mcp_tool": "add",
        }
        # The sanitized name is for the model; the real MCP name rides in metadata.
        assert by_name[prefix + "weird_name_v2"]["_metadata"]["mcp_tool"] == "weird.name/v2"
        # Nothing an approval check would mistake for a function tool.
        assert "namespace" not in add["_metadata"] and "name" not in add["_metadata"]

    @pytest.mark.asyncio
    async def test_server_filters_and_agent_filter(self, db, fake_mcp, test_user):
        server = _server(test_user, tool_allow=["e*", "add", "pic*"], tool_deny=["echo"])
        db.add(server)
        await db.flush()
        await db.refresh(server)

        names = lambda tools: sorted(t["function"]["_metadata"]["mcp_tool"] for t in tools)  # noqa: E731
        everything = await McpToolConverter().get_available_tools(db, _binding(server), str(test_user.id))
        assert names(everything) == ["add", "picture"]  # deny beats allow; allow is a whitelist

        narrowed = await McpToolConverter().get_available_tools(
            db, _binding(server, ["pic*"]), str(test_user.id)
        )
        assert names(narrowed) == ["picture"]

        string_form = await McpToolConverter().get_available_tools(
            db, [f"{server.namespace}/{server.name}"], str(test_user.id)
        )
        assert names(string_form) == ["add", "picture"]

    @pytest.mark.asyncio
    async def test_unreachable_server_means_no_tools_not_a_failed_turn(
        self, db, fake_mcp, test_user, caplog
    ):
        down = _server(test_user, url="http://down.test/mcp")
        db.add(down)
        await db.flush()
        await db.refresh(down)

        with caplog.at_level("WARNING"):
            tools = await McpToolConverter().get_available_tools(db, _binding(down), str(test_user.id))
        assert tools == []
        assert any("MCP tools unavailable" in r.message and "down.test" in r.message for r in caplog.records)

        # The failure is remembered briefly: the next turn doesn't reconnect.
        opens = fake_mcp.transport_opens
        assert await McpToolConverter().get_available_tools(db, _binding(down), str(test_user.id)) == []
        assert fake_mcp.transport_opens == opens

    @pytest.mark.asyncio
    async def test_tool_list_is_cached_per_server_version(self, db, fake_mcp, server, test_user):
        converter = McpToolConverter()
        await converter.get_available_tools(db, _binding(server), str(test_user.id))
        await converter.get_available_tools(db, _binding(server), str(test_user.id))
        assert fake_mcp.transport_opens == 1

        listed = await mcp_client.list_tools(db, server, str(test_user.id), use_cache=False)
        assert fake_mcp.transport_opens == 2 and len(listed) == 5

    @pytest.mark.asyncio
    async def test_missing_inactive_or_malformed_references_are_skipped(self, db, fake_mcp, test_user):
        inactive = _server(test_user, is_active=False)
        db.add(inactive)
        await db.flush()
        tools = await McpToolConverter().get_available_tools(
            db,
            [
                "not-a-reference",
                {"server": "tools/does-not-exist"},
                {"server": f"{inactive.namespace}/{inactive.name}"},
            ],
            str(test_user.id),
        )
        assert tools == []


# ------------------------------------------------------------ execution + mapping


class TestExecution:
    @pytest.mark.asyncio
    async def test_text_and_structured_results(self, db, fake_mcp, server, test_user):
        converter = McpToolConverter()
        echoed = await converter.execute_tool(
            db, "mcp_x", {"text": "hello"}, str(test_user.id), _tool_def(server, "echo")["function"]["_metadata"]
        )
        assert echoed == {"text": "hello"}

        added = await converter.execute_tool(
            db, "mcp_x", {"a": 2, "b": 3}, str(test_user.id), _tool_def(server, "add")["function"]["_metadata"]
        )
        # The SDK wraps a scalar return as {"result": 5} beside the text "5";
        # the model gets one of them, not both.
        assert added == {"text": "5"}

    @pytest.mark.asyncio
    async def test_structured_content_is_kept_when_it_says_more_than_the_text(self):
        from mcp.types import CallToolResult

        structured = {"items": [1, 2], "total": 2}
        repeated = CallToolResult(
            content=[TextContent(type="text", text=json.dumps(structured))], structured_content=structured
        )
        assert await mcp_client.map_call_result(repeated, tool_name="t") == {"structured_content": structured}
        both = CallToolResult(
            content=[TextContent(type="text", text="2 items")], structured_content=structured
        )
        assert await mcp_client.map_call_result(both, tool_name="t") == {
            "text": "2 items", "structured_content": structured,
        }

    @pytest.mark.asyncio
    async def test_image_inlines_as_data_url_without_a_workbench(self, db, fake_mcp, server, test_user):
        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {}, str(test_user.id), _tool_def(server, "picture")["function"]["_metadata"]
        )
        assert result["text"] == "caption"
        assert result["content"] == [
            {"type": "image", "mime_type": "image/png", "image": f"data:image/png;base64,{PNG}"}
        ]

    @pytest.mark.asyncio
    async def test_image_lands_in_the_workbench_when_the_agent_has_one(
        self, db, fake_mcp, server, test_user
    ):
        agent = Agent(
            user_id=test_user.id, namespace=f"ns{_uid()}", name="wb", system_tools=["workbench"]
        )
        db.add(agent)
        await db.flush()
        chat = Chat(user_id=test_user.id, agent_id=agent.id, title="mcp wb")
        db.add(chat)
        await db.flush()
        await db.refresh(chat)

        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {}, str(test_user.id),
            _tool_def(server, "picture")["function"]["_metadata"], chat=chat,
        )
        [chunk] = result["content"]
        assert chunk["type"] == "image" and chunk["mime_type"] == "image/png"
        assert chunk["workbench_file"] == "tool_results/mcp_x_1.png"
        assert "image" not in chunk  # the model gets a path, not a base64 wall

        read = await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_read",
            {"filename": "tool_results/mcp_x_1.png"},
        )
        assert "error" not in read, read

    @pytest.mark.asyncio
    async def test_each_call_gets_its_own_blob_files(self, db, fake_mcp, server, test_user):
        """A second call to the same tool must not overwrite the first call's
        file: the workbench advances the version, so the earlier pointer
        would silently serve the later blob."""
        agent = Agent(
            user_id=test_user.id, namespace=f"ns{_uid()}", name="wb", system_tools=["workbench"]
        )
        db.add(agent)
        await db.flush()
        chat = Chat(user_id=test_user.id, agent_id=agent.id, title="mcp wb")
        db.add(chat)
        await db.flush()
        await db.refresh(chat)

        meta = _tool_def(server, "picture")["function"]["_metadata"]
        name = f"mcp_{server.namespace}__{server.name}__picture"
        paths = []
        for call_id in ("call_a", "call_b"):
            result = await McpToolConverter().execute_tool(
                db, name, {}, str(test_user.id), meta, chat=chat, tool_call_id=call_id
            )
            paths.append(result["content"][0]["workbench_file"])
        assert paths == [f"tool_results/{name}_call_a_1.png", f"tool_results/{name}_call_b_1.png"]

    def test_blob_prefix_is_unique_per_call_and_server(self):
        assert blob_prefix_for("mcp_a__s__pic", "call_1") == "mcp_a__s__pic_call_1"
        assert blob_prefix_for("mcp_a__s__pic", None) == "mcp_a__s__pic"
        # Servers tell the names apart; odd or long call ids get a hash suffix.
        assert blob_prefix_for("mcp_a__t__pic", "call_1") != blob_prefix_for("mcp_a__s__pic", "call_1")
        odd = blob_prefix_for("mcp_a__s__pic", "call/with:odd")
        assert odd.startswith("mcp_a__s__pic_call_with_odd_") and "/" not in odd
        assert blob_prefix_for("t", "x" * 80) != blob_prefix_for("t", "x" * 79 + "y")

    @pytest.mark.asyncio
    async def test_a_failing_tool_is_an_error_result(self, db, fake_mcp, server, test_user):
        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {}, str(test_user.id), _tool_def(server, "fail")["function"]["_metadata"]
        )
        assert result == {"error": "Error executing tool fail"}

    @pytest.mark.asyncio
    async def test_server_side_filter_is_enforced_at_call_time(self, db, fake_mcp, test_user):
        server = _server(test_user, tool_deny=["echo"])
        db.add(server)
        await db.flush()
        await db.refresh(server)
        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {"text": "x"}, str(test_user.id), _tool_def(server, "echo")["function"]["_metadata"]
        )
        assert "not allowed" in result["error"]
        assert fake_mcp.transport_opens == 0

    @pytest.mark.asyncio
    async def test_unreachable_server_is_an_error_result(self, db, fake_mcp, test_user):
        down = _server(test_user, url="http://down.test/mcp")
        db.add(down)
        await db.flush()
        await db.refresh(down)
        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {"text": "x"}, str(test_user.id), _tool_def(down, "echo")["function"]["_metadata"]
        )
        assert "down.test" in result["error"] and "connection refused" in result["error"]

    @pytest.mark.asyncio
    async def test_error_text_never_carries_url_credentials(self, db, fake_mcp, test_user):
        """A URL can hold a password or a query token; the error the model
        and the logs see shows the host and path only."""
        down = _server(test_user, url="http://svc:hunter2@down.test/mcp?token=abc123&x=1")
        db.add(down)
        await db.flush()
        await db.refresh(down)
        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {"text": "x"}, str(test_user.id), _tool_def(down, "echo")["function"]["_metadata"]
        )
        assert "http://down.test/mcp" in result["error"]
        for secret in ("hunter2", "abc123", "svc:", "token="):
            assert secret not in result["error"], secret

        tools = await McpToolConverter().get_available_tools(db, _binding(down), str(test_user.id))
        assert tools == []

    def test_scrubbing_covers_the_underlying_error_text(self):
        from app.services.mcp_client import _scrub

        url = "https://svc:hunter2@mcp.example.com/mcp?token=abc123"
        assert safe_url(url) == "https://mcp.example.com/mcp"
        scrubbed = _scrub(f"GET {url} failed; retried http://x/?token=abc123 as svc:hunter2", url)
        assert "hunter2" not in scrubbed and "abc123" not in scrubbed
        assert "https://mcp.example.com/mcp" in scrubbed
        assert safe_url("not a url at all") == "not a url at all"

    @pytest.mark.asyncio
    async def test_name_parsing_fallback(self, db, fake_mcp, server, test_user):
        assert parse_mcp_tool_name("mcp_tools__srv__echo") == ("tools", "srv", "echo")
        assert parse_mcp_tool_name("connector__a__b__c") is None
        assert parse_mcp_tool_name("mcp_broken") is None
        result = await McpToolConverter().execute_tool(db, "mcp_broken", {}, str(test_user.id), {})
        assert "Invalid MCP tool name" in result["error"]

    @pytest.mark.asyncio
    async def test_map_call_result_covers_every_content_kind(self):
        from mcp.types import (
            AudioContent,
            BlobResourceContents,
            CallToolResult,
            EmbeddedResource,
            ResourceLink,
            TextResourceContents,
        )

        stored: list[tuple[str, bytes, str]] = []

        async def store(filename, content, mime):
            stored.append((filename, content, mime))
            return f"tool_results/{filename}"

        result = CallToolResult(
            content=[
                TextContent(type="text", text="one"),
                TextContent(type="text", text="two"),
                AudioContent(type="audio", data=base64.b64encode(b"wav").decode(), mime_type="audio/wav"),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(uri="file:///notes.md", mime_type="text/markdown", text="# hi"),
                ),
                EmbeddedResource(
                    type="resource",
                    resource=BlobResourceContents(
                        uri="file:///blob.bin", mime_type="application/octet-stream",
                        blob=base64.b64encode(b"\x00\x01").decode(),
                    ),
                ),
                ResourceLink(type="resource_link", uri="https://x.test/doc", name="doc", mime_type="text/html"),
            ],
        )
        mapped = await mcp_client.map_call_result(result, tool_name="t", store_blob=store)
        assert mapped["text"] == "one\ntwo"
        kinds = [c["type"] for c in mapped["content"]]
        assert kinds == ["audio", "resource", "file", "resource_link"]
        assert mapped["content"][0]["workbench_file"] == "tool_results/mcp_t_2.wav"
        assert mapped["content"][1] == {
            "type": "resource", "uri": "file:///notes.md", "mime_type": "text/markdown", "text": "# hi",
        }
        assert mapped["content"][2]["uri"] == "file:///blob.bin"
        assert mapped["content"][3] == {
            "type": "resource_link", "uri": "https://x.test/doc", "name": "doc", "mime_type": "text/html",
        }
        assert [s[1] for s in stored] == [b"wav", b"\x00\x01"]

        # Without a store, audio inlines in the universal shape.
        inline = await mcp_client.map_call_result(result, tool_name="t")
        assert inline["content"][0] == {
            "type": "audio", "mime_type": "audio/wav",
            "data": base64.b64encode(b"wav").decode(), "format": "wav",
        }

        # isError → an error result, text preserved.
        failed = CallToolResult(content=[TextContent(type="text", text="boom")], is_error=True)
        assert await mcp_client.map_call_result(failed, tool_name="t") == {"error": "boom"}


# ------------------------------------------------------------ auth


class TestAuth:
    @pytest.mark.asyncio
    async def test_bearer_secret_is_resolved_and_sent(self, db, fake_mcp, test_user):
        secret_name = f"MCP_TOKEN_{_uid()}"
        db.add(Secret(
            user_id=test_user.id, name=secret_name, visibility="shared",
            encrypted_value=encryption_service.encrypt("tok-123"),
        ))
        server = _server(
            test_user, auth={"type": "bearer", "secret": secret_name}, headers={"X-Tenant": "acme"}
        )
        db.add(server)
        await db.flush()
        await db.refresh(server)

        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {"text": "hi"}, str(test_user.id), _tool_def(server, "echo")["function"]["_metadata"]
        )
        assert result == {"text": "hi"}
        assert fake_mcp.seen_headers[-1] == {"X-Tenant": "acme", "Authorization": "Bearer tok-123"}

    @pytest.mark.asyncio
    async def test_listings_are_cached_per_user_and_credential(self, db, fake_mcp, test_user, admin_user):
        """Private secrets are per user: one user's listing (obtained with
        their credential) is never served to another, and a user without
        the secret is refused even while another user's listing is cached."""
        secret_name = f"MCP_PRIVATE_{_uid()}"
        for user, value in ((test_user, "tok-a"), (admin_user, "tok-b")):
            db.add(Secret(
                user_id=user.id, name=secret_name, visibility="private",
                encrypted_value=encryption_service.encrypt(value),
            ))
        server = _server(test_user, auth={"type": "bearer", "secret": secret_name})
        db.add(server)
        await db.flush()
        await db.refresh(server)

        await mcp_client.list_tools(db, server, str(test_user.id))
        await mcp_client.list_tools(db, server, str(test_user.id))  # a hit
        await mcp_client.list_tools(db, server, str(admin_user.id))  # a miss: other credential
        assert fake_mcp.transport_opens == 2
        assert [h["Authorization"] for h in fake_mcp.seen_headers] == ["Bearer tok-a", "Bearer tok-b"]

        # A third user has no such secret: refused, not served from either cache entry.
        stranger = User(email=f"stranger-{_uid()}@example.com")
        db.add(stranger)
        await db.flush()
        with pytest.raises(mcp_client.McpClientError, match="not found"):
            await mcp_client.list_tools(db, server, str(stranger.id))
        # ...and that refusal poisons nobody else's entry.
        await mcp_client.list_tools(db, server, str(test_user.id))
        assert fake_mcp.transport_opens == 2

    @pytest.mark.asyncio
    async def test_header_auth(self, db, fake_mcp, test_user):
        secret_name = f"MCP_KEY_{_uid()}"
        db.add(Secret(
            user_id=test_user.id, name=secret_name, visibility="shared",
            encrypted_value=encryption_service.encrypt("k-1"),
        ))
        server = _server(test_user, auth={"type": "header", "secret": secret_name, "header": "X-Api-Key"})
        db.add(server)
        await db.flush()
        await db.refresh(server)
        await mcp_client.list_tools(db, server, str(test_user.id))
        assert fake_mcp.seen_headers[-1] == {"X-Api-Key": "k-1"}

    @pytest.mark.asyncio
    async def test_a_missing_secret_refuses_rather_than_sends_unauthenticated(
        self, db, fake_mcp, test_user
    ):
        server = _server(test_user, auth={"type": "bearer", "secret": "NOPE_" + _uid()})
        db.add(server)
        await db.flush()
        await db.refresh(server)

        tools = await McpToolConverter().get_available_tools(db, _binding(server), str(test_user.id))
        assert tools == []
        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {"text": "x"}, str(test_user.id), _tool_def(server, "echo")["function"]["_metadata"]
        )
        assert "not found" in result["error"] and "secret" in result["error"]
        assert fake_mcp.seen_headers == []  # nothing ever went out


# ------------------------------------------------------------ access


class TestAccess:
    @pytest.mark.asyncio
    async def test_another_owners_server_is_not_usable_with_own_permissions(
        self, db, fake_mcp, test_user, admin_user, caplog
    ):
        """Binding a server by name grants nothing: the chat's user needs
        read access to the server itself (:own as owner, or :all)."""
        theirs = _server(admin_user)
        db.add(theirs)
        await db.flush()
        await db.refresh(theirs)

        with caplog.at_level("WARNING"):
            tools = await McpToolConverter().get_available_tools(db, _binding(theirs), str(test_user.id))
        assert tools == []
        assert any("not authorized" in r.message for r in caplog.records)
        result = await McpToolConverter().execute_tool(
            db, "mcp_x", {"text": "x"}, str(test_user.id), _tool_def(theirs, "echo")["function"]["_metadata"]
        )
        assert "not authorized" in result["error"]
        assert fake_mcp.transport_opens == 0

        # The owner (and anyone with :all) can.
        assert len(await McpToolConverter().get_available_tools(db, _binding(theirs), str(admin_user.id))) == 5
        own = await McpToolConverter().get_available_tools(
            db, _binding(theirs), str(test_user.id)
        )
        assert own == []

    @pytest.mark.asyncio
    async def test_agent_bindings_are_checked_against_server_access(
        self, client, db, fake_mcp, test_user, admin_user
    ):
        theirs, mine = _server(admin_user), _server(test_user)
        db.add_all([theirs, mine])
        await db.flush()
        headers = auth_headers(test_user)
        ns = f"ns{_uid()}"

        def body(ref):
            return {"namespace": ns, "name": "a", "system_prompt": "x", "enabled_mcp_servers": [{"server": ref}]}

        response = await client.post("/api/v1/agents", json=body(f"{theirs.namespace}/{theirs.name}"), headers=headers)
        assert response.status_code == 403, response.text
        response = await client.post("/api/v1/agents", json=body("tools/does-not-exist"), headers=headers)
        assert response.status_code == 404, response.text
        response = await client.post("/api/v1/agents", json=body(f"{mine.namespace}/{mine.name}"), headers=headers)
        assert response.status_code == 201, response.text

        response = await client.put(
            f"/api/v1/agents/{ns}/a",
            json={"enabled_mcp_servers": [{"server": f"{theirs.namespace}/{theirs.name}"}]},
            headers=headers,
        )
        assert response.status_code == 403, response.text
        response = await client.put(
            f"/api/v1/agents/{ns}/a",
            json={"enabled_mcp_servers": [{"server": f"{mine.namespace}/{mine.name}", "tools": ["e*"]}]},
            headers=headers,
        )
        assert response.status_code == 200, response.text


# ------------------------------------------------------------ generic dispatch


@pytest_asyncio.fixture
async def committed_env(fake_mcp):
    """Committed user + workbench agent + chat + server: execute_single_tool
    opens its own DB sessions."""
    async with AsyncSessionLocal() as s:
        user = User(email=f"mcp-{_uid()}@example.com")
        s.add(user)
        await s.flush()
        role = Role(name=f"mcp-role-{_uid()}", description="t")
        s.add(role)
        await s.flush()
        s.add(RolePermission(role_id=role.id, permission_key="sinas.*:all", permission_value=True))
        s.add(UserRole(role_id=role.id, user_id=user.id, active=True))
        agent = Agent(
            user_id=user.id, namespace=f"ns{_uid()}", name="mcp-agent",
            system_tools=["workbench"],
            tool_approvals={"rules": [{"match": "mcp_*", "action": "ask"}]},
        )
        s.add(agent)
        await s.flush()
        chat = Chat(user_id=user.id, agent_id=agent.id, title="mcp chat")
        server = _server(user)
        s.add_all([chat, server])
        await s.commit()
        for row in (user, chat, agent, server):
            await s.refresh(row)
        env = {"user": user, "chat": chat, "agent": agent, "server": server, "role": role}

    yield env

    async with AsyncSessionLocal() as s:
        user_id, chat_id = env["user"].id, env["chat"].id
        collections = (await s.execute(select(Collection.id).where(Collection.user_id == user_id))).scalars().all()
        if collections:
            await s.execute(delete(File).where(File.collection_id.in_(collections)))
            await s.execute(delete(Collection).where(Collection.id.in_(collections)))
        await s.execute(delete(PendingToolApproval).where(PendingToolApproval.chat_id == chat_id))
        await s.execute(delete(Message).where(Message.chat_id == chat_id))
        await s.execute(delete(Chat).where(Chat.id == chat_id))
        await s.execute(delete(Agent).where(Agent.id == env["agent"].id))
        await s.execute(delete(McpServer).where(McpServer.id == env["server"].id))
        await s.execute(delete(UserRole).where(UserRole.user_id == user_id))
        await s.execute(delete(RolePermission).where(RolePermission.role_id == env["role"].id))
        await s.execute(delete(Role).where(Role.id == env["role"].id))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


class TestGenericDispatch:
    @pytest.mark.asyncio
    async def test_execute_single_tool_resolves_workbench_references(self, committed_env):
        """{"$workbench": path} arguments reach the MCP server as content —
        the reference resolver runs before dispatch for every tool kind."""
        from app.services.message_service import MessageService

        env = committed_env
        async with AsyncSessionLocal() as s:
            chat = await s.get(Chat, env["chat"].id)
            written = await WorkbenchTools().execute_tool(
                s, chat, str(env["user"].id), "workbench_write",
                {"filename": "note.txt", "content": "from the workbench"},
            )
            assert "error" not in written, written
            await s.commit()
            svc = MessageService(s)
            tool = _tool_def(env["server"], "echo")
            call = {
                "id": "call_mcp_1",
                "type": "function",
                "function": {
                    "name": tool["function"]["name"],
                    "arguments": json.dumps({"text": {"$workbench": "note.txt"}}),
                },
            }
            call_id, name, content = await execute_single_tool(
                call, str(env["chat"].id), str(env["user"].id), "tok", [tool],
                svc.function_converter, svc.query_converter, svc.skill_converter,
                svc.component_converter, svc.collection_converter, svc.create_chat_with_agent,
            )
        assert (call_id, name) == ("call_mcp_1", tool["function"]["name"])
        assert json.loads(content) == {"text": "from the workbench"}

    @pytest.mark.asyncio
    async def test_a_tool_not_in_the_approved_list_is_refused(self, committed_env):
        from app.services.message_service import MessageService

        env = committed_env
        async with AsyncSessionLocal() as s:
            svc = MessageService(s)
            tool = _tool_def(env["server"], "echo")
            call = {"id": "c", "type": "function", "function": {"name": tool["function"]["name"], "arguments": "{}"}}
            _, _, content = await execute_single_tool(
                call, str(env["chat"].id), str(env["user"].id), "tok", [],
                svc.function_converter, svc.query_converter, svc.skill_converter,
                svc.component_converter, svc.collection_converter, svc.create_chat_with_agent,
            )
        assert json.loads(content)["error"] == "Unauthorized tool call"

    @pytest.mark.asyncio
    async def test_approval_rules_gate_mcp_tools_by_name(self, committed_env):
        # Own committed session: the shared `db` fixture's open transaction
        # would hold the message's FK lock on the chat while the env's
        # cleanup tries to delete it.
        env = committed_env
        async with AsyncSessionLocal() as s:
            msg = Message(chat_id=env["chat"].id, role="assistant", content=None)
            s.add(msg)
            await s.commit()
            tool = _tool_def(env["server"], "echo")
            other = _tool_def(env["server"], "add", name="plain_tool")
            calls = [
                {"id": "call_gate", "type": "function", "function": {"name": tool["function"]["name"], "arguments": "{}"}},
                {"id": "call_free", "type": "function", "function": {"name": "plain_tool", "arguments": "{}"}},
            ]
            asked = await check_approval_requirements(
                db=s, tool_calls=calls, chat_id=str(env["chat"].id), user_id=str(env["user"].id),
                message_id=str(msg.id), messages=[], provider=None, model=None,
                temperature=0.7, max_tokens=None, tools=[tool, other],
            )
            await s.commit()
        assert [a["tool_call_id"] for a in asked] == ["call_gate"]
        assert (asked[0]["function_namespace"], asked[0]["function_name"]) == ("tool", tool["function"]["name"])


# ------------------------------------------------------------ status + REST


def test_status_key_and_fallback_status():
    assert tool_name_to_status_key("mcp_tools__github__create_issue") == "mcp:tools/github/create_issue"
    assert build_tool_status("mcp_tools__github__create_issue", {}, {}) == "Calling create issue"
    assert build_tool_status(
        "mcp_tools__github__create_issue", {"title": "x"},
        {"mcp:tools/github/create_issue": "Filing {{title}}"},
    ) == "Filing x"
    # A server-wide template (what the console's status editor saves) applies
    # to every tool of that server; an exact key still wins.
    assert build_tool_status(
        "mcp_tools__github__create_issue", {}, {"mcp:tools/github/*": "Working in GitHub"}
    ) == "Working in GitHub"
    assert build_tool_status(
        "mcp_tools__github__create_issue", {},
        {"mcp:tools/github/*": "Working in GitHub", "mcp:tools/github/create_issue": "Filing"},
    ) == "Filing"
    assert build_tool_status("mcp_tools__other__x", {}, {"mcp:tools/github/*": "Working in GitHub"}) == "Calling x"
    # No collision with the existing prefixes.
    assert tool_name_to_status_key("connector__a__b__c").startswith("function:")


class TestToolsEndpoint:
    @pytest.mark.asyncio
    async def test_live_listing_reports_what_the_filters_hide(self, client, db, fake_mcp, admin_user):
        server = _server(admin_user, tool_deny=["fail"])
        db.add(server)
        await db.flush()
        response = await client.post(
            f"/api/v1/mcp-servers/{server.namespace}/{server.name}/tools", headers=auth_headers(admin_user)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        allowed = {t["name"]: t["allowed"] for t in body["tools"]}
        assert allowed == {"echo": True, "add": True, "picture": True, "fail": False, "weird.name/v2": True}
        assert body["tools"][0]["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_unreachable_server_is_a_502(self, client, db, fake_mcp, admin_user):
        down = _server(admin_user, url="http://down.test/mcp")
        db.add(down)
        await db.flush()
        response = await client.post(
            f"/api/v1/mcp-servers/{down.namespace}/{down.name}/tools", headers=auth_headers(admin_user)
        )
        assert response.status_code == 502
        assert "down.test" in response.json()["detail"]
