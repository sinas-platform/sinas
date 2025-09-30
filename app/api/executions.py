from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
import uuid

from app.core.database import get_db
from app.core.auth import get_subtenant_context
from app.models.execution import Execution, StepExecution, ExecutionStatus, TriggerType
from app.api.schemas import ExecutionResponse, StepExecutionResponse
from app.services.redis_logger import redis_logger

router = APIRouter(prefix="/executions", tags=["executions"])


@router.get("", response_model=List[ExecutionResponse])
async def list_executions(
    function_name: Optional[str] = None,
    status: Optional[ExecutionStatus] = None,
    trigger_type: Optional[TriggerType] = None,
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    query = select(Execution)
    
    # Apply filters
    if function_name:
        query = query.where(Execution.function_name == function_name)
    
    if status:
        query = query.where(Execution.status == status)
    
    if trigger_type:
        query = query.where(Execution.trigger_type == trigger_type)
    
    # Order by most recent first
    query = query.order_by(Execution.started_at.desc())
    
    # Apply pagination
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    executions = result.scalars().all()
    
    return executions


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: str, 
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    result = await db.execute(
        select(Execution).where(
            and_(
                Execution.execution_id == execution_id,
                Execution.subtenant_id == subtenant_id
            )
        )
    )
    execution = result.scalar_one_or_none()
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    return execution


@router.get("/{execution_id}/steps", response_model=List[StepExecutionResponse])
async def get_execution_steps(execution_id: str, db: AsyncSession = Depends(get_db)):
    # First verify execution exists
    execution_result = await db.execute(
        select(Execution).where(Execution.execution_id == execution_id)
    )
    execution = execution_result.scalar_one_or_none()
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    # Get all step executions for this execution
    steps_result = await db.execute(
        select(StepExecution)
        .where(StepExecution.execution_id == execution_id)
        .order_by(StepExecution.started_at)
    )
    steps = steps_result.scalars().all()
    
    return steps


@router.get("/{execution_id}/logs")
async def get_execution_logs(
    execution_id: str, 
    count: Optional[int] = Query(100, le=1000),
    db: AsyncSession = Depends(get_db)
):
    """Get logs from Redis for an execution."""
    # First verify execution exists
    execution_result = await db.execute(
        select(Execution).where(Execution.execution_id == execution_id)
    )
    execution = execution_result.scalar_one_or_none()
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    # Get logs from Redis
    logs = await redis_logger.get_execution_logs(execution_id, count=count)
    
    return {
        "execution_id": execution_id,
        "logs": logs,
        "total_logs": len(logs)
    }