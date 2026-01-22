"""Template endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models import Template
from app.schemas.template import (
    TemplateCreate,
    TemplateUpdate,
    TemplateResponse,
    TemplateRenderRequest,
    TemplateRenderResponse,
)
from app.services.template_service import template_service

router = APIRouter()


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    req: Request,
    template_data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new template (admin only)."""
    user_id, permissions = current_user_data

    # Check permission (admin only)
    perm = "sinas.templates.create:all"
    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create templates (admin only)")
    set_permission_used(req, perm)

    # Check if template name already exists
    result = await db.execute(
        select(Template).where(Template.name == template_data.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Template '{template_data.name}' already exists")

    template = Template(
        name=template_data.name,
        description=template_data.description,
        title=template_data.title,
        html_content=template_data.html_content,
        text_content=template_data.text_content,
        variable_schema=template_data.variable_schema or {},
        is_active=True,
        created_by=uuid.UUID(user_id),
        updated_by=uuid.UUID(user_id),
    )

    db.add(template)
    await db.commit()
    await db.refresh(template)

    return TemplateResponse.model_validate(template)


@router.get("", response_model=List[TemplateResponse])
async def list_templates(
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all templates."""
    user_id, permissions = current_user_data

    # Check permission (admin or users with read access)
    perm = "sinas.templates.read:all"
    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to list templates")
    set_permission_used(req, perm)

    result = await db.execute(
        select(Template).order_by(Template.created_at.desc())
    )
    templates = result.scalars().all()

    return [TemplateResponse.model_validate(t) for t in templates]


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a template by ID."""
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.templates.read:all"
    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read templates")
    set_permission_used(req, perm)

    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return TemplateResponse.model_validate(template)


@router.get("/by-name/{template_name}", response_model=TemplateResponse)
async def get_template_by_name(
    template_name: str,
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a template by name."""
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.templates.read:all"
    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read templates")
    set_permission_used(req, perm)

    result = await db.execute(
        select(Template).where(Template.name == template_name)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return TemplateResponse.model_validate(template)


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    template_data: TemplateUpdate,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a template (admin only)."""
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.templates.update:all"
    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update templates (admin only)")
    set_permission_used(req, perm)

    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Check for name conflict if renaming
    if template_data.name and template_data.name != template.name:
        result = await db.execute(
            select(Template).where(Template.name == template_data.name)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Template '{template_data.name}' already exists")

    # Update fields
    for field, value in template_data.model_dump(exclude_unset=True).items():
        setattr(template, field, value)

    template.updated_by = uuid.UUID(user_id)

    await db.commit()
    await db.refresh(template)

    return TemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a template (admin only)."""
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.templates.delete:all"
    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete templates (admin only)")
    set_permission_used(req, perm)

    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(template)
    await db.commit()


@router.post("/{template_id}/render", response_model=TemplateRenderResponse)
async def render_template(
    template_id: uuid.UUID,
    render_request: TemplateRenderRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Render a template with given variables (preview)."""
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.templates.read:all"
    if not check_permission(permissions, perm):
        set_permission_used(req, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to render templates")
    set_permission_used(req, perm)

    # Get template
    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Render template
    try:
        title, html, text = await template_service.render_template(
            db=db,
            template_name=template.name,
            variables=render_request.variables
        )
        return TemplateRenderResponse(
            title=title,
            html_content=html,
            text_content=text
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Template rendering failed: {str(e)}")
