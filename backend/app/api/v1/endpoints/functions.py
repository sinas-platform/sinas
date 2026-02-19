"""Functions API endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.function import Function, FunctionVersion
from app.models.package import InstalledPackage
from app.schemas import FunctionCreate, FunctionResponse, FunctionUpdate, FunctionVersionResponse
from app.services.execution_engine import executor

router = APIRouter(prefix="/functions", tags=["functions"])


async def validate_requirements(requirements: list[str], db: AsyncSession) -> None:
    """
    Validate that all function requirements are admin-approved packages.

    Raises HTTPException if any requirement is not approved.
    """
    if not requirements:
        return

    # Get all approved packages
    result = await db.execute(select(InstalledPackage.package_name))
    approved_packages = {row[0] for row in result.all()}

    # Check each requirement
    unapproved = []
    for req in requirements:
        # Extract package name from requirement (e.g., "pandas==2.0.0" -> "pandas")
        package_name = (
            req.split("==")[0].split(">=")[0].split("<=")[0].split(">")[0].split("<")[0].strip()
        )

        if package_name not in approved_packages:
            unapproved.append(package_name)

    if unapproved:
        raise HTTPException(
            status_code=400,
            detail=f"Unapproved packages in requirements: {', '.join(unapproved)}. Contact admin to approve these packages first.",
        )


@router.post("", response_model=FunctionResponse)
async def create_function(
    request: Request,
    function_data: FunctionCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new function."""
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

    # Validate requirements against approved packages
    await validate_requirements(function_data.requirements, db)

    # Check if function name already exists in this namespace
    result = await db.execute(
        select(Function).where(
            and_(Function.namespace == function_data.namespace, Function.name == function_data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Function '{function_data.namespace}/{function_data.name}' already exists",
        )

    # Create function
    function = Function(
        user_id=user_id,
        namespace=function_data.namespace,
        name=function_data.name,
        description=function_data.description,
        code=function_data.code,
        input_schema=function_data.input_schema,
        output_schema=function_data.output_schema,
        requirements=function_data.requirements,
        shared_pool=function_data.shared_pool,
        requires_approval=function_data.requires_approval,
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
        created_by=str(user_id),
    )
    db.add(version)
    await db.commit()

    return FunctionResponse.model_validate(function)


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

    return [FunctionResponse.model_validate(f) for f in functions]


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

    return FunctionResponse.model_validate(function)


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

    # Validate requirements if being updated
    if function_data.requirements is not None:
        await validate_requirements(function_data.requirements, db)

    # Check for namespace/name conflict if renaming
    new_namespace = function_data.namespace or function.namespace
    new_name = function_data.name or function.name
    if new_namespace != function.namespace or new_name != function.name:
        result = await db.execute(
            select(Function).where(
                and_(
                    Function.namespace == new_namespace,
                    Function.name == new_name,
                    Function.id != function.id,
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail=f"Function '{new_namespace}/{new_name}' already exists"
            )

    # Update fields
    if function_data.namespace is not None:
        function.namespace = function_data.namespace
    if function_data.name is not None:
        function.name = function_data.name
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
            input_schema=function.input_schema
            if function_data.input_schema is None
            else function_data.input_schema,
            output_schema=function.output_schema
            if function_data.output_schema is None
            else function_data.output_schema,
            created_by=str(user_id),
        )
        db.add(version)

    if function_data.input_schema is not None:
        function.input_schema = function_data.input_schema
    if function_data.output_schema is not None:
        function.output_schema = function_data.output_schema
    if function_data.requirements is not None:
        function.requirements = function_data.requirements
    if function_data.shared_pool is not None:
        function.shared_pool = function_data.shared_pool
    if function_data.requires_approval is not None:
        function.requires_approval = function_data.requires_approval
    if function_data.is_active is not None:
        function.is_active = function_data.is_active
    if function_data.enabled_namespaces is not None:
        function.enabled_namespaces = function_data.enabled_namespaces

    await db.commit()
    await db.refresh(function)

    # Clear execution engine cache to ensure updated code is used
    executor.clear_cache()

    return FunctionResponse.model_validate(function)


@router.delete("/{namespace}/{name}")
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

    await db.delete(function)
    await db.commit()

    # Clear execution engine cache
    executor.clear_cache()

    return {"message": f"Function '{namespace}/{name}' deleted successfully"}


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


@router.post("/{namespace}/{name}/execute")
async def execute_function(
    request: Request,
    namespace: str,
    name: str,
    input_data: dict,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Execute a function directly from the management UI."""
    user_id, permissions = current_user_data

    # Load function
    function = await Function.get_by_name(db, namespace, name)
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")

    # Check execute permission
    permission = f"sinas.functions/{namespace}/{name}.execute:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to execute this function")

    set_permission_used(request, permission)

    # Execute function via queue (shows up in Jobs, runs on worker)
    from app.models.execution import TriggerType
    from app.services.queue_service import queue_service

    execution_id = str(uuid.uuid4())

    try:
        result = await queue_service.enqueue_and_wait(
            function_namespace=namespace,
            function_name=name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=TriggerType.MANUAL.value,
            trigger_id="management-ui",
            user_id=user_id,
        )

        return {"status": "success", "execution_id": execution_id, "result": result}
    except Exception as e:
        return {"status": "error", "execution_id": execution_id, "error": str(e)}
