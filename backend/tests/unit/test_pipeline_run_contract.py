"""Pipeline run-record contract fixes from the 0.4.0 e2e pass.

Three field findings:
- run output was computed and returned to the live caller, then dropped —
  no column, not in the persist UPDATE, so GET /pipelines/runs/{id} always
  said output: null.
- async/replay enqueue responses returned the arq JOB id labelled run_id;
  polling it 404'd forever (the real run got a fresh uuid in the worker).
- schedules with schedule_type="pipeline" could never be created: target
  validation fell into the agent branch ("Agent 'ns/name' not found").
"""

import contextlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Pipeline
from app.models.execution import TriggerType
from app.models.pipeline import PipelineRun
from tests.conftest import auth_headers


class _FakePool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, **kwargs):
        self.jobs.append({"name": name, **kwargs})


class TestEnqueueReturnsRunId:
    async def test_returned_id_is_the_job_kwarg_run_id_not_the_job_id(self, monkeypatch):
        from app.services import queue_service as qs

        pool = _FakePool()

        async def fake_pool():
            return pool

        monkeypatch.setattr(qs, "get_arq_pool", fake_pool)

        returned = await qs.queue_service.enqueue_pipeline_run(
            pipeline_id=str(uuid.uuid4()),
            run_input={},
            trigger_type="MANUAL",
            trigger_id="test",
            user_id=str(uuid.uuid4()),
        )
        (job,) = pool.jobs
        assert returned == job["run_id"]
        assert returned != job["job_id"]  # the old, never-resolvable answer


class TestOutputPersisted:
    @pytest.fixture
    async def run_row(self, db: AsyncSession, test_user):
        pipeline = Pipeline(user_id=test_user.id, name=f"p-{uuid.uuid4().hex[:6]}")
        db.add(pipeline)
        await db.flush()
        run = PipelineRun(
            pipeline_id=pipeline.id,
            run_id=str(uuid.uuid4()),
            user_id=test_user.id,
            trigger_type=TriggerType.MANUAL,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        await db.flush()
        return pipeline, run

    async def _persist(self, db, monkeypatch, pipeline, run, *, status, output):
        from app.services import pipeline_runner as pr

        @contextlib.asynccontextmanager
        async def fake_session():
            yield db

        monkeypatch.setattr(pr, "AsyncSessionLocal", fake_session)

        class _NoRedis:
            async def eval(self, *a):  # lock release path
                return 0

            async def get(self, *a):
                return None

        await pr._persist_outcome(
            pipeline, str(run.user_id), run.run_id, status,
            None if status == "succeeded" else "boom",
            output, [],
            new_cursor=None, t0=0.0, use_lock=False,
            lock_key="", redis=_NoRedis(),
        )

    async def test_success_persists_output(self, db, monkeypatch, run_row):
        pipeline, run = run_row
        await self._persist(
            db, monkeypatch, pipeline, run,
            status="succeeded", output={"answer": 42},
        )
        await db.refresh(run)
        assert run.output == {"answer": 42}
        # And the API record schema must actually expose it (it didn't):
        from app.schemas.pipeline import PipelineRunRecord

        assert PipelineRunRecord.model_validate(run).output == {"answer": 42}

    async def test_failure_persists_no_output(self, db, monkeypatch, run_row):
        pipeline, run = run_row
        await self._persist(
            db, monkeypatch, pipeline, run,
            status="failed", output={"partial": True},
        )
        await db.refresh(run)
        assert run.output is None
        assert run.error == "boom"


class TestPipelineSchedules:
    @pytest.fixture(autouse=True)
    def _fake_redis(self, monkeypatch):
        """The endpoint notifies the running scheduler via Redis pub/sub;
        the unit env has no reachable Redis (same as the known test_auth
        situation), so fake it."""
        class _R:
            async def publish(self, *a):
                return 0

        async def _get():
            return _R()

        monkeypatch.setattr("app.api.v1.endpoints.schedules.get_redis", _get)

    async def test_pipeline_schedule_creates(self, client, db, admin_user):
        pipeline = Pipeline(
            user_id=admin_user.id, namespace="default",
            name=f"sched-{uuid.uuid4().hex[:6]}",
            steps=[{"name": "s", "type": "function", "function": "default/f"}],
        )
        db.add(pipeline)
        await db.flush()

        resp = await client.post(
            "/api/v1/schedules",
            headers=auth_headers(admin_user),
            json={
                "name": f"sched-{uuid.uuid4().hex[:6]}",
                "schedule_type": "pipeline",
                "target_namespace": "default",
                "target_name": pipeline.name,
                "cron_expression": "*/5 * * * *",
            },
        )
        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["schedule_type"] == "pipeline"

    async def test_missing_pipeline_404s_with_pipeline_message(self, client, admin_user):
        resp = await client.post(
            "/api/v1/schedules",
            headers=auth_headers(admin_user),
            json={
                "name": f"sched-{uuid.uuid4().hex[:6]}",
                "schedule_type": "pipeline",
                "target_namespace": "default",
                "target_name": "does_not_exist",
                "cron_expression": "*/5 * * * *",
            },
        )
        assert resp.status_code == 404
        assert "Pipeline" in resp.json()["detail"]  # not "Agent"


class TestVersionHeader:
    async def test_all_responses_carry_version(self, client):
        from app._version import __version__

        # Root app, mounted v1 app, and an error response
        for path in ("/info", "/api/v1/agents", "/definitely-not-a-route"):
            resp = await client.get(path)
            assert resp.headers.get("x-sinas-version") == __version__, path
