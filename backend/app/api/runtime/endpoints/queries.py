"""Runtime query execution endpoint."""
import time

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.query import Query
from app.models.user import User
from app.schemas.query import QueryExecuteRequest, QueryExecuteResponse
from app.services.database_pool import DatabasePoolManager

router = APIRouter()


@router.post(
    "/queries/{namespace}/{name}/execute",
    response_model=QueryExecuteResponse,
)
async def execute_query(
    namespace: str,
    name: str,
    execute_request: QueryExecuteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Execute a query with the given input parameters."""
    user_id, permissions = current_user_data

    query = await Query.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="execute",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.queries/{namespace}/{name}.execute")

    # Validate input against input_schema
    if query.input_schema and query.input_schema.get("properties"):
        try:
            jsonschema.validate(instance=execute_request.input, schema=query.input_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(status_code=400, detail=f"Input validation error: {e.message}")

    # Merge context variables
    params = {**execute_request.input}
    params["user_id"] = str(user_id)

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user:
        params["user_email"] = user.email

    start_time = time.time()
    try:
        pool_manager = DatabasePoolManager.get_instance()
        result = await pool_manager.execute_query(
            db=db,
            connection_id=str(query.database_connection_id),
            sql=query.sql,
            params=params,
            operation=query.operation,
            timeout_ms=query.timeout_ms,
            max_rows=query.max_rows,
        )
        duration_ms = int((time.time() - start_time) * 1000)

        if query.operation == "read":
            return QueryExecuteResponse(
                success=True,
                operation=query.operation,
                data=result.get("rows", []),
                row_count=result.get("row_count", 0),
                duration_ms=duration_ms,
            )
        else:
            return QueryExecuteResponse(
                success=True,
                operation=query.operation,
                data=result.get("rows"),
                row_count=result.get("row_count"),
                affected_rows=result.get("affected_rows", 0),
                duration_ms=duration_ms,
            )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed: {str(e)}",
        )
