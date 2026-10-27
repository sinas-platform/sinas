"""A pipeline run that comes again doesn't run twice or hold the lock.

Run ids are deterministic for a fire (a re-fire after Redis was full
enqueues only the missing runs), so a run job can arrive with a run id that
already has a record. It used to take the single-flight lock and then fail
on the unique run_id, leaving a single-concurrency pipeline locked until the
lock's TTL. Now it reports the existing run without taking the lock, and
any failure before the run record exists releases the lock.
"""

import contextlib
import uuid
from datetime import datetime, timezone

import pytest

from app.core.redis import get_redis
from app.models.pipeline import Pipeline, PipelineRun, TriggerType
from app.services import pipeline_runner as pr


@pytest.fixture
async def single_pipeline(db, test_user, monkeypatch):
    @contextlib.asynccontextmanager
    async def test_session():
        yield db

    monkeypatch.setattr(pr, "AsyncSessionLocal", test_session)
    pipeline = Pipeline(
        user_id=test_user.id, name=f"p-{uuid.uuid4().hex[:6]}", concurrency="single", is_active=True,
    )
    db.add(pipeline)
    await db.commit()
    return pipeline


async def test_an_existing_run_is_reported_not_rerun(db, test_user, single_pipeline):
    run_id = str(uuid.uuid4())
    db.add(PipelineRun(
        pipeline_id=single_pipeline.id, run_id=run_id, user_id=test_user.id,
        trigger_type=TriggerType.MANUAL, status="succeeded", output={"n": 1},
        steps=[], started_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    lock_key = pr._lock_key(single_pipeline, str(test_user.id))

    outcome = await pr.run_pipeline(
        str(single_pipeline.id), {}, trigger_type="MANUAL", trigger_id=None,
        user_id=str(test_user.id), user_token="t", run_id=run_id,
    )

    assert outcome["run_id"] == run_id
    assert outcome["status"] == "succeeded" and outcome["output"] == {"n": 1}
    assert not await (await get_redis()).exists(lock_key)


async def test_a_failure_before_the_run_record_releases_the_lock(db, test_user, single_pipeline, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(pr, "_read_cursor", broken)
    lock_key = pr._lock_key(single_pipeline, str(test_user.id))

    with pytest.raises(RuntimeError, match="went away"):
        await pr.run_pipeline(
            str(single_pipeline.id), {}, trigger_type="MANUAL", trigger_id=None,
            user_id=str(test_user.id), user_token="t", run_id=str(uuid.uuid4()),
        )
    assert not await (await get_redis()).exists(lock_key)  # not held until its TTL
