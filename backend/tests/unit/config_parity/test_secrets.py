"""Shared secrets: one applier for config, packages and the REST API.

No secret write was in the change history, config apply let two config files
(or a package) take over each other's secrets, and export listed private
secrets as if config could declare them. History must never hold a value.
Packages keep their old contract: they may fill in a secret, never delete
one.
"""

import json
import uuid

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.models.config_revision import ConfigRevision
from app.models.secret import Secret
from app.schemas.config import SinasConfig
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers


def _name() -> str:
    return f"TOK_{uuid.uuid4().hex[:8].upper()}"


async def _apply(db, owner, config_name="cfg", dry_run=False, **spec):
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": config_name},
        "spec": spec,
    })
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(config, dry_run=dry_run)


async def _shared(db: AsyncSession, name: str) -> Secret | None:
    row = (await db.execute(
        select(Secret).where(Secret.name == name, Secret.visibility == "shared")
    )).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


def _value(row: Secret) -> str:
    return encryption_service.decrypt(row.encrypted_value)


async def _revisions(db: AsyncSession, name: str) -> list[ConfigRevision]:
    return list((await db.execute(
        select(ConfigRevision)
        .where(ConfigRevision.resource_kind == "secrets", ConfigRevision.resource_key == name)
        .order_by(ConfigRevision.id)
    )).scalars())


class TestConfigApply:
    async def test_value_is_kept_when_left_out_and_never_in_history(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, secrets=[{"name": name, "value": "hunter2-first"}])).success
        again = await _apply(db, admin_user, secrets=[{"name": name}])
        assert again.success and not again.summary.updated, again.summary
        assert _value(await _shared(db, name)) == "hunter2-first"
        assert (await _apply(db, admin_user, secrets=[{"name": name, "value": "hunter2-second"}])).success
        row = await _shared(db, name)
        assert _value(row) == "hunter2-second"

        revisions = await _revisions(db, name)
        assert [r.action for r in revisions] == ["create", "update"]
        recorded = json.dumps([r.spec for r in revisions]) + json.dumps([r.changes for r in revisions])
        assert "hunter2" not in recorded and "redacted" in recorded
        assert "hunter2" not in (row.config_checksum or "")

    async def test_a_new_secret_needs_a_value(self, db: AsyncSession, admin_user):
        name = _name()
        for dry_run in (True, False):
            result = await _apply(db, admin_user, dry_run=dry_run, secrets=[{"name": name}])
            assert not result.success and "no value provided" in result.errors[0]

    async def test_another_config_files_secret_is_left_alone(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, config_name="a", secrets=[{"name": name, "value": "a"}])).success
        result = await _apply(db, admin_user, config_name="b", secrets=[{"name": name, "value": "b"}])
        assert result.success and any("config 'a'" in w for w in result.warnings)
        assert _value(await _shared(db, name)) == "a"

    async def test_export_lists_shared_names_only(self, db: AsyncSession, admin_user, test_user):
        shared, private = _name(), _name()
        assert (await _apply(db, admin_user, secrets=[{"name": shared, "value": "v", "description": "d"}])).success
        db.add(Secret(
            user_id=test_user.id, name=private, visibility="private",
            encrypted_value=encryption_service.encrypt("p"),
        ))
        await db.flush()
        doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
        names = {s["name"]: s for s in doc["secrets"]}
        assert names[shared] == {"name": shared, "description": "d"}
        assert private not in names


class TestRest:
    async def test_shared_writes_are_recorded_and_restorable(self, client, db: AsyncSession, admin_user):
        name, h = _name(), auth_headers(admin_user)
        r = await client.post("/api/v1/secrets", json={"name": name, "value": "one"}, headers=h)
        assert r.status_code == 201, r.text
        # POST upserts, as before.
        r = await client.post(
            "/api/v1/secrets", json={"name": name, "value": "two", "description": "d"}, headers=h
        )
        assert r.status_code == 201, r.text
        r = await client.put(f"/api/v1/secrets/{name}", json={"value": "three"}, headers=h)
        assert r.status_code == 200, r.text
        row = await _shared(db, name)
        assert (_value(row), row.description) == ("three", "d")
        original = row.id
        assert (await client.delete(f"/api/v1/secrets/{name}", headers=h)).status_code == 204
        assert [r.action for r in await _revisions(db, name)] == ["create", "update", "update", "delete"]

        deleted = (await _revisions(db, name))[-1].id
        r = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=h)
        assert r.status_code == 200, r.text
        row = await _shared(db, name)
        assert (row.id, _value(row), row.description) == (original, "three", "d")

    async def test_an_edit_detaches_a_config_secret(self, client, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, secrets=[{"name": name, "value": "cfg"}])).success
        r = await client.put(f"/api/v1/secrets/{name}", json={"value": "mine"}, headers=auth_headers(admin_user))
        assert r.status_code == 200, r.text
        assert (await _shared(db, name)).managed_by is None

    async def test_private_secrets_stay_off_the_record(self, client, db: AsyncSession, admin_user):
        name, h = _name(), auth_headers(admin_user)
        r = await client.post(
            "/api/v1/secrets", json={"name": name, "value": "p", "visibility": "private"}, headers=h
        )
        assert r.status_code == 201, r.text
        assert (await client.put(f"/api/v1/secrets/{name}", json={"value": "q"}, headers=h)).status_code == 200
        r = await client.delete(f"/api/v1/secrets/{name}", params={"visibility": "private"}, headers=h)
        assert r.status_code == 204
        assert await _revisions(db, name) == []


def _package(pkg: str, version: str, secret: str, declare: bool) -> str:
    lines = [
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
        "  variables:", f"    - name: {secret}", "      type: secret", "      description: token",
    ]
    lines.append("  secrets:" + (f"\n    - name: {secret}\n      description: token" if declare else " []"))
    return "\n".join(lines) + "\n"


class TestPackages:
    async def test_a_package_fills_in_but_never_deletes_a_secret(self, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        pkg, name = f"pkg-{uuid.uuid4().hex[:8]}", _name()
        service = PackageService(db)
        _, installed = await service.install(
            _package(pkg, "1.0.0", name, declare=True), str(admin_user.id), variables={name: "s3cret"}
        )
        assert installed.success, (installed.errors, installed.warnings)
        row = await _shared(db, name)
        assert (_value(row), row.managed_by) == ("s3cret", f"pkg:{pkg}")

        # An upgrade that stops declaring it, then uninstall: the credential stays.
        _, upgraded = await service.install(
            _package(pkg, "2.0.0", name, declare=False), str(admin_user.id), variables={name: "s3cret"}
        )
        assert upgraded.success, upgraded.errors
        assert not upgraded.summary.deleted
        await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert _value(await _shared(db, name)) == "s3cret"
        assert "delete" not in [r.action for r in await _revisions(db, name)]
