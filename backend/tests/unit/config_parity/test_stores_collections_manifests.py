"""Stores, collections and manifests: one applier per kind for every write channel.

Config apply kept its own copy of each write: it overwrote hand-made rows
without stamping them (so packages silently took them over), let two config
files overwrite each other, dropped a manifest's storeDependencies, and
switched every manifest it touched back on. None of these writes was in the
change history, a package upgrade never pruned them, and uninstall bulk
deleted them.
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.file import Collection
from app.models.manifest import Manifest
from app.models.state import State
from app.models.store import Store
from app.schemas.config import SinasConfig
from app.schemas.spec.collection import CollectionSpec
from app.schemas.spec.manifest import ManifestSpec
from app.schemas.spec.store import StoreSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

NS = "crm"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _yaml_store(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Notes",
        "schema": {"type": "object"}, "strict": True, "defaultVisibility": "shared", **extra,
    }


def _yaml_collection(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "metadataSchema": {"type": "object"},
        "maxFileSizeMb": 5, "isPublic": True, "allowPrivateFiles": False, **extra,
    }


def _yaml_manifest(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "description": "CRM app",
        "requiredResources": [{"type": "agent", "namespace": NS, "name": "bot"}],
        "requiredPermissions": ["sinas.agents/crm/bot.chat:own"],
        "exposedNamespaces": {"agents": [NS]},
        "storeDependencies": [{"store": f"{NS}/notes", "key": "prefs"}],
        "publicInfo": {"url": "https://crm.example"}, **extra,
    }


def _config(name="cfg", **spec) -> SinasConfig:
    return SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": name},
        "spec": spec,
    })


async def _apply(db, owner, dry_run=False, config_name="cfg", **spec):
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(_config(config_name, **spec), dry_run=dry_run)


async def _row(db: AsyncSession, model, name: str):
    row = (
        await db.execute(select(model).where(model.namespace == NS, model.name == name))
    ).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _actions(db: AsyncSession, kind: str, name: str) -> list[str]:
    return list((await db.execute(
        select(ConfigRevision.action)
        .where(ConfigRevision.resource_kind == kind, ConfigRevision.resource_key == f"{NS}/{name}")
        .order_by(ConfigRevision.id)
    )).scalars())


# ------------------------------------------------------------------ specs


class TestSpecs:
    def test_rest_and_config_shapes_are_the_same_spec(self):
        rest_store = {
            "namespace": NS, "name": "s", "description": "Notes", "schema": {"type": "object"},
            "strict": True, "default_visibility": "shared",
        }
        assert StoreSpec.model_validate(_yaml_store("s")) == StoreSpec.model_validate(rest_store)
        rest_collection = {
            "namespace": NS, "name": "c", "metadata_schema": {"type": "object"},
            "max_file_size_mb": 5, "is_public": True, "allow_private_files": False,
        }
        assert CollectionSpec.model_validate(_yaml_collection("c")) == CollectionSpec.model_validate(
            rest_collection
        )

    def test_a_function_hook_must_be_namespace_slash_name(self):
        with pytest.raises(ValidationError):
            CollectionSpec.model_validate(_yaml_collection("c", postUploadFunction="nightly"))

    def test_public_info_is_still_checked(self):
        with pytest.raises(ValidationError):
            ManifestSpec.model_validate(_yaml_manifest("m", publicInfo={"bad key": 1}))

    def test_misspelt_fields_are_errors(self):
        with pytest.raises(ValidationError):
            StoreSpec.model_validate(_yaml_store("s", stirct=True))


# ------------------------------------------------------------------ config apply


class TestConfigApply:
    async def test_create_then_unchanged_then_update(self, db: AsyncSession, admin_user):
        s, c, m = f"s{_uid()}", f"c{_uid()}", f"m{_uid()}"
        spec = dict(
            stores=[_yaml_store(s)], collections=[_yaml_collection(c)], manifests=[_yaml_manifest(m)]
        )
        result = await _apply(db, admin_user, **spec)
        assert result.success, result.errors
        again = await _apply(db, admin_user, **spec)
        assert again.success and not again.summary.updated, again.summary
        changed = await _apply(
            db, admin_user, stores=[_yaml_store(s, strict=False)], collections=[_yaml_collection(c)],
            manifests=[_yaml_manifest(m)],
        )
        assert changed.success, changed.errors
        assert (await _row(db, Store, s)).strict is False
        assert await _actions(db, "stores", s) == ["create", "update"]
        assert await _actions(db, "collections", c) == ["create"]
        assert await _actions(db, "manifests", m) == ["create"]

    async def test_a_manifests_store_dependencies_are_applied(self, db: AsyncSession, admin_user):
        m = f"m{_uid()}"
        assert (await _apply(db, admin_user, manifests=[_yaml_manifest(m)])).success
        assert (await _row(db, Manifest, m)).store_dependencies == [
            {"store": f"{NS}/notes", "key": "prefs"}
        ]

    async def test_a_switched_off_manifest_stays_off(self, db: AsyncSession, admin_user):
        m = f"m{_uid()}"
        assert (await _apply(db, admin_user, manifests=[_yaml_manifest(m)])).success
        row = await _row(db, Manifest, m)
        row.is_active = False
        await db.flush()
        assert (await _apply(db, admin_user, manifests=[_yaml_manifest(m, description="v2")])).success
        row = await _row(db, Manifest, m)
        assert (row.description, row.is_active) == ("v2", False)
        assert (await _apply(db, admin_user, manifests=[_yaml_manifest(m, isActive=True)])).success
        assert (await _row(db, Manifest, m)).is_active is True

    async def test_another_config_files_store_is_left_alone(self, db: AsyncSession, admin_user):
        s = f"s{_uid()}"
        assert (await _apply(db, admin_user, config_name="a", stores=[_yaml_store(s)])).success
        result = await _apply(
            db, admin_user, config_name="b", stores=[_yaml_store(s, description="mine now")]
        )
        assert result.success
        assert any("config 'a'" in w for w in result.warnings), result.warnings
        assert (await _row(db, Store, s)).description == "Notes"

    async def test_an_adopted_manual_store_is_stamped(self, db: AsyncSession, admin_user):
        s = f"s{_uid()}"
        db.add(Store(namespace=NS, name=s, user_id=admin_user.id))
        await db.flush()
        assert (await _apply(db, admin_user, stores=[_yaml_store(s)])).success
        row = await _row(db, Store, s)
        assert (row.managed_by, row.config_name, row.strict) == ("config", "cfg", True)

    async def test_a_missing_upload_function_fails_the_apply(self, db: AsyncSession, admin_user, fn):
        c = f"c{_uid()}"
        bad = await _apply(
            db, admin_user, collections=[_yaml_collection(c, postUploadFunction=f"{fn.namespace}/nope")]
        )
        assert not bad.success and "not found" in bad.errors[0]
        good = await _apply(
            db, admin_user,
            collections=[_yaml_collection(c, postUploadFunction=f"{fn.namespace}/{fn.name}")],
        )
        assert good.success, good.errors

    async def test_a_dry_run_changes_nothing(self, db: AsyncSession, admin_user):
        s = f"s{_uid()}"
        result = await _apply(db, admin_user, dry_run=True, stores=[_yaml_store(s)])
        assert result.success and result.summary.created.get("stores") == 1
        assert await _row(db, Store, s) is None


# ------------------------------------------------------------------ REST


class TestRest:
    async def test_store_crud_is_recorded(self, client, db: AsyncSession, admin_user):
        s = f"s{_uid()}"
        headers = auth_headers(admin_user)
        r = await client.post(
            "/api/v1/stores", json={"namespace": NS, "name": s, "strict": True}, headers=headers
        )
        assert r.status_code == 201, r.text
        r = await client.post("/api/v1/stores", json={"namespace": NS, "name": s}, headers=headers)
        assert r.status_code == 400 and "already exists" in r.text
        # Null and namespace/name stay ignored, as they always were.
        r = await client.put(
            f"/api/v1/stores/{NS}/{s}",
            json={"description": "d", "strict": None, "name": "renamed"}, headers=headers,
        )
        assert r.status_code == 200, r.text
        assert (r.json()["name"], r.json()["strict"], r.json()["description"]) == (s, True, "d")
        assert (await client.delete(f"/api/v1/stores/{NS}/{s}", headers=headers)).status_code == 204
        assert await _actions(db, "stores", s) == ["create", "update", "delete"]

    async def test_deleting_a_store_removes_its_states(self, client, db: AsyncSession, admin_user):
        s = f"s{_uid()}"
        store = Store(namespace=NS, name=s, user_id=admin_user.id)
        db.add(store)
        await db.flush()
        db.add(State(user_id=admin_user.id, store_id=store.id, key="k", value={"v": 1}))
        await db.flush()
        store_id = store.id
        r = await client.delete(f"/api/v1/stores/{NS}/{s}", headers=auth_headers(admin_user))
        assert r.status_code == 204
        db.expunge_all()
        assert (await db.execute(select(State).where(State.store_id == store_id))).first() is None

    async def test_an_edit_detaches_a_package_store(self, client, db: AsyncSession, admin_user):
        s = f"s{_uid()}"
        db.add(Store(namespace=NS, name=s, user_id=admin_user.id, managed_by="pkg:x", config_name="x"))
        await db.flush()
        r = await client.put(
            f"/api/v1/stores/{NS}/{s}", json={"strict": True}, headers=auth_headers(admin_user)
        )
        assert r.status_code == 200, r.text
        assert (await _row(db, Store, s)).managed_by is None

    async def test_collection_hooks_must_exist(self, client, db: AsyncSession, admin_user, fn):
        c = f"c{_uid()}"
        headers = auth_headers(admin_user)
        body = {"namespace": NS, "name": c, "post_upload_function": f"{fn.namespace}/nope"}
        r = await client.post("/api/v1/collections", json=body, headers=headers)
        assert r.status_code == 404, r.text
        body["post_upload_function"] = f"{fn.namespace}/{fn.name}"
        assert (await client.post("/api/v1/collections", json=body, headers=headers)).status_code == 201
        r = await client.put(
            f"/api/v1/collections/{NS}/{c}", json={"max_file_size_mb": 7}, headers=headers
        )
        assert r.status_code == 200 and r.json()["max_file_size_mb"] == 7
        assert await _actions(db, "collections", c) == ["create", "update"]

    async def test_a_manifest_can_be_renamed_but_not_onto_another(
        self, client, db: AsyncSession, admin_user
    ):
        a, b, c = f"a{_uid()}", f"b{_uid()}", f"c{_uid()}"
        headers = auth_headers(admin_user)
        for name in (a, b):
            r = await client.post("/api/v1/manifests", json={"namespace": NS, "name": name}, headers=headers)
            assert r.status_code == 201, r.text
        r = await client.put(f"/api/v1/manifests/{NS}/{a}", json={"name": b}, headers=headers)
        assert r.status_code == 400 and "already exists" in r.text
        r = await client.put(
            f"/api/v1/manifests/{NS}/{a}", json={"name": c, "is_active": False}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert (r.json()["name"], (await _row(db, Manifest, c)).is_active) == (c, False)

    async def test_an_upload_into_a_new_collection_is_recorded(
        self, client, db: AsyncSession, admin_user, monkeypatch
    ):
        c = f"c{_uid()}"
        # Only the collection matters here, not where the bytes land.
        class _Storage:
            def calculate_hash(self, content):
                return "hash"

            def __getattr__(self, _name):
                async def _noop(*a, **k):
                    return None

                return _noop

        monkeypatch.setattr("app.api.runtime.endpoints.files.get_storage", lambda: _Storage())
        r = await client.post(
            f"/files/{NS}/{c}",
            json={"name": "a.txt", "content_base64": "aGk=", "content_type": "text/plain"},
            headers=auth_headers(admin_user),
        )
        assert r.status_code in (200, 201), r.text
        assert await _actions(db, "collections", c) == ["create"]


# ------------------------------------------------------------------ export + packages


class TestExportAndPackages:
    async def test_export_round_trips(self, db: AsyncSession, admin_user):
        s, c, m = f"s{_uid()}", f"c{_uid()}", f"m{_uid()}"
        spec = dict(
            stores=[_yaml_store(s)], collections=[_yaml_collection(c)],
            manifests=[_yaml_manifest(m, isActive=False)],
        )
        assert (await _apply(db, admin_user, **spec)).success
        exported = await ConfigExportService(db).export_config()
        import yaml

        doc = yaml.safe_load(exported)["spec"]
        mine = lambda items, name: next(i for i in items if i["name"] == name)  # noqa: E731
        assert mine(doc["manifests"], m)["isActive"] is False
        assert mine(doc["manifests"], m)["storeDependencies"] == [{"store": f"{NS}/notes", "key": "prefs"}]
        again = await _apply(
            db, admin_user, stores=[mine(doc["stores"], s)], collections=[mine(doc["collections"], c)],
            manifests=[mine(doc["manifests"], m)],
        )
        assert again.success and not again.summary.updated, (again.errors, again.summary)

    async def test_uninstall_records_each_deletion(self, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        pkg, name = f"pkg-{_uid()}", f"r{_uid()}"
        package = "\n".join([
            "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
            "package:", f"  name: {pkg}", '  version: "1.0.0"', "spec:",
            "  stores:", f"    - {{namespace: {NS}, name: {name}}}",
            "  collections:", f"    - {{namespace: {NS}, name: {name}}}",
            "  manifests:", f"    - {{namespace: {NS}, name: {name}}}",
        ]) + "\n"
        service = PackageService(db)
        _, installed = await service.install(package, str(admin_user.id))
        assert installed.success, installed.errors
        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert (counts.get("stores"), counts.get("collections"), counts.get("manifests")) == (1, 1, 1)
        for kind in ("stores", "collections", "manifests"):
            assert await _actions(db, kind, name) == ["create", "delete"]

    async def test_a_package_never_takes_over_a_hand_made_store(self, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        pkg, name = f"pkg-{_uid()}", f"r{_uid()}"
        db.add(Store(namespace=NS, name=name, user_id=admin_user.id, description="mine"))
        await db.flush()
        package = "\n".join([
            "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
            "package:", f"  name: {pkg}", '  version: "1.0.0"', "spec:",
            "  stores:", f"    - {{namespace: {NS}, name: {name}, description: theirs}}",
        ]) + "\n"
        _, installed = await PackageService(db).install(package, str(admin_user.id))
        row = await _row(db, Store, name)
        assert (row.description, row.managed_by) == ("mine", None)
        assert installed.warnings or not installed.success


def _package(pkg: str, version: str, names: list[str]) -> str:
    lines = [
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
    ]
    for kind in ("stores", "collections", "manifests"):
        lines.append(f"  {kind}:" + ("" if names else " []"))
        lines += [f"    - {{namespace: {NS}, name: {name}}}" for name in names]
    return "\n".join(lines) + "\n"


class TestUpgradeAndRestore:
    async def test_an_upgrade_removes_what_the_new_version_dropped(
        self, db: AsyncSession, admin_user
    ):
        from app.services.package_service import PackageService

        pkg, keep, drop = f"pkg-{_uid()}", f"k{_uid()}", f"d{_uid()}"
        service = PackageService(db)
        _, first = await service.install(_package(pkg, "1.0.0", [keep, drop]), str(admin_user.id))
        assert first.success, first.errors
        store = await _row(db, Store, drop)
        db.add(State(user_id=admin_user.id, store_id=store.id, key="k", value={"v": 1}))
        await db.flush()

        _, second = await service.install(_package(pkg, "2.0.0", [keep]), str(admin_user.id))
        assert second.success, second.errors
        assert second.summary.deleted == {"stores": 1, "collections": 1, "manifests": 1}
        for model in (Store, Collection, Manifest):
            assert await _row(db, model, keep) is not None
            assert await _row(db, model, drop) is None
        db.expunge_all()
        assert (await db.execute(select(State).where(State.store_id == store.id))).first() is None
        for kind in ("stores", "collections", "manifests"):
            assert await _actions(db, kind, drop) == ["create", "delete"]
            assert await _actions(db, kind, keep) == ["create"]

    async def test_hand_edited_ones_survive_an_upgrade(self, client, db: AsyncSession, admin_user):
        """Editing a package's store, collection or manifest by hand detaches
        it, so an upgrade that no longer ships it leaves the edited copy."""
        from app.services.package_service import PackageService

        pkg, name, h = f"pkg-{_uid()}", f"e{_uid()}", auth_headers(admin_user)
        service = PackageService(db)
        await service.install(_package(pkg, "1.0.0", [name]), str(admin_user.id))
        for path, body in (
            ("stores", {"strict": True}),
            ("collections", {"max_file_size_mb": 7}),
            ("manifests", {"description": "ours"}),
        ):
            r = await client.put(f"/api/v1/{path}/{NS}/{name}", json=body, headers=h)
            assert r.status_code == 200, r.text

        _, result = await service.install(_package(pkg, "2.0.0", []), str(admin_user.id))
        assert result.success, result.errors
        assert not result.summary.deleted
        for model in (Store, Collection, Manifest):
            row = await _row(db, model, name)
            assert row is not None and row.managed_by is None

    async def test_a_deleted_definition_can_be_restored(self, client, db: AsyncSession, admin_user):
        """Restore brings the definition back under its original id. The
        states or files it held went with the delete, and stay gone."""
        name, h = f"r{_uid()}", auth_headers(admin_user)
        for path, body in (
            ("stores", {"namespace": NS, "name": name, "strict": True, "schema": {"type": "object"}}),
            ("collections", {"namespace": NS, "name": name, "max_file_size_mb": 7}),
            ("manifests", {"namespace": NS, "name": name, "required_permissions": ["sinas.x.read:own"]}),
        ):
            assert (await client.post(f"/api/v1/{path}", json=body, headers=h)).status_code == 201
        ids = {m: (await _row(db, m, name)).id for m in (Store, Collection, Manifest)}
        for path in ("stores", "collections", "manifests"):
            assert (await client.delete(f"/api/v1/{path}/{NS}/{name}", headers=h)).status_code == 204

        for kind in ("stores", "collections", "manifests"):
            deleted = (await db.execute(
                select(ConfigRevision.id).where(
                    ConfigRevision.resource_kind == kind, ConfigRevision.resource_key == f"{NS}/{name}",
                    ConfigRevision.action == "delete",
                )
            )).scalar_one()
            r = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=h)
            assert r.status_code == 200, r.text

        store, coll, manifest = [await _row(db, m, name) for m in (Store, Collection, Manifest)]
        assert (store.id, store.strict, store.schema) == (ids[Store], True, {"type": "object"})
        assert (coll.id, coll.max_file_size_mb) == (ids[Collection], 7)
        assert (manifest.id, manifest.required_permissions) == (ids[Manifest], ["sinas.x.read:own"])


class TestConfigLeniency:
    async def test_zero_collection_limits_still_apply(self, db: AsyncSession, admin_user):
        c = f"c{_uid()}"
        result = await _apply(db, admin_user, collections=[_yaml_collection(c, maxFileSizeMb=0, maxTotalSizeGb=0)])
        assert result.success, result.errors
