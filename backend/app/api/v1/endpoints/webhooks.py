"""Webhooks API endpoints.

Writes go through WebhookApplier — the same path config apply and package
install use — so validation, ownership and change history are identical on
every channel. Permission checks, including who may run the target, stay
here at the API boundary.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.webhook import Webhook
from app.schemas import WebhookCreate, WebhookResponse, WebhookUpdate
from app.schemas.spec.webhook import WebhookSpec
from app.services.resources import ApplierError, ApplyContext
from app.services.resources.patch import PatchRejected, patched_spec
from app.services.resources.webhooks import WebhookApplier

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_applier = WebhookApplier()


def _context(db: AsyncSession, user_id) -> ApplyContext:
    return ApplyContext(
        db=db,
        origin="api",
        actor_user_id=str(user_id),
        owner_user_id=str(user_id),
        # The REST API has only ever let you target your own functions.
        reference_scope_user_id=str(user_id),
    )


def _spec(data: dict) -> WebhookSpec:
    try:
        return WebhookSpec.model_validate(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=json.loads(e.json(include_url=False)))


async def _lookup(db: AsyncSession, path: str, user_id, any_owner: bool, lock: bool = False):
    """A webhook by path. Not filtered on is_active: a disabled webhook must
    stay reachable here, or it could never be enabled or deleted again."""
    query = select(Webhook).where(Webhook.path == path)
    if not any_owner:
        query = query.where(Webhook.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


def _authorize_target(request: Request, permissions: dict, spec: WebhookSpec) -> None:
    """Fail fast if the caller can't run the target: a webhook they could
    never legitimately trigger shouldn't be creatable. Mirrors invoking the
    target directly; the authoritative check is at execution time
    (permissions can be narrowed after creation). Functions are checked by
    ownership, in the applier."""
    ref = f"{spec.target_namespace}/{spec.target_name}"
    if spec.target_type == "agent":
        perm, detail = f"sinas.agents/{ref}.chat:all", f"Not authorized to chat with agent '{ref}'"
    elif spec.target_type == "pipeline":
        perm, detail = f"sinas.pipelines/{ref}.run:own", f"Not authorized to run pipeline '{ref}'"
    else:
        return
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail=detail)


def _target(spec: WebhookSpec) -> tuple:
    return (spec.target_type, spec.target_namespace, spec.target_name)


def _patch_from_update(stored: WebhookSpec, data: WebhookUpdate) -> dict:
    """The REST PATCH body as canonical spec fields.

    As before: fields sent as null are ignored, except the two that can be
    cleared (session_key_template, dedup). Target references are split per
    target type in the API; only the effective type's own fields count, and
    switching type needs the new type's name — a stale reference stored for
    another type used to be picked up silently, with no existence or
    permission check.
    """
    provided = data.model_fields_set
    patch: dict = {}
    for field in (
        "message_template", "description", "default_values", "is_active",
        "requires_auth", "response_mode",
    ):
        value = getattr(data, field)
        if value is not None:
            patch[field] = value
    if data.http_method is not None:
        patch["http_method"] = data.http_method.value
    if "session_key_template" in provided:
        patch["session_key_template"] = data.session_key_template or None
    if "dedup" in provided:
        patch["dedup"] = data.dedup.model_dump() if data.dedup else None

    target_type = data.target_type or stored.target_type
    namespace = getattr(data, f"{target_type}_namespace")
    name = getattr(data, f"{target_type}_name")
    if target_type != stored.target_type:
        patch.update(target_type=target_type, target_namespace=namespace or "default", target_name=name)
    else:
        if namespace is not None:
            patch["target_namespace"] = namespace
        if name is not None:
            patch["target_name"] = name
    return patch


async def _commit(db: AsyncSession, ctx: ApplyContext) -> None:
    await db.commit()
    await ctx.effects.flush()


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    request: Request,
    webhook_data: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new webhook."""
    user_id, permissions = current_user_data

    # Check create permission
    create_perm = "sinas.webhooks.create:own"
    if not check_permission(permissions, create_perm):
        set_permission_used(request, create_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create webhooks")
    set_permission_used(request, create_perm)

    data = webhook_data.model_dump()
    target_type = data["target_type"]
    spec = _spec({
        **{key: value for key, value in data.items() if key in WebhookSpec.model_fields},
        "http_method": webhook_data.http_method.value,
        "default_values": data["default_values"] or {},
        "target_namespace": data[f"{target_type}_namespace"],
        "target_name": data[f"{target_type}_name"],
    })

    ctx = _context(db, user_id)
    try:
        result = await _applier.apply(spec, ctx, must_create=True)
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    # After the existence check, as before: an unknown target is a 404 even
    # for a caller who couldn't run it. Raising here rolls the write back.
    _authorize_target(request, permissions, spec)

    await _commit(db, ctx)
    await db.refresh(result.obj)
    return WebhookResponse.model_validate(result.obj)


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List webhooks (own and group-accessible)."""
    user_id, permissions = current_user_data

    # Build query based on permissions
    if check_permission(permissions, "sinas.webhooks.read:all"):
        set_permission_used(request, "sinas.webhooks.read:all")
        query = select(Webhook)
    else:
        set_permission_used(request, "sinas.webhooks.read:own")
        query = select(Webhook).where(Webhook.user_id == user_id)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    webhooks = result.scalars().all()

    return [WebhookResponse.model_validate(webhook) for webhook in webhooks]


@router.get("/{path:path}", response_model=WebhookResponse)
async def get_webhook(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific webhook."""
    user_id, permissions = current_user_data

    any_owner = check_permission(permissions, "sinas.webhooks.read:all")
    webhook = await _lookup(db, path, user_id, any_owner)
    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook '{path}' not found")
    set_permission_used(
        request, "sinas.webhooks.read:all" if any_owner else "sinas.webhooks.read:own"
    )
    return WebhookResponse.model_validate(webhook)


@router.patch("/{path:path}", response_model=WebhookResponse)
async def update_webhook(
    request: Request,
    path: str,
    webhook_data: WebhookUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a webhook."""
    user_id, permissions = current_user_data

    any_owner = check_permission(permissions, "sinas.webhooks.update:all")
    webhook = await _lookup(db, path, user_id, any_owner, lock=True)
    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook '{path}' not found")
    set_permission_used(
        request, "sinas.webhooks.update:all" if any_owner else "sinas.webhooks.update:own"
    )

    stored = _applier.spec_from_row(webhook)
    ctx = _context(db, user_id)
    try:
        spec = patched_spec(stored, _patch_from_update(stored, webhook_data))
        await _applier.apply(spec, ctx, existing=webhook)
    except PatchRejected as e:
        raise HTTPException(status_code=422, detail=e.detail)
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    if _target(spec) != _target(stored):
        _authorize_target(request, permissions, spec)

    await _commit(db, ctx)
    await db.refresh(webhook)
    return WebhookResponse.model_validate(webhook)


@router.delete("/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a webhook."""
    user_id, permissions = current_user_data

    any_owner = check_permission(permissions, "sinas.webhooks.delete:all")
    webhook = await _lookup(db, path, user_id, any_owner, lock=True)
    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook '{path}' not found")
    set_permission_used(
        request, "sinas.webhooks.delete:all" if any_owner else "sinas.webhooks.delete:own"
    )

    ctx = _context(db, user_id)
    await _applier.delete(webhook, ctx)
    await _commit(db, ctx)
    return None
