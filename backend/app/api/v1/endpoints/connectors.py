"""Connectors API endpoints."""
import ipaddress
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.connector import Connector
from app.schemas.connector import (
    ConnectorCreate,
    ConnectorResponse,
    ConnectorTestRequest,
    ConnectorTestResponse,
    ConnectorUpdate,
    OpenAPIImportRequest,
    OpenAPIImportResponse,
    OperationConfig,
)
from app.services.connector_openapi import extract_operations, parse_openapi_spec
from app.services.connector_service import connector_service
from app.services.package_service import detach_if_package_managed

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post("/parse-openapi", response_model=OpenAPIImportResponse)
async def parse_openapi_standalone(
    request: Request,
    import_data: OpenAPIImportRequest,
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Parse an OpenAPI spec and return operations. No connector required."""
    _user_id, permissions = current_user_data

    permission = "sinas.connectors.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized")
    set_permission_used(request, permission)

    spec_str = import_data.spec
    if not spec_str and import_data.spec_url:
        parsed = urlparse(import_data.spec_url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")
        try:
            import socket
            resolved_ip = socket.getaddrinfo(parsed.hostname, None, socket.AF_UNSPEC)[0][4][0]
            if ipaddress.ip_address(resolved_ip).is_private:
                raise HTTPException(status_code=400, detail="URLs pointing to private networks are not allowed")
        except socket.gaierror:
            raise HTTPException(status_code=400, detail="Could not resolve hostname")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(import_data.spec_url)
                resp.raise_for_status()
                spec_str = resp.text
        except httpx.HTTPError as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch spec: {e}")

    if not spec_str:
        raise HTTPException(status_code=400, detail="Either 'spec' or 'spec_url' must be provided")

    try:
        spec = parse_openapi_spec(spec_str)
        raw_ops = extract_operations(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if import_data.operations:
        raw_ops = [op for op in raw_ops if op["name"] in import_data.operations]

    warnings: list[str] = []
    parsed_ops = []
    for op in raw_ops:
        try:
            parsed_ops.append(OperationConfig(**op))
        except Exception as e:
            warnings.append(f"Skipped operation '{op.get('name', '?')}': {e}")

    return OpenAPIImportResponse(operations=parsed_ops, warnings=warnings)


@router.post("", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    request: Request,
    data: ConnectorCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new connector."""
    user_id, permissions = current_user_data

    permission = "sinas.connectors.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create connectors")
    set_permission_used(request, permission)

    # Check uniqueness
    result = await db.execute(
        select(Connector).where(
            and_(Connector.namespace == data.namespace, Connector.name == data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Connector '{data.namespace}/{data.name}' already exists")

    connector = Connector(
        user_id=user_id,
        namespace=data.namespace,
        name=data.name,
        description=data.description,
        base_url=data.base_url,
        auth=data.auth.model_dump(),
        headers=data.headers,
        retry=data.retry.model_dump(),
        timeout_seconds=data.timeout_seconds,
        operations=[op.model_dump() for op in data.operations],
    )
    db.add(connector)
    await db.flush()
    await db.refresh(connector)
    return ConnectorResponse.model_validate(connector)


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List connectors."""
    user_id, permissions = current_user_data

    connectors = await Connector.list_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read"
    )
    set_permission_used(request, "sinas.connectors.read")
    return [ConnectorResponse.model_validate(c) for c in connectors]


@router.get("/{namespace}/{name}", response_model=ConnectorResponse)
async def get_connector(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific connector."""
    user_id, permissions = current_user_data

    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.connectors/{namespace}/{name}.read")
    return ConnectorResponse.model_validate(connector)


@router.put("/{namespace}/{name}", response_model=ConnectorResponse)
async def update_connector(
    request: Request,
    namespace: str,
    name: str,
    data: ConnectorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a connector."""
    user_id, permissions = current_user_data

    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="update",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.connectors/{namespace}/{name}.update")

    detach_if_package_managed(connector)

    if data.namespace is not None:
        connector.namespace = data.namespace
    if data.name is not None:
        connector.name = data.name
    if data.description is not None:
        connector.description = data.description
    if data.base_url is not None:
        connector.base_url = data.base_url
    if data.auth is not None:
        connector.auth = data.auth.model_dump()
    if data.headers is not None:
        connector.headers = data.headers
    if data.retry is not None:
        connector.retry = data.retry.model_dump()
    if data.timeout_seconds is not None:
        connector.timeout_seconds = data.timeout_seconds
    if data.operations is not None:
        connector.operations = [op.model_dump() for op in data.operations]
    if data.is_active is not None:
        connector.is_active = data.is_active

    await db.flush()
    await db.refresh(connector)
    return ConnectorResponse.model_validate(connector)


@router.delete("/{namespace}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a connector."""
    user_id, permissions = current_user_data

    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="delete",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.connectors/{namespace}/{name}.delete")

    await db.delete(connector)
    await db.flush()
    return None


@router.post("/{namespace}/{name}/import-openapi", response_model=OpenAPIImportResponse)
async def import_openapi(
    request: Request,
    namespace: str,
    name: str,
    import_data: OpenAPIImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Import operations from an OpenAPI spec into a connector."""
    user_id, permissions = current_user_data

    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="update",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.connectors/{namespace}/{name}.update")

    # Get spec
    spec_str = import_data.spec
    if not spec_str and import_data.spec_url:
        parsed = urlparse(import_data.spec_url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")
        try:
            import socket
            resolved_ip = socket.getaddrinfo(parsed.hostname, None, socket.AF_UNSPEC)[0][4][0]
            if ipaddress.ip_address(resolved_ip).is_private:
                raise HTTPException(status_code=400, detail="URLs pointing to private networks are not allowed")
        except socket.gaierror:
            raise HTTPException(status_code=400, detail="Could not resolve hostname")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(import_data.spec_url)
                resp.raise_for_status()
                spec_str = resp.text
        except httpx.HTTPError as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch spec: {e}")

    if not spec_str:
        raise HTTPException(status_code=400, detail="Either 'spec' or 'spec_url' must be provided")

    try:
        spec = parse_openapi_spec(spec_str)
        raw_ops = extract_operations(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Filter operations if requested
    if import_data.operations:
        raw_ops = [op for op in raw_ops if op["name"] in import_data.operations]

    warnings = []
    parsed_ops = []
    for op in raw_ops:
        try:
            parsed_ops.append(OperationConfig(**op))
        except Exception as e:
            warnings.append(f"Skipped operation '{op.get('name', '?')}': {e}")

    applied = 0
    if import_data.apply:
        # Merge into connector: add new, update existing by name, keep manually-added
        existing_names = {op.get("name") for op in connector.operations}
        new_ops = list(connector.operations)  # Copy existing

        for op in parsed_ops:
            op_dict = op.model_dump()
            # Find existing operation with same name
            found = False
            for i, existing_op in enumerate(new_ops):
                if existing_op.get("name") == op.name:
                    new_ops[i] = op_dict
                    found = True
                    break
            if not found:
                new_ops.append(op_dict)
            applied += 1

        connector.operations = new_ops
        await db.flush()

    return OpenAPIImportResponse(
        operations=parsed_ops,
        warnings=warnings,
        applied=applied,
    )


@router.post("/{namespace}/{name}/test/{operation_name}", response_model=ConnectorTestResponse)
async def test_operation(
    request: Request,
    namespace: str,
    name: str,
    operation_name: str,
    test_data: ConnectorTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Test a specific connector operation."""
    user_id, permissions = current_user_data

    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.connectors/{namespace}/{name}.read")

    # Get user token for sinas_token auth
    from app.core.auth import create_access_token
    from app.models.user import User
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    user_token = create_access_token(user_id, user.email if user else "unknown")

    try:
        result = await connector_service.execute_operation(
            db=db,
            connector=connector,
            operation_name=operation_name,
            parameters=test_data.parameters,
            user_token=user_token,
        )
        return ConnectorTestResponse(
            status_code=result["status_code"],
            headers={k: v for k, v in result.get("headers", {}).items() if isinstance(v, str)},
            body=result.get("body"),
            elapsed_ms=result.get("elapsed_ms", 0),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Request failed: {e}")
