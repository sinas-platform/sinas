"""The MCP gateway's audit log: every tools/call made through a manifest's
gateway, who made it and how it went.

`sinas.gateway_calls.read:all` sees everything (the hub's audit view);
`sinas.gateway_calls.read:own` (every member) sees the calls made as
oneself, including through one's own API keys.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.gateway_call import GatewayCall
from app.schemas.gateway_call import GatewayCallListResponse, GatewayCallResponse

router = APIRouter(prefix="/gateway/calls", tags=["gateway"])


def _scope(request: Request, permissions: dict[str, bool], current_user_id: str, user_id: str | None) -> str | None:
    """The user the listing is restricted to, or None for all."""
    if check_permission(permissions, "sinas.gateway_calls.read:all"):
        set_permission_used(request, "sinas.gateway_calls.read:all")
        return user_id
    if not check_permission(permissions, "sinas.gateway_calls.read:own"):
        set_permission_used(request, "sinas.gateway_calls.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read gateway calls")
    set_permission_used(request, "sinas.gateway_calls.read:own")
    if user_id and user_id != current_user_id:
        raise HTTPException(status_code=403, detail="You can only view your own gateway calls")
    return current_user_id


@router.get("", response_model=GatewayCallListResponse)
async def list_gateway_calls(
    request: Request,
    manifest: str | None = Query(None, description="namespace/name of the gateway"),
    user_id: str | None = Query(None),
    api_key_id: str | None = Query(None),
    tool: str | None = Query(None, description="Tool name as the agent called it"),
    resource: str | None = Query(None, description="namespace/name of the underlying resource"),
    is_error: bool | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    current_user_id, permissions = current_user_data
    scope_user = _scope(request, permissions, current_user_id, user_id)

    conditions = []
    if scope_user:
        conditions.append(GatewayCall.user_id == uuid.UUID(str(scope_user)))
    if manifest:
        if "/" not in manifest:
            raise HTTPException(status_code=422, detail="manifest must be namespace/name")
        ns, name = manifest.split("/", 1)
        conditions += [GatewayCall.manifest_namespace == ns, GatewayCall.manifest_name == name]
    if api_key_id:
        try:
            conditions.append(GatewayCall.api_key_id == uuid.UUID(api_key_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="api_key_id must be a UUID") from None
    if tool:
        conditions.append(GatewayCall.tool_name == tool)
    if resource:
        conditions.append(GatewayCall.resource_ref == resource)
    if is_error is not None:
        conditions.append(GatewayCall.is_error == is_error)
    if since:
        conditions.append(GatewayCall.created_at >= since)
    if until:
        conditions.append(GatewayCall.created_at <= until)

    total = (await db.execute(select(func.count()).select_from(GatewayCall).where(*conditions))).scalar_one()
    rows = (
        await db.execute(
            select(GatewayCall).where(*conditions)
            .order_by(GatewayCall.created_at.desc(), GatewayCall.id.desc())
            .offset(offset).limit(limit)
        )
    ).scalars().all()
    return GatewayCallListResponse(
        items=[GatewayCallResponse.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{call_id}", response_model=GatewayCallResponse)
async def get_gateway_call(
    call_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    current_user_id, permissions = current_user_data
    scope_user = _scope(request, permissions, current_user_id, None)
    row = await db.get(GatewayCall, call_id)
    if row is None or (scope_user and str(row.user_id) != str(scope_user)):
        raise HTTPException(status_code=404, detail="Gateway call not found")
    return GatewayCallResponse.model_validate(row)
