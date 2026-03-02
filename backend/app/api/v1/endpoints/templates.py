"""Template endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Template
from app.services.package_service import detach_if_package_managed
from app.schemas.template import (
    TemplateCreate,
    TemplateRenderRequest,
    TemplateRenderResponse,
    TemplateResponse,
    TemplateUpdate,
)

router = APIRouter()


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    req: Request,
    template_data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new template."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Check create permission
    perm = "sinas.templates.create:own"

    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create templates")
    set_permission_used(req, perm)

    # Check if template namespace+name already exists
    result = await db.execute(
        select(Template).where(
            and_(Template.namespace == template_data.namespace, Template.name == template_data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Template '{template_data.namespace}/{template_data.name}' already exists",
        )

    template = Template(
        namespace=template_data.namespace,
        name=template_data.name,
        description=template_data.description,
        title=template_data.title,
        html_content=template_data.html_content,
        text_content=template_data.text_content,
        variable_schema=template_data.variable_schema or {},
        is_active=True,
        user_id=user_uuid,
        created_by=user_uuid,
        updated_by=user_uuid,
    )

    db.add(template)
    await db.commit()
    await db.refresh(template)

    return TemplateResponse.model_validate(template)


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
    user_uuid = uuid.UUID(user_id)

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

    detach_if_package_managed(template)

    # Check for namespace/name conflict if renaming
    new_namespace = template_data.namespace or template.namespace
    new_name = template_data.name or template.name
    if new_namespace != template.namespace or new_name != template.name:
        result = await db.execute(
            select(Template).where(
                and_(
                    Template.namespace == new_namespace,
                    Template.name == new_name,
                    Template.id != template_id,
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail=f"Template '{new_namespace}/{new_name}' already exists"
            )

    # Update fields
    for field, value in template_data.model_dump(exclude_unset=True).items():
        setattr(template, field, value)

    template.updated_by = user_uuid

    await db.commit()
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

    await db.delete(template)
    await db.commit()


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
        from app.services.template_renderer import render_template

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
