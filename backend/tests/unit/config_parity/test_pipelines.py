"""Pipelines: one applier for every write channel.

Config apply kept its own copy of the write: it switched pipelines that were
turned off (or auto-disabled after failures) back on, and overwrote
hand-made ones (so packages took them over). Nothing was in the change
history, and packages never removed their pipelines, not even on uninstall.
"""

import uuid

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.pipeline import Pipeline
from app.schemas.config import SinasConfig
from app.schemas.spec.pipeline import PipelineSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

NS = "flows"
STEP = {"name": "fetch", "type": "connector", "connector": "google/gmail", "operation": "list-history"}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _yaml(name: str, **extra) -> dict:
    return {"namespace": NS, "name": name, "description": "Sync", "steps": [STEP], **extra}


def _rest(name: str, **extra) -> dict:
    return {"namespace": NS, "name": name, "description": "Sync", "steps": [STEP], **extra}


async def _apply(db, owner, config_name="cfg", dry_run=False, **spec):
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": config_name},
        "spec": spec,
    })
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(config, dry_run=dry_run)


async def _row(db: AsyncSession, name: str) -> Pipeline | None:
    row = (
        await db.execute(select(Pipeline).where(Pipeline.namespace == NS, Pipeline.name == name))
    ).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _actions(db: AsyncSession, name: str) -> list[str]:
    return list((await db.execute(
        select(ConfigRevision.action)
        .where(ConfigRevision.resource_kind == "pipelines", ConfigRevision.resource_key == f"{NS}/{name}")
        .order_by(ConfigRevision.id)
    )).scalars())


class TestSpec:
    def test_config_output_fields_are_the_rest_output_mapping(self):
        config = PipelineSpec.model_validate({**_yaml("p"), "output.$": "steps.fetch.output"})
        rest = PipelineSpec.model_validate({**_rest("p"), "output_mapping": {"output.$": "steps.fetch.output"}})
        assert config == rest
        assert config.to_config()["output.$"] == "steps.fetch.output"

    def test_an_invalid_definition_is_refused(self):
        with pytest.raises(ValidationError):
            PipelineSpec.model_validate({"name": "p", "steps": []})


class TestConfigApply:
    async def test_create_unchanged_update(self, db: AsyncSession, admin_user):
        name = f"p{_uid()}"
        assert (await _apply(db, admin_user, pipelines=[_yaml(name)])).success
        again = await _apply(db, admin_user, pipelines=[_yaml(name)])
        assert again.success and not again.summary.updated, again.summary
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        assert (await _apply(db, admin_user, pipelines=[_yaml(name, asTool=True, toolDescription="t", inputSchema=schema)])).success
        assert (await _row(db, name)).as_tool is True
        assert await _actions(db, name) == ["create", "update"]

    async def test_an_auto_disabled_pipeline_stays_off(self, db: AsyncSession, admin_user):
        name = f"p{_uid()}"
        assert (await _apply(db, admin_user, pipelines=[_yaml(name)])).success
        row = await _row(db, name)
        row.is_active, row.consecutive_failures, row.error_message = False, 5, "boom"
        await db.flush()
        assert (await _apply(db, admin_user, pipelines=[_yaml(name, description="v2")])).success
        row = await _row(db, name)
        assert (row.description, row.is_active, row.consecutive_failures) == ("v2", False, 5)
        # A schedule on it fails in the preview just as in the real apply.
        schedule = {
            "name": f"s-{_uid()}", "scheduleType": "pipeline", "pipelineName": f"{NS}/{name}",
            "cronExpression": "0 3 * * *",
        }
        for dry_run in (True, False):
            result = await _apply(db, admin_user, dry_run=dry_run, pipelines=[_yaml(name, description="v2")], schedules=[schedule])
            assert not result.success, dry_run
        # Switching it back on clears the failure state (the cursor stays).
        row.cursor_value = "c1"
        await db.flush()
        assert (await _apply(db, admin_user, pipelines=[_yaml(name, isActive=True)])).success
        row = await _row(db, name)
        assert (row.is_active, row.consecutive_failures, row.error_message, row.cursor_value) == (True, 0, None, "c1")

    async def test_an_invalid_definition_fails_the_apply(self, db: AsyncSession, admin_user):
        result = await _apply(db, admin_user, pipelines=[_yaml(f"p{_uid()}", steps=[])])
        assert not result.success and "steps" in result.errors[0]

    async def test_another_config_files_pipeline_is_left_alone(self, db: AsyncSession, admin_user):
        name = f"p{_uid()}"
        assert (await _apply(db, admin_user, config_name="a", pipelines=[_yaml(name)])).success
        result = await _apply(db, admin_user, config_name="b", pipelines=[_yaml(name, description="b")])
        assert result.success and any("config 'a'" in w for w in result.warnings)
        assert (await _row(db, name)).description == "Sync"

    async def test_export_round_trips_including_inactive(self, db: AsyncSession, admin_user):
        name = f"p{_uid()}"
        assert (await _apply(db, admin_user, pipelines=[_yaml(name, isActive=False, output={"done": True})])).success
        doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
        exported = next(p for p in doc["pipelines"] if p["name"] == name)
        assert (exported["isActive"], exported["output"]) == (False, {"done": True})
        again = await _apply(db, admin_user, pipelines=[exported])
        assert again.success and not again.summary.updated, (again.errors, again.summary)


class TestRest:
    async def test_crud_is_recorded(self, client, db: AsyncSession, admin_user):
        name, h = f"p{_uid()}", auth_headers(admin_user)
        r = await client.post("/api/v1/pipelines", json=_rest(name), headers=h)
        assert r.status_code == 201, r.text
        r = await client.post("/api/v1/pipelines", json=_rest(name), headers=h)
        assert r.status_code == 400 and "already exists" in r.text
        r = await client.put(f"/api/v1/pipelines/{NS}/{name}", json={"description": "d2"}, headers=h)
        assert r.status_code == 200 and r.json()["description"] == "d2"
        # An invalid merged definition is still a 400.
        r = await client.put(f"/api/v1/pipelines/{NS}/{name}", json={"steps": [{"name": "x"}]}, headers=h)
        assert r.status_code == 400, r.text
        assert (await client.delete(f"/api/v1/pipelines/{NS}/{name}", headers=h)).status_code == 204
        assert await _actions(db, name) == ["create", "update", "delete"]

    async def test_a_deleted_pipeline_can_be_restored(self, client, db: AsyncSession, admin_user):
        name, h = f"p{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/pipelines", json=_rest(
            name, as_tool=True, tool_description="t",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        ), headers=h)
        original = (await _row(db, name)).id
        await client.delete(f"/api/v1/pipelines/{NS}/{name}", headers=h)
        deleted = (await db.execute(
            select(ConfigRevision.id).where(
                ConfigRevision.resource_kind == "pipelines",
                ConfigRevision.resource_key == f"{NS}/{name}", ConfigRevision.action == "delete",
            )
        )).scalar_one()
        r = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=h)
        assert r.status_code == 200, r.text
        row = await _row(db, name)
        assert (row.id, row.as_tool, row.steps) == (original, True, [STEP])


def _package(pkg: str, version: str, names: list[str]) -> str:
    lines = [
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
        "  pipelines:" + ("" if names else " []"),
    ]
    for name in names:
        lines += [
            f"    - namespace: {NS}", f"      name: {name}", "      steps:",
            "        - {name: fetch, type: connector, connector: google/gmail, operation: list-history}",
        ]
    return "\n".join(lines) + "\n"


class TestPackages:
    async def test_upgrade_prunes_and_uninstall_removes(self, client, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        pkg, keep, drop, edited = f"pkg-{_uid()}", f"k{_uid()}", f"d{_uid()}", f"e{_uid()}"
        service = PackageService(db)
        _, first = await service.install(_package(pkg, "1.0.0", [keep, drop, edited]), str(admin_user.id))
        assert first.success, first.errors
        r = await client.put(
            f"/api/v1/pipelines/{NS}/{edited}", json={"description": "ours"}, headers=auth_headers(admin_user)
        )
        assert r.status_code == 200, r.text

        _, second = await service.install(_package(pkg, "2.0.0", [keep]), str(admin_user.id))
        assert second.success, second.errors
        assert second.summary.deleted == {"pipelines": 1}
        assert await _row(db, drop) is None

        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert counts.get("pipelines") == 1  # used to stay behind after uninstall
        assert await _row(db, keep) is None
        assert (await _row(db, edited)).managed_by is None
        assert await _actions(db, keep) == ["create", "delete"]
