"""Components API endpoints."""
import secrets
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
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
)
from app.api.runtime.endpoints.components import generate_component_render_token
from app.services.package_service import detach_if_package_managed

router = APIRouter(prefix="/components", tags=["components"])


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


async def _do_compile(component_id, namespace: str, name: str):
    """Background task to compile a component via the builder service."""
    from app.core.database import AsyncSessionLocal
    from app.services.component_builder import ComponentBuilderService

    async with AsyncSessionLocal() as db:
        component = await db.execute(
            select(Component).where(Component.id == component_id)
        )
        component = component.scalar_one_or_none()
        if not component:
            return

        component.compile_status = "compiling"
        await db.commit()

        builder = ComponentBuilderService()
        result = await builder.compile(component.source_code)

        if result["success"]:
            component.compiled_bundle = result["bundle"]
            component.source_map = result.get("sourceMap")
            component.compile_status = "success"
            component.compile_errors = None
        else:
            component.compile_status = "error"
            component.compile_errors = result.get("errors", [])
            component.compiled_bundle = None
            component.source_map = None

        await db.commit()


@router.post("", response_model=ComponentResponse)
async def create_component(
    request: Request,
    component_data: ComponentCreate,
    background_tasks: BackgroundTasks,
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

    # Check for existing component with same namespace/name
    result = await db.execute(
        select(Component).where(
            and_(
                Component.namespace == component_data.namespace,
                Component.name == component_data.name,
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Component '{component_data.namespace}/{component_data.name}' already exists",
        )

    component = Component(
        user_id=user_id,
        namespace=component_data.namespace,
        name=component_data.name,
        title=component_data.title,
        description=component_data.description,
        source_code=component_data.source_code,
        input_schema=component_data.input_schema,
        enabled_agents=component_data.enabled_agents or [],
        enabled_functions=component_data.enabled_functions or [],
        enabled_queries=component_data.enabled_queries or [],
        enabled_components=component_data.enabled_components or [],
        state_namespaces_readonly=component_data.state_namespaces_readonly or [],
        state_namespaces_readwrite=component_data.state_namespaces_readwrite or [],
        css_overrides=component_data.css_overrides,
        visibility=component_data.visibility,
        compile_status="pending",
    )

    db.add(component)
    await db.commit()
    await db.refresh(component)

    # Trigger background compilation
    background_tasks.add_task(_do_compile, component.id, component.namespace, component.name)

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
    background_tasks: BackgroundTasks,
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

    detach_if_package_managed(component)

    # Check for namespace/name conflicts if changing
    new_namespace = component_data.namespace or component.namespace
    new_name = component_data.name or component.name

    if new_namespace != component.namespace or new_name != component.name:
        result = await db.execute(
            select(Component).where(
                and_(
                    Component.namespace == new_namespace,
                    Component.name == new_name,
                    Component.id != component.id,
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Component '{new_namespace}/{new_name}' already exists",
            )

    source_changed = False

    # Update fields
    if component_data.namespace is not None:
        component.namespace = component_data.namespace
    if component_data.name is not None:
        component.name = component_data.name
    if component_data.title is not None:
        component.title = component_data.title
    if component_data.description is not None:
        component.description = component_data.description
    if component_data.source_code is not None:
        if component_data.source_code != component.source_code:
            component.source_code = component_data.source_code
            component.compile_status = "pending"
            component.compiled_bundle = None
            component.source_map = None
            component.compile_errors = None
            component.version += 1
            source_changed = True
    if component_data.input_schema is not None:
        component.input_schema = component_data.input_schema
    if component_data.enabled_agents is not None:
        component.enabled_agents = component_data.enabled_agents
    if component_data.enabled_functions is not None:
        component.enabled_functions = component_data.enabled_functions
    if component_data.enabled_queries is not None:
        component.enabled_queries = component_data.enabled_queries
    if component_data.enabled_components is not None:
        component.enabled_components = component_data.enabled_components
    if component_data.state_namespaces_readonly is not None:
        component.state_namespaces_readonly = component_data.state_namespaces_readonly
    if component_data.state_namespaces_readwrite is not None:
        component.state_namespaces_readwrite = component_data.state_namespaces_readwrite
    if component_data.css_overrides is not None:
        component.css_overrides = component_data.css_overrides
    if component_data.visibility is not None:
        component.visibility = component_data.visibility
    if component_data.is_active is not None:
        component.is_active = component_data.is_active
    if component_data.is_published is not None:
        component.is_published = component_data.is_published

    await db.commit()
    await db.refresh(component)

    # Trigger recompilation if source changed
    if source_changed:
        background_tasks.add_task(_do_compile, component.id, component.namespace, component.name)

    return _component_response(component, user_id)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_component(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Soft-delete a component."""
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

    component.is_active = False
    await db.commit()

    return None


@router.post("/{namespace}/{name}/compile", response_model=ComponentResponse)
async def compile_component(
    namespace: str,
    name: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Trigger compilation of a component."""
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

    component.compile_status = "pending"
    component.compile_errors = None
    await db.commit()
    await db.refresh(component)

    background_tasks.add_task(_do_compile, component.id, component.namespace, component.name)

    return _component_response(component, user_id)


# --- Share Link Endpoints ---


class ShareCreateRequest(BaseModel):
    input_data: Optional[dict[str, Any]] = None
    expires_at: Optional[datetime] = None
    max_views: Optional[int] = None
    label: Optional[str] = None


class ShareResponse(BaseModel):
    id: str
    token: str
    component_id: str
    input_data: Optional[dict[str, Any]]
    expires_at: Optional[datetime]
    max_views: Optional[int]
    view_count: int
    label: Optional[str]
    created_at: datetime
    share_url: str

    class Config:
        from_attributes = True


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

    token = secrets.token_urlsafe(32)
    share = ComponentShare(
        token=token,
        component_id=component.id,
        created_by=user_id,
        input_data=body.input_data,
        expires_at=body.expires_at,
        max_views=body.max_views,
        label=body.label,
    )

    db.add(share)
    await db.commit()
    await db.refresh(share)

    return ShareResponse(
        id=str(share.id),
        token=share.token,
        component_id=str(share.component_id),
        input_data=share.input_data,
        expires_at=share.expires_at,
        max_views=share.max_views,
        view_count=share.view_count,
        label=share.label,
        created_at=share.created_at,
        share_url=f"/components/shared/{share.token}",
    )


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

    result = await db.execute(
        select(ComponentShare).where(ComponentShare.component_id == component.id)
    )
    shares = result.scalars().all()

    return [
        ShareResponse(
            id=str(s.id),
            token=s.token,
            component_id=str(s.component_id),
            input_data=s.input_data,
            expires_at=s.expires_at,
            max_views=s.max_views,
            view_count=s.view_count,
            label=s.label,
            created_at=s.created_at,
            share_url=f"/components/shared/{s.token}",
        )
        for s in shares
    ]


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
    await db.commit()
    return None
