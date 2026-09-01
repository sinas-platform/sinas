"""Workbench: per-chat working tree — fence, tools, checkout/promote.

Covers the two contracts from the workbench ADR:
- the kind='workbench' fence: workbench rows are invisible to every
  collections-facing surface (by-name resolution, wildcard tool expansion,
  the collections API), even for wildcard/admin grants;
- workbench semantics: private-only files, chat-scoped access, checkout
  with provenance and visibility filtering, promote as checkout's reverse
  (source update with conflict detection, else create-in-target).
"""
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
