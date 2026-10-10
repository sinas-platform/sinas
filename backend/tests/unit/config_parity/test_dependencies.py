"""Dependencies: one applier for config, packages and the REST API.

Nothing recorded who declared a dependency, none of its writes were in the
change history, and a package could approve a Python package the
deployment's own allow-list refuses. Packages keep the dependencies they add
(agreed): uninstall and upgrades never remove them.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.dependency import Dependency
from app.schemas.config import SinasConfig
from app.schemas.spec.dependency import DependencySpec
from app.services.config_apply.service import ConfigApplyService
from tests.conftest import auth_headers


def _pkg() -> str:
    return f"pkg-{uuid.uuid4().hex[:8]}"


async def _apply(db, owner, config_name="cfg", dry_run=False, **spec):
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": config_name},
        "spec": spec,
    })
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(config, dry_run=dry_run)


async def _row(db: AsyncSession, name: str) -> Dependency | None:
    row = (await db.execute(select(Dependency).where(Dependency.package_name == name))).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _actions(db: AsyncSession, name: str) -> list[str]:
    return list((await db.execute(
        select(ConfigRevision.action)
        .where(ConfigRevision.resource_kind == "dependencies", ConfigRevision.resource_key == name)
        .order_by(ConfigRevision.id)
    )).scalars())


def test_a_pinned_name_splits_into_name_and_version():
    spec = DependencySpec.model_validate({"packageName": "httpx==0.27.0"})
    assert (spec.package_name, spec.version) == ("httpx", "0.27.0")


class TestConfigApply:
    async def test_create_keep_version_and_update(self, db: AsyncSession, admin_user):
        name = _pkg()
        assert (await _apply(db, admin_user, dependencies=[{"packageName": f"{name}==1.0"}])).success
        row = await _row(db, name)
        assert (row.version, row.managed_by, row.config_name) == ("1.0", "config", "cfg")
        again = await _apply(db, admin_user, dependencies=[{"packageName": name}])  # no version: kept
        assert again.success and not again.summary.updated, again.summary
        assert (await _apply(db, admin_user, dependencies=[{"packageName": name, "version": "2.0"}])).success
        assert (await _row(db, name)).version == "2.0"
        assert await _actions(db, name) == ["create", "update"]

    async def test_another_config_files_dependency_is_left_alone(self, db: AsyncSession, admin_user):
        name = _pkg()
        assert (await _apply(db, admin_user, config_name="a", dependencies=[{"packageName": name, "version": "1"}])).success
        result = await _apply(db, admin_user, config_name="b", dependencies=[{"packageName": name, "version": "2"}])
        assert result.success and any("config 'a'" in w for w in result.warnings)
        assert (await _row(db, name)).version == "1"


class TestRest:
    async def test_install_and_remove_are_recorded(self, client, db: AsyncSession, admin_user):
        name, h = _pkg(), auth_headers(admin_user)
        r = await client.post("/api/v1/dependencies", json={"package_name": name, "version": "1.0"}, headers=h)
        assert r.status_code == 200, r.text
        r = await client.post("/api/v1/dependencies", json={"package_name": name}, headers=h)
        assert r.status_code == 400 and "already approved" in r.text
        dep_id = (await _row(db, name)).id
        assert (await client.delete(f"/api/v1/dependencies/{dep_id}", headers=h)).status_code == 204
        assert await _actions(db, name) == ["create", "delete"]

    async def test_the_allow_list_still_applies(self, client, admin_user, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "allowed_packages", "requests")
        r = await client.post("/api/v1/dependencies", json={"package_name": _pkg()}, headers=auth_headers(admin_user))
        assert r.status_code == 403


def _package(pkg: str, version: str, deps: list[str]) -> str:
    lines = [
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
        "  dependencies:" + ("" if deps else " []"),
    ]
    lines += [f"    - packageName: {d}" for d in deps]
    return "\n".join(lines) + "\n"


class TestPackages:
    async def test_a_package_keeps_what_it_added(self, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        pkg, name = f"p-{uuid.uuid4().hex[:8]}", _pkg()
        service = PackageService(db)
        _, installed = await service.install(_package(pkg, "1.0.0", [name]), str(admin_user.id))
        assert installed.success, installed.errors
        assert (await _row(db, name)).managed_by == f"pkg:{pkg}"
        _, upgraded = await service.install(_package(pkg, "2.0.0", []), str(admin_user.id))
        assert upgraded.success and not upgraded.summary.deleted
        await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert await _row(db, name) is not None

    async def test_a_package_respects_the_allow_list(self, db: AsyncSession, admin_user, monkeypatch):
        from app.core.config import settings
        from app.services.package_service import PackageService

        monkeypatch.setattr(settings, "allowed_packages", "requests")
        name = _pkg()
        result, *_ = await PackageService(db).preview(
            _package(f"p-{uuid.uuid4().hex[:8]}", "1.0.0", [name]), str(admin_user.id)
        )
        assert not result.success and "whitelist" in result.errors[0]


async def test_a_pin_in_the_name_updates_and_a_blank_version_keeps(db: AsyncSession, admin_user):
    name = _pkg()
    assert (await _apply(db, admin_user, dependencies=[{"packageName": f"{name}==1.0"}])).success
    assert (await _apply(db, admin_user, dependencies=[{"packageName": f"{name}==2.0"}])).success
    assert (await _row(db, name)).version == "2.0"
    assert (await _apply(db, admin_user, dependencies=[{"packageName": name, "version": ""}])).success
    assert (await _row(db, name)).version == "2.0"
