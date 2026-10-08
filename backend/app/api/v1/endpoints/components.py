"""Components API endpoints.

Writes go through ComponentApplier, the path config apply and package install
use too: the same validation, ownership and change history on every channel.
There is no build step: a component's source is served as is.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.component import Component
from app.models.component_share import ComponentShare
from app.schemas.component import (
    ComponentCreate,
    ComponentListResponse,
    ComponentResponse,
    ComponentUpdate,
    ShareCreateRequest,
    ShareResponse,
)
from app.services.content_tokens import generate_component_render_token
from app.services.resources import rest
from app.services.resources.components import ComponentApplier

router = APIRouter(prefix="/components", tags=["components"])

_applier = ComponentApplier()


def _component_response(component: Component, user_id: str) -> ComponentResponse:
    """Build a ComponentResponse with a render token."""
    resp = ComponentResponse.model_validate(component)
    resp.render_token = generate_component_render_token(
        component.namespace, component.name, user_id
    )
    return resp


def _component_list_response(component: Component, user_id: str) -> ComponentListResponse:
    """Build a ComponentListResponse with a render token."""
    resp = ComponentListResponse.model_validate(component)
    resp.render_token = generate_component_render_token(
        component.namespace, component.name, user_id
    )
    return resp


@router.post("", response_model=ComponentResponse, status_code=status.HTTP_201_CREATED)
async def create_component(
    request: Request,
    component_data: ComponentCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new component."""
    user_id, permissions = current_user_data

    permission = "sinas.components.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create components")
    set_permission_used(request, permission)

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Component 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, component_data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    component = result.obj

    return _component_response(component, user_id)


@router.get("", response_model=list[ComponentListResponse])
async def list_components(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all components accessible to the user."""
    user_id, permissions = current_user_data

    additional_filters = Component.is_active == True
    if namespace:
        additional_filters = and_(additional_filters, Component.namespace == namespace)

    components = await Component.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=additional_filters,
    )

    set_permission_used(request, "sinas.components.read")

    return [_component_list_response(c, user_id) for c in components]


@router.get("/{namespace}/{name}", response_model=ComponentResponse)
async def get_component(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific component by namespace and name."""
    user_id, permissions = current_user_data

    component = await Component.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.components/{namespace}/{name}.read")

    return _component_response(component, user_id)


@router.put("/{namespace}/{name}", response_model=ComponentResponse)
async def update_component(
    namespace: str,
    name: str,
    component_data: ComponentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a component."""
    user_id, permissions = current_user_data

    component = await Component.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.components/{namespace}/{name}.update")

    ctx = rest.api_context(db, user_id)
    component = await rest.locked(_applier, ctx, component)
    # As before: fields left out (or null) are unchanged.
    patch = {key: value for key, value in component_data.model_dump().items() if value is not None}
    await rest.write(_applier, ctx, rest.patch_spec(_applier, component, patch), existing=component)
    await rest.commit(db, ctx)
    await db.refresh(component)

    return _component_response(component, user_id)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_component(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a component (recorded in change history, so it can be restored;
    its share links go with it)."""
    user_id, permissions = current_user_data

    component = await Component.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.components/{namespace}/{name}.delete")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, component), ctx)
    await rest.commit(db, ctx)

    return None


# --- Share Link Endpoints ---


def _share_response(share: ComponentShare) -> ShareResponse:
    return ShareResponse(
        id=str(share.id),
        token=share.token,
        component_id=str(share.component_id),
        input_data=share.input_data,
        expires_at=share.expires_at,
        max_views=share.max_views,
        view_count=share.view_count,
        label=share.label,
        mode=share.mode,
        allow_writes=share.allow_writes,
        created_at=share.created_at,
        # Every mode opens here; a viewer link forwards to the console.
        share_url=f"/components/shared/{share.token}",
    )


@router.post("/{namespace}/{name}/shares", response_model=ShareResponse)
async def create_share_link(
    namespace: str,
    name: str,
    body: ShareCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a share link for a component."""
    user_id, permissions = current_user_data

    component = await Component.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.components/{namespace}/{name}.update")

    if body.mode == "creator" and getattr(request.state, "via_api_key", False):
        # A creator link acts with its creator's full live permissions; an API
        # key's are deliberately narrower, so it may not mint one.
        raise HTTPException(
            status_code=403, detail="Creator links can't be created with an API key; sign in"
        )

    token = secrets.token_urlsafe(32)
    share = ComponentShare(
        token=token,
        component_id=component.id,
        created_by=user_id,
        input_data=body.input_data,
        expires_at=body.expires_at,
        max_views=body.max_views,
        label=body.label,
        mode=body.mode,
        allow_writes=body.allow_writes,
    )

    db.add(share)
    await db.flush()
    await db.refresh(share)

    return _share_response(share)


@router.get("/{namespace}/{name}/shares", response_model=list[ShareResponse])
async def list_share_links(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all share links for a component."""
    user_id, permissions = current_user_data

    component = await Component.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.components/{namespace}/{name}.read")

    # Your own links only: a link's token is a credential (a creator link
    # acts with its creator's permissions), so reading the component must
    # not hand out other people's.
    result = await db.execute(
        select(ComponentShare)
        .where(
            ComponentShare.component_id == component.id,
            ComponentShare.created_by == user_id,
        )
        .order_by(ComponentShare.created_at.desc())
    )
    return [_share_response(share) for share in result.scalars().all()]


@router.delete("/{namespace}/{name}/shares/{token}", status_code=204)
async def revoke_share_link(
    namespace: str,
    name: str,
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Revoke a share link."""
    user_id, permissions = current_user_data

    component = await Component.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.components/{namespace}/{name}.update")

    result = await db.execute(
        select(ComponentShare).where(
            ComponentShare.token == token,
            ComponentShare.component_id == component.id,
        )
    )
    share = result.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=404, detail="Share link not found")

    await db.delete(share)
    await db.flush()
    return None
