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
from app.models.file import Collection, File
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
    async def test_wildcard_tool_expansion_skips_workbenches(self, db, chat, test_user):
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
