"""Pipelines API endpoints (management plane: CRUD)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.pipeline import Pipeline
from app.schemas.pipeline import PipelineCreate, PipelineResponse, PipelineUpdate
from app.services.package_service import detach_if_package_managed
from app.services.pipeline_validation import validate_pipeline_definition

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


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

    result = await db.execute(
        select(Pipeline).where(
            and_(Pipeline.namespace == data.namespace, Pipeline.name == data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail=f"Pipeline '{data.namespace}/{data.name}' already exists"
        )

    pipeline = Pipeline(
        user_id=user_id,
        namespace=data.namespace,
        name=data.name,
        description=data.description,
        input_schema=data.input_schema or {},
        steps=data.steps,
        per_user=data.per_user,
        as_tool=data.as_tool,
        tool_description=data.tool_description,
        sync_timeout_seconds=data.sync_timeout_seconds,
        concurrency=data.concurrency,
        disable_after_failures=data.disable_after_failures,
        output_mapping=data.output_mapping,
    )
    db.add(pipeline)
    await db.flush()
    await db.refresh(pipeline)
    return PipelineResponse.model_validate(pipeline)


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

    detach_if_package_managed(pipeline)

    new_namespace = data.namespace or pipeline.namespace
    new_name = data.name or pipeline.name
    if new_namespace != pipeline.namespace or new_name != pipeline.name:
        result = await db.execute(
            select(Pipeline).where(
                and_(
                    Pipeline.namespace == new_namespace,
                    Pipeline.name == new_name,
                    Pipeline.id != pipeline.id,
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail=f"Pipeline '{new_namespace}/{new_name}' already exists"
            )

    # Validate the merged definition (partial updates can't validate in isolation)
    merged = {
        "steps": data.steps if data.steps is not None else pipeline.steps,
        "per_user": data.per_user if data.per_user is not None else pipeline.per_user,
        "as_tool": data.as_tool if data.as_tool is not None else pipeline.as_tool,
        "input_schema": data.input_schema if data.input_schema is not None else pipeline.input_schema,
        "description": data.description if data.description is not None else pipeline.description,
        "tool_description": data.tool_description if data.tool_description is not None else pipeline.tool_description,
        "concurrency": data.concurrency if data.concurrency is not None else pipeline.concurrency,
        "output_mapping": data.output_mapping if data.output_mapping is not None else pipeline.output_mapping,
    }
    errors = validate_pipeline_definition(
        merged["steps"],
        per_user=merged["per_user"],
        as_tool=merged["as_tool"],
        input_schema=merged["input_schema"],
        description=merged["description"],
        tool_description=merged["tool_description"],
        concurrency=merged["concurrency"],
        output_mapping=merged["output_mapping"],
    )
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    if data.namespace is not None:
        pipeline.namespace = data.namespace
    if data.name is not None:
        pipeline.name = data.name
    if data.description is not None:
        pipeline.description = data.description
    if data.input_schema is not None:
        pipeline.input_schema = data.input_schema
    if data.steps is not None:
        pipeline.steps = data.steps
    if data.per_user is not None:
        pipeline.per_user = data.per_user
    if data.as_tool is not None:
        pipeline.as_tool = data.as_tool
    if data.tool_description is not None:
        pipeline.tool_description = data.tool_description
    if data.sync_timeout_seconds is not None:
        pipeline.sync_timeout_seconds = data.sync_timeout_seconds
    if data.concurrency is not None:
        pipeline.concurrency = data.concurrency
    if data.disable_after_failures is not None:
        pipeline.disable_after_failures = data.disable_after_failures
    if data.output_mapping is not None:
        pipeline.output_mapping = data.output_mapping
    if data.is_active is not None:
        pipeline.is_active = data.is_active
        if data.is_active:
            # Reactivation clears the auto-disable state.
            pipeline.consecutive_failures = 0
            pipeline.error_message = None

    await db.flush()
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

    await db.delete(pipeline)
    await db.flush()
    return None
