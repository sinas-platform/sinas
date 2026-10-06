"""Query API endpoints with namespace-based permissions.

Writes go through QueryApplier, the path config apply and package install use
too: the same validation, ownership and change history on every channel.
"""
import time

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.database_connection import DatabaseConnection
from app.models.query import Query
from app.models.user import User
from app.schemas.query import (
    QueryCreate,
    QueryExecuteRequest,
    QueryExecuteResponse,
    QueryResponse,
    QueryUpdate,
)
from app.services.database_pool import DatabasePoolManager
from app.services.resources import rest
from app.services.resources.queries import QueryApplier, connection_name

router = APIRouter(prefix="/queries", tags=["queries"])

_applier = QueryApplier()


async def _authorize_connection(
    request: Request, db: AsyncSession, permissions: dict, connection_id
) -> str:
    """A query runs its SQL with the connection's credentials, so binding one
    takes the same right as seeing connections (admin-granted by default) —
    the console's editor can only list them with it. Holding queries.create
    alone used to let anyone bind any connection by its UUID."""
    permission = "sinas.database_connections.read:all"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to use database connections")
    # On success the request log keeps the query permission the endpoint set.
    found = (
        await db.execute(
            select(DatabaseConnection.name).where(
                DatabaseConnection.id == connection_id,
                DatabaseConnection.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if found is None:
        # Previously an unknown id failed the foreign key at flush: a 500.
        raise HTTPException(status_code=404, detail="Database connection not found or inactive")
    return found


@router.post("", response_model=QueryResponse, status_code=status.HTTP_201_CREATED)
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

    connection = await _authorize_connection(
        request, db, permissions, query_data.database_connection_id
    )

    data = query_data.model_dump(exclude={"database_connection_id"})
    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Query 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, {**data, "connection_name": connection}),
        must_create=True,
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return QueryResponse.model_validate(result.obj)


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

    ctx = rest.api_context(db, user_id)
    query = await rest.locked(_applier, ctx, query)

    # As before: fields left out (or null) are unchanged.
    patch = {
        key: value
        for key, value in query_data.model_dump(exclude={"database_connection_id"}).items()
        if value is not None
    }
    if (
        query_data.database_connection_id is not None
        and query_data.database_connection_id != query.database_connection_id
    ):
        patch["connection_name"] = await _authorize_connection(
            request, db, permissions, query_data.database_connection_id
        )
    current = _applier.spec_from_row(query, await connection_name(db, query.database_connection_id))
    spec = rest.patch_spec(_applier, query, patch, current=current)
    await rest.write(_applier, ctx, spec, existing=query)
    await rest.commit(db, ctx)
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

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, query), ctx)
    await rest.commit(db, ctx)

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
