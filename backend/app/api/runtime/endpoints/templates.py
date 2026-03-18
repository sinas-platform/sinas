"""Template runtime API endpoints - rendering and email sending."""
import asyncio
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.config import settings
from app.core.database import get_db
from app.core.email import _send_email_sync
from app.models.template import Template
from app.schemas.template import (
    TemplateEmailRequest,
    TemplateEmailResponse,
    TemplateRenderRequest,
    TemplateRenderResponse,
)
from app.services.template_renderer import render_template

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates")


async def get_template_with_permission_check(
    db: AsyncSession,
    namespace: str,
    name: str,
    user_id: str,
    permissions: dict[str, bool],
    action: str,  # "render" or "send"
    request: Request,
) -> Template:
    """
    Get template by namespace/name and check permissions using mixin.

    Args:
        db: Database session
        namespace: Template namespace
        name: Template name
        user_id: Current user ID (string)
        permissions: User permissions
        action: Action being performed (for permission check)
        request: FastAPI request (for permission logging)

    Returns:
        Template object if authorized

    Raises:
        HTTPException: If not found or not authorized
    """
    # Use mixin for permission-aware get
    template = await Template.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action=action,
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.templates/{namespace}/{name}.{action}")
    return template


@router.post("/{namespace}/{name}/render", response_model=TemplateRenderResponse)
async def render_template_endpoint(
    namespace: str,
    name: str,
    render_request: TemplateRenderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """
    Render a template with given variables.

    Requires permission: sinas.templates.{namespace}.{name}.render:scope
    """
    user_id, permissions = current_user_data

    # Get template with permission check
    template = await get_template_with_permission_check(
        db=db,
        namespace=namespace,
        name=name,
        user_id=user_id,
        permissions=permissions,
        action="render",
        request=request,
    )

    # Validate variables against schema if defined
    if template.variable_schema:
        try:
            import jsonschema

            jsonschema.validate(render_request.variables, template.variable_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(status_code=400, detail=f"Variable validation failed: {e.message}")

    # Render title
    rendered_title = None
    if template.title:
        try:
            rendered_title = render_template(template.title, render_request.variables)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to render title: {str(e)}")

    # Render HTML content
    try:
        rendered_html = render_template(template.html_content, render_request.variables)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to render HTML content: {str(e)}")

    # Render text content
    rendered_text = None
    if template.text_content:
        try:
            rendered_text = render_template(template.text_content, render_request.variables)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to render text content: {str(e)}")

    return TemplateRenderResponse(
        title=rendered_title, html_content=rendered_html, text_content=rendered_text
    )


@router.post("/{namespace}/{name}/email", response_model=TemplateEmailResponse)
async def send_email_with_template(
    namespace: str,
    name: str,
    email_request: TemplateEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """
    Send email using a template.

    Requires permission: sinas.templates.{namespace}.{name}.send:scope
    """
    user_id, permissions = current_user_data

    # Check SMTP configuration
    if not settings.smtp_host or not settings.smtp_domain:
        raise HTTPException(status_code=503, detail="Email service not configured")

    # Get template with permission check
    template = await get_template_with_permission_check(
        db=db,
        namespace=namespace,
        name=name,
        user_id=user_id,
        permissions=permissions,
        action="send",
        request=request,
    )

    # Validate variables against schema if defined
    if template.variable_schema:
        try:
            import jsonschema

            jsonschema.validate(email_request.variables, template.variable_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(status_code=400, detail=f"Variable validation failed: {e.message}")

    # Render title (subject)
    subject = "Notification"  # Default subject
    if template.title:
        try:
            subject = render_template(template.title, email_request.variables)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to render subject: {str(e)}")

    # Render HTML content
    try:
        html_content = render_template(template.html_content, email_request.variables)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to render HTML content: {str(e)}")

    # Render text content (or generate plain text from HTML)
    text_content = None
    if template.text_content:
        try:
            text_content = render_template(template.text_content, email_request.variables)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to render text content: {str(e)}")
    else:
        # Generate basic plain text version if not provided
        import re

        text_content = re.sub("<[^<]+?>", "", html_content)

    # Determine from email
    if email_request.from_alias:
        from_email = f"{email_request.from_alias}@{settings.smtp_domain}"
    else:
        from_email = f"noreply@{settings.smtp_domain}"

    # Send email
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{email_request.from_name or 'SINAS'} <{from_email}>"
        msg["To"] = email_request.to

        # Attach both text and HTML versions
        part1 = MIMEText(text_content, "plain")
        part2 = MIMEText(html_content, "html")
        msg.attach(part1)
        msg.attach(part2)

        # Send via SMTP in thread pool (non-blocking)
        await asyncio.wait_for(
            asyncio.to_thread(
                _send_email_sync, email_request.to, subject, html_content, text_content, settings
            ),
            timeout=12,  # SMTP_TIMEOUT + 2
        )

        logger.info(
            f"Email sent successfully to {email_request.to} using template {namespace}/{name}"
        )

        return TemplateEmailResponse(message="Email sent successfully", to=email_request.to)

    except asyncio.TimeoutError:
        logger.error(f"Timeout sending email to {email_request.to}")
        raise HTTPException(status_code=504, detail="Email service timeout")
    except Exception as e:
        logger.error(f"Failed to send email to {email_request.to}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send email: {str(e)}")
