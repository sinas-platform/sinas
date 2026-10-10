"""Deleting a collection removes its files' stored bytes too, after the
delete commits. The database cascade removed the file rows, but the blobs
stayed in storage for good: on a REST delete, package uninstall and upgrade.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import Collection, File, FileVersion
from tests.conftest import auth_headers

NS = "docs"


@pytest.fixture
def storage(monkeypatch):
    removed: list[str] = []

    class _Storage:
        async def delete(self, path):
            removed.append(path)

    monkeypatch.setattr("app.services.file_storage.get_storage", lambda: _Storage())
    return removed


async def _collection_with_files(db: AsyncSession, owner, name: str, **extra) -> list[str]:
    coll = Collection(namespace=NS, name=name, user_id=owner.id, **extra)
    db.add(coll)
    await db.flush()
    paths = []
    for i in range(2):
        f = File(collection_id=coll.id, name=f"f{i}.txt", user_id=owner.id, content_type="text/plain")
        db.add(f)
        await db.flush()
        for v in (1, 2):
            path = f"{NS}/{name}/{uuid.uuid4().hex}"
            db.add(FileVersion(
                file_id=f.id, version_number=v, storage_path=path, size_bytes=1,
                hash_sha256="0" * 64, uploaded_by=owner.id,
            ))
            paths.append(path)
    await db.flush()
    return paths


async def test_a_rest_delete_removes_the_stored_files(client, db: AsyncSession, admin_user, storage):
    name = f"c{uuid.uuid4().hex[:8]}"
    paths = await _collection_with_files(db, admin_user, name)
    r = await client.delete(f"/api/v1/collections/{NS}/{name}", headers=auth_headers(admin_user))
    assert r.status_code == 204
    assert sorted(storage) == sorted(paths)


async def test_package_uninstall_removes_them_too(db: AsyncSession, admin_user, storage):
    from app.services.package_service import PackageService

    pkg, name = f"p-{uuid.uuid4().hex[:8]}", f"c{uuid.uuid4().hex[:8]}"
    package = "\n".join([
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", '  version: "1.0.0"', "spec:",
        "  collections:", f"    - {{namespace: {NS}, name: {name}}}",
    ]) + "\n"
    service = PackageService(db)
    _, installed = await service.install(package, str(admin_user.id))
    assert installed.success, installed.errors
    coll = await Collection.get_by_name(db, NS, name)
    f = File(collection_id=coll.id, name="a.txt", user_id=admin_user.id, content_type="text/plain")
    db.add(f)
    await db.flush()
    db.add(FileVersion(file_id=f.id, version_number=1, storage_path=f"{NS}/{name}/x", size_bytes=1,
                       hash_sha256="0" * 64, uploaded_by=admin_user.id))
    await db.flush()
    await service.uninstall(pkg, actor_user_id=str(admin_user.id))
    assert storage == [f"{NS}/{name}/x"]


async def test_nothing_is_removed_when_the_apply_fails(db: AsyncSession, admin_user, storage):
    """An upgrade that would drop the collection, but fails as a whole, keeps
    every file: removal only follows a commit."""
    from app.services.package_service import PackageService

    pkg, name = f"p-{uuid.uuid4().hex[:8]}", f"c{uuid.uuid4().hex[:8]}"

    def package(version, extra=""):
        lines = [
            "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
            "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
        ]
        if extra:
            lines.append(extra)
        else:
            lines += ["  collections:", f"    - {{namespace: {NS}, name: {name}}}"]
        return "\n".join(lines) + "\n"

    service = PackageService(db)
    await service.install(package("1.0.0"), str(admin_user.id))
    coll = await Collection.get_by_name(db, NS, name)
    f = File(collection_id=coll.id, name="a.txt", user_id=admin_user.id, content_type="text/plain")
    db.add(f)
    await db.flush()
    db.add(FileVersion(file_id=f.id, version_number=1, storage_path=f"{NS}/{name}/y", size_bytes=1,
                       hash_sha256="0" * 64, uploaded_by=admin_user.id))
    await db.flush()
    # 2.0.0 drops the collection but also has an invalid pipeline: the whole upgrade fails.
    with pytest.raises(ValueError, match="steps"):  # failed in the apply, after the prune
        await service.install(
            package("2.0.0", "  pipelines:\n    - {namespace: x, name: y, steps: []}"), str(admin_user.id)
        )
    assert storage == []


async def test_files_are_locked_before_their_paths_are_read(db: AsyncSession, admin_user, storage):
    """A concurrent new-version upload locks its file: taking those locks
    before reading the paths means its version is either read here or waits
    for the delete (a two-session race test doesn't fit this test DB)."""
    from sqlalchemy import event

    from app.services.resources import ApplyContext
    from app.services.resources.collections import CollectionApplier

    name = f"c{uuid.uuid4().hex[:8]}"
    await _collection_with_files(db, admin_user, name)
    coll = await Collection.get_by_name(db, NS, name)
    statements: list[str] = []

    def capture(conn, cursor, statement, *args):
        statements.append(" ".join(statement.split()).lower())

    engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        await CollectionApplier().delete(coll, ApplyContext(db=db, origin="api", actor_user_id=str(admin_user.id)))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    lock = next(i for i, s in enumerate(statements) if s.startswith("select files.id") and "for update" in s)
    read = next(i for i, s in enumerate(statements) if "file_versions.storage_path" in s)
    assert lock < read
