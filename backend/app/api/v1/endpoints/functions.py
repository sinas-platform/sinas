"""Functions API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from typing import List, Optional
import uuid

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, require_permission, set_permission_used
from app.core.permissions import check_permission
from app.models.function import Function, FunctionVersion
from app.schemas import FunctionCreate, FunctionUpdate, FunctionResponse, FunctionVersionResponse

router = APIRouter(prefix="/functions", tags=["functions"])


@router.post("", response_model=FunctionResponse)
async def create_function(
    request: Request,
    function_data: FunctionCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new function."""
    user_id, permissions = current_user_data

    # Check namespace-based permission
    permission = f"sinas.functions.{function_data.namespace}.post:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create functions in this namespace")
    set_permission_used(request, permission)

    # Check if function name already exists in this namespace
    result = await db.execute(
        select(Function).where(
            and_(
                Function.namespace == function_data.namespace,
                Function.name == function_data.name
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Function '{function_data.namespace}/{function_data.name}' already exists")

    # Create function
    function = Function(
        user_id=user_id,
        namespace=function_data.namespace,
        name=function_data.name,
        description=function_data.description,
        code=function_data.code,
        input_schema=function_data.input_schema,
        output_schema=function_data.output_schema,
        requirements=function_data.requirements
    )

    db.add(function)
    await db.commit()
    await db.refresh(function)

    # Create initial version
    version = FunctionVersion(
        function_id=function.id,
        version=1,
        code=function.code,
        input_schema=function.input_schema,
        output_schema=function.output_schema,
        created_by=str(user_id)
    )
    db.add(version)
    await db.commit()

    return function


@router.get("", response_model=List[FunctionResponse])
async def list_functions(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List functions (own and group-accessible)."""
    user_id, permissions = current_user_data

    # Build query based on permissions
    if check_permission(permissions, "sinas.functions.*.get:all"):
        set_permission_used(request, "sinas.functions.*.get:all")
        # Admin - see all functions
        query = select(Function)
    elif check_permission(permissions, "sinas.functions.*.get:group"):
        set_permission_used(request, "sinas.functions.*.get:group")
        # Can see own and group functions
        # TODO: Get user's groups
        query = select(Function).where(Function.user_id == user_id)
    else:
        set_permission_used(request, "sinas.functions.*.get:own")
        # Own functions only
        query = select(Function).where(Function.user_id == user_id)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    functions = result.scalars().all()

    return functions


@router.get("/{namespace}/{name}", response_model=FunctionResponse)
async def get_function(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific function."""
    user_id, permissions = current_user_data

    function = await Function.get_by_name(db, namespace, name, user_id)

    if not function:
        raise HTTPException(status_code=404, detail=f"Function '{namespace}/{name}' not found")

    # Check permissions
    permission = f"sinas.functions.{namespace}.get:own"
    if check_permission(permissions, permission):
        set_permission_used(request, permission)
    else:
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view this function")

    return function


@router.put("/{namespace}/{name}", response_model=FunctionResponse)
async def update_function(
    request: Request,
    namespace: str,
    name: str,
    function_data: FunctionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a function."""
    user_id, permissions = current_user_data

    function = await Function.get_by_name(db, namespace, name, user_id)

    if not function:
        raise HTTPException(status_code=404, detail=f"Function '{namespace}/{name}' not found")

    # Check permissions
    permission = f"sinas.functions.{namespace}.put:own"
    if check_permission(permissions, permission):
        set_permission_used(request, permission)
    else:
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update this function")

    # Update fields
    if function_data.description is not None:
        function.description = function_data.description
    if function_data.code is not None:
        function.code = function_data.code
        # Create new version if code changed
        result = await db.execute(
            select(FunctionVersion)
            .where(FunctionVersion.function_id == function.id)
            .order_by(FunctionVersion.version.desc())
            .limit(1)
        )
        latest_version = result.scalar_one_or_none()
        new_version_num = (latest_version.version + 1) if latest_version else 1

        version = FunctionVersion(
            function_id=function.id,
            version=new_version_num,
            code=function.code,
            input_schema=function.input_schema if function_data.input_schema is None else function_data.input_schema,
            output_schema=function.output_schema if function_data.output_schema is None else function_data.output_schema,
            created_by=str(user_id)
        )
        db.add(version)

    if function_data.input_schema is not None:
        function.input_schema = function_data.input_schema
    if function_data.output_schema is not None:
        function.output_schema = function_data.output_schema
    if function_data.requirements is not None:
        function.requirements = function_data.requirements
    if function_data.is_active is not None:
        function.is_active = function_data.is_active

    await db.commit()
    await db.refresh(function)

    return function


@router.delete("/{namespace}/{name}")
async def delete_function(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a function."""
    user_id, permissions = current_user_data

    function = await Function.get_by_name(db, namespace, name, user_id)

    if not function:
        raise HTTPException(status_code=404, detail=f"Function '{namespace}/{name}' not found")

    # Check permissions
    permission = f"sinas.functions.{namespace}.delete:own"
    if check_permission(permissions, permission):
        set_permission_used(request, permission)
    else:
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete this function")

    await db.delete(function)
    await db.commit()

    return {"message": f"Function '{namespace}/{name}' deleted successfully"}


@router.get("/{namespace}/{name}/versions", response_model=List[FunctionVersionResponse])
async def list_function_versions(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List all versions of a function."""
    user_id, permissions = current_user_data

    # First check if function exists and user has access
    function = await Function.get_by_name(db, namespace, name, user_id)

    if not function:
        raise HTTPException(status_code=404, detail=f"Function '{namespace}/{name}' not found")

    permission = f"sinas.functions.{namespace}.get:own"
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
