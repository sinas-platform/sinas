"""Query API endpoints with namespace-based permissions."""
import time

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.query import Query
from app.schemas.query import (
    QueryCreate,
    QueryExecuteRequest,
    QueryExecuteResponse,
    QueryResponse,
    QueryUpdate,
)
from app.services.database_pool import DatabasePoolManager

router = APIRouter(prefix="/queries", tags=["queries"])


@router.post("", response_model=QueryResponse)
async def create_query(
    request: Request,
    query_data: QueryCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new query."""
    user_id, permissions = current_user_data

    permission = "sinas.queries.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create queries")
    set_permission_used(request, permission)

    # Check uniqueness
    result = await db.execute(
        select(Query).where(
            and_(Query.namespace == query_data.namespace, Query.name == query_data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Query '{query_data.namespace}/{query_data.name}' already exists",
        )

    query = Query(
        user_id=user_id,
        namespace=query_data.namespace,
        name=query_data.name,
        description=query_data.description,
        database_connection_id=query_data.database_connection_id,
        operation=query_data.operation,
        sql=query_data.sql,
        input_schema=query_data.input_schema or {},
        output_schema=query_data.output_schema or {},
        timeout_ms=query_data.timeout_ms,
        max_rows=query_data.max_rows,
    )

    db.add(query)
    await db.commit()
    await db.refresh(query)

    return QueryResponse.model_validate(query)


@router.get("", response_model=list[QueryResponse])
async def list_queries(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all queries accessible to the user."""
    user_id, permissions = current_user_data

    additional_filters = Query.is_active == True
    if namespace:
        additional_filters = and_(additional_filters, Query.namespace == namespace)

    queries = await Query.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=additional_filters,
    )

    set_permission_used(request, "sinas.queries.read")

    return [QueryResponse.model_validate(q) for q in queries]


@router.get("/{namespace}/{name}", response_model=QueryResponse)
async def get_query(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific query by namespace and name."""
    user_id, permissions = current_user_data

    query = await Query.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.queries/{namespace}/{name}.read")

    return QueryResponse.model_validate(query)


@router.put("/{namespace}/{name}", response_model=QueryResponse)
async def update_query(
    namespace: str,
    name: str,
    query_data: QueryUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a query."""
    user_id, permissions = current_user_data

    query = await Query.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.queries/{namespace}/{name}.update")

    # Check for namespace/name conflicts
    new_namespace = query_data.namespace or query.namespace
    new_name = query_data.name or query.name

    if new_namespace != query.namespace or new_name != query.name:
        result = await db.execute(
            select(Query).where(
                and_(Query.namespace == new_namespace, Query.name == new_name, Query.id != query.id)
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail=f"Query '{new_namespace}/{new_name}' already exists"
            )

    if query_data.namespace is not None:
        query.namespace = query_data.namespace
    if query_data.name is not None:
        query.name = query_data.name
    if query_data.description is not None:
        query.description = query_data.description
    if query_data.database_connection_id is not None:
        query.database_connection_id = query_data.database_connection_id
    if query_data.operation is not None:
        query.operation = query_data.operation
    if query_data.sql is not None:
        query.sql = query_data.sql
    if query_data.input_schema is not None:
        query.input_schema = query_data.input_schema
    if query_data.output_schema is not None:
        query.output_schema = query_data.output_schema
    if query_data.timeout_ms is not None:
        query.timeout_ms = query_data.timeout_ms
    if query_data.max_rows is not None:
        query.max_rows = query_data.max_rows
    if query_data.is_active is not None:
        query.is_active = query_data.is_active

    await db.commit()
    await db.refresh(query)

    return QueryResponse.model_validate(query)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_query(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a query."""
    user_id, permissions = current_user_data

    query = await Query.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.queries/{namespace}/{name}.delete")

    await db.delete(query)
    await db.commit()

    return None


@router.post("/{namespace}/{name}/execute", response_model=QueryExecuteResponse)
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
    # Get user email
    from app.models.user import User
    from sqlalchemy import select as sa_select

    user_result = await db.execute(sa_select(User).where(User.id == user_id))
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
                affected_rows=result.get("affected_rows", 0),
                duration_ms=duration_ms,
            )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed: {str(e)}",
        )
