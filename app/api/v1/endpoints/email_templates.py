from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models.email import EmailTemplate
from app.schemas.email import (
    EmailTemplateCreate,
    EmailTemplateUpdate,
    EmailTemplateResponse
)
from app.services.email_service import email_template_renderer

router = APIRouter(prefix="/email-templates", tags=["email-templates"])


@router.post("/", response_model=EmailTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    request: Request,
    template: EmailTemplateCreate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Create a new email template"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.templates.create:own"):
        set_permission_used(request, "sinas.email.templates.create:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create email templates")

    set_permission_used(request, "sinas.email.templates.create:own", has_perm=True)

    # Check if template name already exists
    result = await db.execute(
        select(EmailTemplate).filter(EmailTemplate.name == template.name)
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email template with name '{template.name}' already exists"
        )

    # Validate template syntax
    try:
        email_template_renderer.validate_template(
            html_content=template.html_content,
            text_content=template.text_content,
            subject=template.subject,
            test_variables=template.example_variables
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template validation failed: {str(e)}"
        )

    db_template = EmailTemplate(**template.model_dump())
    db.add(db_template)
    await db.commit()
    await db.refresh(db_template)

    return db_template


@router.get("/", response_model=List[EmailTemplateResponse])
async def list_templates(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all email templates"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.templates.read:own"):
        set_permission_used(request, "sinas.email.templates.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read email templates")

    set_permission_used(request, "sinas.email.templates.read:own", has_perm=True)

    result = await db.execute(
        select(EmailTemplate).offset(skip).limit(limit)
    )
    templates = result.scalars().all()

    return templates


@router.get("/{template_id}", response_model=EmailTemplateResponse)
async def get_template(
    request: Request,
    template_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific email template"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.templates.read:own"):
        set_permission_used(request, "sinas.email.templates.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read email templates")

    set_permission_used(request, "sinas.email.templates.read:own", has_perm=True)

    result = await db.execute(
        select(EmailTemplate).filter(EmailTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email template with id {template_id} not found"
        )

    return template


@router.get("/name/{template_name}", response_model=EmailTemplateResponse)
async def get_template_by_name(
    request: Request,
    template_name: str,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get an email template by name"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.templates.read:own"):
        set_permission_used(request, "sinas.email.templates.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read email templates")

    set_permission_used(request, "sinas.email.templates.read:own", has_perm=True)

    result = await db.execute(
        select(EmailTemplate).filter(EmailTemplate.name == template_name)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email template with name '{template_name}' not found"
        )

    return template


@router.patch("/{template_id}", response_model=EmailTemplateResponse)
async def update_template(
    request: Request,
    template_id: int,
    template_update: EmailTemplateUpdate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Update an email template"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.templates.update:own"):
        set_permission_used(request, "sinas.email.templates.update:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update email templates")

    set_permission_used(request, "sinas.email.templates.update:own", has_perm=True)

    result = await db.execute(
        select(EmailTemplate).filter(EmailTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email template with id {template_id} not found"
        )

    update_data = template_update.model_dump(exclude_unset=True)

    if update_data:
        # Validate template with updated values
        try:
            email_template_renderer.validate_template(
                html_content=update_data.get('html_content', template.html_content),
                text_content=update_data.get('text_content', template.text_content),
                subject=update_data.get('subject', template.subject),
                test_variables=update_data.get('example_variables', template.example_variables)
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Template validation failed: {str(e)}"
            )

        for field, value in update_data.items():
            setattr(template, field, value)

        await db.commit()
        await db.refresh(template)

    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    request: Request,
    template_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Delete an email template"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.templates.delete:own"):
        set_permission_used(request, "sinas.email.templates.delete:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete email templates")

    set_permission_used(request, "sinas.email.templates.delete:own", has_perm=True)

    result = await db.execute(
        select(EmailTemplate).filter(EmailTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email template with id {template_id} not found"
        )

    await db.delete(template)
    await db.commit()

    return None


@router.post("/{template_id}/preview")
async def preview_template(
    request: Request,
    template_id: int,
    variables: dict = {},
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Preview an email template with variables"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.templates.read:own"):
        set_permission_used(request, "sinas.email.templates.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read email templates")

    set_permission_used(request, "sinas.email.templates.read:own", has_perm=True)

    result = await db.execute(
        select(EmailTemplate).filter(EmailTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email template with id {template_id} not found"
        )

    try:
        subject, html_content, text_content = await email_template_renderer.render_template(
            db=db,
            template_name=template.name,
            variables=variables
        )

        return {
            "subject": subject,
            "html_content": html_content,
            "text_content": text_content
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template rendering failed: {str(e)}"
        )
