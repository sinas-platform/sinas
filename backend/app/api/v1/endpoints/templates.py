"""Template endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models import Template
from app.models.user import GroupMember
from app.schemas.template import (
    TemplateCreate,
    TemplateUpdate,
    TemplateResponse,
    TemplateRenderRequest,
    TemplateRenderResponse,
)
from app.services.template_service import template_service

router = APIRouter()


async def get_user_group_ids(db: AsyncSession, user_id: uuid.UUID) -> List[uuid.UUID]:
    """Get all group IDs that the user is a member of."""
    result = await db.execute(
        select(GroupMember.group_id).where(
            and_(
                GroupMember.user_id == user_id,
                GroupMember.active == True
            )
        )
    )
    return [row[0] for row in result.all()]


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    req: Request,
    template_data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new template."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Check permission based on namespace and group_id
    perm_base = f"sinas.templates.{template_data.namespace}.*.post"

    if template_data.group_id:
        # Creating group template - need :group or :all permission
        if not check_permission(permissions, f"{perm_base}:group"):
            set_permission_used(req, f"{perm_base}:group", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to create group templates")

        # Verify user is member of the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if template_data.group_id not in user_groups:
            raise HTTPException(status_code=403, detail="Not a member of the specified group")

        set_permission_used(req, f"{perm_base}:group")
    else:
        # Creating own template - need :own, :group, or :all permission
        if not check_permission(permissions, f"{perm_base}:own"):
            set_permission_used(req, f"{perm_base}:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to create templates")
        set_permission_used(req, f"{perm_base}:own")

    # Check if template namespace+name already exists
    result = await db.execute(
        select(Template).where(
            and_(
                Template.namespace == template_data.namespace,
                Template.name == template_data.name
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Template '{template_data.namespace}/{template_data.name}' already exists"
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
        group_id=template_data.group_id,
        created_by=user_uuid,
        updated_by=user_uuid,
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
    """List templates accessible to the current user."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Build query based on permissions
    if check_permission(permissions, "sinas.templates.*.*.get:all"):
        set_permission_used(req, "sinas.templates.*.*.get:all")
        # Admin - see all templates
        query = select(Template).order_by(Template.created_at.desc())
    elif check_permission(permissions, "sinas.templates.*.*.get:group"):
        set_permission_used(req, "sinas.templates.*.*.get:group")
        # Can see own templates and group templates they have access to
        user_groups = await get_user_group_ids(db, user_uuid)
        query = select(Template).where(
            or_(
                Template.user_id == user_uuid,
                and_(
                    Template.group_id.in_(user_groups) if user_groups else False
                )
            )
        ).order_by(Template.created_at.desc())
    else:
        set_permission_used(req, "sinas.templates.*.*.get:own")
        # Own templates only
        query = select(Template).where(
            Template.user_id == user_uuid
        ).order_by(Template.created_at.desc())

    result = await db.execute(query)
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
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Check permissions based on ownership
    perm_base = f"sinas.templates.{template.namespace}.{template.name}.get"

    if check_permission(permissions, f"{perm_base}:all"):
        set_permission_used(req, f"{perm_base}:all")
    elif template.user_id == user_uuid:
        if check_permission(permissions, f"{perm_base}:own"):
            set_permission_used(req, f"{perm_base}:own")
        else:
            set_permission_used(req, f"{perm_base}:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to get this template")
    elif template.group_id:
        user_groups = await get_user_group_ids(db, user_uuid)
        if template.group_id in user_groups:
            if check_permission(permissions, f"{perm_base}:group"):
                set_permission_used(req, f"{perm_base}:group")
            else:
                set_permission_used(req, f"{perm_base}:group", has_perm=False)
                raise HTTPException(status_code=403, detail="Not authorized to get this template")
        else:
            set_permission_used(req, f"{perm_base}:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to get this template")
    else:
        set_permission_used(req, f"{perm_base}:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to get this template")

    return TemplateResponse.model_validate(template)


@router.get("/by-name/{namespace}/{name}", response_model=TemplateResponse)
async def get_template_by_name(
    namespace: str,
    name: str,
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a template by namespace and name."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(Template).where(
            and_(
                Template.namespace == namespace,
                Template.name == name
            )
        )
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{namespace}/{name}' not found")

    # Check permissions based on ownership
    perm_base = f"sinas.templates.{namespace}.{name}.get"

    if check_permission(permissions, f"{perm_base}:all"):
        set_permission_used(req, f"{perm_base}:all")
    elif template.user_id == user_uuid:
        if check_permission(permissions, f"{perm_base}:own"):
            set_permission_used(req, f"{perm_base}:own")
        else:
            set_permission_used(req, f"{perm_base}:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to get this template")
    elif template.group_id:
        user_groups = await get_user_group_ids(db, user_uuid)
        if template.group_id in user_groups:
            if check_permission(permissions, f"{perm_base}:group"):
                set_permission_used(req, f"{perm_base}:group")
            else:
                set_permission_used(req, f"{perm_base}:group", has_perm=False)
                raise HTTPException(status_code=403, detail="Not authorized to get this template")
        else:
            set_permission_used(req, f"{perm_base}:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to get this template")
    else:
        set_permission_used(req, f"{perm_base}:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to get this template")

    return TemplateResponse.model_validate(template)


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    template_data: TemplateUpdate,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a template."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Check permissions based on ownership
    perm_base = f"sinas.templates.{template.namespace}.{template.name}.put"

    can_update = False
    if check_permission(permissions, f"{perm_base}:all"):
        set_permission_used(req, f"{perm_base}:all")
        can_update = True
    elif template.user_id == user_uuid:
        if check_permission(permissions, f"{perm_base}:own"):
            set_permission_used(req, f"{perm_base}:own")
            can_update = True
    elif template.group_id:
        user_groups = await get_user_group_ids(db, user_uuid)
        if template.group_id in user_groups:
            if check_permission(permissions, f"{perm_base}:group"):
                set_permission_used(req, f"{perm_base}:group")
                can_update = True

    if not can_update:
        set_permission_used(req, f"{perm_base}:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update this template")

    # Check for namespace/name conflict if renaming
    new_namespace = template_data.namespace or template.namespace
    new_name = template_data.name or template.name
    if (new_namespace != template.namespace or new_name != template.name):
        result = await db.execute(
            select(Template).where(
                and_(
                    Template.namespace == new_namespace,
                    Template.name == new_name,
                    Template.id != template_id
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Template '{new_namespace}/{new_name}' already exists"
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
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a template."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Check permissions based on ownership
    perm_base = f"sinas.templates.{template.namespace}.{template.name}.delete"

    can_delete = False
    if check_permission(permissions, f"{perm_base}:all"):
        set_permission_used(req, f"{perm_base}:all")
        can_delete = True
    elif template.user_id == user_uuid:
        if check_permission(permissions, f"{perm_base}:own"):
            set_permission_used(req, f"{perm_base}:own")
            can_delete = True
    elif template.group_id:
        user_groups = await get_user_group_ids(db, user_uuid)
        if template.group_id in user_groups:
            if check_permission(permissions, f"{perm_base}:group"):
                set_permission_used(req, f"{perm_base}:group")
                can_delete = True

    if not can_delete:
        set_permission_used(req, f"{perm_base}:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete this template")

    await db.delete(template)
    await db.commit()


@router.post("/{template_id}/render", response_model=TemplateRenderResponse)
async def render_template_preview(
    template_id: uuid.UUID,
    render_request: TemplateRenderRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Render a template with given variables (preview for testing)."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Get template
    result = await db.execute(
        select(Template).where(Template.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Check permissions - use get permission for preview
    perm_base = f"sinas.templates.{template.namespace}.{template.name}.get"

    if check_permission(permissions, f"{perm_base}:all"):
        set_permission_used(req, f"{perm_base}:all")
    elif template.user_id == user_uuid:
        if check_permission(permissions, f"{perm_base}:own"):
            set_permission_used(req, f"{perm_base}:own")
        else:
            set_permission_used(req, f"{perm_base}:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to render this template")
    elif template.group_id:
        user_groups = await get_user_group_ids(db, user_uuid)
        if template.group_id in user_groups:
            if check_permission(permissions, f"{perm_base}:group"):
                set_permission_used(req, f"{perm_base}:group")
            else:
                set_permission_used(req, f"{perm_base}:group", has_perm=False)
                raise HTTPException(status_code=403, detail="Not authorized to render this template")
        else:
            set_permission_used(req, f"{perm_base}:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to render this template")
    else:
        set_permission_used(req, f"{perm_base}:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to render this template")

    # Render template using inline rendering (don't need to look up by name again)
    try:
        from app.services.template_renderer import render_template

        # Validate variables against schema if defined
        if template.variable_schema:
            import jsonschema
            try:
                jsonschema.validate(render_request.variables, template.variable_schema)
            except jsonschema.ValidationError as e:
                raise HTTPException(status_code=400, detail=f"Variable validation failed: {e.message}")

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
            title=rendered_title,
            html_content=rendered_html,
            text_content=rendered_text
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Template rendering failed: {str(e)}")
