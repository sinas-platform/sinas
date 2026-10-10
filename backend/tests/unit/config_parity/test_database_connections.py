"""Database connections: one applier for config and the REST API.

Config apply never adopted a connection made in the console, let two config
files overwrite each other's, couldn't declare read-only or switched-off
connections, and connections weren't exported at all. The built-in
connection stays the platform's. A password is never in history.
"""

import json
import uuid

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.models.config_revision import ConfigRevision
from app.models.database_connection import DatabaseConnection
from app.models.table_annotation import TableAnnotation
from app.schemas.config import SinasConfig
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers


def _name() -> str:
    return f"wh-{uuid.uuid4().hex[:8]}"


def _yaml(name: str, **extra) -> dict:
    return {
        "name": name, "connectionType": "postgresql", "host": "db.example", "port": 5432,
        "database": "sales", "username": "reader", **extra,
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


async def _row(db: AsyncSession, name: str) -> DatabaseConnection | None:
    row = (await db.execute(select(DatabaseConnection).where(DatabaseConnection.name == name))).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _revisions(db: AsyncSession, name: str) -> list[ConfigRevision]:
    return list((await db.execute(
        select(ConfigRevision)
        .where(ConfigRevision.resource_kind == "databaseConnections", ConfigRevision.resource_key == name)
        .order_by(ConfigRevision.id)
    )).scalars())


class TestConfigApply:
    async def test_password_kept_when_left_out_and_never_in_history(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, databaseConnections=[_yaml(name, password="pw-first-1")])).success
        again = await _apply(db, admin_user, databaseConnections=[_yaml(name, password="")])
        assert again.success and not again.summary.updated, again.summary
        assert (await _apply(db, admin_user, databaseConnections=[_yaml(name, password="pw-second-2")])).success
        row = await _row(db, name)
        assert encryption_service.decrypt(row.password) == "pw-second-2"
        revisions = await _revisions(db, name)
        assert [r.action for r in revisions] == ["create", "update"]
        assert "pw-" not in json.dumps([r.spec for r in revisions]) + json.dumps([r.changes for r in revisions])

    async def test_console_state_is_kept_unless_declared(self, client, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, databaseConnections=[_yaml(name)])).success
        row = await _row(db, name)
        h = auth_headers(admin_user)
        assert (await client.patch(f"/api/v1/database-connections/{row.id}", json={"read_only": True}, headers=h)).status_code == 200
        assert (await client.delete(f"/api/v1/database-connections/{row.id}", headers=h)).status_code == 204
        assert (await _apply(db, admin_user, databaseConnections=[_yaml(name, host="db2.example")])).success
        row = await _row(db, name)
        assert (row.host, row.read_only, row.is_active) == ("db2.example", True, False)
        assert (await _apply(db, admin_user, databaseConnections=[_yaml(name, isActive=True, readOnly=False)])).success
        row = await _row(db, name)
        assert (row.read_only, row.is_active) == (False, True)

    async def test_a_query_on_a_connection_from_the_same_config(self, db: AsyncSession, admin_user):
        name = _name()
        query = {
            "namespace": "sales", "name": f"q{uuid.uuid4().hex[:6]}", "connectionName": name,
            "operation": "read", "sql": "select 1",
        }
        for dry_run in (True, False):
            result = await _apply(db, admin_user, dry_run=dry_run, databaseConnections=[_yaml(name)], queries=[query])
            assert result.success, result.errors

    async def test_annotations_are_added_after_the_connection(self, db: AsyncSession, admin_user):
        name = _name()
        spec = _yaml(name, annotations=[{"tableName": "orders", "description": "One row per order"}])
        assert (await _apply(db, admin_user, databaseConnections=[spec])).success
        row = await _row(db, name)
        ann = (await db.execute(
            select(TableAnnotation).where(TableAnnotation.database_connection_id == row.id)
        )).scalar_one()
        assert (ann.table_name, ann.description) == ("orders", "One row per order")

    async def test_another_config_files_connection_is_left_alone(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, config_name="a", databaseConnections=[_yaml(name)])).success
        result = await _apply(db, admin_user, config_name="b", databaseConnections=[_yaml(name, host="b")])
        assert result.success and any("config 'a'" in w for w in result.warnings)
        assert (await _row(db, name)).host == "db.example"

    async def test_the_built_in_connection_is_left_alone(self, db: AsyncSession, admin_user):
        name = _name()
        db.add(DatabaseConnection(
            name=name, connection_type="postgresql", host="internal", port=5432,
            database="sinas_data", username="sinas", managed_by="system",
        ))
        await db.flush()
        result = await _apply(db, admin_user, databaseConnections=[_yaml(name)])
        assert result.success and result.warnings
        assert (await _row(db, name)).host == "internal"

    async def test_export_round_trips_and_leaves_out_built_in_and_password(self, db: AsyncSession, admin_user):
        name, builtin = _name(), _name()
        spec = _yaml(name, password="pw-export-3", readOnly=True, annotations=[{"tableName": "t", "displayName": "T"}])
        assert (await _apply(db, admin_user, databaseConnections=[spec])).success
        db.add(DatabaseConnection(
            name=builtin, connection_type="postgresql", host="internal", port=5432,
            database="sinas_data", username="sinas", managed_by="system",
        ))
        await db.flush()
        doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
        names = {c["name"]: c for c in doc["databaseConnections"]}
        assert builtin not in names and "password" not in names[name]
        assert names[name]["readOnly"] is True
        assert names[name]["annotations"] == [{"schemaName": "public", "tableName": "t", "displayName": "T"}]
        again = await _apply(db, admin_user, databaseConnections=[names[name]])
        assert again.success and not again.summary.updated, (again.errors, again.summary)
        with_secrets = yaml.safe_load(await ConfigExportService(db, include_secrets=True).export_config())["spec"]
        assert next(c for c in with_secrets["databaseConnections"] if c["name"] == name)["password"] == "pw-export-3"


class TestRest:
    async def test_crud_is_recorded_and_the_password_kept(self, client, db: AsyncSession, admin_user):
        name, h = _name(), auth_headers(admin_user)
        body = {
            "name": name, "connection_type": "postgresql", "host": "db.example", "port": 5432,
            "database": "sales", "username": "reader", "password": "pw-rest-1",
        }
        r = await client.post("/api/v1/database-connections", json=body, headers=h)
        assert r.status_code == 201, r.text
        r = await client.post("/api/v1/database-connections", json=body, headers=h)
        assert r.status_code == 400 and "already exists" in r.text
        conn_id = (await _row(db, name)).id
        r = await client.patch(f"/api/v1/database-connections/{conn_id}", json={"port": 6543, "password": ""}, headers=h)
        assert r.status_code == 200 and r.json()["port"] == 6543
        assert encryption_service.decrypt((await _row(db, name)).password) == "pw-rest-1"
        assert (await client.delete(f"/api/v1/database-connections/{conn_id}", headers=h)).status_code == 204
        assert [r.action for r in await _revisions(db, name)] == ["create", "update", "update"]

    async def test_the_built_in_connection_stays_the_platforms(self, client, db: AsyncSession, admin_user):
        name, h = _name(), auth_headers(admin_user)
        db.add(DatabaseConnection(
            name=name, connection_type="postgresql", host="internal", port=5432,
            database="sinas_data", username="sinas", managed_by="system",
        ))
        await db.flush()
        conn_id = (await _row(db, name)).id
        r = await client.patch(f"/api/v1/database-connections/{conn_id}", json={"host": "evil"}, headers=h)
        assert r.status_code == 400
        r = await client.patch(f"/api/v1/database-connections/{conn_id}", json={"read_only": True}, headers=h)
        assert r.status_code == 200, r.text
        row = await _row(db, name)
        assert (row.read_only, row.managed_by) == (True, "system")  # not detached


def test_a_blank_config_password_is_unset():
    from app.schemas.config import DatabaseConnectionConfig

    assert DatabaseConnectionConfig.model_validate(_yaml("x", password="")).password is None


@pytest.mark.parametrize("field", ["annotations"])
def test_annotations_are_not_part_of_the_spec(field):
    from app.schemas.spec.database_connection import DatabaseConnectionSpec

    spec = DatabaseConnectionSpec.model_validate({**_yaml("x"), field: [{"tableName": "t"}]})
    assert field not in spec.model_dump()


class TestReviewFixes:
    async def test_a_restore_keeps_the_built_in_connections_platform_fields(self, client, db: AsyncSession, admin_user):
        name, h = _name(), auth_headers(admin_user)
        db.add(DatabaseConnection(
            name=name, connection_type="postgresql", host="old-host", port=5432,
            database="sinas_data", username="sinas", managed_by="system",
        ))
        await db.flush()
        row = await _row(db, name)
        await client.patch(f"/api/v1/database-connections/{row.id}", json={"read_only": True}, headers=h)
        revision = (await _revisions(db, name))[-1]
        row.host = "new-host"  # startup follows a deployment change
        await db.flush()
        r = await client.post(f"/api/v1/config/history/{revision.id}/restore", headers=h)
        assert r.status_code == 200, r.text
        row = await _row(db, name)
        assert (row.host, row.read_only) == ("new-host", True)

    async def test_an_unchanged_password_is_not_re_encrypted(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, databaseConnections=[_yaml(name, password="pw-same")])).success
        before = (await _row(db, name)).password
        assert (await _apply(db, admin_user, databaseConnections=[_yaml(name, readOnly=True)])).success
        row = await _row(db, name)
        assert row.read_only is True and row.password == before
