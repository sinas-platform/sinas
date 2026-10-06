"""Skills, templates and queries: one applier per kind for every write channel.

Config apply kept its own copy of each write (and re-enabled whatever an
operator had switched off), REST edits were never recorded, a template PATCH
that nulled its HTML was a 500, a config query with a misspelt operation ran
through the write path, and the default-template seed crashed startup once
anyone created an `otp_email` in another namespace.
"""

import uuid

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.database_connection import DatabaseConnection
from app.models.query import Query
from app.models.skill import Skill
from app.models.template import Template
from app.schemas.config import SinasConfig
from app.schemas.spec.query import QuerySpec
from app.schemas.spec.skill import SkillSpec
from app.schemas.spec.template import TemplateSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

NS = "kb"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def connection(db: AsyncSession) -> DatabaseConnection:
    conn = DatabaseConnection(
        name=f"warehouse-{_uid()}", connection_type="postgresql",
        host="localhost", port=5432, database="test", username="test",
    )
    db.add(conn)
    await db.flush()
    return conn


def _yaml_skill(name: str, **extra) -> dict:
    return {"namespace": NS, "name": name, "description": "Refunds", "content": "# Steps", **extra}


def _yaml_template(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Receipt", "title": "Your order {{ id }}",
        "htmlContent": "<p>{{ id }}</p>", "textContent": "Order {{ id }}",
        "variableSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
        **extra,
    }


def _yaml_query(name: str, connection: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Orders", "connectionName": connection,
        "operation": "read", "sql": "select * from orders where id = :id",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}},
        "timeoutMs": 2000, "maxRows": 50, **extra,
    }


def _rest_template(name: str) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Receipt", "title": "Your order {{ id }}",
        "html_content": "<p>{{ id }}</p>", "text_content": "Order {{ id }}",
        "variable_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
    }


def _rest_query(name: str, connection_id) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Orders",
        "database_connection_id": str(connection_id), "operation": "read",
        "sql": "select * from orders where id = :id",
        "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
        "timeout_ms": 2000, "max_rows": 50,
    }


def _config(**spec) -> SinasConfig:
    return SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
        "spec": spec,
    })


async def _apply(db, owner, dry_run=False, **spec):
    svc = ConfigApplyService(
        db, "cfg", owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(_config(**spec), dry_run=dry_run)


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
        assert TemplateSpec.model_validate(_yaml_template("t")) == TemplateSpec.model_validate(
            _rest_template("t")
        )
        rest = {**_rest_query("q", uuid.uuid4()), "connection_name": "wh"}
        del rest["database_connection_id"]
        assert QuerySpec.model_validate(_yaml_query("q", "wh")) == QuerySpec.model_validate(rest)

    @pytest.mark.parametrize("bad", [
        {"operation": "select"},  # ran as a write: no rows back, no LIMIT
        {"timeoutMs": 0},  # every call timed out
        {"maxRows": 0},  # LIMIT 0: never a row
        {"namespace": "a/b"},  # no "ns/name" reference could reach it
    ])
    def test_a_query_refuses_what_never_worked(self, bad):
        with pytest.raises(ValidationError):
            QuerySpec.model_validate({**_yaml_query("q", "wh"), **bad})

    def test_operation_case_is_forgiven(self):
        assert QuerySpec.model_validate(_yaml_query("q", "wh", operation="Read")).operation == "read"

    def test_empty_template_fields_are_one_form(self):
        spec = TemplateSpec.model_validate(
            _yaml_template("t", description="", textContent="", variableSchema=None)
        )
        assert (spec.description, spec.text_content, spec.variable_schema) == (None, None, {})

    def test_a_skill_needs_its_text(self):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate({"namespace": NS, "name": "s", "description": "d"})


# ------------------------------------------------------------------ one write path


class TestOneWritePath:
    async def test_api_and_config_write_identical_templates(self, client, db: AsyncSession, admin_user):
        api, cfg = f"api-{_uid()}", f"cfg-{_uid()}"
        response = await client.post(
            "/api/v1/templates", json=_rest_template(api), headers=auth_headers(admin_user)
        )
        assert response.status_code == 201, response.text
        result = await _apply(db, admin_user, templates=[_yaml_template(cfg)])
        assert result.success, result.errors
        fields = ("description", "title", "html_content", "text_content", "variable_schema", "is_active")
        api_row, cfg_row = await _row(db, Template, api), await _row(db, Template, cfg)
        assert {f: getattr(api_row, f) for f in fields} == {f: getattr(cfg_row, f) for f in fields}
        assert cfg_row.created_by == admin_user.id

    async def test_api_and_config_write_identical_queries(
        self, client, db: AsyncSession, admin_user, connection
    ):
        api, cfg = f"api-{_uid()}", f"cfg-{_uid()}"
        response = await client.post(
            "/api/v1/queries", json=_rest_query(api, connection.id), headers=auth_headers(admin_user)
        )
        assert response.status_code == 201, response.text
        result = await _apply(db, admin_user, queries=[_yaml_query(cfg, connection.name)])
        assert result.success, result.errors
        fields = (
            "description", "database_connection_id", "operation", "sql", "input_schema",
            "output_schema", "timeout_ms", "max_rows", "is_active",
        )
        api_row, cfg_row = await _row(db, Query, api), await _row(db, Query, cfg)
        assert {f: getattr(api_row, f) for f in fields} == {f: getattr(cfg_row, f) for f in fields}

    async def test_every_api_change_is_recorded(self, client, db: AsyncSession, admin_user):
        name, headers = f"s-{_uid()}", auth_headers(admin_user)
        body = {"namespace": NS, "name": name, "description": "Refunds", "content": "# Steps"}
        assert (await client.post("/api/v1/skills", json=body, headers=headers)).status_code == 201
        response = await client.put(f"/api/v1/skills/{NS}/{name}", json={"content": "# New"}, headers=headers)
        assert response.status_code == 200, response.text
        assert (await client.delete(f"/api/v1/skills/{NS}/{name}", headers=headers)).status_code == 204
        assert await _actions(db, "skills", name) == ["create", "update", "delete"]

    async def test_a_rename_onto_an_existing_skill_is_a_400(self, client, admin_user):
        first, second, headers = f"a-{_uid()}", f"b-{_uid()}", auth_headers(admin_user)
        for name in (first, second):
            body = {"namespace": NS, "name": name, "description": "d", "content": "c"}
            await client.post("/api/v1/skills", json=body, headers=headers)
        response = await client.put(f"/api/v1/skills/{NS}/{first}", json={"name": second}, headers=headers)
        assert response.status_code == 400
        assert response.json()["detail"] == f"Skill '{NS}/{second}' already exists"

    async def test_a_manual_edit_detaches_a_config_managed_query(
        self, client, db: AsyncSession, admin_user, connection
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, queries=[_yaml_query(name, connection.name)])
        response = await client.put(
            f"/api/v1/queries/{NS}/{name}", json={"max_rows": 10}, headers=auth_headers(admin_user)
        )
        assert response.status_code == 200, response.text
        row = await _row(db, Query, name)
        assert (row.max_rows, row.managed_by) == (10, None)

    async def test_an_edit_keeps_the_connection_it_has(
        self, client, db: AsyncSession, admin_user, connection, monkeypatch
    ):
        """Re-resolving the name on every edit could follow it to another
        database, should the connection be renamed and the name reused
        meanwhile. Only a change of connection resolves a name."""
        from app.services.resources.queries import QueryApplier

        name, headers = f"q-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/queries", json=_rest_query(name, connection.id), headers=headers)
        resolved: list[str] = []
        original = QueryApplier._connection_id

        async def spy(self, ctx, conn_name):
            resolved.append(conn_name)
            return await original(self, ctx, conn_name)

        monkeypatch.setattr(QueryApplier, "_connection_id", spy)
        response = await client.put(
            f"/api/v1/queries/{NS}/{name}", json={"description": "Renamed"}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert resolved == []
        assert (await _row(db, Query, name)).database_connection_id == connection.id

    async def test_config_reports_a_missing_connection(self, db: AsyncSession, admin_user):
        name = f"q-{_uid()}"
        result = await _apply(db, admin_user, queries=[_yaml_query(name, "nope")])
        assert not result.success
        assert "Database connection 'nope' not found" in result.errors[0]

    async def test_a_preview_accepts_a_connection_the_same_config_declares(
        self, db: AsyncSession, admin_user
    ):
        conn = f"wh-{_uid()}"
        result = await _apply(
            db, admin_user, dry_run=True,
            databaseConnections=[{
                "name": conn, "connectionType": "postgresql", "host": "h", "port": 5432,
                "database": "d", "username": "u",
            }],
            queries=[_yaml_query(f"q-{_uid()}", conn)],
        )
        assert result.success, result.errors


class TestTemplatePatch:
    async def test_nulling_the_html_is_a_422_not_a_500(self, client, admin_user):
        headers = auth_headers(admin_user)
        created = (await client.post(
            "/api/v1/templates", json=_rest_template(f"t-{_uid()}"), headers=headers
        )).json()
        response = await client.patch(
            f"/api/v1/templates/{created['id']}", json={"html_content": None}, headers=headers
        )
        assert response.status_code == 422

    async def test_null_still_clears_an_optional_field(self, client, admin_user):
        headers = auth_headers(admin_user)
        created = (await client.post(
            "/api/v1/templates", json=_rest_template(f"t-{_uid()}"), headers=headers
        )).json()
        response = await client.patch(
            f"/api/v1/templates/{created['id']}", json={"text_content": None}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["text_content"] is None
        assert response.json()["html_content"] == "<p>{{ id }}</p>"


# ------------------------------------------------------------------ operator state + export


class TestIsActiveAndExport:
    async def test_a_re_apply_does_not_re_enable_a_disabled_skill(
        self, client, db: AsyncSession, admin_user
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, skills=[_yaml_skill(name)])
        await client.put(
            f"/api/v1/skills/{NS}/{name}", json={"is_active": False}, headers=auth_headers(admin_user)
        )
        result = await _apply(db, admin_user, skills=[_yaml_skill(name)])
        assert result.success, result.errors
        assert (await _row(db, Skill, name)).is_active is False

    async def test_a_declared_state_is_applied(self, db: AsyncSession, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, templates=[_yaml_template(name)])
        await _apply(db, admin_user, templates=[_yaml_template(name, isActive=False)])
        assert (await _row(db, Template, name)).is_active is False

    async def test_export_keeps_disabled_templates_and_round_trips(self, db: AsyncSession, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, templates=[_yaml_template(name, isActive=False)])
        exported = await ConfigExportService(db, managed_only=True)._export_templates()
        [mine] = [t for t in exported if t["name"] == name]
        assert mine["isActive"] is False
        result = await _apply(db, admin_user, templates=[mine])
        assert result.summary.unchanged.get("templates") == 1

    async def test_query_export_names_the_connection_and_round_trips(
        self, db: AsyncSession, admin_user, connection
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, queries=[_yaml_query(name, connection.name)])
        exported = await ConfigExportService(db, managed_only=True)._export_queries()
        [mine] = [q for q in exported if q["name"] == name]
        assert mine["connectionName"] == connection.name
        result = await _apply(db, admin_user, queries=[mine])
        assert result.summary.unchanged.get("queries") == 1


# ------------------------------------------------------------------ packages + seed


class TestPackagesAndSeed:
    async def test_uninstall_records_each_deletion(
        self, db: AsyncSession, admin_user, published, connection
    ):
        from app.services.package_service import PackageService

        pkg, name = f"pkg-{_uid()}", f"r-{_uid()}"
        package = "\n".join([
            "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
            "package:", f"  name: {pkg}", '  version: "1.0.0"', "spec:",
            "  skills:", f"    - {{namespace: {NS}, name: {name}, description: d, content: c}}",
            "  templates:", f"    - {{namespace: {NS}, name: {name}, htmlContent: '<p/>'}}",
            "  queries:",
            f"    - {{namespace: {NS}, name: {name}, connectionName: {connection.name},"
            " operation: read, sql: select 1}",
        ]) + "\n"
        service = PackageService(db)
        _, installed = await service.install(package, str(admin_user.id))
        assert installed.success, installed.errors
        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert (counts.get("skills"), counts.get("templates"), counts.get("queries")) == (1, 1, 1)
        for kind in ("skills", "templates", "queries"):
            assert await _actions(db, kind, name) == ["create", "delete"]

    async def test_the_seed_ignores_an_otp_email_in_another_namespace(self, db: AsyncSession):
        from app.core.templates import initialize_default_templates

        db.add(Template(namespace=f"other-{_uid()}", name="otp_email", html_content="<p/>"))
        await db.flush()
        await initialize_default_templates(db)  # MultipleResultsFound before
        default = (await db.execute(
            select(Template).where(Template.namespace == "default", Template.name == "otp_email")
        )).scalar_one()
        assert "{{ otp_code }}" in default.html_content
