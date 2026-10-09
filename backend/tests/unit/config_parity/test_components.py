"""Components: one applier for every write channel.

Config apply kept its own copy of the write (and re-enabled what an operator
switched off), REST edits weren't recorded, and a REST delete only flagged
the row inactive, so its name stayed taken and nothing could bring it back.
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component import Component
from app.models.config_revision import ConfigRevision
from app.schemas.config import SinasConfig
from app.schemas.spec.component import ComponentSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

NS = "ui"
FIELDS = (
    "title", "description", "source_code", "input_schema", "enabled_agents",
    "enabled_functions", "enabled_queries", "enabled_components", "enabled_stores",
    "visibility", "is_active",
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _yaml(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "title": "Orders", "sourceCode": "<p>orders</p>",
        "inputSchema": {"type": "object", "properties": {"customer": {"type": "string"}}},
        "enabledQueries": ["sales/orders"], "enabledStores": ["sales/notes"],
        "visibility": "shared", **extra,
    }


def _rest(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "title": "Orders", "source_code": "<p>orders</p>",
        "input_schema": {"type": "object", "properties": {"customer": {"type": "string"}}},
        "enabled_queries": ["sales/orders"],
        "enabled_stores": [{"store": "sales/notes", "access": "readwrite"}],
        "visibility": "shared", **extra,
    }


async def _apply(db, owner, *components, managed_by="config"):
    svc = ConfigApplyService(
        db, "cfg", owner_user_id=str(owner.id), managed_by=managed_by, auto_commit=False
    )
    return await svc.apply_config(SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
        "spec": {"components": list(components)},
    }))


async def _row(db: AsyncSession, name: str) -> Component | None:
    row = (await db.execute(
        select(Component).where(Component.namespace == NS, Component.name == name)
    )).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _actions(db: AsyncSession, name: str) -> list[str]:
    return list((await db.execute(
        select(ConfigRevision.action)
        .where(ConfigRevision.resource_kind == "components", ConfigRevision.resource_key == f"{NS}/{name}")
        .order_by(ConfigRevision.id)
    )).scalars())


class TestSpec:
    def test_rest_and_config_shapes_are_the_same_spec(self):
        assert ComponentSpec.model_validate(_yaml("c")) == ComponentSpec.model_validate(_rest("c"))

    def test_a_bare_store_means_read_write(self):
        spec = ComponentSpec.model_validate(_yaml("c"))
        assert spec.enabled_stores[0].access == "readwrite"

    @pytest.mark.parametrize("bad", [
        {"visibility": "everyone"},
        {"enabledStores": [{"store": "nonamespace", "access": "readwrite"}]},
        {"enabledStores": [{"store": "a/b", "access": "write"}]},
        {"sourceCode": ""},
        {"namespace": "a/b"},
        {"cssOverrides": "p {}"},  # folded into the page; no longer a field
    ])
    def test_refuses_what_cannot_work(self, bad):
        with pytest.raises(ValidationError):
            ComponentSpec.model_validate({**_yaml("c"), **bad})


class TestOneWritePath:
    async def test_api_and_config_write_identical_rows(self, client, db: AsyncSession, admin_user):
        api, cfg = f"api-{_uid()}", f"cfg-{_uid()}"
        r = await client.post("/api/v1/components", json=_rest(api), headers=auth_headers(admin_user))
        assert r.status_code == 201, r.text
        result = await _apply(db, admin_user, _yaml(cfg))
        assert result.success, result.errors
        api_row, cfg_row = await _row(db, api), await _row(db, cfg)
        assert {f: getattr(api_row, f) for f in FIELDS} == {f: getattr(cfg_row, f) for f in FIELDS}

    async def test_every_api_change_is_recorded(self, client, db: AsyncSession, admin_user):
        name, h = f"c-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/components", json=_rest(name), headers=h)
        r = await client.put(f"/api/v1/components/{NS}/{name}", json={"source_code": "<p>v2</p>"}, headers=h)
        assert r.status_code == 200, r.text
        assert (await client.delete(f"/api/v1/components/{NS}/{name}", headers=h)).status_code == 204
        assert await _actions(db, name) == ["create", "update", "delete"]

    async def test_delete_frees_the_name_and_can_be_restored(self, client, db: AsyncSession, admin_user):
        name, h = f"c-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/components", json=_rest(name), headers=h)
        await client.delete(f"/api/v1/components/{NS}/{name}", headers=h)
        assert await _row(db, name) is None
        deleted = (await db.execute(
            select(ConfigRevision.id).where(
                ConfigRevision.resource_kind == "components", ConfigRevision.resource_key == f"{NS}/{name}",
                ConfigRevision.action == "delete",
            )
        )).scalar_one()
        r = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=h)
        assert r.status_code == 200, r.text
        assert (await _row(db, name)).source_code == "<p>orders</p>"

    async def test_a_rename_onto_an_existing_component_is_a_400(self, client, admin_user):
        a, b, h = f"a-{_uid()}", f"b-{_uid()}", auth_headers(admin_user)
        for name in (a, b):
            await client.post("/api/v1/components", json=_rest(name), headers=h)
        r = await client.put(f"/api/v1/components/{NS}/{a}", json={"name": b}, headers=h)
        assert r.status_code == 400
        assert r.json()["detail"] == f"Component '{NS}/{b}' already exists"

    async def test_a_manual_edit_detaches_a_config_managed_component(self, client, db, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml(name))
        r = await client.put(
            f"/api/v1/components/{NS}/{name}", json={"title": "Mine"}, headers=auth_headers(admin_user)
        )
        assert r.status_code == 200, r.text
        assert (await _row(db, name)).managed_by is None


class TestIsActiveAndExport:
    async def test_a_re_apply_does_not_re_enable_a_disabled_component(self, client, db, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml(name))
        await client.put(
            f"/api/v1/components/{NS}/{name}", json={"is_active": False}, headers=auth_headers(admin_user)
        )
        result = await _apply(db, admin_user, _yaml(name))
        assert result.success, result.errors
        assert (await _row(db, name)).is_active is False

    async def test_export_round_trips(self, db: AsyncSession, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml(name, isActive=False))
        exported = await ConfigExportService(db, managed_only=True)._export_components()
        [mine] = [c for c in exported if c["name"] == name]
        assert mine["isActive"] is False
        assert mine["enabledStores"] == [{"store": "sales/notes", "access": "readwrite"}]
        result = await _apply(db, admin_user, mine)
        assert result.summary.unchanged.get("components") == 1


class TestPackages:
    async def test_uninstall_records_the_deletion(self, db: AsyncSession, admin_user, published):
        from app.services.package_service import PackageService

        pkg, name = f"pkg-{_uid()}", f"c-{_uid()}"
        package = "\n".join([
            "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
            "package:", f"  name: {pkg}", '  version: "1.0.0"', "spec:", "  components:",
            f"    - {{namespace: {NS}, name: {name}, sourceCode: '<p>hi</p>'}}",
        ]) + "\n"
        service = PackageService(db)
        _, installed = await service.install(package, str(admin_user.id))
        assert installed.success, installed.errors
        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert counts.get("components") == 1
        assert await _actions(db, name) == ["create", "delete"]
