"""Pipelines API endpoints (management plane: CRUD).

Writes go through PipelineApplier, the path config apply and package install
use too: the same validation, ownership and change history on every channel.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.pipeline import Pipeline
from app.schemas.pipeline import PipelineCreate, PipelineResponse, PipelineUpdate
from app.services.resources import rest
from app.services.resources.pipelines import PipelineApplier

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

_applier = PipelineApplier()


@router.post("", response_model=PipelineResponse, status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    request: Request,
    data: PipelineCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new pipeline."""
    user_id, permissions = current_user_data

    permission = "sinas.pipelines.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create pipelines")
    set_permission_used(request, permission)

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Pipeline 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return PipelineResponse.model_validate(result.obj)


@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List pipelines."""
    user_id, permissions = current_user_data

    pipelines = await Pipeline.list_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read"
    )
    set_permission_used(request, "sinas.pipelines.read")
    return [PipelineResponse.model_validate(p) for p in pipelines]


@router.get("/{namespace}/{name}", response_model=PipelineResponse)
async def get_pipeline(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific pipeline."""
    user_id, permissions = current_user_data

    pipeline = await Pipeline.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.pipelines/{namespace}/{name}.read")
    return PipelineResponse.model_validate(pipeline)


@router.put("/{namespace}/{name}", response_model=PipelineResponse)
async def update_pipeline(
    request: Request,
    namespace: str,
    name: str,
    data: PipelineUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a pipeline. The merged definition is re-validated."""
    user_id, permissions = current_user_data

    pipeline = await Pipeline.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="update",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.pipelines/{namespace}/{name}.update")

    ctx = rest.api_context(db, user_id)
    pipeline = await rest.locked(_applier, ctx, pipeline)
    # As before: a field left out or sent as null stays as it is, a new
    # namespace/name renames it, and the merged definition is re-validated
    # (an invalid one is a 400, as it was).
    patch = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None
    }
    try:
        spec = rest.patch_spec(_applier, pipeline, patch)
    except HTTPException as e:
        if e.status_code != 422:
            raise
        raise HTTPException(
            status_code=400, detail="; ".join(err["msg"].removeprefix("Value error, ") for err in e.detail)
        )
    await rest.write(_applier, ctx, spec, existing=pipeline)
    if data.is_active:
        # Sending is_active: true resets the failure state even when the
        # pipeline is still on (runtime state, not config: not in history).
        pipeline.consecutive_failures = 0
        pipeline.error_message = None
    await rest.commit(db, ctx)
    await db.refresh(pipeline)
    return PipelineResponse.model_validate(pipeline)


@router.delete("/{namespace}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pipeline(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a pipeline (its runs and cursors cascade)."""
    user_id, permissions = current_user_data

    pipeline = await Pipeline.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="delete",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.pipelines/{namespace}/{name}.delete")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, pipeline), ctx)
    await rest.commit(db, ctx)
    return None
