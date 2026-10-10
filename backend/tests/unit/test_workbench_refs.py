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


class TestReferenceBudget:
    @pytest.mark.asyncio
    async def test_aggregate_limit_across_references(self, db, chat, test_user, monkeypatch):
        """Per-file cap alone would let three near-cap references triple the
        argument size; the budget is shared across the whole call."""
        monkeypatch.setattr(settings, "workbench_ref_max_bytes", 100)
        await _write(db, chat, test_user, "a.txt", "a" * 60)
        await _write(db, chat, test_user, "b.txt", "b" * 60)
        with pytest.raises(workbench_refs.ReferenceError_, match="combined"):
            await workbench_refs.resolve_references(
                db, chat, str(test_user.id),
                {"x": {"$workbench": "a.txt"}, "y": {"$workbench": "b.txt"}},
            )

    @pytest.mark.asyncio
    async def test_same_file_referenced_twice_is_read_once_and_charged_once(
        self, db, chat, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "workbench_ref_max_bytes", 100)
        await _write(db, chat, test_user, "a.txt", "a" * 60)
        resolved = await workbench_refs.resolve_references(
            db, chat, str(test_user.id),
            {"x": {"$workbench": "a.txt"}, "y": {"$workbench": "a.txt"}},
        )
        assert resolved == {"x": "a" * 60, "y": "a" * 60}


class TestSpillFilename:
    def test_distinct_call_ids_never_collide(self):
        a = workbench_refs._spill_filename("q", "call_" + "x" * 11 + "AAAAAAAAAAAAA", "{}")
        b = workbench_refs._spill_filename("q", "call_" + "x" * 11 + "BBBBBBBBBBBBB", "{}")
        assert a != b
        long_a = workbench_refs._spill_filename("q", "k" * 70 + "1", "{}")
        long_b = workbench_refs._spill_filename("q", "k" * 70 + "2", "{}")
        assert long_a != long_b
        assert len(long_a.split("/")[-1]) < 120

    def test_sanitized_ids_keep_identity(self):
        # 'a/b' and 'a_b' sanitize alike; the hash suffix keeps them apart.
        assert workbench_refs._spill_filename("q", "a/b", "x") != workbench_refs._spill_filename("q", "a_b", "x")


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


class TestRetrieveServesSpilledCopy:
    """retrieve_tool_result follows the inline pointer back to the full spill
    (issue #226): the messages row only holds the clipped copy."""

    @pytest_asyncio.fixture
    async def wb_chat(self, db, test_user):
        from app.models import Agent

        agent = Agent(
            namespace="test", name="retriever", user_id=test_user.id,
            system_tools=["workbench"],
        )
        db.add(agent)
        await db.flush()
        c = Chat(user_id=test_user.id, agent_id=agent.id, title="retrieve chat")
        db.add(c)
        await db.flush()
        await db.refresh(c)
        return c

    async def _spill_and_store(self, db, chat, test_user, call_id, full):
        from app.models.chat import Message
        from app.services.tool_execution import truncate_tool_result

        full_json = json.dumps(full)
        path = await workbench_refs.spill_result(
            db, chat, str(test_user.id), "big_query", call_id, full_json
        )
        assert path
        clipped = workbench_refs.attach_spill_pointer(
            truncate_tool_result(full_json, 500), path
        )
        db.add(Message(chat_id=chat.id, role="tool", name="big_query",
                       tool_call_id=call_id, content=clipped))
        await db.flush()
        return path, clipped

    async def _retrieve(self, db, chat, test_user, call_id, monkeypatch):
        import contextlib

        from app.services import tool_execution
        from app.services.message_service import MessageService
        from app.services.tool_execution import execute_single_tool

        # The executor opens its own session; the fixture's rows live in a
        # rolled-back transaction it can't see, so hand it the same session.
        @contextlib.asynccontextmanager
        async def _same_session():
            yield db

        monkeypatch.setattr(tool_execution, "AsyncSessionLocal", _same_session)
        svc = MessageService(db)
        _, name, content = await execute_single_tool(
            {"id": "call_r", "type": "function",
             "function": {"name": "retrieve_tool_result",
                          "arguments": json.dumps({"tool_call_id": call_id})}},
            str(chat.id), str(test_user.id), "tok",
            [{"type": "function", "function": {"name": "retrieve_tool_result"}}],
            svc.function_converter, svc.query_converter, svc.skill_converter,
            svc.component_converter, svc.collection_converter,
            svc.create_chat_with_agent,
        )
        assert name == "retrieve_tool_result"
        out = json.loads(content)
        assert "error" not in out, out
        return out

    @pytest.mark.asyncio
    async def test_full_copy_is_served(self, db, wb_chat, test_user, monkeypatch):
        full = {"rows": list(range(2000))}
        path, clipped = await self._spill_and_store(db, wb_chat, test_user, "call_full", full)
        assert json.loads(clipped)["rows"] != full["rows"]  # inline copy really is clipped

        out = await self._retrieve(db, wb_chat, test_user, "call_full", monkeypatch)
        assert out["result"] == full
        assert out["source"] == "workbench"
        assert out["workbench_file"] == path

    @pytest.mark.asyncio
    async def test_oversized_copy_stays_a_pointer(self, db, wb_chat, test_user, monkeypatch):
        from app.services import tool_execution

        monkeypatch.setitem(
            tool_execution.TOOL_RESULT_SIZE_OVERRIDES, "retrieve_tool_result", 2000
        )
        full = {"rows": list(range(2000))}
        path, _ = await self._spill_and_store(db, wb_chat, test_user, "call_huge", full)

        out = await self._retrieve(db, wb_chat, test_user, "call_huge", monkeypatch)
        assert out["result"]["_full_result"]["workbench_file"] == path  # still clipped
        assert out["workbench_file"] == path
        assert "workbench_read" in out["note"]

    @pytest.mark.asyncio
    async def test_missing_spill_falls_back_to_stored_copy(self, db, wb_chat, test_user, monkeypatch):
        from app.models.chat import Message

        clipped = workbench_refs.attach_spill_pointer(
            json.dumps({"rows": [1], "_truncated": True}), "tool_results/gone.json"
        )
        db.add(Message(chat_id=wb_chat.id, role="tool", name="big_query",
                       tool_call_id="call_gone", content=clipped))
        await db.flush()

        out = await self._retrieve(db, wb_chat, test_user, "call_gone", monkeypatch)
        assert out["result"]["rows"] == [1]
        assert out["source"] == "messages"
