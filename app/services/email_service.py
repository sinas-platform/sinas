"""
Email service for SINAS - handles email templates, sending, and receiving.
Integrates with existing webhook system for incoming email processing.
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from typing import Optional, List, Dict, Any
import uuid
from jinja2 import Template, TemplateError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.email import Email, EmailStatus, EmailTemplate
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class EmailTemplateRenderer:
    """Renders email templates using Jinja2"""

    @staticmethod
    def validate_template(
        html_content: str,
        text_content: Optional[str] = None,
        subject: Optional[str] = None,
        test_variables: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Validate template syntax by attempting to render with test variables"""
        try:
            test_vars = test_variables or {}
            Template(html_content).render(**test_vars)
            if text_content:
                Template(text_content).render(**test_vars)
            if subject:
                Template(subject).render(**test_vars)
            return True
        except Exception as e:
            logger.error(f"Template validation failed: {e}")
            raise TemplateError(f"Template validation failed: {e}")

    @staticmethod
    async def render_template(
        db: AsyncSession,
        template_name: str,
        variables: Optional[Dict[str, Any]] = None
    ) -> tuple[str, str, Optional[str]]:
        """Render template and return (subject, html_content, text_content)"""
        result = await db.execute(
            select(EmailTemplate).filter(EmailTemplate.name == template_name)
        )
        template_record = result.scalar_one_or_none()

        if not template_record:
            raise ValueError(f"Email template '{template_name}' not found")

        variables = variables or {}

        try:
            html_template = Template(template_record.html_content)
            html_content = html_template.render(**variables)

            text_content = None
            if template_record.text_content:
                text_template = Template(template_record.text_content)
                text_content = text_template.render(**variables)

            subject_template = Template(template_record.subject)
            subject = subject_template.render(**variables)

            return subject, html_content, text_content

        except Exception as e:
            logger.error(f"Error rendering email template '{template_name}': {e}")
            raise TemplateError(f"Failed to render email template: {e}")


class EmailSender:
    """Handles sending emails via SMTP"""

    def __init__(self):
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password
        self.smtp_domain = settings.smtp_domain

    def _is_smtp_configured(self) -> bool:
        """Check if SMTP is properly configured"""
        return all([self.smtp_host, self.smtp_user, self.smtp_password, self.smtp_domain])

    async def send_email(
        self,
        db: AsyncSession,
        to_email: str,
        subject: str,
        html_content: Optional[str] = None,
        text_content: Optional[str] = None,
        from_email: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        template_id: Optional[int] = None,
        template_variables: Optional[Dict[str, Any]] = None,
        skip_db_record: bool = False  # For internal system emails like OTP
    ) -> Email:
        """Send an email and store record in database"""

        # Determine from_email
        if not from_email:
            if self.smtp_domain:
                from_email = f"noreply@{self.smtp_domain}"
            else:
                from_email = "noreply@localhost"

        message_id = f"<{uuid.uuid4()}@{self.smtp_domain or 'localhost'}>"

        # Check if SMTP is configured
        if not self._is_smtp_configured():
            logger.warning(f"SMTP not configured - Cannot send email to {to_email}")
            logger.warning(f"Subject: {subject}")
            if text_content:
                logger.warning(f"Content: {text_content[:200]}")

            # Still create record if not skipped
            if not skip_db_record:
                email_record = Email(
                    message_id=message_id,
                    from_email=from_email or "noreply@localhost",
                    to_email=to_email,
                    cc=cc,
                    bcc=bcc,
                    subject=subject,
                    html_content=html_content,
                    text_content=text_content,
                    status=EmailStatus.FAILED,
                    direction="outbound",
                    template_id=template_id,
                    template_variables=template_variables,
                    error_message="SMTP not configured",
                    created_at=datetime.utcnow()
                )
                db.add(email_record)
                await db.commit()
                await db.refresh(email_record)
                return email_record
            else:
                # For system emails like OTP, return a dummy record
                return Email(
                    id=0,
                    message_id=message_id,
                    from_email=from_email or "noreply@localhost",
                    to_email=to_email,
                    subject=subject,
                    html_content=html_content,
                    text_content=text_content,
                    status=EmailStatus.FAILED,
                    direction="outbound",
                    error_message="SMTP not configured",
                    created_at=datetime.utcnow()
                )

        # Create email record
        if not skip_db_record:
            email_record = Email(
                message_id=message_id,
                from_email=from_email,
                to_email=to_email,
                cc=cc,
                bcc=bcc,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                status=EmailStatus.PENDING,
                direction="outbound",
                template_id=template_id,
                template_variables=template_variables,
                created_at=datetime.utcnow()
            )
            db.add(email_record)
            await db.commit()
            await db.refresh(email_record)
        else:
            # Create dummy record for tracking
            email_record = Email(
                id=0,
                message_id=message_id,
                from_email=from_email,
                to_email=to_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                status=EmailStatus.PENDING,
                direction="outbound",
                created_at=datetime.utcnow()
            )

        try:
            # Build MIME message
            message = MIMEMultipart("alternative")
            message["From"] = from_email
            message["To"] = to_email
            message["Subject"] = subject
            message["Message-ID"] = message_id
            message["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

            if cc:
                message["Cc"] = ", ".join(cc)

            if text_content:
                part1 = MIMEText(text_content, "plain")
                message.attach(part1)

            if html_content:
                part2 = MIMEText(html_content, "html")
                message.attach(part2)

            if attachments:
                for attachment in attachments:
                    if "content" in attachment and "filename" in attachment:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(attachment["content"])
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f"attachment; filename= {attachment['filename']}"
                        )
                        message.attach(part)

            # Send email
            recipients = [to_email]
            if cc:
                recipients.extend(cc)
            if bcc:
                recipients.extend(bcc)

            context = ssl.create_default_context()

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls(context=context)
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(message, from_email, recipients)

            # Update status
            email_record.status = EmailStatus.SENT
            email_record.sent_at = datetime.utcnow()
            if not skip_db_record:
                await db.commit()

            logger.info(f"Email sent successfully to {to_email}")

        except Exception as e:
            email_record.status = EmailStatus.FAILED
            email_record.error_message = str(e)
            if not skip_db_record:
                await db.commit()
            logger.error(f"Failed to send email to {to_email}: {e}")
            raise

        return email_record


async def send_otp_email_async(db: AsyncSession, email: str, otp_code: str) -> bool:
    """
    Send OTP code via email service.
    Falls back to console logging if SMTP is not configured.

    Args:
        db: Database session
        email: Recipient email address
        otp_code: The OTP code to send

    Returns:
        True if email was sent or logged successfully
    """
    from app.core.config import settings

    subject = "Your Login Code"

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #333;">Your Login Code</h2>
        <p>Hello,</p>
        <p>Your login verification code is:</p>
        <div style="background-color: #f8f9fa; padding: 20px; text-align: center; margin: 20px 0; border-radius: 8px;">
            <h1 style="color: #007bff; font-size: 32px; margin: 0; letter-spacing: 8px;">{otp_code}</h1>
        </div>
        <p>This code will expire in {settings.otp_expire_minutes} minutes.</p>
        <p>If you didn't request this code, please ignore this email.</p>
        <p>Best regards,<br>SINAS Team</p>
    </div>
    """

    text_content = f"""
Your Login Code

Hello,

Your login verification code is: {otp_code}

This code will expire in {settings.otp_expire_minutes} minutes.

If you didn't request this code, please ignore this email.

Best regards,
SINAS Team
    """

    # Use login@{smtp_domain} as sender for OTP emails
    from_email = f"login@{settings.smtp_domain}" if settings.smtp_domain else None

    try:
        await email_sender.send_email(
            db=db,
            to_email=email,
            from_email=from_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            skip_db_record=True  # Don't clutter email logs with OTP emails
        )
        logger.info(f"OTP email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Error sending OTP email to {email}: {e}")
        # Fallback to console logging
        logger.warning("SMTP failed - printing OTP to console")
        print(f"\n{'='*60}")
        print(f"OTP Code for {email}: {otp_code}")
        print(f"{'='*60}\n")
        return True  # Still return True so auth flow continues


email_sender = EmailSender()
email_template_renderer = EmailTemplateRenderer()
