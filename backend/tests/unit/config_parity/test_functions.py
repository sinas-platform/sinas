"""Functions: one applier for every write channel.

Config apply kept its own copy of the write: it switched disabled functions
back on, overwrote hand-made ones (so packages silently took them over), and
let two config files overwrite each other. REST added a version whenever
code was sent, changed or not. No function write was in the change history,
a package upgrade never removed a function the new version dropped, and
uninstall bulk-deleted them.
"""

import uuid

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.function import Function, FunctionVersion
from app.schemas.config import SinasConfig
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

NS = "fx"
CODE = "def handler(input, context):\n    return {'ok': True}\n"
SCHEMA = {"type": "object", "properties": {}}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _yaml(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Sync", "code": CODE,
        "inputSchema": SCHEMA, "outputSchema": SCHEMA, "timeout": 30, **extra,
    }


def _rest(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Sync", "code": CODE,
        "input_schema": SCHEMA, "output_schema": SCHEMA, **extra,
    }


async def _apply(db, owner, config_name="cfg", dry_run=False, **spec):
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": config_name},
        "spec": spec,
    })
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(config, dry_run=dry_run)


async def _row(db: AsyncSession, name: str) -> Function | None:
    row = (
        await db.execute(select(Function).where(Function.namespace == NS, Function.name == name))
    ).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _versions(db: AsyncSession, row: Function) -> list[int]:
    return list((await db.execute(
        select(FunctionVersion.version)
        .where(FunctionVersion.function_id == row.id)
        .order_by(FunctionVersion.version)
    )).scalars())


async def _actions(db: AsyncSession, name: str) -> list[str]:
    return list((await db.execute(
        select(ConfigRevision.action)
        .where(ConfigRevision.resource_kind == "functions", ConfigRevision.resource_key == f"{NS}/{name}")
        .order_by(ConfigRevision.id)
    )).scalars())


class TestRest:
    async def test_versions_follow_real_changes(self, client, db: AsyncSession, admin_user):
        name, h = f"f_{_uid()}", auth_headers(admin_user)
        assert (await client.post("/api/v1/functions", json=_rest(name), headers=h)).status_code == 201
        row = await _row(db, name)
        assert await _versions(db, row) == [1]

        url = f"/api/v1/functions/{NS}/{name}"
        # The same code sent again used to add a version.
        r = await client.put(url, json={"code": CODE, "description": "d2"}, headers=h)
        assert r.status_code == 200, r.text
        assert await _versions(db, row) == [1]
        assert (await client.put(url, json={"code": CODE + "# v2\n"}, headers=h)).status_code == 200
        assert (await client.put(url, json={"input_schema": {"type": "object", "required": []}}, headers=h)).status_code == 200
        assert await _versions(db, row) == [1, 2, 3]
        assert await _actions(db, name) == ["create", "update", "update", "update"]

    async def test_rename_clash_and_delete(self, client, db: AsyncSession, admin_user):
        a, b, c, h = f"a_{_uid()}", f"b_{_uid()}", f"c_{_uid()}", auth_headers(admin_user)
        for name in (a, b):
            assert (await client.post("/api/v1/functions", json=_rest(name), headers=h)).status_code == 201
        r = await client.post("/api/v1/functions", json=_rest(a), headers=h)
        assert r.status_code == 400 and "already exists" in r.text
        r = await client.put(f"/api/v1/functions/{NS}/{a}", json={"name": b}, headers=h)
        assert r.status_code == 400 and "already exists" in r.text
        r = await client.put(f"/api/v1/functions/{NS}/{a}", json={"name": c}, headers=h)
        assert r.status_code == 200 and r.json()["name"] == c
        await client.put(f"/api/v1/functions/{NS}/{c}", json={"code": CODE + "# x\n"}, headers=h)
        # Versions go with it (no ON DELETE rule on their FK).
        assert (await client.delete(f"/api/v1/functions/{NS}/{c}", headers=h)).status_code == 204
        assert await _row(db, c) is None

    async def test_a_deleted_function_can_be_restored(self, client, db: AsyncSession, admin_user):
        name, h = f"r_{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/functions", json=_rest(name, requires_approval=True), headers=h)
        original = (await _row(db, name)).id
        await client.delete(f"/api/v1/functions/{NS}/{name}", headers=h)
        deleted = (await db.execute(
            select(ConfigRevision.id).where(
                ConfigRevision.resource_kind == "functions",
                ConfigRevision.resource_key == f"{NS}/{name}", ConfigRevision.action == "delete",
            )
        )).scalar_one()
        r = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=h)
        assert r.status_code == 200, r.text
        row = await _row(db, name)
        assert (row.id, row.code, row.requires_approval) == (original, CODE, True)
        assert await _versions(db, row) == [1]

    async def test_the_rest_only_gates_still_hold(self, client, db: AsyncSession, admin_user, test_user, monkeypatch):
        from app.core.config import settings

        r = await client.post(
            "/api/v1/functions", json=_rest(f"s_{_uid()}", shared_pool=True), headers=auth_headers(test_user)
        )
        assert r.status_code == 403
        r = await client.post(
            "/api/v1/functions", json=_rest(f"x_{_uid()}", code="def handler(:"), headers=auth_headers(admin_user)
        )
        assert r.status_code == 422
        monkeypatch.setattr(settings, "code_execution_enabled", False)
        r = await client.post("/api/v1/functions", json=_rest(f"o_{_uid()}"), headers=auth_headers(admin_user))
        assert r.status_code == 403


class TestConfigApply:
    async def test_create_unchanged_update(self, db: AsyncSession, admin_user):
        name = f"f_{_uid()}"
        assert (await _apply(db, admin_user, functions=[_yaml(name)])).success
        again = await _apply(db, admin_user, functions=[_yaml(name)])
        assert again.success and not again.summary.updated
        assert (await _apply(db, admin_user, functions=[_yaml(name, code=CODE + "# 2\n")])).success
        row = await _row(db, name)
        assert await _versions(db, row) == [1, 2]
        assert await _actions(db, name) == ["create", "update"]

    async def test_operator_state_is_kept_unless_declared(self, db: AsyncSession, admin_user):
        name = f"f_{_uid()}"
        assert (await _apply(db, admin_user, functions=[_yaml(name)])).success
        row = await _row(db, name)
        row.is_active, row.shared_pool, row.requires_approval = False, True, True
        await db.flush()
        assert (await _apply(db, admin_user, functions=[_yaml(name, description="v2")])).success
        row = await _row(db, name)
        assert (row.description, row.is_active, row.shared_pool, row.requires_approval) == (
            "v2", False, True, True,
        )
        assert (await _apply(db, admin_user, functions=[_yaml(name, isActive=True, requiresApproval=False)])).success
        row = await _row(db, name)
        assert (row.is_active, row.requires_approval) == (True, False)

    async def test_another_config_files_function_is_left_alone(self, db: AsyncSession, admin_user):
        name = f"f_{_uid()}"
        assert (await _apply(db, admin_user, config_name="a", functions=[_yaml(name)])).success
        result = await _apply(db, admin_user, config_name="b", functions=[_yaml(name, description="b")])
        assert result.success and any("config 'a'" in w for w in result.warnings)
        assert (await _row(db, name)).description == "Sync"

    async def test_applies_with_code_execution_off(self, db: AsyncSession, admin_user, monkeypatch):
        """Config and packages may still declare functions (they just can't run)."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "code_execution_enabled", False)
        name = f"f_{_uid()}"
        assert (await _apply(db, admin_user, functions=[_yaml(name)])).success
        assert await _row(db, name) is not None

    async def test_a_schedule_can_target_a_function_from_the_same_config(self, db: AsyncSession, admin_user):
        name = f"f_{_uid()}"
        schedule = {
            "name": f"s-{_uid()}", "functionName": f"{NS}/{name}", "cronExpression": "0 3 * * *",
        }
        for dry_run in (True, False):
            result = await _apply(
                db, admin_user, dry_run=dry_run, functions=[_yaml(name)], schedules=[schedule]
            )
            assert result.success, result.errors

    async def test_export_round_trips_including_disabled(self, db: AsyncSession, admin_user):
        name = f"f_{_uid()}"
        assert (await _apply(db, admin_user, functions=[_yaml(name, isActive=False)])).success
        doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
        exported = next(f for f in doc["functions"] if f["name"] == name)
        assert exported["isActive"] is False
        again = await _apply(db, admin_user, functions=[exported])
        assert again.success and not again.summary.updated, (again.errors, again.summary)


def _package(pkg: str, version: str, names: list[str]) -> str:
    lines = [
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
        "  functions:" + ("" if names else " []"),
    ]
    for name in names:
        lines += [
            f"    - namespace: {NS}", f"      name: {name}", "      code: |",
            "        def handler(input, context):", "            return {}",
        ]
    return "\n".join(lines) + "\n"


class TestPackages:
    async def test_upgrade_prunes_and_uninstall_records(self, client, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        pkg, keep, drop, edited = f"pkg-{_uid()}", f"k_{_uid()}", f"d_{_uid()}", f"e_{_uid()}"
        service = PackageService(db)
        _, first = await service.install(_package(pkg, "1.0.0", [keep, drop, edited]), str(admin_user.id))
        assert first.success, first.errors
        # A hand edit detaches it from the package.
        r = await client.put(
            f"/api/v1/functions/{NS}/{edited}", json={"description": "ours"},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 200, r.text

        _, second = await service.install(_package(pkg, "2.0.0", [keep]), str(admin_user.id))
        assert second.success, second.errors
        assert second.summary.deleted == {"functions": 1}
        assert await _row(db, drop) is None
        assert (await _row(db, edited)).managed_by is None

        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert counts.get("functions") == 1
        assert await _row(db, keep) is None and await _row(db, edited) is not None
        assert await _actions(db, keep) == ["create", "delete"]
        assert await _actions(db, drop) == ["create", "delete"]

    async def test_a_package_never_takes_over_a_hand_made_function(self, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        name = f"h_{_uid()}"
        db.add(Function(
            namespace=NS, name=name, user_id=admin_user.id, code=CODE, input_schema={}, output_schema={},
        ))
        await db.flush()
        await PackageService(db).install(_package(f"pkg-{_uid()}", "1.0.0", [name]), str(admin_user.id))
        row = await _row(db, name)
        assert (row.code, row.managed_by) == (CODE, None)


async def test_versions_have_no_gaps_after_config_updates(db: AsyncSession, admin_user):
    name = f"g_{_uid()}"
    for i in range(3):
        assert (await _apply(db, admin_user, functions=[_yaml(name, code=CODE + f"# {i}\n")])).success
    row = await _row(db, name)
    count = (await db.execute(
        select(func.count()).select_from(FunctionVersion).where(FunctionVersion.function_id == row.id)
    )).scalar()
    assert await _versions(db, row) == [1, 2, 3] and count == 3
