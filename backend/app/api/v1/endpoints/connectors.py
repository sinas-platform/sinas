"""Connectors API endpoints.

Writes go through ConnectorApplier — the same path config apply and package
install use — so validation, ownership and change history are identical on
every channel. OAuth sign-in and test calls are runtime operations and stay
here.
"""
import ipaddress
import json
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.oauth_state import (
    BIND_COOKIE_NAME,
    generate_browser_nonce,
    generate_pkce_pair,
    store_state,
)
from app.core.permissions import check_permission
from app.models.connector import Connector
from app.models.connector_oauth_token import ConnectorOAuthToken
from app.schemas.connector import (
    ConnectorCreate,
    ConnectorResponse,
    ConnectorTestRequest,
    ConnectorTestResponse,
    ConnectorUpdate,
    OAuthAuthorizeResponse,
    OAuthStatusResponse,
    OpenAPIImportRequest,
    OpenAPIImportResponse,
    OperationConfig,
)
from app.services.connector_openapi import extract_auth, extract_operations, parse_openapi_spec
from app.services.connector_service import ConnectorAuthError, connector_service
from app.schemas.spec.connector import ConnectorSpec
from app.services.resources import ApplierError, ApplyContext
from app.services.resources.connectors import ConnectorApplier
from app.services.resources.patch import PatchRejected, patched_spec

router = APIRouter(prefix="/connectors", tags=["connectors"])

_applier = ConnectorApplier()


def _context(db: AsyncSession, user_id) -> ApplyContext:
    return ApplyContext(
        db=db, origin="api", actor_user_id=str(user_id), owner_user_id=str(user_id)
    )


def _spec(data: dict) -> ConnectorSpec:
    from pydantic import ValidationError

    try:
        return ConnectorSpec.model_validate(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=json.loads(e.json(include_url=False)))


async def _locked(ctx: ApplyContext, authorized: Connector) -> Connector:
    """The row the permission check authorized, locked until commit (the
    permission lookup doesn't lock). Re-read by id, never by name: a
    connector deleted and recreated under that name meanwhile is another
    resource, which this caller may not be allowed to touch."""
    connector = await _applier.find_by_id(ctx, authorized.id)
    if connector is None:  # deleted between the permission check and now
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


async def _write(ctx: ApplyContext, spec: ConnectorSpec, **kwargs):
    from sqlalchemy.exc import IntegrityError

    try:
        return await _applier.apply(spec, ctx, **kwargs)
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except IntegrityError:
        # Lost a race to a concurrent create or rename of the same name (the
        # applier's check can't lock a row that doesn't exist yet).
        raise HTTPException(status_code=400, detail=f"Connector '{spec.key}' already exists")


async def _commit(db: AsyncSession, ctx: ApplyContext) -> None:
    await db.commit()
    await ctx.effects.flush()


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

    # Extract spec metadata
    info = spec.get("info", {})
    spec_title = info.get("title")
    spec_description = info.get("description")
    servers = spec.get("servers", [])
    spec_base_url = servers[0].get("url") if servers else None

    # Derive base_url from spec_url if not in spec
    if not spec_base_url and import_data.spec_url:
        parsed_url = urlparse(import_data.spec_url)
        spec_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    if import_data.operations:
        raw_ops = [op for op in raw_ops if op["name"] in import_data.operations]

    warnings: list[str] = []
    parsed_ops = []
    for op in raw_ops:
        try:
            parsed_ops.append(OperationConfig(**op))
        except Exception as e:
            warnings.append(f"Skipped operation '{op.get('name', '?')}': {e}")

    return OpenAPIImportResponse(
        operations=parsed_ops,
        warnings=warnings,
        spec_title=spec_title,
        spec_description=spec_description,
        spec_base_url=spec_base_url,
        suggested_auth=extract_auth(spec),
    )


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

    ctx = _context(db, user_id)
    # A clash is a 400 "Connector 'ns/name' already exists", as before — now
    # also when two creates race (see _write).
    result = await _write(ctx, _spec(data.model_dump()), must_create=True)
    await _commit(db, ctx)
    await db.refresh(result.obj)
    return ConnectorResponse.model_validate(result.obj)


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

    ctx = _context(db, user_id)
    connector = await _locked(ctx, connector)
    # As before: fields left out (or sent as null) are unchanged; auth,
    # headers and operations are replaced whole. A rename onto an existing
    # connector is now a 400 rather than a 500.
    patch = {key: value for key, value in data.model_dump().items() if value is not None}
    try:
        spec = patched_spec(_applier.spec_from_row(connector), patch)
    except PatchRejected as e:
        raise HTTPException(status_code=422, detail=e.detail)
    await _write(ctx, spec, existing=connector)
    await _commit(db, ctx)
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

    ctx = _context(db, user_id)
    await _applier.delete(await _locked(ctx, connector), ctx)
    await _commit(db, ctx)
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
    if import_data.apply and parsed_ops:
        # Merge into the connector's operations (replace by name, append the
        # rest, keep manually added ones) — as a normal edit: validated,
        # recorded in history, and detaching a managed connector.
        ctx = _context(db, user_id)
        connector = await _locked(ctx, connector)
        stored = _applier.spec_from_row(connector)
        operations = [op.model_dump() for op in stored.operations]
        for op in parsed_ops:
            op_dict = op.model_dump()
            index = next(
                (i for i, existing in enumerate(operations) if existing.get("name") == op.name),
                None,
            )
            if index is None:
                operations.append(op_dict)
            else:
                operations[index] = op_dict
            applied += 1
        try:
            spec = patched_spec(stored, {"operations": operations})
        except PatchRejected as e:
            raise HTTPException(status_code=422, detail=e.detail)
        await _write(ctx, spec, existing=connector)
        await _commit(db, ctx)

    return OpenAPIImportResponse(
        operations=parsed_ops,
        warnings=warnings,
        applied=applied,
    )


@router.post("/{namespace}/{name}/oauth/authorize", response_model=OAuthAuthorizeResponse)
async def begin_oauth_authorization(
    request: Request,
    response: Response,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Begin the per-user OAuth authorization-code flow; returns the provider URL to open."""
    user_id, permissions = current_user_data
    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.connectors/{namespace}/{name}.read")

    if (connector.auth or {}).get("type") != "oauth2_authorization_code":
        raise HTTPException(status_code=400, detail="Connector is not configured for OAuth authorization-code auth")

    verifier, challenge = generate_pkce_pair()
    # Bind this flow to the initiating browser: the nonce is stored with the state AND set
    # as an HttpOnly cookie here; the callback requires the two to match. This is what stops
    # an attacker minting a state and having a victim complete it under the attacker's account.
    browser_nonce = generate_browser_nonce()
    state = await store_state(
        user_id=str(user_id), namespace=namespace, name=name,
        code_verifier=verifier, browser_nonce=browser_nonce,
    )
    url = connector_service.build_authorize_url(connector.auth, state, challenge)
    if not url:
        raise HTTPException(status_code=400, detail="Connector OAuth config is incomplete (authorize_url/client_id)")
    response.set_cookie(
        key=BIND_COOKIE_NAME,
        value=browser_nonce,
        max_age=600,  # matches STATE_TTL_SECONDS
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",  # sent on the provider's top-level GET redirect back to the callback
        path="/",
    )
    return OAuthAuthorizeResponse(authorize_url=url)


@router.get("/{namespace}/{name}/oauth/status", response_model=OAuthStatusResponse)
async def oauth_status(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Report whether the current user has a stored OAuth token for this connector."""
    user_id, permissions = current_user_data
    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    result = await db.execute(
        select(ConnectorOAuthToken).where(
            and_(ConnectorOAuthToken.connector_id == connector.id, ConnectorOAuthToken.user_id == user_id)
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return OAuthStatusResponse(connected=False)
    return OAuthStatusResponse(connected=True, expires_at=row.expires_at, scope=row.scope)


@router.delete("/{namespace}/{name}/oauth/token", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_oauth(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete the current user's stored OAuth token for this connector."""
    user_id, permissions = current_user_data
    connector = await Connector.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    await db.execute(
        delete(ConnectorOAuthToken).where(
            and_(ConnectorOAuthToken.connector_id == connector.id, ConnectorOAuthToken.user_id == user_id)
        )
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
            user_id=user_id,
        )
        return ConnectorTestResponse(
            status_code=result["status_code"],
            headers={k: v for k, v in result.get("headers", {}).items() if isinstance(v, str)},
            body=result.get("body"),
            elapsed_ms=result.get("elapsed_ms", 0),
        )
    except ConnectorAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Request failed: {e}")
