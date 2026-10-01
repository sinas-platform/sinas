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
        # Field names always; values and spec only on request (they can be large)
        assert body[0]["changed_fields"] == ["timezone"]
        assert all(r["changes"] is None and r["spec"] is None for r in body)

        detailed = await client.get(
            f"/api/v1/config/history?key={name}&include_details=true", headers=headers
        )
        assert detailed.json()[0]["changes"] == {"timezone": {"from": "UTC", "to": "Europe/Amsterdam"}}

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

    async def test_one_bad_schedule_fails_the_whole_apply(self, db: AsyncSession, admin_user, fn):
        """All or nothing: the error is reported for the bad one, and the
        apply as a whole fails rather than committing the rest."""
        bad, good = f"cfg-{_uid()}", f"cfg-{_uid()}"
        svc, result = await _apply(
            db, admin_user,
            {"name": bad, "functionName": "nowhere/ghost", "cronExpression": "0 * * * *"},
            _yaml_schedule(good, fn),
        )
        assert result.success is False
        assert len(result.errors) == 1 and bad in result.errors[0]
        assert svc.effects.pending == []  # nothing will be announced

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


# ------------------------------------------------- review follow-ups (#206)


async def _legacy_row(db: AsyncSession, owner, fn, **overrides) -> ScheduledJob:
    """A row as the config path used to write it, before it validated."""
    fields = {
        "user_id": owner.id,
        "name": f"legacy-{_uid()}",
        "schedule_type": "function",
        "target_namespace": fn.namespace,
        "target_name": fn.name,
        "cron_expression": "not a cron",
        "timezone": "UTC",
        "input_data": {},
        "is_active": True,
    }
    row = ScheduledJob(**{**fields, **overrides})
    db.add(row)
    await db.flush()
    return row


class TestLegacyRows:
    async def test_a_legacy_invalid_schedule_can_still_be_paused(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        row = await _legacy_row(db, admin_user, fn)
        response = await client.patch(
            f"/api/v1/schedules/{row.name}", json={"is_active": False},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 200, response.text
        await db.refresh(row)
        assert row.is_active is False
        assert row.cron_expression == "not a cron"  # untouched, not "repaired"

    async def test_a_patch_still_cannot_introduce_a_bad_value(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        row = await _legacy_row(db, admin_user, fn)
        response = await client.patch(
            f"/api/v1/schedules/{row.name}", json={"cron_expression": "also not a cron"},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 422

    async def test_fixing_one_problem_is_not_blocked_by_a_hidden_second_one(
        self, client, db: AsyncSession, admin_user, fn, agent, published
    ):
        """pydantic skips whole-spec checks while a field fails, so a stored
        agent schedule with a bad cron AND no content only reports the cron.
        Fixing the cron must not be blamed for the content."""
        row = await _legacy_row(
            db, admin_user, fn,
            schedule_type="agent", target_namespace=agent.namespace, target_name=agent.name,
        )
        response = await client.patch(
            f"/api/v1/schedules/{row.name}", json={"cron_expression": "0 6 * * *"},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 200, response.text

    async def test_making_a_valid_schedule_an_agent_without_content_is_refused(
        self, client, admin_user, fn, agent, published
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        response = await client.patch(
            f"/api/v1/schedules/{name}",
            json={"schedule_type": "agent", "target_namespace": agent.namespace, "target_name": agent.name},
            headers=headers,
        )
        assert response.status_code == 422


class TestPreviewMatchesApply:
    async def test_a_preview_refuses_what_the_apply_would(self, db: AsyncSession, admin_user):
        from app.models import Pipeline

        pipeline = Pipeline(
            user_id=admin_user.id, namespace="default", name=f"off-{_uid()}",
            steps=[{"name": "s", "type": "function", "function": "default/f"}], is_active=False,
        )
        db.add(pipeline)
        await db.flush()

        spec = {"name": f"cfg-{_uid()}", "scheduleType": "pipeline",
                "pipelineName": f"default/{pipeline.name}", "cronExpression": "0 * * * *"}
        _, preview = await _apply(db, admin_user, spec, dry_run=True)
        assert any("not found or inactive" in e for e in preview.errors)
        assert preview.summary.created.get("schedules") is None

    async def test_a_preview_accepts_a_target_the_same_config_creates(
        self, db: AsyncSession, admin_user
    ):
        ns = f"ns{_uid()}"
        svc = ConfigApplyService(db, "cfg", owner_user_id=str(admin_user.id), auto_commit=False)
        config = SinasConfig.model_validate({
            "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
            "spec": {
                "functions": [{"namespace": ns, "name": "job", "code": "def handler(input, context):\n    return {}"}],
                "schedules": [{"name": f"cfg-{_uid()}", "functionName": f"{ns}/job", "cronExpression": "0 * * * *"}],
            },
        })
        preview = await svc.apply_config(config, dry_run=True)
        assert preview.errors == []
        assert preview.summary.created.get("schedules") == 1

    async def test_a_preview_refuses_a_same_config_target_declared_inactive(
        self, db: AsyncSession, admin_user
    ):
        """The real apply requires an active pipeline; a preview that took
        the declaration alone approved installs that then failed."""
        ns = f"ns{_uid()}"
        svc = ConfigApplyService(db, "cfg", owner_user_id=str(admin_user.id), auto_commit=False)
        config = SinasConfig.model_validate({
            "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
            "spec": {
                "functions": [{"namespace": ns, "name": "job", "code": "def handler(input, context):\n    return {}"}],
                "pipelines": [{
                    "namespace": ns, "name": "off", "isActive": False,
                    "steps": [{"name": "s", "type": "function", "function": f"{ns}/job"}],
                }],
                "schedules": [{
                    "name": f"cfg-{_uid()}", "scheduleType": "pipeline",
                    "pipelineName": f"{ns}/off", "cronExpression": "0 * * * *",
                }],
            },
        })
        preview = await svc.apply_config(config, dry_run=True)
        assert any("not found or inactive" in e for e in preview.errors), preview.errors


class TestPackageUninstall:
    async def test_uninstall_records_deletions_and_tells_the_scheduler(
        self, db: AsyncSession, admin_user, published
    ):
        from app.services.package_service import PackageService

        pkg, ns, sched = f"pkg-{_uid()}", f"ns{_uid()}", f"pkg-sched-{_uid()}"
        yaml = f"""
apiVersion: sinas.co/v1
kind: SinasPackage
metadata:
  name: {pkg}
package:
  name: {pkg}
  version: "1.0.0"
spec:
  functions:
    - namespace: {ns}
      name: job
      code: |
        def handler(input, context):
            return {{}}
  schedules:
    - name: {sched}
      functionName: {ns}/job
      cronExpression: "0 3 * * *"
"""
        service = PackageService(db)
        await service.install(yaml, str(admin_user.id))
        row = await _row(db, sched)
        assert row is not None and row.managed_by == f"pkg:{pkg}"
        job_id = str(row.id)
        published.clear()

        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))

        assert counts.get("schedules") == 1
        assert await _row(db, sched) is None
        [deleted] = [r for r in await _revisions(db, resource_key=sched) if r.action == "delete"]
        assert (deleted.origin, deleted.actor_user_id) == ("package", admin_user.id)
        assert ("sinas:scheduler:jobs", {"action": "remove", "job_id": job_id}) in published


# --------------------------------------------------------- all or nothing


class TestAllOrNothing:
    async def test_config_apply_with_an_error_rolls_back_instead_of_committing(
        self, db: AsyncSession, admin_user, fn, monkeypatch
    ):
        calls: list[str] = []

        async def fake_commit():
            calls.append("commit")

        async def fake_rollback():
            calls.append("rollback")

        svc = ConfigApplyService(db, "cfg", owner_user_id=str(admin_user.id))  # auto_commit
        monkeypatch.setattr(svc.db, "commit", fake_commit)
        monkeypatch.setattr(svc.db, "rollback", fake_rollback)
        result = await svc.apply_config(_config(
            _yaml_schedule(f"cfg-{_uid()}", fn),
            _yaml_schedule(f"cfg-{_uid()}", fn, cronExpression="whenever"),
        ))
        assert result.success is False
        assert calls == ["rollback"]

    async def test_a_dry_run_gives_the_verdict_the_apply_would(self, db: AsyncSession, admin_user, fn):
        _, preview = await _apply(
            db, admin_user, _yaml_schedule(f"cfg-{_uid()}", fn, cronExpression="whenever"),
            dry_run=True,
        )
        assert preview.success is False


@pytest_asyncio.fixture
async def committed_owner():
    """A user that really exists, for tests that need independent sessions to
    see real commits (the rolled-back `db` fixture is invisible to them)."""
    from sqlalchemy import delete

    from app.core.database import AsyncSessionLocal, async_engine
    from app.models.user import User

    async with AsyncSessionLocal() as setup:
        user = User(email=f"owner-{_uid()}@example.com")
        setup.add(user)
        await setup.commit()
        user_id = user.id
    try:
        yield user_id
    finally:
        async with AsyncSessionLocal() as cleanup:
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()
        await async_engine.dispose()


class TestPackageInstallIsAtomic:
    async def test_a_package_with_one_bad_resource_installs_nothing(self, committed_owner):
        """The function is fine and the schedule's cron is not. Before, the
        function was committed and the package reported installed."""
        from app.core.database import AsyncSessionLocal
        from app.models.package import Package
        from app.services.package_service import PackageService

        pkg, ns = f"pkg-{_uid()}", f"ns{_uid()}"
        yaml = f"""
apiVersion: sinas.co/v1
kind: SinasPackage
metadata:
  name: {pkg}
package:
  name: {pkg}
  version: "1.0.0"
spec:
  functions:
    - namespace: {ns}
      name: job
      code: |
        def handler(input, context):
            return {{}}
  schedules:
    - name: {pkg}-sched
      functionName: {ns}/job
      cronExpression: "whenever"
"""
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match="nothing was applied"):
                await PackageService(session).install(yaml, str(committed_owner))
            await session.rollback()  # what the request / tool session does

        async with AsyncSessionLocal() as check:
            assert (await check.execute(select(Package).where(Package.name == pkg))).first() is None
            assert (await check.execute(select(Function).where(Function.namespace == ns))).first() is None


# ------------------------------------------------------------------ restore


class TestRestore:
    async def test_a_deleted_schedule_comes_back_whole(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        created = (await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)).json()
        await client.delete(f"/api/v1/schedules/{name}", headers=headers)
        [deleted] = [r for r in await _revisions(db, resource_key=name) if r.action == "delete"]
        published.clear()

        response = await client.post(f"/api/v1/config/history/{deleted.id}/restore", headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["action"] == "create"
        # Same identity, so its history is one timeline, not two
        assert body["resource_id"] == created["id"]
        assert body["revision"]["restored_from_id"] == deleted.id
        row = await _row(db, name)
        assert {f: getattr(row, f) for f in FIELDS} == {
            f: deleted.spec[f] for f in FIELDS
        }
        assert ("sinas:scheduler:jobs", {"action": "add", "job_id": created["id"]}) in published
        actions = [r.action for r in await _revisions(db, resource_id=uuid.UUID(created["id"]))]
        assert actions == ["create", "delete", "create"]

    async def test_restoring_an_older_revision_reverts_the_resource(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        await client.patch(f"/api/v1/schedules/{name}", json={"cron_expression": "0 9 * * *", "description": "edited"}, headers=headers)
        [original] = [r for r in await _revisions(db, resource_key=name) if r.action == "create"]

        response = await client.post(f"/api/v1/config/history/{original.id}/restore", headers=headers)

        assert response.json()["action"] == "update"
        row = await _row(db, name)
        await db.refresh(row)
        assert (row.cron_expression, row.description) == ("0 3 * * *", "Nightly run")

    async def test_a_restore_also_undoes_a_rename(self, client, db: AsyncSession, admin_user, fn, published):
        old, new, headers = f"api-{_uid()}", f"api-{_uid()}", auth_headers(admin_user)
        created = (await client.post("/api/v1/schedules", json=_rest_schedule(old, fn), headers=headers)).json()
        await client.patch(f"/api/v1/schedules/{old}", json={"name": new}, headers=headers)
        [original] = [r for r in await _revisions(db, resource_id=uuid.UUID(created["id"])) if r.action == "create"]

        await client.post(f"/api/v1/config/history/{original.id}/restore", headers=headers)
        assert await _row(db, old) is not None and await _row(db, new) is None

    async def test_restoring_the_current_state_changes_nothing(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        [current] = await _revisions(db, resource_key=name)
        response = await client.post(f"/api/v1/config/history/{current.id}/restore", headers=headers)
        assert response.json()["action"] == "unchanged"
        assert response.json()["revision"] is None
        assert len(await _revisions(db, resource_key=name)) == 1

    async def test_a_restore_gives_the_resource_back_to_its_owner(
        self, client, db: AsyncSession, admin_user, test_user, fn, published
    ):
        """An admin restoring someone's schedule must not become its owner —
        the owner could otherwise no longer see it."""
        name = f"cfg-{_uid()}"
        await _apply(db, test_user, _yaml_schedule(name, fn))  # owned by test_user
        row = await _row(db, name)
        from app.services.resources import ApplyContext
        from app.services.resources.schedules import ScheduleApplier

        await ScheduleApplier().delete(
            row, ApplyContext(db=db, origin="api", actor_user_id=str(admin_user.id))
        )
        [deleted] = [r for r in await _revisions(db, resource_key=name) if r.action == "delete"]
        assert deleted.owner_user_id == test_user.id

        response = await client.post(
            f"/api/v1/config/history/{deleted.id}/restore", headers=auth_headers(admin_user)
        )
        assert response.status_code == 200, response.text
        assert (await _row(db, name)).user_id == test_user.id

    async def test_restoring_onto_a_name_now_taken_is_refused(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)
        await client.delete(f"/api/v1/schedules/{name}", headers=headers)
        [deleted] = [r for r in await _revisions(db, resource_key=name) if r.action == "delete"]
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=headers)

        response = await client.post(f"/api/v1/config/history/{deleted.id}/restore", headers=headers)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    async def test_restore_needs_apply_permission_and_a_real_revision(
        self, client, db: AsyncSession, admin_user, test_user, fn, published
    ):
        name = f"api-{_uid()}"
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=auth_headers(admin_user))
        [rev] = await _revisions(db, resource_key=name)
        denied = await client.post(f"/api/v1/config/history/{rev.id}/restore", headers=auth_headers(test_user))
        assert denied.status_code == 403
        missing = await client.post("/api/v1/config/history/999999999/restore", headers=auth_headers(admin_user))
        assert missing.status_code == 404


# ------------------------------------------------------ package upgrades


def _package_yaml(pkg: str, version: str, ns: str, schedules: list[str]) -> str:
    lines = [
        "apiVersion: sinas.co/v1",
        "kind: SinasPackage",
        "metadata:",
        f"  name: {pkg}",
        "package:",
        f"  name: {pkg}",
        f'  version: "{version}"',
        "spec:",
        "  functions:",
        f"    - namespace: {ns}",
        "      name: job",
        "      code: |",
        "        def handler(input, context):",
        "            return {}",
        "  schedules:" + ("" if schedules else " []"),
    ]
    for name in schedules:
        lines += [
            f"    - name: {name}",
            f"      functionName: {ns}/job",
            '      cronExpression: "0 3 * * *"',
        ]
    return "\n".join(lines) + "\n"


class TestPackageUpgrade:
    async def test_an_upgrade_removes_what_the_new_version_dropped(
        self, db: AsyncSession, admin_user, published
    ):
        from app.services.package_service import PackageService

        pkg, ns, keep, drop = f"pkg-{_uid()}", f"ns{_uid()}", f"keep-{_uid()}", f"drop-{_uid()}"
        service = PackageService(db)
        await service.install(_package_yaml(pkg, "1.0.0", ns, [keep, drop]), str(admin_user.id))
        dropped_id = str((await _row(db, drop)).id)
        published.clear()

        _, result = await service.install(_package_yaml(pkg, "2.0.0", ns, [keep]), str(admin_user.id))

        assert result.summary.deleted == {"schedules": 1}
        assert await _row(db, keep) is not None
        assert await _row(db, drop) is None
        [removal] = [r for r in await _revisions(db, resource_key=drop) if r.action == "delete"]
        assert (removal.origin, removal.managed_by) == ("package", f"pkg:{pkg}")
        assert ("sinas:scheduler:jobs", {"action": "remove", "job_id": dropped_id}) in published

    async def test_a_hand_edited_schedule_survives_an_upgrade(
        self, client, db: AsyncSession, admin_user, published
    ):
        """Editing a package schedule by hand detaches it from the package, so
        an upgrade that no longer ships it leaves the edited copy alone."""
        from app.services.package_service import PackageService

        pkg, ns, edited = f"pkg-{_uid()}", f"ns{_uid()}", f"edited-{_uid()}"
        service = PackageService(db)
        await service.install(_package_yaml(pkg, "1.0.0", ns, [edited]), str(admin_user.id))
        await client.patch(
            f"/api/v1/schedules/{edited}", json={"cron_expression": "0 7 * * *"},
            headers=auth_headers(admin_user),
        )

        await service.install(_package_yaml(pkg, "2.0.0", ns, []), str(admin_user.id))
        row = await _row(db, edited)
        assert row is not None and row.managed_by is None

    async def test_an_upgrade_never_touches_another_packages_schedules(
        self, db: AsyncSession, admin_user, published
    ):
        from app.services.package_service import PackageService

        mine, theirs = f"pkg-{_uid()}", f"pkg-{_uid()}"
        ns_mine, ns_theirs = f"ns{_uid()}", f"ns{_uid()}"
        other = f"other-{_uid()}"
        service = PackageService(db)
        await service.install(_package_yaml(theirs, "1.0.0", ns_theirs, [other]), str(admin_user.id))
        await service.install(_package_yaml(mine, "1.0.0", ns_mine, [f"m-{_uid()}"]), str(admin_user.id))
        await service.install(_package_yaml(mine, "2.0.0", ns_mine, []), str(admin_user.id))
        assert await _row(db, other) is not None

    async def test_an_upgrade_preview_lists_removals_without_removing(
        self, db: AsyncSession, admin_user, published
    ):
        from app.services.package_service import PackageService

        pkg, ns, drop = f"pkg-{_uid()}", f"ns{_uid()}", f"drop-{_uid()}"
        service = PackageService(db)
        await service.install(_package_yaml(pkg, "1.0.0", ns, [drop]), str(admin_user.id))

        preview, _, _ = await service.preview(_package_yaml(pkg, "2.0.0", ns, []), str(admin_user.id))

        assert preview.summary.deleted == {"schedules": 1}
        assert await _row(db, drop) is not None

    async def test_a_removed_schedule_can_be_restored(
        self, client, db: AsyncSession, admin_user, published
    ):
        from app.services.package_service import PackageService

        pkg, ns, drop = f"pkg-{_uid()}", f"ns{_uid()}", f"drop-{_uid()}"
        service = PackageService(db)
        await service.install(_package_yaml(pkg, "1.0.0", ns, [drop]), str(admin_user.id))
        await service.install(_package_yaml(pkg, "2.0.0", ns, []), str(admin_user.id))
        [removal] = [r for r in await _revisions(db, resource_key=drop) if r.action == "delete"]

        response = await client.post(
            f"/api/v1/config/history/{removal.id}/restore", headers=auth_headers(admin_user)
        )
        assert response.status_code == 200, response.text
        assert await _row(db, drop) is not None

    async def test_plain_config_apply_never_deletes(self, db: AsyncSession, admin_user, fn):
        """A config file may legitimately be partial; removal stays opt-in."""
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_schedule(name, fn))
        _, result = await _apply(db, admin_user)  # same config, now without it
        assert result.summary.deleted == {}
        assert await _row(db, name) is not None


# ------------------------------------------- second review round (#206)


class TestConfigShapesAcceptedBefore:
    """All-or-nothing makes one refused entry fail a whole config (and a
    startup auto-apply, the boot), so the spec must not refuse YAML the old
    config path accepted and ran."""

    async def test_an_agent_in_a_hyphenated_namespace(self, db: AsyncSession, admin_user):
        row = Agent(
            user_id=admin_user.id, namespace=f"customer-support-{_uid()}", name="triage",
            system_prompt="Triage.",
        )
        db.add(row)
        await db.flush()
        name = f"cfg-{_uid()}"
        _, result = await _apply(db, admin_user, {
            "name": name, "scheduleType": "agent", "agentName": f"{row.namespace}/triage",
            "content": "Go", "cronExpression": "0 3 * * *",
        })
        assert result.success, result.errors
        assert (await _row(db, name)).target_namespace == row.namespace

    async def test_a_miscased_schedule_type_is_stored_lowercase(self, db: AsyncSession, admin_user, fn):
        name = f"cfg-{_uid()}"
        _, result = await _apply(db, admin_user, _yaml_schedule(name, fn, scheduleType="Function"))
        assert result.success, result.errors
        assert (await _row(db, name)).schedule_type == "function"

    async def test_export_survives_legacy_schedule_types(self, db: AsyncSession, admin_user, fn, agent):
        odd = await _legacy_row(db, admin_user, fn, schedule_type="Weird")
        cased = await _legacy_row(
            db, admin_user, fn, schedule_type="Agent",
            target_namespace=agent.namespace, target_name=agent.name, content="Go",
        )
        exported = {s["name"]: s for s in await ConfigExportService(db)._export_schedules()}
        assert exported[odd.name]["scheduleType"] == "Weird"
        assert not {"functionName", "agentName", "pipelineName"} & exported[odd.name].keys()
        assert exported[cased.name]["scheduleType"] == "agent"
        assert exported[cased.name]["agentName"] == f"{agent.namespace}/{agent.name}"


class TestPatchTouchesOnlyWhatItChanges:
    async def test_a_full_form_resending_a_stored_invalid_cron_is_accepted(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        """The console sends the whole form: an unchanged legacy cron must not
        block pausing the schedule."""
        row = await _legacy_row(db, admin_user, fn)
        response = await client.patch(
            f"/api/v1/schedules/{row.name}",
            json={**_rest_schedule(row.name, fn), "cron_expression": "not a cron", "is_active": False},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 200, response.text
        await db.refresh(row)
        assert row.is_active is False

    async def test_a_stored_field_error_does_not_hide_a_new_whole_spec_error(
        self, client, db: AsyncSession, admin_user, fn, agent, published
    ):
        """With the stored cron failing, pydantic never runs the whole-spec
        rule, so switching to an agent without content slipped through."""
        row = await _legacy_row(db, admin_user, fn)
        response = await client.patch(
            f"/api/v1/schedules/{row.name}",
            json={"schedule_type": "agent", "target_namespace": agent.namespace, "target_name": agent.name},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 422
        assert "content is required" in response.text
        await db.refresh(row)
        assert row.schedule_type == "function"


class TestPackagesDoNotAdopt:
    async def test_a_package_leaves_a_manual_schedule_alone(
        self, client, db: AsyncSession, admin_user, fn, published
    ):
        """Adopting it would let an uninstall or upgrade delete something an
        operator made by hand."""
        from app.services.package_service import PackageService

        pkg, ns, name = f"pkg-{_uid()}", f"ns{_uid()}", f"shared-{_uid()}"
        await client.post("/api/v1/schedules", json=_rest_schedule(name, fn), headers=auth_headers(admin_user))
        service = PackageService(db)

        _, result = await service.install(_package_yaml(pkg, "1.0.0", ns, [name]), str(admin_user.id))
        assert any("by hand" in w for w in result.warnings)
        row = await _row(db, name)
        assert (row.managed_by, row.target_namespace) == (None, fn.namespace)

        await service.install(_package_yaml(pkg, "2.0.0", ns, []), str(admin_user.id))
        assert await _row(db, name) is not None
        await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert await _row(db, name) is not None


class TestRoundTwoFixes:
    async def test_a_restore_falls_back_to_the_restorer_when_the_owner_is_gone(
        self, client, db: AsyncSession, admin_user, test_user, fn, published
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, test_user, _yaml_schedule(name, fn))
        from app.services.resources import ApplyContext
        from app.services.resources.schedules import ScheduleApplier

        await ScheduleApplier().delete(
            await _row(db, name), ApplyContext(db=db, origin="api", actor_user_id=str(admin_user.id))
        )
        [deleted] = [r for r in await _revisions(db, resource_key=name) if r.action == "delete"]
        test_user.is_active = False
        await db.flush()

        response = await client.post(
            f"/api/v1/config/history/{deleted.id}/restore", headers=auth_headers(admin_user)
        )
        assert response.status_code == 200, response.text
        assert (await _row(db, name)).user_id == admin_user.id

    async def test_an_agent_tool_uninstall_records_who_did_it(
        self, db: AsyncSession, admin_user, published
    ):
        from app.services.package_service import PackageService
        from app.services.package_tools import _uninstall

        pkg, ns, sched = f"pkg-{_uid()}", f"ns{_uid()}", f"s-{_uid()}"
        await PackageService(db).install(_package_yaml(pkg, "1.0.0", ns, [sched]), str(admin_user.id))

        await _uninstall(
            db, {"package_name": pkg}, admin_user.id, {"sinas.packages.uninstall:all": True}
        )
        [deleted] = [r for r in await _revisions(db, resource_key=sched) if r.action == "delete"]
        assert deleted.actor_user_id == admin_user.id
