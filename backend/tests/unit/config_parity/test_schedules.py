"""Schedules: one applier for every write channel (design §5 pilot).

The REST API and config apply used to be two hand-written write paths that had
drifted apart. These pin that both now behave identically — same rows, same
validation, same scheduler notifications, same ownership rules — and that every
change through either is recorded in the change history.
"""

import json
import uuid

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.config_revision import ConfigRevision
from app.models.function import Function
from app.models.schedule import ScheduledJob
from app.schemas.config import SinasConfig
from app.schemas.spec.schedule import ScheduleSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from app.services.resources import SchedulerJobChanged
from tests.conftest import auth_headers

FIELDS = (
    "schedule_type", "target_namespace", "target_name", "description",
    "cron_expression", "timezone", "input_data", "content", "is_active",
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ------------------------------------------------------------------- fixtures


@pytest.fixture
def published(monkeypatch):
    """Capture what reaches Redis; everything else is a harmless no-op."""
    sent: list[tuple[str, dict]] = []

    class _Redis:
        async def publish(self, channel, payload):
            sent.append((channel, json.loads(payload)))

        def __getattr__(self, _name):
            async def _noop(*args, **kwargs):
                return None

            return _noop

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr("app.core.redis.get_redis", fake_get_redis)
    return sent


@pytest_asyncio.fixture
async def fn(db: AsyncSession, admin_user) -> Function:
    function = Function(
        user_id=admin_user.id,
        namespace=f"ns{_uid()}",
        name="nightly",
        code="def handler(input, context):\n    return {}",
        input_schema={},
        output_schema={},
    )
    db.add(function)
    await db.flush()
    return function


@pytest_asyncio.fixture
async def agent(db: AsyncSession, admin_user) -> Agent:
    row = Agent(
        user_id=admin_user.id,
        namespace=f"ns{_uid()}",
        name="digest",
        system_prompt="Summarise.",
    )
    db.add(row)
    await db.flush()
    return row


def _yaml_schedule(name: str, fn: Function, **extra) -> dict:
    return {
        "name": name,
        "scheduleType": "function",
        "functionName": f"{fn.namespace}/{fn.name}",
        "cronExpression": "0 3 * * *",
        "description": "Nightly run",
        "inputData": {"full": True},
        **extra,
    }


def _rest_schedule(name: str, fn: Function, **extra) -> dict:
    return {
        "name": name,
        "schedule_type": "function",
        "target_namespace": fn.namespace,
        "target_name": fn.name,
        "cron_expression": "0 3 * * *",
        "description": "Nightly run",
        "input_data": {"full": True},
        **extra,
    }


def _config(*schedules: dict) -> SinasConfig:
    return SinasConfig.model_validate(
        {
            "apiVersion": "sinas.co/v1",
            "kind": "SinasConfig",
            "metadata": {"name": "cfg"},
            "spec": {"schedules": list(schedules)},
        }
    )


async def _apply(db, owner, *schedules, managed_by="config", config_name="cfg", dry_run=False):
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by=managed_by, auto_commit=False
    )
    result = await svc.apply_config(_config(*schedules), dry_run=dry_run)
    return svc, result


async def _row(db: AsyncSession, name: str) -> ScheduledJob | None:
    return (
        await db.execute(select(ScheduledJob).where(ScheduledJob.name == name))
    ).scalar_one_or_none()


async def _revisions(db: AsyncSession, **filters) -> list[ConfigRevision]:
    stmt = select(ConfigRevision).order_by(ConfigRevision.id)
    for column, value in filters.items():
        stmt = stmt.where(getattr(ConfigRevision, column) == value)
    return list((await db.execute(stmt)).scalars())


# ----------------------------------------------------------------------- spec


class TestScheduleSpec:
    def test_rest_and_config_shapes_are_the_same_spec(self):
        rest = ScheduleSpec.model_validate({
            "name": "n", "schedule_type": "agent", "target_namespace": "team",
            "target_name": "bot", "cron_expression": "*/5 * * * *", "content": "go",
        })
        config = ScheduleSpec.model_validate({
            "name": "n", "scheduleType": "agent", "agentName": "team/bot",
            "cronExpression": "*/5 * * * *", "content": "go",
        })
        assert rest == config

    def test_a_config_reference_without_namespace_means_default(self):
        spec = ScheduleSpec.model_validate(
            {"name": "n", "functionName": "job", "cronExpression": "0 * * * *"}
        )
        assert (spec.target_namespace, spec.target_name) == ("default", "job")

    @pytest.mark.parametrize("shape", ["rest", "config"])
    def test_invalid_cron_is_refused_on_both_shapes(self, shape):
        data = (
            {"name": "n", "target_name": "f", "cron_expression": "every day"}
            if shape == "rest"
            else {"name": "n", "functionName": "f", "cronExpression": "every day"}
        )
        with pytest.raises(ValidationError, match="Invalid cron expression"):
            ScheduleSpec.model_validate(data)

    def test_agent_schedules_need_content(self):
        with pytest.raises(ValidationError, match="content is required"):
            ScheduleSpec.model_validate(
                {"name": "n", "scheduleType": "agent", "agentName": "a/b", "cronExpression": "0 * * * *"}
            )

    def test_the_reference_for_the_type_is_required(self):
        with pytest.raises(ValidationError, match="pipelineName is required"):
            ScheduleSpec.model_validate(
                {"name": "n", "scheduleType": "pipeline", "functionName": "a/b",
                 "cronExpression": "0 * * * *"}
            )

    def test_unknown_fields_are_refused(self):
        with pytest.raises(ValidationError):
            ScheduleSpec.model_validate(
                {"name": "n", "functionName": "f", "cronExpression": "0 * * * *", "cronExpresion": "x"}
            )

    def test_config_form_round_trips(self):
        spec = ScheduleSpec.model_validate({
            "name": "n", "functionName": "ns/f", "cronExpression": "0 * * * *",
            "description": "d", "isActive": False, "inputData": {"a": 1},
        })
        assert ScheduleSpec.model_validate(spec.to_config()) == spec
        assert spec.to_config()["isActive"] is False


# ------------------------------------------------------------- two channels


class TestOneWritePath:
    async def test_api_and_config_write_identical_rows(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        via_api, via_config = f"api-{_uid()}", f"cfg-{_uid()}"
        response = await client.post(
            "/api/v1/schedules", json=_rest_schedule(via_api, fn), headers=auth_headers(admin_user)
        )
        assert response.status_code == 201, response.text
        _, result = await _apply(db, admin_user, _yaml_schedule(via_config, fn))
        assert result.errors == []

        api_row, config_row = await _row(db, via_api), await _row(db, via_config)
        assert {f: getattr(api_row, f) for f in FIELDS} == {f: getattr(config_row, f) for f in FIELDS}

    async def test_config_no_longer_drops_description(self, db: AsyncSession, admin_user, fn):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn, description="Kept now"))
        assert (await _row(db, name)).description == "Kept now"


class TestSchedulerNotifications:
    async def test_config_created_schedules_are_announced_as_add(
        self, db: AsyncSession, admin_user, fn
    ):
        """Regression: config apply announced new schedules as "create", which
        the scheduler doesn't understand — they never ran until a restart."""
        name = f"cfg-{_uid()}"
        svc, _ = await _apply(db, admin_user, _yaml_schedule(name, fn))
        row = await _row(db, name)
        assert svc.effects.pending == [SchedulerJobChanged("add", str(row.id))]

    async def test_api_publishes_add_update_remove_after_commit(
        self, client, admin_user, fn, published
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        created = (await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)).json()
        await client.patch(f"/api/v1/schedules/{name}", json={"cron_expression": "0 4 * * *"}, headers=headers)
        await client.delete(f"/api/v1/schedules/{name}", headers=headers)

        jobs = [payload for channel, payload in published if channel == "sinas:scheduler:jobs"]
        assert jobs == [
            {"action": "add", "job_id": created["id"]},
            {"action": "update", "job_id": created["id"]},
            {"action": "remove", "job_id": created["id"]},
        ]

    async def test_a_failed_config_apply_announces_nothing(
        self, db: AsyncSession, admin_user, fn, monkeypatch
    ):
        svc = ConfigApplyService(db, "cfg", owner_user_id=str(admin_user.id), auto_commit=False)

        async def explode(*args, **kwargs):
            raise RuntimeError("database fell over")

        # A fatal error after the schedule was applied rolls the whole thing back
        monkeypatch.setattr(
            "app.services.config_apply.service.apply_database_triggers", explode
        )
        result = await svc.apply_config(_config(_yaml_schedule(f"cfg-{_uid()}", fn)))
        assert result.success is False
        assert svc.effects.pending == []

    async def test_a_no_op_reapply_announces_nothing(self, db: AsyncSession, admin_user, fn):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn))
        svc, result = await _apply(db, admin_user, _yaml_schedule(name, fn))
        assert result.summary.unchanged.get("schedules") == 1
        assert svc.effects.pending == []


# ------------------------------------------------------------------ history


class TestChangeHistory:
    async def test_every_api_change_is_recorded(self, client, db: AsyncSession, admin_user, fn, published):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        created = (await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)).json()
        await client.patch(f"/api/v1/schedules/{name}", json={"is_active": False}, headers=headers)
        await client.delete(f"/api/v1/schedules/{name}", headers=headers)

        revisions = await _revisions(db, resource_id=uuid.UUID(created["id"]))
        assert [r.action for r in revisions] == ["create", "update", "delete"]
        assert {r.origin for r in revisions} == {"api"}
        assert {r.actor_user_id for r in revisions} == {admin_user.id}
        assert revisions[0].actor_email == admin_user.email
        # An update records exactly what changed
        assert revisions[1].changes == {"is_active": {"from": True, "to": False}}
        # A delete keeps the last state, so the schedule can be inspected later
        assert revisions[2].spec["cron_expression"] == "0 3 * * *"

    async def test_config_and_package_changes_are_recorded_with_their_origin(
        self, db: AsyncSession, admin_user, fn
    ):
        via_config, via_package = f"cfg-{_uid()}", f"pkg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(via_config, fn))
        await _apply(db, admin_user, _yaml_schedule(via_package, fn), managed_by="pkg:demo")

        [config_rev] = await _revisions(db, resource_key=via_config)
        [package_rev] = await _revisions(db, resource_key=via_package)
        assert (config_rev.origin, config_rev.managed_by, config_rev.config_name) == ("config", "config", "cfg")
        assert (package_rev.origin, package_rev.managed_by) == ("package", "pkg:demo")

    async def test_no_op_and_dry_run_record_nothing(self, db: AsyncSession, admin_user, fn):
        name = f"cfg-{_uid()}"
        _, dry = await _apply(db, admin_user, _yaml_schedule(name, fn), dry_run=True)
        assert dry.summary.created.get("schedules") == 1
        assert await _row(db, name) is None
        assert await _revisions(db, resource_key=name) == []

        await _apply(db, admin_user, _yaml_schedule(name, fn))
        await _apply(db, admin_user, _yaml_schedule(name, fn))
        assert len(await _revisions(db, resource_key=name)) == 1

    async def test_history_follows_a_rename(self, client, db: AsyncSession, admin_user, fn, published):
        old, new, headers = f"api-{_uid()}", f"api-{_uid()}", auth_headers(admin_user)
        created = (await client.post("/api/v1/schedules", json=_rest_schedule(old, fn), headers=headers)).json()
        response = await client.patch(f"/api/v1/schedules/{old}", json={"name": new}, headers=headers)
        assert response.status_code == 200, response.text

        revisions = await _revisions(db, resource_id=uuid.UUID(created["id"]))
        assert [r.resource_key for r in revisions] == [old, new]
        assert revisions[1].changes == {"name": {"from": old, "to": new}}

    async def test_history_api(self, client, db: AsyncSession, admin_user, test_user, fn, published):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        await client.patch(f"/api/v1/schedules/{name}", json={"timezone": "Europe/Amsterdam"}, headers=headers)

        listed = await client.get(f"/api/v1/config/history?kind=schedules&key={name}", headers=headers)
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert [r["action"] for r in body] == ["update", "create"]  # newest first
        assert all(r["spec"] is None for r in body)  # opt-in

        detail = await client.get(f"/api/v1/config/history/{body[0]['id']}", headers=headers)
        assert detail.json()["spec"]["timezone"] == "Europe/Amsterdam"

        page = await client.get(
            f"/api/v1/config/history?key={name}&before={body[0]['id']}", headers=headers
        )
        assert [r["action"] for r in page.json()] == ["create"]

        denied = await client.get("/api/v1/config/history", headers=auth_headers(test_user))
        assert denied.status_code == 403


# --------------------------------------------------------------- validation


class TestValidationParity:
    async def test_config_now_refuses_invalid_cron(self, db: AsyncSession, admin_user, fn):
        name = f"cfg-{_uid()}"
        _, result = await _apply(db, admin_user, _yaml_schedule(name, fn, cronExpression="daily"))
        assert any("Invalid cron expression" in e for e in result.errors)
        assert await _row(db, name) is None

    async def test_config_now_checks_the_target_exists(self, db: AsyncSession, admin_user):
        name = f"cfg-{_uid()}"
        _, result = await _apply(db, admin_user, {
            "name": name, "functionName": "nowhere/ghost", "cronExpression": "0 * * * *",
        })
        assert any("Function 'nowhere/ghost' not found" in e for e in result.errors)
        assert await _row(db, name) is None

    async def test_one_bad_schedule_does_not_sink_the_others(self, db: AsyncSession, admin_user, fn):
        bad, good = f"cfg-{_uid()}", f"cfg-{_uid()}"
        _, result = await _apply(
            db, admin_user,
            {"name": bad, "functionName": "nowhere/ghost", "cronExpression": "0 * * * *"},
            _yaml_schedule(good, fn),
        )
        assert len(result.errors) == 1
        assert await _row(db, good) is not None

    async def test_api_patch_to_a_missing_pipeline_is_refused(self, client, admin_user, fn, published):
        """The update path used to check functions and agents, never pipelines."""
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        response = await client.patch(
            f"/api/v1/schedules/{name}",
            json={"schedule_type": "pipeline", "target_namespace": "nowhere", "target_name": "ghost"},
            headers=headers,
        )
        assert response.status_code == 404
        assert "Pipeline 'nowhere/ghost' not found" in response.json()["detail"]

    async def test_a_schedule_with_a_deleted_target_can_still_be_paused(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        fn.is_active = False
        await db.flush()

        response = await client.patch(f"/api/v1/schedules/{name}", json={"is_active": False}, headers=headers)
        assert response.status_code == 200, response.text

    async def test_api_create_conflict_keeps_its_400(self, client, admin_user, fn, published):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        again = await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        assert again.status_code == 400
        assert again.json()["detail"] == f"Schedule '{name}' already exists"

    async def test_renaming_onto_an_existing_schedule_is_a_400_not_a_500(
        self, client, admin_user, fn, published
    ):
        first, second, headers = f"api-{_uid()}", f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(first, fn), headers=headers)
        await client.post("/api/v1/schedules", json=_rest_schedule(second, fn), headers=headers)
        response = await client.patch(f"/api/v1/schedules/{second}", json={"name": first}, headers=headers)
        assert response.status_code == 400


# ---------------------------------------------------------------- ownership


class TestOwnership:
    async def test_a_manual_edit_detaches_a_config_managed_schedule(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn))
        assert (await _row(db, name)).managed_by == "config"

        await client.patch(
            f"/api/v1/schedules/{name}", json={"cron_expression": "0 5 * * *"},
            headers=auth_headers(admin_user),
        )
        row = await _row(db, name)
        await db.refresh(row)
        assert (row.managed_by, row.config_name, row.config_checksum) == (None, None, None)

    async def test_a_no_op_api_patch_does_not_detach(self, client, db: AsyncSession, admin_user, fn, published):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn))
        await client.patch(
            f"/api/v1/schedules/{name}", json={"cron_expression": "0 3 * * *"},
            headers=auth_headers(admin_user),
        )
        row = await _row(db, name)
        await db.refresh(row)
        assert row.managed_by == "config"

    async def test_config_does_not_overwrite_a_package_schedule(self, db: AsyncSession, admin_user, fn):
        name = f"pkg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn), managed_by="pkg:demo")
        _, result = await _apply(db, admin_user, _yaml_schedule(name, fn, cronExpression="0 9 * * *"))
        assert any("managed by 'pkg:demo'" in w for w in result.warnings)
        assert (await _row(db, name)).cron_expression == "0 3 * * *"

    async def test_a_paused_package_schedule_is_not_taken_over(self, db: AsyncSession, admin_user, fn):
        """For schedules is_active means paused, not deleted: the shared
        config-apply helper let any other manager claim a paused one."""
        name = f"pkg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn, isActive=False), managed_by="pkg:demo")
        await _apply(db, admin_user, _yaml_schedule(name, fn, cronExpression="0 9 * * *"))
        row = await _row(db, name)
        assert (row.managed_by, row.cron_expression, row.is_active) == ("pkg:demo", "0 3 * * *", False)

    async def test_config_adopts_and_stamps_a_manual_schedule(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        name = f"api-{_uid()}"
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=auth_headers(admin_user))
        await _apply(db, admin_user, _yaml_schedule(name, fn))
        row = await _row(db, name)
        await db.refresh(row)
        assert (row.managed_by, row.config_name) == ("config", "cfg")


# -------------------------------------------------------------------- export


class TestExport:
    async def test_paused_schedules_are_exported(self, db: AsyncSession, admin_user, fn):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn, isActive=False))
        exported = await ConfigExportService(db, managed_only=True)._export_schedules()
        [mine] = [s for s in exported if s["name"] == name]
        assert mine["isActive"] is False
        assert mine["description"] == "Nightly run"

    async def test_export_then_apply_changes_nothing(self, db: AsyncSession, admin_user, fn):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn))
        exported = await ConfigExportService(db, managed_only=True)._export_schedules()
        [mine] = [s for s in exported if s["name"] == name]

        svc, result = await _apply(db, admin_user, mine)
        assert result.summary.unchanged.get("schedules") == 1
        assert len(await _revisions(db, resource_key=name)) == 1
