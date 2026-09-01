"""Workbench file references in tool calls + result spill.

Contracts under test: the typed sentinel resolves to file content (text or
base64) anywhere in the argument tree; every failure mode errors the call
instead of leaking the sentinel; resolution is chat-scoped; oversized
results spill in full to tool_results/ with provenance and the inline copy
points at them; no workbench means exactly the old truncate-only behavior.
"""
import base64
import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat import Chat
from app.models.file import File
from app.models.user import User
from app.services import workbench_refs
from app.services.workbench import WorkbenchTools, get_or_create_workbench


@pytest.fixture(autouse=True)
def _tmp_file_storage(tmp_path, monkeypatch):
    import app.services.file_storage as fs

    monkeypatch.setenv("FILE_STORAGE_PATH", str(tmp_path / "files"))
    fs._storage = None
    yield
    fs._storage = None


@pytest_asyncio.fixture
async def chat(db: AsyncSession, test_user: User) -> Chat:
    c = Chat(user_id=test_user.id, title="refs test chat")
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return c


async def _write(db, chat, test_user, filename: str, content: str):
    result = await WorkbenchTools().execute_tool(
        db, chat, str(test_user.id), "workbench_write",
        {"filename": filename, "content": content},
    )
    assert "error" not in result, result


class TestContainsReference:
    def test_fast_precheck(self):
        assert workbench_refs.contains_reference('{"body": {"$workbench": "a.txt"}}')
        assert not workbench_refs.contains_reference('{"body": "plain"}')
        assert workbench_refs.contains_reference({"body": {"$workbench": "a.txt"}})
        assert not workbench_refs.contains_reference({"body": "plain"})


class TestResolveReferences:
    @pytest.mark.asyncio
    async def test_text_reference_resolves_anywhere_in_tree(self, db, chat, test_user):
        await _write(db, chat, test_user, "report.md", "# Findings\nAll good.")
        resolved = await workbench_refs.resolve_references(
            db, chat, str(test_user.id),
            {
                "title": "Weekly report",
                "body": {"$workbench": "report.md"},
                "attachments": [{"content": {"$workbench": "report.md"}}],
            },
        )
        assert resolved["body"] == "# Findings\nAll good."
        assert resolved["attachments"][0]["content"] == "# Findings\nAll good."
        assert resolved["title"] == "Weekly report"

    @pytest.mark.asyncio
    async def test_base64_encoding_on_request(self, db, chat, test_user):
        await _write(db, chat, test_user, "data.txt", "payload")
        resolved = await workbench_refs.resolve_references(
            db, chat, str(test_user.id),
            {"file": {"$workbench": "data.txt", "encoding": "base64"}},
        )
        assert base64.b64decode(resolved["file"]) == b"payload"

    @pytest.mark.asyncio
    async def test_missing_file_errors(self, db, chat, test_user):
        with pytest.raises(workbench_refs.ReferenceError_, match="not found"):
            await workbench_refs.resolve_references(
                db, chat, str(test_user.id), {"body": {"$workbench": "nope.txt"}}
            )

    @pytest.mark.asyncio
    async def test_traversal_path_errors(self, db, chat, test_user):
        with pytest.raises(workbench_refs.ReferenceError_, match="Invalid"):
            await workbench_refs.resolve_references(
                db, chat, str(test_user.id), {"body": {"$workbench": "../secrets"}}
            )

    @pytest.mark.asyncio
    async def test_size_cap(self, db, chat, test_user, monkeypatch):
        monkeypatch.setattr(settings, "workbench_ref_max_bytes", 10)
        await _write(db, chat, test_user, "big.txt", "x" * 100)
        with pytest.raises(workbench_refs.ReferenceError_, match="reference limit"):
            await workbench_refs.resolve_references(
                db, chat, str(test_user.id), {"body": {"$workbench": "big.txt"}}
            )

    @pytest.mark.asyncio
    async def test_other_users_chat_is_rejected(self, db, chat, admin_user):
        with pytest.raises(workbench_refs.ReferenceError_, match="different user"):
            await workbench_refs.resolve_references(
                db, chat, str(admin_user.id), {"body": {"$workbench": "a.txt"}}
            )

    @pytest.mark.asyncio
    async def test_non_sentinel_dicts_pass_through(self, db, chat, test_user):
        args = {"payload": {"$workbench": "a.txt", "extra": "key"}}  # extra key → not a reference
        resolved = await workbench_refs.resolve_references(db, chat, str(test_user.id), args)
        assert resolved == args


class TestResultSpill:
    @pytest_asyncio.fixture
    async def wb_chat(self, db, test_user):
        """A chat whose agent has the workbench enabled (spill requires it)."""
        from app.models import Agent

        agent = Agent(
            namespace="test", name="spiller", user_id=test_user.id,
            system_tools=["workbench"],
        )
        db.add(agent)
        await db.flush()
        c = Chat(user_id=test_user.id, agent_id=agent.id, title="spill chat")
        db.add(c)
        await db.flush()
        await db.refresh(c)
        return c

    @pytest.mark.asyncio
    async def test_spill_saves_full_result_with_provenance(self, db, wb_chat, test_user):
        big = json.dumps({"rows": list(range(1000))})
        path = await workbench_refs.spill_result(
            db, wb_chat, str(test_user.id), "some_query", "call_abc123", big
        )
        assert path == "tool_results/some_query_call_abc123.json"

        wb = await get_or_create_workbench(db, wb_chat)
        from sqlalchemy import select
        f = (
            await db.execute(select(File).where(File.collection_id == wb.id))
        ).scalar_one()
        assert f.name == path
        assert f.file_metadata["origin"] == "tool"
        assert f.file_metadata["tool_call_id"] == "call_abc123"

        # And the agent can read it back in full.
        result = await WorkbenchTools().execute_tool(
            db, wb_chat, str(test_user.id), "workbench_read", {"filename": path, "limit": 1}
        )
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_no_workbench_agent_means_no_spill(self, db, chat, test_user):
        path = await workbench_refs.spill_result(
            db, chat, str(test_user.id), "some_query", "call_x", "content"
        )
        assert path is None

    def test_pointer_attaches_inside_json_dict(self):
        truncated = json.dumps({"rows": [1, 2], "_truncated": True})
        out = workbench_refs.attach_spill_pointer(truncated, "tool_results/q.json")
        parsed = json.loads(out)
        assert parsed["_full_result"]["workbench_file"] == "tool_results/q.json"

    def test_pointer_appends_for_non_dict_content(self):
        out = workbench_refs.attach_spill_pointer("plain text tail", "tool_results/q.txt")
        assert out.startswith("plain text tail")
        assert "tool_results/q.txt" in out
