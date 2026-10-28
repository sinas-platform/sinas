"""Workbench: per-chat working tree — fence, tools, checkout/promote.

Covers the two contracts from the workbench ADR:
- the kind='workbench' fence: workbench rows are invisible to every
  collections-facing surface (by-name resolution, wildcard tool expansion,
  the collections API), even for wildcard/admin grants;
- workbench semantics: private-only files, chat-scoped access, checkout
  with provenance and visibility filtering, promote as checkout's reverse
  (source update with conflict detection, else create-in-target).
"""
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from sqlalchemy import select
from app.models.file import Collection, File, FileVersion
from app.models.user import RolePermission, User
from app.services.collection_tools import CollectionToolConverter
from app.services.workbench import (
    PROVENANCE_KEY,
    WorkbenchTools,
    get_or_create_workbench,
    get_workbench_tool_definitions,
)

from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _tmp_file_storage(tmp_path, monkeypatch):
    """Point file storage at a per-test temp dir (and drop the cached backend)."""
    import app.services.file_storage as fs

    monkeypatch.setenv("FILE_STORAGE_PATH", str(tmp_path / "files"))
    fs._storage = None
    yield
    fs._storage = None


@pytest_asyncio.fixture
async def chat(db: AsyncSession, test_user: User) -> Chat:
    c = Chat(user_id=test_user.id, title="workbench test chat")
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return c


async def _grant(db: AsyncSession, user: User, *perm_keys: str) -> None:
    """Attach permission keys to the user's first active role."""
    from sqlalchemy import select
    from app.models.user import UserRole

    role_id = (
        await db.execute(select(UserRole.role_id).where(UserRole.user_id == user.id).limit(1))
    ).scalar_one()
    for key in perm_keys:
        db.add(RolePermission(role_id=role_id, permission_key=key, permission_value=True))
    await db.flush()


async def _make_collection(db: AsyncSession, owner: User, name: str, **kwargs) -> Collection:
    coll = Collection(namespace="test", name=name, user_id=owner.id, **kwargs)
    db.add(coll)
    await db.flush()
    await db.refresh(coll)
    return coll


async def _agent_on(db: AsyncSession, chat: Chat):
    """Attach a fresh agent to the chat, as if the agent were running it."""
    from app.models.agent import Agent

    agent = Agent(namespace="ns", name=f"agent-{uuid.uuid4().hex[:6]}", system_prompt="x")
    db.add(agent)
    await db.flush()
    chat.agent_id = agent.id
    await db.flush()
    return agent


# ---------------------------------------------------------------------------
# The fence
# ---------------------------------------------------------------------------


class TestWorkbenchFence:
    @pytest.mark.asyncio
    async def test_get_by_name_never_resolves_workbenches(self, db, chat):
        wb = await get_or_create_workbench(db, chat)
        assert wb.kind == "workbench"
        found = await Collection.get_by_name(db, wb.namespace, wb.name)
        assert found is None

    @pytest.mark.asyncio
    async def test_wildcard_tool_expansion_skips_workbenches(self, db, chat, test_user, test_role):
        # A wildcard only reaches collections the user may download from:
        # grant every one, workbenches included, so only the fence keeps them out.
        from app.models.user import RolePermission

        db.add(RolePermission(
            role_id=test_role.id, permission_key="sinas.collections/*/*.download:own", permission_value=True
        ))
        await db.flush()
        await get_or_create_workbench(db, chat)
        await _make_collection(db, test_user, f"real-{uuid.uuid4().hex[:6]}")

        tools = await CollectionToolConverter().get_available_collections(
            db=db, user_id=str(test_user.id), enabled_collections=["*/*"]
        )
        refs = {t["function"]["_metadata"]["collection_ref"] for t in tools}
        assert refs  # the real collection produced tools
        assert not any(ref.startswith("_chat/") for ref in refs)

    @pytest.mark.asyncio
    async def test_collections_api_hides_workbenches_even_from_admin(
        self, db, chat, client, admin_user
    ):
        wb = await get_or_create_workbench(db, chat)

        resp = await client.get("/api/v1/collections", headers=auth_headers(admin_user))
        assert resp.status_code == 200
        names = {(c["namespace"], c["name"]) for c in resp.json()}
        assert (wb.namespace, wb.name) not in names

        resp = await client.get(
            f"/api/v1/collections/{wb.namespace}/{wb.name}", headers=auth_headers(admin_user)
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Core tool semantics
# ---------------------------------------------------------------------------


class TestWorkbenchTools:
    @pytest.mark.asyncio
    async def test_get_or_create_is_idempotent(self, db, chat):
        first = await get_or_create_workbench(db, chat)
        second = await get_or_create_workbench(db, chat)
        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_write_read_edit_roundtrip_forces_private(self, db, chat, test_user):
        tools = WorkbenchTools()
        uid = str(test_user.id)

        result = await tools.execute_tool(
            db, chat, uid, "workbench_write",
            {"filename": "src/app.py", "content": "print('hello')\n"},
        )
        assert result.get("created") is True and result["version"] == 1

        wb = await get_or_create_workbench(db, chat)
        from sqlalchemy import select
        f = (
            await db.execute(select(File).where(File.collection_id == wb.id))
        ).scalar_one()
        assert f.visibility == "private"
        assert f.user_id == chat.user_id

        result = await tools.execute_tool(
            db, chat, uid, "workbench_edit",
            {"filename": "src/app.py", "old_string": "hello", "new_string": "workbench"},
        )
        assert "error" not in result and result["version"] == 2

        result = await tools.execute_tool(db, chat, uid, "workbench_read", {"filename": "src/app.py"})
        assert "workbench" in str(result)

        result = await tools.execute_tool(db, chat, uid, "workbench_list", {})
        assert result.get("count", len(result.get("files", []))) >= 1

    @pytest.mark.asyncio
    async def test_versions_record_the_acting_agent(self, db, chat, test_user):
        """An agent writes as the user: uploaded_by stays the person, and the
        version says it was an agent (and which)."""
        from app.models.agent import Agent

        agent = Agent(namespace="ns", name="writer", system_prompt="x")
        db.add(agent)
        await db.flush()
        chat.agent_id = agent.id
        await db.flush()

        tools = WorkbenchTools()
        uid = str(test_user.id)
        await tools.execute_tool(db, chat, uid, "workbench_write", {"filename": "a.txt", "content": "one"})
        await tools.execute_tool(
            db, chat, uid, "workbench_edit", {"filename": "a.txt", "old_string": "one", "new_string": "two"}
        )

        versions = (
            await db.execute(select(FileVersion).order_by(FileVersion.version_number))
        ).scalars().all()
        assert [v.actor_type for v in versions] == ["agent", "agent"]
        assert {v.agent_id for v in versions} == {agent.id}
        assert {v.uploaded_by for v in versions} == {test_user.id}

    @pytest.mark.asyncio
    async def test_versions_without_an_agent_are_user_writes(self, db, chat, test_user):
        await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_write", {"filename": "a.txt", "content": "one"}
        )
        version = (await db.execute(select(FileVersion))).scalar_one()
        assert version.actor_type == "user" and version.agent_id is None

    @pytest.mark.asyncio
    async def test_promote_records_the_acting_agent(self, db, chat, test_user):
        """Promote is how an agent's file lands in a real collection: the
        target's version names the agent too."""
        await _grant(db, test_user, "sinas.collections/*/*.upload:own")
        target = await _make_collection(db, test_user, f"tgt-{uuid.uuid4().hex[:6]}")
        agent = await _agent_on(db, chat)
        tools = WorkbenchTools()
        uid = str(test_user.id)

        await tools.execute_tool(db, chat, uid, "workbench_write", {"filename": "r.md", "content": "# R\n"})
        result = await tools.execute_tool(
            db, chat, uid, "workbench_promote", {"filename": "r.md", "collection": f"test/{target.name}"}
        )
        assert "error" not in result, result

        version = (
            await db.execute(
                select(FileVersion).join(File).where(File.collection_id == target.id)
            )
        ).scalar_one()
        assert version.actor_type == "agent" and version.agent_id == agent.id
        assert version.uploaded_by == test_user.id

    @pytest.mark.asyncio
    async def test_collection_tool_write_records_the_chats_agent(
        self, db, chat, test_user, monkeypatch
    ):
        """execute_single_tool hands the chat's agent to the collection tools."""
        import contextlib
        import json

        from app.services import tool_execution
        from app.services.message_service import MessageService

        await _grant(db, test_user, "sinas.collections/*/*.upload:own")
        target = await _make_collection(db, test_user, f"tgt-{uuid.uuid4().hex[:6]}")
        agent = await _agent_on(db, chat)

        # The executor opens its own session; hand it the test's.
        @contextlib.asynccontextmanager
        async def _same_session():
            yield db

        monkeypatch.setattr(tool_execution, "AsyncSessionLocal", _same_session)
        svc = MessageService(db)
        tool = {
            "type": "function",
            "function": {
                "name": "write_file_target",
                "_metadata": {
                    "collection_ref": f"test/{target.name}",
                    "tool_type": "collection_write_file",
                },
            },
        }
        _, _, content = await tool_execution.execute_single_tool(
            {"id": "call_w", "type": "function",
             "function": {"name": "write_file_target",
                          "arguments": json.dumps({"filename": "n.txt", "content": "hi"})}},
            str(chat.id), str(test_user.id), "tok", [tool],
            svc.function_converter, svc.query_converter, svc.skill_converter,
            svc.component_converter, svc.collection_converter,
            svc.create_chat_with_agent,
        )
        assert "error" not in json.loads(content), content

        version = (
            await db.execute(
                select(FileVersion).join(File).where(File.collection_id == target.id)
            )
        ).scalar_one()
        assert version.actor_type == "agent" and version.agent_id == agent.id

    @pytest.mark.asyncio
    async def test_versions_api_reports_the_actor(self, db, chat, client, test_user):
        await _grant(
            db, test_user,
            "sinas.collections/*/*.upload:own", "sinas.collections/*/*.list:own",
        )
        target = await _make_collection(db, test_user, f"tgt-{uuid.uuid4().hex[:6]}")
        agent = await _agent_on(db, chat)
        await CollectionToolConverter().execute_tool(
            db, "write_file_target", {"filename": "n.txt", "content": "hi"}, str(test_user.id),
            {"collection_ref": f"test/{target.name}", "tool_type": "collection_write_file"},
            agent_id=str(agent.id),
        )

        resp = await client.get(f"/files/test/{target.name}", headers=auth_headers(test_user))
        assert resp.status_code == 200, resp.text
        (version,) = resp.json()[0]["versions"]
        assert version["actor_type"] == "agent"
        assert version["agent_id"] == str(agent.id)

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, db, chat, test_user):
        tools = WorkbenchTools()
        for bad in ("../escape.txt", "/abs.txt", "a//b.txt", "a/../b.txt"):
            result = await tools.execute_tool(
                db, chat, str(test_user.id), "workbench_write", {"filename": bad, "content": "x"}
            )
            assert "error" in result, bad

    @pytest.mark.asyncio
    async def test_other_user_cannot_use_someone_elses_chat_workbench(
        self, db, chat, admin_user
    ):
        result = await WorkbenchTools().execute_tool(
            db, chat, str(admin_user.id), "workbench_write", {"filename": "x.txt", "content": "x"}
        )
        assert "error" in result

    def test_tool_definitions_shape(self):
        defs = get_workbench_tool_definitions()
        names = {d["function"]["name"] for d in defs}
        assert names == {
            "workbench_list", "workbench_read", "workbench_write", "workbench_edit",
            "workbench_delete", "workbench_checkout", "workbench_promote",
        }
        for d in defs:
            assert d["function"]["_metadata"]["tool_type"].startswith("workbench_")


# ---------------------------------------------------------------------------
# Checkout / promote
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def source_collection(db, test_user, admin_user):
    """A collection with a shared file, an own-private file, and another
    user's private file (which checkout must never copy)."""
    coll = await _make_collection(db, test_user, f"src-{uuid.uuid4().hex[:6]}")
    from app.services.workbench import _write_bytes
    from app.services.file_storage import get_storage

    storage = get_storage()
    await _write_bytes(
        db, storage, coll, filename="shared.txt", content=b"shared data",
        content_type="text/plain", user_id=str(test_user.id), visibility="shared",
    )
    await _write_bytes(
        db, storage, coll, filename="mine.txt", content=b"my private data",
        content_type="text/plain", user_id=str(test_user.id), visibility="private",
    )
    await _write_bytes(
        db, storage, coll, filename="theirs.txt", content=b"someone else's secret",
        content_type="text/plain", user_id=str(admin_user.id), visibility="private",
    )
    return coll


class TestCheckoutPromote:
    @pytest.mark.asyncio
    async def test_checkout_requires_download_permission(self, db, chat, test_user, source_collection):
        result = await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_checkout",
            {"collection": f"test/{source_collection.name}"},
        )
        assert result.get("error") == "Permission denied"

    @pytest.mark.asyncio
    async def test_bulk_checkout_respects_visibility_and_records_provenance(
        self, db, chat, test_user, source_collection
    ):
        await _grant(db, test_user, "sinas.collections/*/*.download:own")

        result = await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_checkout",
            {"collection": f"test/{source_collection.name}"},
        )
        names = {e["filename"] for e in result["checked_out"]}
        assert names == {"shared.txt", "mine.txt"}  # never theirs.txt

        wb = await get_or_create_workbench(db, chat)
        from sqlalchemy import select
        rows = (await db.execute(select(File).where(File.collection_id == wb.id))).scalars().all()
        assert {f.name for f in rows} == names
        for f in rows:
            assert f.visibility == "private"
            prov = f.file_metadata[PROVENANCE_KEY]
            assert prov["collection"] == f"test/{source_collection.name}"
            assert prov["version"] == 1

    @pytest.mark.asyncio
    async def test_checkout_glob_pattern(self, db, chat, test_user, source_collection):
        await _grant(db, test_user, "sinas.collections/*/*.download:own")
        result = await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_checkout",
            {"collection": f"test/{source_collection.name}", "pattern": "shared.*"},
        )
        assert [e["filename"] for e in result["checked_out"]] == ["shared.txt"]

    @pytest.mark.asyncio
    async def test_promote_new_file_into_target(self, db, chat, test_user):
        await _grant(db, test_user, "sinas.collections/*/*.upload:own")
        target = await _make_collection(db, test_user, f"tgt-{uuid.uuid4().hex[:6]}")
        tools = WorkbenchTools()
        uid = str(test_user.id)

        await tools.execute_tool(
            db, chat, uid, "workbench_write", {"filename": "report.md", "content": "# Findings\n"}
        )
        result = await tools.execute_tool(
            db, chat, uid, "workbench_promote",
            {"filename": "report.md", "collection": f"test/{target.name}"},
        )
        assert result.get("updated_source") is False and result["version"] == 1

        from sqlalchemy import select
        promoted = (
            await db.execute(select(File).where(File.collection_id == target.id))
        ).scalar_one()
        assert promoted.name == "report.md"
        assert promoted.visibility == "private"

    @pytest.mark.asyncio
    async def test_promote_updates_source_and_detects_conflict(
        self, db, chat, test_user, source_collection
    ):
        await _grant(
            db, test_user,
            "sinas.collections/*/*.download:own",
            "sinas.collections/*/*.upload:own",
        )
        tools = WorkbenchTools()
        uid = str(test_user.id)
        ref = f"test/{source_collection.name}"

        await tools.execute_tool(
            db, chat, uid, "workbench_checkout", {"collection": ref, "path": "shared.txt"}
        )
        await tools.execute_tool(
            db, chat, uid, "workbench_edit",
            {"filename": "shared.txt", "old_string": "shared data", "new_string": "edited data"},
        )

        result = await tools.execute_tool(
            db, chat, uid, "workbench_promote", {"filename": "shared.txt", "collection": ref}
        )
        assert result.get("updated_source") is True and result["version"] == 2

        # A second promote must not conflict with its own update…
        result = await tools.execute_tool(
            db, chat, uid, "workbench_promote", {"filename": "shared.txt", "collection": ref}
        )
        assert result.get("updated_source") is True and result["version"] == 3

        # …but someone else moving the source on must surface a conflict.
        from sqlalchemy import select
        source_file = (
            await db.execute(
                select(File).where(
                    File.collection_id == source_collection.id, File.name == "shared.txt"
                )
            )
        ).scalar_one()
        source_file.current_version += 1
        await db.flush()

        result = await tools.execute_tool(
            db, chat, uid, "workbench_promote", {"filename": "shared.txt", "collection": ref}
        )
        assert result.get("error") == "conflict"


# ---------------------------------------------------------------------------
# Sandbox sync (copy-in manifest, wrapper diff, copy-out write-back)
# ---------------------------------------------------------------------------


class TestSandboxSync:
    @pytest.mark.asyncio
    async def test_manifest_and_write_back_roundtrip(self, db, chat, test_user):
        from app.services.workbench import apply_sync_changes, build_sync_manifest

        tools = WorkbenchTools()
        uid = str(test_user.id)
        await tools.execute_tool(
            db, chat, uid, "workbench_write", {"filename": "data/input.txt", "content": "42\n"}
        )

        manifest = await build_sync_manifest(db, chat)
        assert [f["path"] for f in manifest["files"]] == ["data/input.txt"]
        assert manifest["skipped"] == []

        import base64
        result = await apply_sync_changes(
            db, chat, uid,
            [
                {"path": "data/output.txt", "content_b64": base64.b64encode(b"84\n").decode()},
                {"path": "../evil.txt", "content_b64": base64.b64encode(b"x").decode()},
            ],
        )
        assert result["synced"] == ["data/output.txt"]
        assert result["rejected"][0]["path"] == "../evil.txt"

        manifest = await build_sync_manifest(db, chat)
        assert {f["path"] for f in manifest["files"]} == {"data/input.txt", "data/output.txt"}

        # A file born in an execution carries that provenance (like uploads
        # and spilled tool results do); a file the code merely modified keeps
        # the metadata it had.
        wb = await get_or_create_workbench(db, chat)
        from sqlalchemy import select
        rows = {
            f.name: f
            for f in (await db.execute(select(File).where(File.collection_id == wb.id))).scalars()
        }
        assert rows["data/output.txt"].file_metadata == {"origin": "execution"}
        assert rows["data/input.txt"].file_metadata == {}
        rows["data/input.txt"].file_metadata = {PROVENANCE_KEY: {"collection": "test/src", "file_id": "x", "version": 1}}
        await db.flush()
        result = await apply_sync_changes(
            db, chat, uid,
            [{"path": "data/input.txt", "content_b64": base64.b64encode(b"43\n").decode()}],
        )
        assert result["synced"] == ["data/input.txt"]
        await db.refresh(rows["data/input.txt"])
        assert rows["data/input.txt"].current_version == 2
        assert rows["data/input.txt"].file_metadata[PROVENANCE_KEY]["collection"] == "test/src"

    def test_wrapper_runs_user_code_in_its_own_namespace(self):
        """The trailing expression and nested scopes must see the names the
        code defined. Running user code in the handler's own frame relied on
        locals() being one shared dict, which Python 3.13 (PEP 667) ended:
        every program ending in an expression failed with a NameError."""
        from app.services.code_execution import _build_wrapper

        cases = {
            "x = 1\nx + 1\n": 2,
            "import json\nd = {'a': 1}\njson.dumps(d)\n": '{"a": 1}',
            "rows = [1.5, 2]\ntotal = sum(float(r) for r in rows)\ntotal\n": 3.5,
            "x = 1\ndef f():\n    return x\nf()\n": 1,
        }
        for code, expected in cases.items():
            for workbench in (False, True):
                ns: dict = {}
                exec(_build_wrapper(code, workbench=workbench), ns)
                output = ns["handler"]({"workbench_files": [], "workbench_limits": {}}, {})
                assert output["status"] == "completed", (code, output.get("error"))
                assert output["result"] == expected, code

    @pytest.mark.asyncio
    async def test_manifest_marks_oversized_files_lazy(self, db, chat, test_user, monkeypatch):
        from app.core.config import settings as app_settings
        from app.services.workbench import build_sync_manifest, fetch_file_bytes

        monkeypatch.setattr(app_settings, "workbench_sync_max_file_bytes", 10)
        tools = WorkbenchTools()
        uid = str(test_user.id)
        await tools.execute_tool(
            db, chat, uid, "workbench_write", {"filename": "big.txt", "content": "x" * 100}
        )
        await tools.execute_tool(
            db, chat, uid, "workbench_write", {"filename": "small.txt", "content": "ok"}
        )

        manifest = await build_sync_manifest(db, chat)
        assert [f["path"] for f in manifest["files"]] == ["small.txt"]
        assert [e["path"] for e in manifest["lazy"]] == ["big.txt"]
        assert manifest["skipped"] == []

        # The lazy file is servable over the fetch channel…
        import base64
        result = await fetch_file_bytes(db, chat, "big.txt", max_bytes=1024)
        assert base64.b64decode(result["content_b64"]) == b"x" * 100
        # …within the fetch cap, and traversal paths are rejected.
        result = await fetch_file_bytes(db, chat, "big.txt", max_bytes=10)
        assert "error" in result
        result = await fetch_file_bytes(db, chat, "../escape", max_bytes=1024)
        assert "error" in result

    def test_wrapper_materializes_and_reports_changes(self):
        """Run the real sandbox wrapper in-process: files land in cwd, and
        created/changed files come back as workbench_changes by hash diff."""
        import base64
        import hashlib

        from app.services.code_execution import _build_wrapper

        user_code = (
            "content = open('data/input.txt').read()\n"
            "with open('result.txt', 'w') as f:\n"
            "    f.write(content.upper())\n"
            "with open('untouched.txt', 'w') as f:\n"
            "    f.write('same')\n"
        )
        wrapper = _build_wrapper(user_code, workbench=True)
        ns: dict = {}
        exec(wrapper, ns)

        untouched = b"same"
        input_data = {
            "workbench_files": [
                {
                    "path": "data/input.txt",
                    "content_b64": base64.b64encode(b"hello").decode(),
                    "sha256": hashlib.sha256(b"hello").hexdigest(),
                },
                {
                    "path": "untouched.txt",
                    "content_b64": base64.b64encode(untouched).decode(),
                    "sha256": hashlib.sha256(untouched).hexdigest(),
                },
            ],
            "workbench_limits": {"max_file_bytes": 1024, "max_total_bytes": 4096},
        }
        output = ns["handler"](input_data, {})
        assert output["status"] == "completed", output

        changes = {c["path"]: base64.b64decode(c["content_b64"]) for c in output["workbench_changes"]}
        # result.txt is new; untouched.txt was rewritten with identical bytes
        # (hash-identical → not a change); input.txt was only read.
        assert changes == {"result.txt": b"HELLO"}

    def test_wrapper_reports_changes_even_when_user_code_fails(self):
        import base64

        from app.services.code_execution import _build_wrapper

        user_code = (
            "with open('partial.txt', 'w') as f:\n"
            "    f.write('progress')\n"
            "raise RuntimeError('boom')\n"
        )
        wrapper = _build_wrapper(user_code, workbench=True)
        ns: dict = {}
        exec(wrapper, ns)
        output = ns["handler"]({"workbench_files": [], "workbench_limits": {}}, {})
        assert output["status"] == "failed"
        changes = {c["path"] for c in output["workbench_changes"]}
        assert changes == {"partial.txt"}

    def test_wrapper_cleans_up_temp_tree(self, tmp_path):
        import glob
        import tempfile

        from app.services.code_execution import _build_wrapper

        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "workbench_*")))
        wrapper = _build_wrapper("x = 1\n", workbench=True)
        ns: dict = {}
        exec(wrapper, ns)
        ns["handler"]({"workbench_files": [], "workbench_limits": {}}, {})
        after = set(glob.glob(os.path.join(tempfile.gettempdir(), "workbench_*")))
        assert after == before


# ---------------------------------------------------------------------------
# Runtime workbench API (chat uploads + browsing)
# ---------------------------------------------------------------------------


class TestWorkbenchAPI:
    @pytest.mark.asyncio
    async def test_upload_list_read_delete_roundtrip(self, db, chat, client, test_user):
        import base64

        headers = auth_headers(test_user)
        upload = {
            "name": "data/report.csv",
            "content_base64": base64.b64encode(b"a,b\n1,2\n").decode(),
        }
        resp = await client.post(f"/chats/{chat.id}/workbench/files", json=upload, headers=headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "data/report.csv"
        assert body["content_type"] == "text/csv"
        assert body["file_metadata"]["origin"] == "upload"

        # The stored file is private and owned by the chat user
        wb = await get_or_create_workbench(db, chat)
        from sqlalchemy import select
        f = (await db.execute(select(File).where(File.collection_id == wb.id))).scalar_one()
        assert f.visibility == "private"

        resp = await client.get(f"/chats/{chat.id}/workbench/files", headers=headers)
        assert resp.status_code == 200
        assert [e["name"] for e in resp.json()] == ["data/report.csv"]

        resp = await client.get(
            f"/chats/{chat.id}/workbench/files/data/report.csv", headers=headers
        )
        assert resp.status_code == 200
        assert base64.b64decode(resp.json()["content_base64"]) == b"a,b\n1,2\n"

        resp = await client.delete(
            f"/chats/{chat.id}/workbench/files/data/report.csv", headers=headers
        )
        assert resp.status_code == 204
        resp = await client.get(
            f"/chats/{chat.id}/workbench/files/data/report.csv", headers=headers
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_other_users_chat_is_404(self, chat, client, admin_user):
        resp = await client.get(f"/chats/{chat.id}/workbench/files", headers=auth_headers(admin_user))
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_traversal_upload_rejected(self, chat, client, test_user):
        import base64

        resp = await client.post(
            f"/chats/{chat.id}/workbench/files",
            json={"name": "../../etc/passwd", "content_base64": base64.b64encode(b"x").decode()},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_agent_tools_see_uploaded_file(self, db, chat, client, test_user):
        import base64

        resp = await client.post(
            f"/chats/{chat.id}/workbench/files",
            json={"name": "notes.md", "content_base64": base64.b64encode(b"# hi").decode()},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 201
        result = await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_read", {"filename": "notes.md"}
        )
        assert "# hi" in str(result)


# ---------------------------------------------------------------------------
# Lazy fetch (stub materialization, open() hook, pause-channel roundtrip)
# ---------------------------------------------------------------------------


def _serve_one_fetch(eid: str, responses: dict):
    """Background thread playing the backend: waits for the wrapper's fetch
    request file and answers it, like the executor wait-loop does."""
    import base64
    import hashlib
    import json as _json
    import os as _os
    import time as _time

    req = f"/tmp/wb_fetch_req_{eid}.json"
    resp = f"/tmp/wb_fetch_resp_{eid}.json"
    deadline = _time.time() + 10
    while _time.time() < deadline:
        if _os.path.exists(req):
            with open(req) as fh:
                request = _json.load(fh)
            _os.remove(req)
            path = request["path"]
            if path in responses:
                content = responses[path]
                payload = {
                    "path": path,
                    "content_b64": base64.b64encode(content).decode(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            else:
                payload = {"path": path, "error": "not found"}
            with open(resp + ".tmp", "w") as fh:
                _json.dump(payload, fh)
            _os.replace(resp + ".tmp", resp)
            return
        _time.sleep(0.02)


class TestLazyFetchWrapper:
    def _run(self, user_code: str, lazy: list, responses: dict, files: list = ()):
        import threading
        import uuid as _uuid

        from app.services.code_execution import _build_wrapper

        eid = _uuid.uuid4().hex
        wrapper = _build_wrapper(user_code, workbench=True)
        ns: dict = {}
        exec(wrapper, ns)
        server = threading.Thread(target=_serve_one_fetch, args=(eid, responses), daemon=True)
        server.start()
        output = ns["handler"](
            {
                "workbench_files": list(files),
                "workbench_lazy": [{"path": p} for p in lazy],
                "workbench_limits": {"max_file_bytes": 4096, "max_total_bytes": 8192},
            },
            {"execution_id": eid},
        )
        server.join(timeout=5)
        return output

    def test_open_of_lazy_file_fetches_real_bytes(self):
        output = self._run(
            "data = open('big.csv').read()\n"
            "with open('summary.txt', 'w') as f:\n"
            "    f.write(str(len(data)))\n",
            lazy=["big.csv"],
            responses={"big.csv": b"a,b\n" * 50},
        )
        assert output["status"] == "completed", output
        import base64
        changes = {c["path"]: base64.b64decode(c["content_b64"]) for c in output["workbench_changes"]}
        # The fetched-but-unmodified lazy file is NOT a change; only summary.txt is.
        assert changes == {"summary.txt": b"200"}

    def test_unfetched_stub_never_reported_as_change(self):
        output = self._run(
            "x = 1\n",
            lazy=["huge.bin"],
            responses={},
        )
        assert output["status"] == "completed"
        assert output["workbench_changes"] == []

    def test_modified_lazy_file_is_a_change(self):
        output = self._run(
            "content = open('doc.txt').read()\n"
            "with open('doc.txt', 'w') as f:\n"
            "    f.write(content + ' edited')\n",
            lazy=["doc.txt"],
            responses={"doc.txt": b"original"},
        )
        assert output["status"] == "completed", output
        import base64
        changes = {c["path"]: base64.b64decode(c["content_b64"]) for c in output["workbench_changes"]}
        assert changes == {"doc.txt": b"original edited"}

    def test_fetch_error_surfaces_to_user_code(self):
        output = self._run(
            "open('missing.bin').read()\n",
            lazy=["missing.bin"],
            responses={},
        )
        assert output["status"] == "failed"
        assert "missing.bin" in output.get("error", "")

    def test_explicit_workbench_fetch_helper(self):
        output = self._run(
            "workbench_fetch('blob.bin')\n"
            "import os\n"
            "size = os.path.getsize('blob.bin')\n"
            "with open('size.txt', 'w') as f:\n"
            "    f.write(str(size))\n",
            lazy=["blob.bin"],
            responses={"blob.bin": b"\x00" * 123},
        )
        assert output["status"] == "completed", output
        import base64
        changes = {c["path"]: base64.b64decode(c["content_b64"]) for c in output["workbench_changes"]}
        assert changes["size.txt"] == b"123"


# ---------------------------------------------------------------------------
# docker_pool delta sync + affinity
# ---------------------------------------------------------------------------


class TestDeltaAndAffinity:
    def test_wrapper_writes_and_reuses_blob_cache(self):
        """First run ships bytes and populates /tmp/wb_cache; second run
        ships a cached reference and the wrapper restores it from the cache."""
        import base64
        import hashlib
        import os as _os
        import uuid as _uuid

        from app.services.code_execution import _build_wrapper

        content = f"unique-{_uuid.uuid4().hex}".encode()
        sha = hashlib.sha256(content).hexdigest()

        wrapper = _build_wrapper("data = open('input.txt').read()\n", workbench=True)
        ns: dict = {}
        exec(wrapper, ns)
        out = ns["handler"](
            {
                "workbench_files": [
                    {"path": "input.txt", "content_b64": base64.b64encode(content).decode(), "sha256": sha}
                ],
                "workbench_limits": {},
            },
            {"execution_id": _uuid.uuid4().hex},
        )
        assert out["status"] == "completed", out
        assert _os.path.exists(f"/tmp/wb_cache/_none/{sha}")  # no chat_id in context → "_none" namespace

        # Second execution: cached reference only, no bytes.
        wrapper2 = _build_wrapper(
            "content = open('input.txt').read()\n"
            "with open('echo.txt', 'w') as f:\n"
            "    f.write(content)\n",
            workbench=True,
        )
        ns2: dict = {}
        exec(wrapper2, ns2)
        out = ns2["handler"](
            {
                "workbench_files": [{"path": "input.txt", "sha256": sha, "cached": True}],
                "workbench_limits": {},
            },
            {"execution_id": _uuid.uuid4().hex},
        )
        assert out["status"] == "completed", out
        changes = {c["path"]: base64.b64decode(c["content_b64"]) for c in out["workbench_changes"]}
        assert changes == {"echo.txt": content}

    def test_cached_reference_with_cold_cache_degrades_to_lazy_fetch(self):
        import base64
        import hashlib
        import threading
        import uuid as _uuid

        from app.services.code_execution import _build_wrapper

        content = b"cold-cache-content"
        sha = hashlib.sha256(b"never-cached-" + _uuid.uuid4().hex.encode()).hexdigest()
        eid = _uuid.uuid4().hex

        wrapper = _build_wrapper(
            "data = open('cold.txt').read()\n"
            "with open('copy.txt', 'w') as f:\n"
            "    f.write(data)\n",
            workbench=True,
        )
        ns: dict = {}
        exec(wrapper, ns)
        server = threading.Thread(
            target=_serve_one_fetch, args=(eid, {"cold.txt": content}), daemon=True
        )
        server.start()
        out = ns["handler"](
            {
                "workbench_files": [{"path": "cold.txt", "sha256": sha, "cached": True}],
                "workbench_limits": {},
            },
            {"execution_id": eid},
        )
        server.join(timeout=5)
        assert out["status"] == "completed", out
        changes = {c["path"]: base64.b64decode(c["content_b64"]) for c in out["workbench_changes"]}
        assert changes == {"copy.txt": content}

    @pytest.mark.asyncio
    async def test_pool_acquire_prefers_chat_affine_container(self):
        from app.services.container_pool import ContainerPool, PooledContainer

        pool = ContainerPool()

        class _FakeContainers:
            def get(self, name):
                return object()

        class _FakeClient:
            containers = _FakeContainers()

        pool._client = _FakeClient()
        a = PooledContainer(name="sinas-sandbox-1", container_id="a")
        b = PooledContainer(name="sinas-sandbox-2", container_id="b", last_chat_id="chat-42")
        c = PooledContainer(name="sinas-sandbox-3", container_id="c")
        pool.idle.extend([a, b, c])

        got = await pool.acquire(timeout=1, chat_id="chat-42")
        assert got is b

        # Without affinity, FIFO order holds.
        got = await pool.acquire(timeout=1)
        assert got is a

        # Affinity stamps the chat on whatever container was handed out.
        got = await pool.acquire(timeout=1, chat_id="chat-7")
        assert got is c and c.last_chat_id == "chat-7"


class TestWriteGuards:
    """Byte writes honour what uploads honour: the filter's approved bytes
    and metadata, the target's metadata schema, its storage quota, its
    post-upload hook, and a tree where a path is a file or a directory,
    never both."""

    @pytest.mark.asyncio
    async def test_file_and_directory_paths_cannot_coexist(self, db, chat, test_user):
        tools = WorkbenchTools()
        ok = await tools.execute_tool(db, chat, str(test_user.id), "workbench_write", {"filename": "a/b.txt", "content": "x"})
        assert "error" not in ok, ok
        clash = await tools.execute_tool(db, chat, str(test_user.id), "workbench_write", {"filename": "a", "content": "x"})
        assert "directory" in clash["error"]
        clash = await tools.execute_tool(db, chat, str(test_user.id), "workbench_write", {"filename": "a/b.txt/c.txt", "content": "x"})
        assert "is a file" in clash["error"]
        # same guard on the byte path (uploads, checkout)
        wb = await get_or_create_workbench(db, chat)
        from app.services.file_storage import get_storage
        from app.services.workbench import _write_bytes
        res = await _write_bytes(db, get_storage(), wb, filename="a", content=b"x", content_type="text/plain", user_id=str(test_user.id), visibility="private")
        assert "directory" in res["error"]

    def test_hidden_directories_are_rejected_but_hidden_files_allowed(self):
        from app.services.workbench import _validate_path
        assert _validate_path(".config/settings.json")
        assert _validate_path("src/.cache/x")
        assert _validate_path(".gitignore") is None
        assert _validate_path("src/.env") is None

    @pytest.mark.asyncio
    async def test_filter_result_is_what_gets_written(self, db, chat, test_user, monkeypatch):
        import base64
        from app.services import workbench as wb_mod
        from app.services.queue_service import queue_service

        async def fake_filter(**kwargs):
            assert kwargs["input_data"]["filename"] == "renamed.txt"  # the destination name
            assert kwargs["input_data"]["user_metadata"] == {"topic": "q"}
            return {"approved": True, "modified_content": base64.b64encode(b"CLEANED").decode(), "metadata": {"scanned": True}}

        monkeypatch.setattr(queue_service, "enqueue_and_wait", fake_filter)
        from app.models.function import Function
        monkeypatch.setattr(Function, "get_by_name", classmethod(lambda cls, db, ns, name: _coro(object())))
        await _grant(db, test_user, "sinas.collections/*/*.upload:own")

        outcome = await wb_mod.run_content_filter(
            db, "f/filter", namespace="ns", collection_name="c", filename="renamed.txt",
            content=b"dirty", content_type="text/plain", user_id=str(test_user.id), user_metadata={"topic": "q"},
        )
        assert outcome == {"content": b"CLEANED", "metadata": {"scanned": True}}

        # promote uses it: filtered bytes + merged metadata land in the target
        target = Collection(namespace="ns", name="c", user_id=test_user.id, content_filter_function="f/filter")
        db.add(target)
        await db.flush()
        await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_write", {"filename": "in.txt", "content": "dirty"})
        out = await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_promote",
            {"filename": "in.txt", "collection": "ns/c", "target_filename": "renamed.txt", "metadata": {"topic": "q"}},
        )
        assert "error" not in out, out
        f = (await db.execute(select(File).where(File.collection_id == target.id))).scalar_one()
        assert f.name == "renamed.txt" and f.file_metadata == {"topic": "q", "scanned": True}
        from app.services.file_storage import get_storage
        v = (await db.execute(select(FileVersion).where(FileVersion.file_id == f.id))).scalar_one()
        assert await get_storage().read(v.storage_path) == b"CLEANED"

    @pytest.mark.asyncio
    async def test_promote_validates_target_metadata_schema(self, db, chat, test_user):
        await _grant(db, test_user, "sinas.collections/*/*.upload:own")
        target = Collection(
            namespace="ns", name="strict", user_id=test_user.id,
            metadata_schema={"type": "object", "required": ["owner"], "properties": {"owner": {"type": "string"}}},
        )
        db.add(target)
        await db.flush()
        await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_write", {"filename": "r.md", "content": "x"})
        out = await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_promote", {"filename": "r.md", "collection": "ns/strict"})
        assert out["error"] == "Invalid metadata" and "owner" in out["message"]
        out = await WorkbenchTools().execute_tool(
            db, chat, str(test_user.id), "workbench_promote", {"filename": "r.md", "collection": "ns/strict", "metadata": {"owner": "me"}}
        )
        assert "error" not in out, out

    @pytest.mark.asyncio
    async def test_promote_runs_the_post_upload_function(self, db, chat, test_user, monkeypatch):
        from app.models.function import Function
        from app.services.queue_service import queue_service

        queued = []

        async def fake_enqueue(**kwargs):
            queued.append(kwargs)
            return "job"

        monkeypatch.setattr(queue_service, "enqueue_function", fake_enqueue)
        monkeypatch.setattr(Function, "get_by_name", classmethod(lambda cls, db, ns, name: _coro(object())))
        await _grant(db, test_user, "sinas.collections/*/*.upload:own")
        target = Collection(namespace="ns", name="indexed", user_id=test_user.id, post_upload_function="f/index")
        db.add(target)
        await db.flush()
        await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_write", {"filename": "doc.md", "content": "x"})
        out = await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_promote", {"filename": "doc.md", "collection": "ns/indexed"})
        assert "error" not in out, out
        (call,) = queued
        assert call["function_name"] == "index" and call["trigger_id"] == "post_upload:ns/indexed"
        assert call["input_data"]["filename"] == "doc.md" and call["input_data"]["version"] == 1
        assert call["input_data"]["file_id"] and call["input_data"]["file_path"]

    @pytest.mark.asyncio
    async def test_storage_quota_applies_to_promote(self, db, chat, test_user):
        await _grant(db, test_user, "sinas.collections/*/*.upload:own")
        target = Collection(namespace="ns", name="tiny", user_id=test_user.id, max_total_size_gb=1)
        db.add(target)
        await db.flush()
        # Pretend the collection already holds ~1GB (a version row; no bytes needed)
        existing = File(collection_id=target.id, name="big.bin", user_id=test_user.id, content_type="application/octet-stream", current_version=1, visibility="private")
        db.add(existing)
        await db.flush()
        db.add(FileVersion(file_id=existing.id, version_number=1, storage_path="x", size_bytes=1024 ** 3 - 10, hash_sha256="h", uploaded_by=test_user.id))
        await db.flush()
        await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_write", {"filename": "more.txt", "content": "x" * 100})
        out = await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_promote", {"filename": "more.txt", "collection": "ns/tiny"})
        assert "quota" in out["error"]

    @pytest.mark.asyncio
    async def test_checkout_skips_names_that_would_escape_or_collide(self, db, chat, test_user):
        await _grant(db, test_user, "sinas.collections/*/*.download:own")
        src = Collection(namespace="ns", name="src", user_id=test_user.id)
        db.add(src)
        await db.flush()
        for name in ("../outside.txt", "fine.txt"):
            f = File(collection_id=src.id, name=name, user_id=test_user.id, content_type="text/plain", current_version=1, visibility="private")
            db.add(f)
            await db.flush()
            from app.services.file_storage import get_storage
            path = f"ns/src/{f.id}/v1"
            await get_storage().save(path, b"x")
            db.add(FileVersion(file_id=f.id, version_number=1, storage_path=path, size_bytes=1, hash_sha256="h", uploaded_by=test_user.id))
        await db.flush()
        out = await WorkbenchTools().execute_tool(db, chat, str(test_user.id), "workbench_checkout", {"collection": "ns/src", "pattern": "*"})
        assert [c["filename"] for c in out["checked_out"]] == ["fine.txt"]
        assert any("outside" in e for e in out["errors"])

    @pytest.mark.asyncio
    async def test_runtime_discovery_hides_workbenches(self, db, chat, client, test_user):
        await get_or_create_workbench(db, chat)
        await db.flush()
        resp = await client.get("/collections", headers=auth_headers(test_user))
        assert resp.status_code == 200
        assert all(c["namespace"] != "_chat" for c in resp.json())


async def _coro(value):
    return value


class TestCopyOutConflicts:
    @pytest.mark.asyncio
    async def test_file_changed_during_the_run_is_not_overwritten(self, db, chat, test_user):
        import base64
        from app.services.workbench import apply_sync_changes, build_sync_manifest

        uid = str(test_user.id)
        tools = WorkbenchTools()
        await tools.execute_tool(db, chat, uid, "workbench_write", {"filename": "notes.md", "content": "v1"})
        manifest = await build_sync_manifest(db, chat)
        base = {e["path"]: e["sha256"] for e in manifest["files"] + manifest["lazy"]}
        # The agent edits the file while the run is in progress.
        await tools.execute_tool(db, chat, uid, "workbench_write", {"filename": "notes.md", "content": "v2 by agent"})

        out = await apply_sync_changes(db, chat, uid, [
            {"path": "notes.md", "content_b64": base64.b64encode(b"v2 by the run").decode()},
            {"path": "new.txt", "content_b64": base64.b64encode(b"fresh").decode()},
        ], base_hashes=base)
        assert out["synced"] == ["new.txt"]
        (conflict,) = out["conflicts"]
        assert conflict["path"] == "notes.md" and conflict["saved_as"] == "notes.md.run-conflict"
        kept = await tools.execute_tool(db, chat, uid, "workbench_read", {"filename": "notes.md"})
        assert "v2 by agent" in kept["content"] and "by the run" not in kept["content"]
        aside = await tools.execute_tool(db, chat, uid, "workbench_read", {"filename": "notes.md.run-conflict"})
        assert "v2 by the run" in aside["content"]

    @pytest.mark.asyncio
    async def test_unchanged_file_is_written_back(self, db, chat, test_user):
        import base64
        from app.services.workbench import apply_sync_changes, build_sync_manifest

        uid = str(test_user.id)
        await WorkbenchTools().execute_tool(db, chat, uid, "workbench_write", {"filename": "a.txt", "content": "v1"})
        manifest = await build_sync_manifest(db, chat)
        base = {e["path"]: e["sha256"] for e in manifest["files"]}
        out = await apply_sync_changes(db, chat, uid, [
            {"path": "a.txt", "content_b64": base64.b64encode(b"v2").decode()},
        ], base_hashes=base)
        assert out["synced"] == ["a.txt"] and out["conflicts"] == []


class TestChatResponseFlag:
    @pytest.mark.asyncio
    async def test_chat_detail_reports_workbench_enabled(self, db, client, test_user):
        from app.models import Agent

        for tools, expected in (([], False), (["workbench"], True)):
            agent = Agent(namespace="test", name=f"flag-{uuid.uuid4().hex[:6]}", user_id=test_user.id, system_tools=tools)
            db.add(agent)
            await db.flush()
            c = Chat(user_id=test_user.id, agent_id=agent.id, agent_namespace=agent.namespace, agent_name=agent.name, title="t")
            db.add(c)
            await db.flush()
            resp = await client.get(f"/chats/{c.id}", headers=auth_headers(test_user))
            assert resp.status_code == 200, resp.text
            assert resp.json()["workbench_enabled"] is expected



class TestRoundFour:
    @pytest.mark.asyncio
    async def test_storage_failure_leaves_no_dangling_version(self, db, chat, test_user, monkeypatch):
        from app.services import file_storage
        from app.services.workbench import _write_bytes

        wb = await get_or_create_workbench(db, chat)
        storage = file_storage.get_storage()
        uid = str(test_user.id)
        ok = await _write_bytes(db, storage, wb, filename="a.txt", content=b"v1", content_type="text/plain", user_id=uid, visibility="private")
        assert ok["version"] == 1

        async def boom(path, content):
            raise OSError("disk full")

        monkeypatch.setattr(storage, "save", boom)
        res = await _write_bytes(db, storage, wb, filename="a.txt", content=b"v2", content_type="text/plain", user_id=uid, visibility="private")
        assert "Storage write failed" in res["error"]
        f = (await db.execute(select(File).where(File.collection_id == wb.id, File.name == "a.txt"))).scalar_one()
        await db.refresh(f)
        assert f.current_version == 1  # the bump was rolled back with the failed write

    @pytest.mark.asyncio
    async def test_expired_chat_takes_its_workbench_along(self, db, chat, test_user, monkeypatch):
        from app.services.chat_deletion import delete_workbenches_for_chats
        from app.services.file_storage import get_storage

        uid = str(test_user.id)
        await WorkbenchTools().execute_tool(db, chat, uid, "workbench_write", {"filename": "gone.txt", "content": "bye"})
        wb = await get_or_create_workbench(db, chat)
        paths = await delete_workbenches_for_chats(db, [chat.id])
        assert len(paths) == 1 and paths[0].startswith(f"_chat/{chat.id}/")
        assert (await db.execute(select(Collection).where(Collection.id == wb.id))).scalar_one_or_none() is None
        assert (await db.execute(select(File).where(File.collection_id == wb.id))).scalars().all() == []
        assert await get_storage().read(paths[0]) == b"bye"  # bytes removed after the commit, by the caller

    @staticmethod
    def _sandbox_open(tmp_path, lazy):
        """The sandbox's open() shim, lifted out of the wrapper script and
        bound to a real directory, with fetches failing loudly."""
        import os
        import re
        import textwrap

        from app.services.code_execution import _build_wrapper

        wrapper = _build_wrapper("pass", workbench=True)
        block = re.search(r"\n    def _wb_open\(.*?\n        return _wb_real_open\(file, \*args, \*\*kwargs\)\n", wrapper, re.S)
        assert block, "the open() shim must stay in the wrapper"
        fetched = []

        def workbench_fetch(path, _timeout=120):
            fetched.append(path)
            raise AssertionError("fetch must not run for a truncating open")

        ns = {"_os": os, "_wb_root": str(tmp_path), "_wb_lazy_unfetched": lazy,
              "_wb_real_open": open, "workbench_fetch": workbench_fetch}
        exec(textwrap.dedent(block.group(0)), ns)
        return ns["_wb_open"], fetched

    def test_truncating_open_drops_the_lazy_flag_only_once_it_succeeded(self, tmp_path):
        lazy = {"data.csv"}
        (tmp_path / "data.csv").write_bytes(b"")  # the stub
        wb_open, fetched = self._sandbox_open(tmp_path, lazy)

        # Exclusive creation of a file the workbench already has fails like
        # on any file system — and the stub stays lazy (skipped at copy-out),
        # or the failed run would save empty bytes over the real file.
        with pytest.raises(FileExistsError):
            wb_open(str(tmp_path / "data.csv"), "x+b")
        assert lazy == {"data.csv"}

        # Once the code removed the stub, exclusive creation succeeds and the
        # replacement is a real change.
        lazy.add("fresh.txt")
        (tmp_path / "fresh.txt").write_bytes(b"")
        (tmp_path / "fresh.txt").unlink()
        with wb_open(str(tmp_path / "fresh.txt"), "x") as fh:
            fh.write("made anew")
        assert "fresh.txt" not in lazy

        # A failed truncating open keeps the flag too ...
        lazy.add("missing/dir.txt")
        with pytest.raises(FileNotFoundError):
            wb_open(str(tmp_path / "missing/dir.txt"), "w")
        assert "missing/dir.txt" in lazy

        # ... and a successful one replaces the file without any fetch.
        with wb_open(str(tmp_path / "data.csv"), "w") as fh:
            fh.write("new")
        assert "data.csv" not in lazy
        assert fetched == []
        assert (tmp_path / "data.csv").read_text() == "new"

    def test_reading_open_fetches_a_lazy_file_first(self, tmp_path):
        lazy = {"data.csv"}
        (tmp_path / "data.csv").write_bytes(b"")
        wb_open, fetched = self._sandbox_open(tmp_path, lazy)
        with pytest.raises(AssertionError, match="fetch must not run"):
            wb_open(str(tmp_path / "data.csv"), "rb")
        assert fetched == [str(tmp_path / "data.csv")]

    @pytest.mark.asyncio
    async def test_cleanup_leaves_ordinary_chat_collections_alone(self, db, chat, test_user):
        """Config apply may create an ordinary collection in `_chat` — even
        one named like a chat id. Expiry removes workbenches only."""
        from app.services.chat_deletion import delete_workbenches_for_chats

        plain = Collection(namespace="_chat", name=str(chat.id), kind="collection", user_id=test_user.id)
        db.add(plain)
        await db.commit()
        assert await delete_workbenches_for_chats(db, [chat.id]) == []
        assert (await db.execute(select(Collection).where(Collection.id == plain.id))).scalar_one_or_none() is not None
