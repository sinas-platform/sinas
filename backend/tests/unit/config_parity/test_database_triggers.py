"""Database triggers (CDC): one applier for every write channel.

The REST API and config apply had drifted apart: config checked no targets
and no ranges (a poll interval of 0 spun against the external database),
looked names up across all users (and so rewrote other people's triggers),
and told the CDC worker only "reload", which never restarted a running poll
loop. These pin that both channels now behave identically.
"""

import uuid

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.models.function import Function
from app.schemas.config import SinasConfig
from app.schemas.spec.database_trigger import DatabaseTriggerSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

FIELDS = (
    "database_connection_id", "schema_name", "table_name", "operations", "target_type",
    "function_namespace", "function_name", "pipeline_namespace", "pipeline_name",
    "poll_column", "poll_interval_seconds", "batch_size", "is_active",
)
CDC = "sinas:cdc:triggers"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def conn(db: AsyncSession) -> DatabaseConnection:
    row = DatabaseConnection(
        name=f"warehouse-{_uid()}", connection_type="postgresql", host="db", port=5432,
        database="dwh", username="reader",
    )
    db.add(row)
    await db.flush()
    return row


def _yaml_trigger(name: str, conn, fn: Function, **extra) -> dict:
    return {
        "name": name,
        "connectionName": conn.name,
        "tableName": "orders",
        "functionName": f"{fn.namespace}/{fn.name}",
        "pollColumn": "updated_at",
        "pollIntervalSeconds": 30,
        **extra,
    }


def _rest_trigger(name: str, conn, fn: Function, **extra) -> dict:
    return {
        "name": name,
        "database_connection_id": str(conn.id),
        "table_name": "orders",
        "function_namespace": fn.namespace,
        "function_name": fn.name,
        "poll_column": "updated_at",
        "poll_interval_seconds": 30,
        **extra,
    }


def _config(*triggers: dict, connections: list | None = None) -> SinasConfig:
    return SinasConfig.model_validate(
        {
            "apiVersion": "sinas.co/v1",
            "kind": "SinasConfig",
            "metadata": {"name": "cfg"},
            "spec": {"databaseTriggers": list(triggers), "databaseConnections": connections or []},
        }
    )


async def _apply(db, owner, *triggers, dry_run=False, connections=None):
    svc = ConfigApplyService(db, "cfg", owner_user_id=str(owner.id), auto_commit=False)
    result = await svc.apply_config(_config(*triggers, connections=connections), dry_run=dry_run)
    return svc, result


async def _rows(db: AsyncSession, name: str) -> list[DatabaseTrigger]:
    return list(
        (await db.execute(select(DatabaseTrigger).where(DatabaseTrigger.name == name))).scalars()
    )


# ------------------------------------------------------------------ spec


class TestTriggerSpec:
    def test_rest_and_config_shapes_are_the_same_spec(self):
        config = DatabaseTriggerSpec.model_validate({
            "name": "t", "connectionName": "c", "tableName": "orders",
            "targetType": "pipeline", "pipelineName": "crm/sync", "pollColumn": "id",
        })
        rest = DatabaseTriggerSpec.model_validate({
            "name": "t", "connection_name": "c", "table_name": "orders", "target_type": "pipeline",
            "target_namespace": "crm", "target_name": "sync", "poll_column": "id",
        })
        assert config == rest

    @pytest.mark.parametrize("bad", [
        {"pollIntervalSeconds": 0},  # the poll loop spun against the external DB
        {"batchSize": 0},  # the bookmark never advanced
        {"tableName": 'orders" ; drop table x; --'},  # breaks out of the quoted identifier
        {"operations": ["DELETE"]},
    ])
    def test_config_now_refuses_what_never_worked(self, bad):
        with pytest.raises(ValidationError):
            DatabaseTriggerSpec.model_validate({
                "name": "t", "connectionName": "c", "tableName": "orders",
                "functionName": "ns/f", "pollColumn": "id", **bad,
            })

    def test_config_keeps_accepting_what_ran(self):
        """The poller ignores operations, so an empty list ran fine; and
        config has always allowed intervals past the API's 3600s cap."""
        spec = DatabaseTriggerSpec.model_validate({
            "name": "t", "connectionName": "c", "tableName": "orders", "functionName": "ns/f",
            "pollColumn": "id", "operations": [], "pollIntervalSeconds": 7200,
        })
        assert (spec.operations, spec.poll_interval_seconds) == ([], 7200)


# ------------------------------------------------------------ one write path


class TestOneWritePath:
    async def test_api_and_config_write_identical_rows(
        self, client, db: AsyncSession, admin_user, conn, fn, published
    ):
        api_name, cfg_name = f"api-{_uid()}", f"cfg-{_uid()}"
        response = await client.post(
            "/api/v1/database-triggers", json=_rest_trigger(api_name, conn, fn),
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 201, response.text
        _, result = await _apply(db, admin_user, _yaml_trigger(cfg_name, conn, fn))
        assert result.success, result.errors

        [api_row], [cfg_row] = await _rows(db, api_name), await _rows(db, cfg_name)
        await db.refresh(api_row)
        assert {f: getattr(api_row, f) for f in FIELDS} == {f: getattr(cfg_row, f) for f in FIELDS}

    async def test_config_now_checks_the_target_and_connection(self, db: AsyncSession, admin_user, conn, fn):
        _, missing_fn = await _apply(
            db, admin_user, _yaml_trigger(f"t-{_uid()}", conn, fn, functionName="no/such")
        )
        assert "Function 'no/such' not found" in missing_fn.errors[0]
        _, missing_conn = await _apply(
            db, admin_user, {**_yaml_trigger(f"t-{_uid()}", conn, fn), "connectionName": "gone"}
        )
        assert "Database connection 'gone' not found" in missing_conn.errors[0]

    async def test_a_preview_accepts_a_connection_the_same_config_creates(
        self, db: AsyncSession, admin_user, conn, fn
    ):
        new_conn = {
            "name": f"new-{_uid()}", "connectionType": "postgresql", "host": "h", "port": 5432,
            "database": "d", "username": "u",
        }
        trigger = {**_yaml_trigger(f"t-{_uid()}", conn, fn), "connectionName": new_conn["name"]}
        _, result = await _apply(db, admin_user, trigger, dry_run=True, connections=[new_conn])
        assert result.success, result.errors

    async def test_another_admin_applying_the_same_config_updates_not_duplicates(
        self, db: AsyncSession, admin_user, test_user, conn, fn
    ):
        """Names are unique per owner; a lookup scoped to the applying user
        created a second trigger on the same table, so every change ran the
        target twice."""
        name = f"sync-{_uid()}"
        await _apply(db, test_user, _yaml_trigger(name, conn, fn))
        _, result = await _apply(db, admin_user, _yaml_trigger(name, conn, fn, batchSize=7))
        assert result.success, result.errors
        [row] = await _rows(db, name)
        assert (row.user_id, row.batch_size) == (test_user.id, 7)

    async def test_a_preview_does_not_count_on_connections_a_package_skips(
        self, db: AsyncSession, admin_user, conn, fn
    ):
        """Packages never create connections, so the install would fail."""
        new_conn = {
            "name": f"new-{_uid()}", "connectionType": "postgresql", "host": "h", "port": 5432,
            "database": "d", "username": "u",
        }
        svc = ConfigApplyService(
            db, "cfg", owner_user_id=str(admin_user.id), auto_commit=False,
            skip_resource_types={"databaseConnections"},
        )
        trigger = {**_yaml_trigger(f"t-{_uid()}", conn, fn), "connectionName": new_conn["name"]}
        result = await svc.apply_config(_config(trigger, connections=[new_conn]), dry_run=True)
        assert any("Database connection" in e for e in result.errors), result.errors


# ------------------------------------------------------------ CDC notifications


class TestCdcNotifications:
    async def test_config_announces_each_trigger_not_a_reload(
        self, db: AsyncSession, admin_user, conn, fn
    ):
        """"reload" never restarted a running poll loop, so a changed trigger
        kept its old settings until its next interval."""
        name = f"t-{_uid()}"
        svc, _ = await _apply(db, admin_user, _yaml_trigger(name, conn, fn))
        [row] = await _rows(db, name)
        assert [(e.channel, e.action, e.trigger_id) for e in svc.effects.pending] == [
            (CDC, "add", str(row.id))
        ]
        svc, _ = await _apply(db, admin_user, _yaml_trigger(name, conn, fn, batchSize=10))
        assert [e.action for e in svc.effects.pending] == ["update"]
        svc, _ = await _apply(db, admin_user, _yaml_trigger(name, conn, fn, batchSize=10))
        assert svc.effects.pending == []  # nothing changed, nothing announced

    async def test_api_publishes_after_commit(self, client, admin_user, conn, fn, published):
        name, headers = f"t-{_uid()}", auth_headers(admin_user)
        created = (
            await client.post("/api/v1/database-triggers", json=_rest_trigger(name, conn, fn), headers=headers)
        ).json()
        await client.patch(f"/api/v1/database-triggers/{name}", json={"batch_size": 5}, headers=headers)
        await client.delete(f"/api/v1/database-triggers/{name}", headers=headers)
        assert [m for c, m in published if c == CDC] == [
            {"action": "add", "trigger_id": created["id"]},
            {"action": "update", "trigger_id": created["id"]},
            {"action": "remove", "trigger_id": created["id"]},
        ]


# ------------------------------------------------------------------ REST PATCH


class TestPatch:
    async def test_changing_the_poll_column_resets_the_bookmark(
        self, client, db: AsyncSession, admin_user, conn, fn, published
    ):
        """The old bookmark was compared against (and cast to) the new
        column, which failed every poll when the types differed."""
        name, headers = f"t-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/database-triggers", json=_rest_trigger(name, conn, fn), headers=headers)
        [row] = await _rows(db, name)
        row.last_poll_value, row.error_message = "2026-09-30T10:00:00", "boom"
        await db.flush()

        await client.patch(f"/api/v1/database-triggers/{name}", json={"batch_size": 5}, headers=headers)
        await db.refresh(row)
        assert row.last_poll_value == "2026-09-30T10:00:00"  # unrelated change: kept

        await client.patch(f"/api/v1/database-triggers/{name}", json={"poll_column": "id"}, headers=headers)
        await db.refresh(row)
        assert (row.last_poll_value, row.error_message) == (None, None)

    async def test_a_rename_onto_an_existing_trigger_is_a_400_not_a_500(
        self, client, admin_user, conn, fn, published
    ):
        first, second, headers = f"a-{_uid()}", f"b-{_uid()}", auth_headers(admin_user)
        for name in (first, second):
            await client.post("/api/v1/database-triggers", json=_rest_trigger(name, conn, fn), headers=headers)
        response = await client.patch(
            f"/api/v1/database-triggers/{first}", json={"name": second}, headers=headers
        )
        assert response.status_code == 400
        assert response.json()["detail"] == f"Database trigger '{second}' already exists"

    async def test_switching_to_a_pipeline_needs_its_name(self, client, admin_user, conn, fn, published):
        name, headers = f"t-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/database-triggers", json=_rest_trigger(name, conn, fn), headers=headers)
        response = await client.patch(
            f"/api/v1/database-triggers/{name}", json={"target_type": "pipeline"}, headers=headers
        )
        assert response.status_code == 422
        assert "pipeline_name is required" in response.text

    async def test_every_api_change_is_recorded(self, client, db: AsyncSession, admin_user, conn, fn, published):
        name, headers = f"t-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/database-triggers", json=_rest_trigger(name, conn, fn), headers=headers)
        await client.patch(f"/api/v1/database-triggers/{name}", json={"is_active": False}, headers=headers)
        await client.delete(f"/api/v1/database-triggers/{name}", headers=headers)
        revisions = (
            await db.execute(
                select(ConfigRevision)
                .where(ConfigRevision.resource_kind == "databaseTriggers", ConfigRevision.resource_key == name)
                .order_by(ConfigRevision.id)
            )
        ).scalars().all()
        assert [r.action for r in revisions] == ["create", "update", "delete"]
        assert revisions[0].spec["connection_name"] == conn.name


# ------------------------------------------------------------------ export


class TestExport:
    async def test_paused_triggers_are_exported_and_re_apply_cleanly(
        self, db: AsyncSession, admin_user, conn, fn
    ):
        name = f"t-{_uid()}"
        await _apply(db, admin_user, _yaml_trigger(name, conn, fn, isActive=False))
        exported = await ConfigExportService(db, managed_only=True)._export_database_triggers()
        [mine] = [t for t in exported if t["name"] == name]
        assert (mine["isActive"], mine["connectionName"]) == (False, conn.name)

        _, result = await _apply(db, admin_user, mine)
        assert result.summary.unchanged.get("databaseTriggers") == 1
