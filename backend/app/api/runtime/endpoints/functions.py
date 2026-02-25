"""Runtime function execution endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Optional

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.execution import TriggerType
from app.models.function import Function
from app.services.queue_service import queue_service

router = APIRouter()


class FunctionExecuteRequest(BaseModel):
    input: dict[str, Any] = {}
    timeout: Optional[int] = None  # Timeout in seconds (sync only)


class FunctionExecuteResponse(BaseModel):
    status: str
    execution_id: str
    result: Any = None
    error: Optional[str] = None


class FunctionExecuteAsyncResponse(BaseModel):
    execution_id: str
    status: str = "queued"


@router.post(
    "/functions/{namespace}/{name}/execute",
    response_model=FunctionExecuteResponse,
)
async def execute_function(
    request: Request,
    namespace: str,
    name: str,
    body: FunctionExecuteRequest,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """
    Execute a function and wait for the result.

    Enqueues the function on the worker queue and blocks until the result
    is available or the timeout is reached.
    """
    user_id, permissions = current_user_data

    function = await Function.get_by_name(db, namespace, name)
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")

    permission = f"sinas.functions/{namespace}/{name}.execute:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to execute this function")

    set_permission_used(request, permission)

    execution_id = str(uuid.uuid4())

    try:
        result = await queue_service.enqueue_and_wait(
            function_namespace=namespace,
            function_name=name,
            input_data=body.input,
            execution_id=execution_id,
            trigger_type=TriggerType.API.value,
            trigger_id="runtime-api",
            user_id=user_id,
            timeout=body.timeout,
        )

        return FunctionExecuteResponse(
            status="success",
            execution_id=execution_id,
            result=result,
        )
    except TimeoutError:
        return FunctionExecuteResponse(
            status="timeout",
            execution_id=execution_id,
            error="Function execution timed out. Poll GET /executions/{execution_id} for status.",
        )
    except Exception as e:
        return FunctionExecuteResponse(
            status="error",
            execution_id=execution_id,
            error=str(e),
        )


@router.post(
    "/functions/{namespace}/{name}/execute/async",
    response_model=FunctionExecuteAsyncResponse,
    status_code=202,
)
async def execute_function_async(
    request: Request,
    namespace: str,
    name: str,
    body: FunctionExecuteRequest,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """
    Execute a function asynchronously (fire-and-forget).

    Enqueues the function and returns immediately with an execution_id.
    Poll GET /executions/{execution_id} for status and result.
    """
    user_id, permissions = current_user_data

    function = await Function.get_by_name(db, namespace, name)
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")

    permission = f"sinas.functions/{namespace}/{name}.execute:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to execute this function")

    set_permission_used(request, permission)

    execution_id = str(uuid.uuid4())

    await queue_service.enqueue_function(
        function_namespace=namespace,
        function_name=name,
        input_data=body.input,
        execution_id=execution_id,
        trigger_type=TriggerType.API.value,
        trigger_id="runtime-api",
        user_id=user_id,
    )

    return FunctionExecuteAsyncResponse(
        execution_id=execution_id,
    )
