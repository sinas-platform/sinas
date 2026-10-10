"""Functions API endpoints.

Writes go through FunctionApplier, the path config apply and package install
use too: the same ownership, versions and change history on every channel.
Code execution being off and the shared-pool permission gate this API only;
config and packages may still declare functions.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.function import Function, FunctionVersion
from app.schemas import FunctionCreate, FunctionResponse, FunctionUpdate, FunctionVersionResponse
from app.services.execution_engine import executor
from app.services.icon_resolver import resolve_icon_url
from app.services.resources import rest
from app.services.resources.functions import FunctionApplier

router = APIRouter(prefix="/functions", tags=["functions"])

_applier = FunctionApplier()


async def _function_response(func: "Function", db: AsyncSession) -> FunctionResponse:
    """Build FunctionResponse with resolved icon_url."""
    resp = FunctionResponse.model_validate(func)
    resp.icon_url = await resolve_icon_url(func.icon, db)
    return resp


def _require_code_execution() -> None:
    """With code execution off, functions can be viewed (and deleted) but not
    created or changed: they could never run, and the console only hiding its
    buttons left the API — and the editor's URL — open."""
    if not settings.code_execution_enabled:
        raise HTTPException(
            status_code=403,
            detail="Code execution is disabled on this deployment (CODE_EXECUTION_ENABLED=false): "
            "functions can't be created or changed.",
        )


@router.post("", response_model=FunctionResponse, status_code=status.HTTP_201_CREATED)
async def create_function(
    request: Request,
    function_data: FunctionCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new function."""
    _require_code_execution()
    user_id, permissions = current_user_data

    # Check permission to create functions
    permission = "sinas.functions.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create functions")
    set_permission_used(request, permission)

    # Check shared_pool permission (admin-only)
    if function_data.shared_pool:
        shared_pool_permission = "sinas.functions.shared_pool:all"
        if not check_permission(permissions, shared_pool_permission):
            set_permission_used(request, shared_pool_permission, has_perm=False)
            raise HTTPException(
                status_code=403,
                detail="Not authorized to create shared pool functions (admin only)",
            )
        set_permission_used(request, shared_pool_permission)

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Function 'ns/name' already exists", as before; the
    # applier records version 1.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, function_data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return await _function_response(result.obj, db)


@router.get("", response_model=list[FunctionResponse])
async def list_functions(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List functions (own or all based on permissions)."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware filtering
    functions = await Function.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        skip=skip,
        limit=limit,
    )

    set_permission_used(request, "sinas.functions.read")

    return [await _function_response(f, db) for f in functions]


@router.get("/{namespace}/{name}", response_model=FunctionResponse)
async def get_function(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific function."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    function = await Function.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.functions/{namespace}/{name}.read")

    return await _function_response(function, db)


@router.put("/{namespace}/{name}", response_model=FunctionResponse)
async def update_function(
    request: Request,
    namespace: str,
    name: str,
    function_data: FunctionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a function."""
    _require_code_execution()
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    function = await Function.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.functions/{namespace}/{name}.update")

    # Check shared_pool permission (admin-only) if trying to enable it
    if function_data.shared_pool is not None and function_data.shared_pool:
        shared_pool_permission = "sinas.functions.shared_pool:all"
        if not check_permission(permissions, shared_pool_permission):
            set_permission_used(request, shared_pool_permission, has_perm=False)
            raise HTTPException(
                status_code=403, detail="Not authorized to enable shared pool (admin only)"
            )
        set_permission_used(request, shared_pool_permission)

    ctx = rest.api_context(db, user_id)
    function = await rest.locked(_applier, ctx, function)
    # As before: a field left out or sent as null stays as it is, and a new
    # namespace/name renames it (a clash is a 400). A new version is recorded
    # when code or schemas actually change.
    patch = {
        field: value
        for field, value in function_data.model_dump(exclude_unset=True).items()
        if value is not None
    }
    await rest.write(_applier, ctx, rest.patch_spec(_applier, function, patch), existing=function)
    await rest.commit(db, ctx)
    await db.refresh(function)

    # Clear execution engine cache to ensure updated code is used
    executor.clear_cache()

    return await _function_response(function, db)


@router.delete("/{namespace}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_function(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a function."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    function = await Function.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.functions/{namespace}/{name}.delete")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, function), ctx)
    await rest.commit(db, ctx)

    # Clear execution engine cache
    executor.clear_cache()

    return None


@router.get("/{namespace}/{name}/versions", response_model=list[FunctionVersionResponse])
async def list_function_versions(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all versions of a function."""
    user_id, permissions = current_user_data

    # First check if function exists and user has access
    function = await Function.get_by_name(db, namespace, name, user_id)

    if not function:
        raise HTTPException(status_code=404, detail=f"Function '{namespace}/{name}' not found")

    permission = f"sinas.functions/{namespace}/{name}.read:own"
    if check_permission(permissions, permission):
        set_permission_used(request, permission)
    else:
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view this function")

    # Get versions
    result = await db.execute(
        select(FunctionVersion)
        .where(FunctionVersion.function_id == function.id)
        .order_by(FunctionVersion.version.desc())
    )
    versions = result.scalars().all()

    return versions


