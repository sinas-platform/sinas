"""Runtime pipeline endpoints — manual runs, run history, replay."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.execution import TriggerType
from app.models.pipeline import Pipeline, PipelineRun
from app.schemas.pipeline import (
    PipelineFanOutResponse,
    PipelineRunEnqueuedResponse,
    PipelineRunRecord,
    PipelineRunRequest,
    PipelineRunResponse,
)
# Shared depth resolution with the runtime function endpoints (PR #81 semantics).
from app.api.runtime.endpoints.functions import _resolve_child_depth
from app.services import pipeline_runner
from app.services.pipeline_runner import PipelineBusyError
from app.services.queue_service import queue_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_run_permission(permissions: dict, namespace: str, name: str) -> tuple[bool, str]:
    perm_own = f"sinas.pipelines/{namespace}/{name}.run:own"
    return check_permission(permissions, perm_own), perm_own


async def _load_pipeline(db: AsyncSession, namespace: str, name: str) -> Pipeline:
    pipeline = await Pipeline.get_by_name(db, namespace, name)
    if not pipeline:
        raise HTTPException(status_code=404, detail=f"Pipeline '{namespace}/{name}' not found")
    return pipeline


# NOTE: literal-prefix routes (/pipelines/runs/...) are declared before the
# parameterized ones so "runs" is never captured as a namespace.


@router.get("/pipelines/runs/{run_id}", response_model=PipelineRunRecord)
async def get_pipeline_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Fetch one run record (input, per-step summaries, error, cursor movement)."""
    user_id, permissions = current_user_data

    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == run_id))
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    pipeline = (
        await db.execute(select(Pipeline).where(Pipeline.id == run.pipeline_id))
    ).scalar_one_or_none()
    ref = f"{pipeline.namespace}/{pipeline.name}" if pipeline else "?/?"

    read_own = check_permission(permissions, f"sinas.pipelines/{ref}.read:own") if pipeline else False
    read_all = check_permission(permissions, f"sinas.pipelines/{ref}.read:all") if pipeline else False
    if not (read_all or (read_own and str(run.user_id) == user_id)):
        raise HTTPException(status_code=403, detail="Not authorized to read this run")
    set_permission_used(request, f"sinas.pipelines/{ref}.read")

    return PipelineRunRecord.model_validate(run)


@router.post("/pipelines/runs/{run_id}/replay", response_model=PipelineRunEnqueuedResponse, status_code=202)
async def replay_pipeline_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Re-enqueue a run with its stored input (the dead-letter replay path).

    Safe for cursor runs: a failed run never advanced the bookmark.
    """
    user_id, permissions = current_user_data

    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == run_id))
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    pipeline = (
        await db.execute(select(Pipeline).where(Pipeline.id == run.pipeline_id))
    ).scalar_one_or_none()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline for this run no longer exists")
    if not pipeline.is_active:
        raise HTTPException(status_code=400, detail="Pipeline is inactive")

    ref = f"{pipeline.namespace}/{pipeline.name}"
    run_own = check_permission(permissions, f"sinas.pipelines/{ref}.run:own")
    run_all = check_permission(permissions, f"sinas.pipelines/{ref}.run:all")
    # Replaying someone else's run executes as THAT user — require :all for it.
    if not (run_all or (run_own and str(run.user_id) == user_id)):
        raise HTTPException(status_code=403, detail="Not authorized to replay this run")
    set_permission_used(request, f"sinas.pipelines/{ref}.run")

    job_id = await queue_service.enqueue_pipeline_run(
        pipeline_id=str(pipeline.id),
        run_input=run.input,
        trigger_type=TriggerType.MANUAL.value,
        trigger_id=f"replay:{run_id}",
        user_id=str(run.user_id),
    )
    return PipelineRunEnqueuedResponse(run_id=job_id)


@router.post("/pipelines/{namespace}/{name}/run")
async def run_pipeline_endpoint(
    namespace: str,
    name: str,
    body: PipelineRunRequest,
    request: Request,
    all_users: bool = Query(default=False, alias="allUsers"),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Manual pipeline run (testing / backfills).

    - sync (default): executes inline and returns the outcome.
    - async: enqueues one run for the calling user, returns 202.
    - ?allUsers=true (perUser pipelines, requires run:all): fans out like a
      trigger firing — one queued run per connected user.
    """
    user_id, permissions = current_user_data
    pipeline = await _load_pipeline(db, namespace, name)
    if not pipeline.is_active:
        raise HTTPException(status_code=400, detail=f"Pipeline '{namespace}/{name}' is inactive")

    allowed, perm = _check_run_permission(permissions, namespace, name)
    if not allowed:
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to run this pipeline")
    set_permission_used(request, perm)

    if all_users:
        if not pipeline.per_user:
            raise HTTPException(status_code=400, detail="?allUsers=true requires a perUser pipeline")
        if not check_permission(permissions, f"sinas.pipelines/{namespace}/{name}.run:all"):
            raise HTTPException(status_code=403, detail="?allUsers=true requires run:all permission")
        job_ids = await pipeline_runner.fire_pipeline(
            namespace, name, body.input,
            trigger_type=TriggerType.MANUAL.value, trigger_id=f"manual:{user_id}",
        )
        return PipelineFanOutResponse(
            runs=[PipelineRunEnqueuedResponse(run_id=j) for j in job_ids],
            users=len(job_ids),
        )

    if body.mode == "async":
        job_id = await queue_service.enqueue_pipeline_run(
            pipeline_id=str(pipeline.id),
            run_input=body.input,
            trigger_type=TriggerType.API.value,
            trigger_id=f"manual:{user_id}",
            user_id=str(user_id),
        )
        return PipelineRunEnqueuedResponse(run_id=job_id)

    # Sync: run inline under the pipeline's sync budget.
    user_token = request.headers.get("authorization", "").replace("Bearer ", "")
    depth = _resolve_child_depth(request)
    try:
        outcome = await asyncio.wait_for(
            pipeline_runner.run_pipeline(
                str(pipeline.id),
                body.input,
                trigger_type=TriggerType.API.value,
                trigger_id=f"manual:{user_id}",
                user_id=str(user_id),
                user_token=user_token,
                exec_depth=depth,
                sync=True,
            ),
            timeout=pipeline.sync_timeout_seconds,
        )
    except PipelineBusyError as e:
        raise HTTPException(
            status_code=409,
            detail=f"A run of this pipeline is already in progress (run_id={e.active_run_id})",
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Pipeline run exceeded syncTimeoutSeconds ({pipeline.sync_timeout_seconds}s). "
                f"Completed steps had side effects — see GET /pipelines/{namespace}/{name}/runs. "
                "Use mode=async for long runs."
            ),
        )

    return PipelineRunResponse(**outcome)


@router.get("/pipelines/{namespace}/{name}/runs", response_model=list[PipelineRunRecord])
async def list_pipeline_runs(
    namespace: str,
    name: str,
    request: Request,
    status: str = Query(default=None),
    user: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List run records for a pipeline, newest first."""
    user_id, permissions = current_user_data
    pipeline = await Pipeline.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.pipelines/{namespace}/{name}.read")

    stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline.id)
        .order_by(PipelineRun.started_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(PipelineRun.status == status)
    if user:
        stmt = stmt.where(PipelineRun.user_id == user)

    runs = (await db.execute(stmt)).scalars().all()
    return [PipelineRunRecord.model_validate(r) for r in runs]
