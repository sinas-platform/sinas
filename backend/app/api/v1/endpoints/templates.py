"""Template endpoints.

Writes go through TemplateApplier, the path config apply and package install
use too: the same validation, ownership and change history on every channel.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Template
from app.services.resources import rest
from app.services.resources.templates import TemplateApplier
from app.services.template_renderer import render_template
from app.schemas.template import (
    TemplateCreate,
    TemplateRenderRequest,
    TemplateRenderResponse,
    TemplateResponse,
    TemplateUpdate,
)

router = APIRouter()

_applier = TemplateApplier()


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    req: Request,
    template_data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new template."""
    user_id, permissions = current_user_data

    # Check create permission
    perm = "sinas.templates.create:own"

    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create templates")
    set_permission_used(req, perm)

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Template 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, template_data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return TemplateResponse.model_validate(result.obj)


@router.get("", response_model=list[TemplateResponse])
async def list_templates(
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """List templates accessible to the current user."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware filtering
    templates = await Template.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
    )

    set_permission_used(req, "sinas.templates.read")

    return [TemplateResponse.model_validate(t) for t in templates]


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Get a template by ID."""
    user_id, permissions = current_user_data

    # Load template
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Use mixin to check permission
    if not template.can_user_access(user_id, permissions, "read"):
        set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.read", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to get this template")

    set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.read")
    return TemplateResponse.model_validate(template)


@router.get("/by-name/{namespace}/{name}", response_model=TemplateResponse)
async def get_template_by_name(
    namespace: str,
    name: str,
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Get a template by namespace and name."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    template = await Template.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(req, f"sinas.templates/{namespace}/{name}.read")
    return TemplateResponse.model_validate(template)


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    template_data: TemplateUpdate,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a template."""
    user_id, permissions = current_user_data

    # Load template
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Use mixin to check permission
    if not template.can_user_access(user_id, permissions, "update"):
        set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.update", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update this template")

    set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.update")

    ctx = rest.api_context(db, user_id)
    template = await rest.locked(_applier, ctx, template)
    # As before: only the fields sent change, and null clears an optional
    # one. Nulling html_content (NOT NULL) used to be a 500; now a 422.
    patch = template_data.model_dump(exclude_unset=True)
    await rest.write(_applier, ctx, rest.patch_spec(_applier, template, patch), existing=template)
    await rest.commit(db, ctx)
    await db.refresh(template)
    return TemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a template."""
    user_id, permissions = current_user_data

    # Load template
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Use mixin to check permission
    if not template.can_user_access(user_id, permissions, "delete"):
        set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.delete", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete this template")

    set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.delete")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, template), ctx)
    await rest.commit(db, ctx)


@router.post("/{template_id}/render", response_model=TemplateRenderResponse)
async def render_template_preview(
    template_id: uuid.UUID,
    render_request: TemplateRenderRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Render a template with given variables (preview for testing)."""
    user_id, permissions = current_user_data

    # Load template
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Use mixin to check permission - use render action
    if not template.can_user_access(user_id, permissions, "render"):
        set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.render", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to render this template")

    set_permission_used(req, f"sinas.templates/{template.namespace}/{template.name}.render")

    # Render template using inline rendering (don't need to look up by name again)
    try:
        # Validate variables against schema if defined
        if template.variable_schema:
            import jsonschema

            try:
                jsonschema.validate(render_request.variables, template.variable_schema)
            except jsonschema.ValidationError as e:
                raise HTTPException(
                    status_code=400, detail=f"Variable validation failed: {e.message}"
                )

        # Render title
        rendered_title = None
        if template.title:
            rendered_title = render_template(template.title, render_request.variables)

        # Render HTML
        rendered_html = render_template(template.html_content, render_request.variables)

        # Render text
        rendered_text = None
        if template.text_content:
            rendered_text = render_template(template.text_content, render_request.variables)

        return TemplateRenderResponse(
            title=rendered_title, html_content=rendered_html, text_content=rendered_text
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Template rendering failed: {str(e)}")
