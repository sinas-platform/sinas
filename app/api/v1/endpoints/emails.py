from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime
from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models.email import Email, EmailTemplate, EmailStatus
from app.schemas.email import (
    EmailSend,
    EmailResponse,
    EmailListResponse
)
from app.services.email_service import email_sender, email_template_renderer
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emails", tags=["emails"])


@router.post("/send", response_model=EmailResponse, status_code=status.HTTP_201_CREATED)
async def send_email(
    request: Request,
    email_data: EmailSend,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Send an email using a template or direct content"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.send:own"):
        set_permission_used(request, "sinas.email.send:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to send emails")

    set_permission_used(request, "sinas.email.send:own", has_perm=True)

    subject = email_data.subject
    html_content = email_data.html_content
    text_content = email_data.text_content
    template_id = None

    # If template specified, render it
    if email_data.template_name:
        result = await db.execute(
            select(EmailTemplate).filter(EmailTemplate.name == email_data.template_name)
        )
        template = result.scalar_one_or_none()

        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Email template '{email_data.template_name}' not found"
            )

        try:
            subject, html_content, text_content = await email_template_renderer.render_template(
                db=db,
                template_name=email_data.template_name,
                variables=email_data.template_variables
            )
            template_id = template.id
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Template rendering failed: {str(e)}"
            )
    else:
        # Direct content - validate required fields
        if not subject:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subject is required when not using a template"
            )

        if not html_content and not text_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either html_content or text_content is required"
            )

    try:
        email_record = await email_sender.send_email(
            db=db,
            to_email=email_data.to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            from_email=email_data.from_email,
            cc=email_data.cc,
            bcc=email_data.bcc,
            attachments=email_data.attachments,
            template_id=template_id,
            template_variables=email_data.template_variables
        )

        return email_record

    except Exception as e:
        logger.error(f"Failed to send email: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send email: {str(e)}"
        )


@router.get("/", response_model=EmailListResponse)
async def list_emails(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    to_email: Optional[str] = None,
    from_email: Optional[str] = None,
    status_filter: Optional[EmailStatus] = None,
    direction: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List emails with filtering"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.read:own"):
        set_permission_used(request, "sinas.email.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read emails")

    set_permission_used(request, "sinas.email.read:own", has_perm=True)

    query = select(Email)

    if to_email:
        query = query.filter(Email.to_email == to_email)

    if from_email:
        query = query.filter(Email.from_email == from_email)

    if status_filter:
        query = query.filter(Email.status == status_filter)

    if direction:
        query = query.filter(Email.direction == direction)

    if start_date:
        query = query.filter(Email.created_at >= start_date)

    if end_date:
        query = query.filter(Email.created_at <= end_date)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Get paginated results
    query = query.order_by(Email.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    emails = result.scalars().all()

    return EmailListResponse(
        emails=emails,
        total=total,
        page=page,
        per_page=per_page
    )


@router.get("/received", response_model=EmailListResponse)
async def list_received_emails(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    recipient: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List received (inbound) emails"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.read:own"):
        set_permission_used(request, "sinas.email.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read emails")

    set_permission_used(request, "sinas.email.read:own", has_perm=True)

    query = select(Email).filter(Email.direction == "inbound")

    if recipient:
        query = query.filter(Email.to_email == recipient)

    if start_date:
        query = query.filter(Email.received_at >= start_date)

    if end_date:
        query = query.filter(Email.received_at <= end_date)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Get paginated results
    query = query.order_by(Email.received_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    emails = result.scalars().all()

    return EmailListResponse(
        emails=emails,
        total=total,
        page=page,
        per_page=per_page
    )


@router.get("/{email_id}", response_model=EmailResponse)
async def get_email(
    request: Request,
    email_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific email by ID"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.read:own"):
        set_permission_used(request, "sinas.email.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read emails")

    set_permission_used(request, "sinas.email.read:own", has_perm=True)

    result = await db.execute(
        select(Email).filter(Email.id == email_id)
    )
    email = result.scalar_one_or_none()

    if not email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email with id {email_id} not found"
        )

    return email


@router.delete("/{email_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_email(
    request: Request,
    email_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Delete an email"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.delete:own"):
        set_permission_used(request, "sinas.email.delete:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete emails")

    set_permission_used(request, "sinas.email.delete:own", has_perm=True)

    result = await db.execute(
        select(Email).filter(Email.id == email_id)
    )
    email = result.scalar_one_or_none()

    if not email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email with id {email_id} not found"
        )

    await db.delete(email)
    await db.commit()

    return None


@router.post("/{email_id}/resend", response_model=EmailResponse)
async def resend_email(
    request: Request,
    email_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Resend an outbound email"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.send:own"):
        set_permission_used(request, "sinas.email.send:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to send emails")

    set_permission_used(request, "sinas.email.send:own", has_perm=True)

    result = await db.execute(
        select(Email).filter(Email.id == email_id)
    )
    original_email = result.scalar_one_or_none()

    if not original_email:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email with id {email_id} not found"
        )

    if original_email.direction != "outbound":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only resend outbound emails"
        )

    try:
        email_record = await email_sender.send_email(
            db=db,
            to_email=original_email.to_email,
            subject=original_email.subject,
            html_content=original_email.html_content,
            text_content=original_email.text_content,
            from_email=original_email.from_email,
            cc=original_email.cc,
            bcc=original_email.bcc,
            attachments=original_email.attachments,
            template_id=original_email.template_id,
            template_variables=original_email.template_variables
        )

        return email_record

    except Exception as e:
        logger.error(f"Failed to resend email: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resend email: {str(e)}"
        )
