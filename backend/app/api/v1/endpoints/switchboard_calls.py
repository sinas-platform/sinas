"""The switchboard's audit log: every tools/call made through a manifest's
switchboard, who made it and how it went.

`sinas.switchboard_calls.read:all` sees everything (the Switchboard audit view);
`sinas.switchboard_calls.read:own` (every member) sees the calls made as
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
from app.models.switchboard_call import SwitchboardCall
from app.schemas.switchboard_call import SwitchboardCallListResponse, SwitchboardCallResponse

router = APIRouter(prefix="/switchboard/calls", tags=["switchboard"])


def _scope(request: Request, permissions: dict[str, bool], current_user_id: str, user_id: uuid.UUID | None) -> str | None:
    """The user the listing is restricted to, or None for all."""
    if check_permission(permissions, "sinas.switchboard_calls.read:all"):
        set_permission_used(request, "sinas.switchboard_calls.read:all")
        return str(user_id) if user_id else None
    if not check_permission(permissions, "sinas.switchboard_calls.read:own"):
        set_permission_used(request, "sinas.switchboard_calls.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read switchboard calls")
    set_permission_used(request, "sinas.switchboard_calls.read:own")
    if user_id and str(user_id) != str(current_user_id):
        raise HTTPException(status_code=403, detail="You can only view your own switchboard calls")
    return current_user_id


@router.get("", response_model=SwitchboardCallListResponse)
async def list_switchboard_calls(
    request: Request,
    manifest: str | None = Query(None, description="namespace/name of the switchboard"),
    user_id: uuid.UUID | None = Query(None),
    api_key_id: uuid.UUID | None = Query(None),
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
        conditions.append(SwitchboardCall.user_id == uuid.UUID(str(scope_user)))
    if manifest:
        if "/" not in manifest:
            raise HTTPException(status_code=422, detail="manifest must be namespace/name")
        ns, name = manifest.split("/", 1)
        conditions += [SwitchboardCall.manifest_namespace == ns, SwitchboardCall.manifest_name == name]
    if api_key_id:
        conditions.append(SwitchboardCall.api_key_id == api_key_id)
    if tool:
        conditions.append(SwitchboardCall.tool_name == tool)
    if resource:
        conditions.append(SwitchboardCall.resource_ref == resource)
    if is_error is not None:
        conditions.append(SwitchboardCall.is_error == is_error)
    if since:
        conditions.append(SwitchboardCall.created_at >= since)
    if until:
        conditions.append(SwitchboardCall.created_at <= until)

    total = (await db.execute(select(func.count()).select_from(SwitchboardCall).where(*conditions))).scalar_one()
    rows = (
        await db.execute(
            select(SwitchboardCall).where(*conditions)
            .order_by(SwitchboardCall.created_at.desc(), SwitchboardCall.id.desc())
            .offset(offset).limit(limit)
        )
    ).scalars().all()
    return SwitchboardCallListResponse(
        items=[SwitchboardCallResponse.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{call_id}", response_model=SwitchboardCallResponse)
async def get_switchboard_call(
    call_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    current_user_id, permissions = current_user_data
    scope_user = _scope(request, permissions, current_user_id, None)
    row = await db.get(SwitchboardCall, call_id)
    if row is None or (scope_user and str(row.user_id) != str(scope_user)):
        raise HTTPException(status_code=404, detail="Switchboard call not found")
    return SwitchboardCallResponse.model_validate(row)
