"""Executions API endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.execution import Execution, ExecutionStatus
from app.schemas import (
    ContinueExecutionRequest,
    ContinueExecutionResponse,
    ExecutionResponse,
)

from app.services.execution_engine import executor
from app.services.queue_service import queue_service

router = APIRouter(prefix="/executions")


@router.get("", response_model=list[ExecutionResponse])
async def list_executions(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    function_name: Optional[str] = None,
    status: Optional[ExecutionStatus] = None,
    chat_id: Optional[str] = None,
    trigger_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List executions (own and group-accessible)."""
    user_id, permissions = current_user_data

    # Build query based on permissions
    if check_permission(permissions, "sinas.executions.read:all"):
        set_permission_used(request, "sinas.executions.read:all")
        query = select(Execution)
    else:
        set_permission_used(request, "sinas.executions.read:own")
        query = select(Execution).where(Execution.user_id == user_id)

    if function_name:
        query = query.where(Execution.function_name == function_name)
    if status:
        query = query.where(Execution.status == status)
    if chat_id:
        query = query.where(Execution.chat_id == chat_id)
    if trigger_type:
        query = query.where(Execution.trigger_type == trigger_type)

    query = query.order_by(Execution.started_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    executions = result.scalars().all()

    return executions


@router.get("/{execution_id}")
async def get_execution(
    request: Request,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific execution. Falls back to queue status if the DB record hasn't been created yet."""
    user_id, permissions = current_user_data

    result = await db.execute(select(Execution).where(Execution.execution_id == execution_id))
    execution = result.scalar_one_or_none()

    if execution:
        # Check permissions
        if check_permission(permissions, "sinas.executions.read:all"):
            set_permission_used(request, "sinas.executions.read:all")
        else:
            if execution.user_id != user_id:
                set_permission_used(request, "sinas.executions.read:own", has_perm=False)
                raise HTTPException(status_code=403, detail="Not authorized to view this execution")
            set_permission_used(request, "sinas.executions.read:own")

        return ExecutionResponse.model_validate(execution)

    # No DB record yet — check Redis for queued/in-progress job status.
    # Since job_id = execution_id, we can look up directly.

    status = await queue_service.get_job_status(execution_id)
    if status:
        set_permission_used(request, "sinas.executions.read:own")
        return {
            "execution_id": execution_id,
            "status": status.get("status", "queued").upper(),
            "function_name": status.get("function", "").split("/")[-1] if status.get("function") else None,
            "trigger_type": status.get("trigger_type"),
        }

    raise HTTPException(status_code=404, detail="Execution not found")


@router.post("/{execution_id}/continue", response_model=ContinueExecutionResponse)
async def continue_execution(
    http_request: Request,
    execution_id: str,
    request: ContinueExecutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Continue a paused execution with user input."""
    user_id, permissions = current_user_data

    # Get execution
    result = await db.execute(select(Execution).where(Execution.execution_id == execution_id))
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    # Check permissions
    if check_permission(permissions, "sinas.executions.update:all"):
        set_permission_used(http_request, "sinas.executions.update:all")
    else:
        if execution.user_id != user_id:
            set_permission_used(http_request, "sinas.executions.update:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to continue this execution")
        set_permission_used(http_request, "sinas.executions.update:own")

    # Check if execution is awaiting input
    if execution.status != ExecutionStatus.AWAITING_INPUT:
        raise HTTPException(status_code=400, detail="Execution is not awaiting input")

    # Resume directly in the container (bypasses queue)
    try:
        result = await executor.resume_execution(
            execution_id=execution_id,
            resume_value=request.input,
        )

        # Refresh execution from DB to get updated status
        await db.refresh(execution)

        return ContinueExecutionResponse(
            execution_id=execution_id,
            status=execution.status,
            output_data=result if execution.status == ExecutionStatus.COMPLETED else None,
            prompt=result.get("prompt")
            if isinstance(result, dict) and execution.status == ExecutionStatus.AWAITING_INPUT
            else None,
            schema=result.get("schema")
            if isinstance(result, dict) and execution.status == ExecutionStatus.AWAITING_INPUT
            else None,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to continue execution: {str(e)}")
