"""Email utility with template support."""
import smtplib
import logging
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Tuple

from app.core.config import settings
from app.services.template_service import template_service

logger = logging.getLogger(__name__)

# SMTP timeout in seconds (fail fast instead of waiting 2 minutes)
SMTP_TIMEOUT = 10


def _send_email_sync(
    email: str,
    subject: str,
    html_content: str,
    text_content: str,
    settings
) -> None:
    """
    Synchronous SMTP email sending (to be run in thread pool).

    Args:
        email: Recipient email address
        subject: Email subject line
        html_content: HTML email body
        text_content: Plain text email body
        settings: App settings (for SMTP config)
    """
    from_email = f"login@{settings.smtp_domain}"

    # Create message
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = email

    # Attach both text and HTML versions
    part1 = MIMEText(text_content, 'plain')
    part2 = MIMEText(html_content, 'html')
    msg.attach(part1)
    msg.attach(part2)

    # Send via SMTP with timeout
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT) as server:
        server.starttls()
        if settings.smtp_user and settings.smtp_password:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


async def send_otp_email_async(db: AsyncSession, email: str, otp_code: str) -> None:
    """
    Send OTP code via SMTP (non-blocking) using database template if available.
    Falls back to console logging if SMTP is not configured (development mode).
    Raises exception if SMTP is configured but fails (production mode).

    Args:
        db: Database session
        email: Recipient email address
        otp_code: The OTP code to send

    Raises:
        Exception: If SMTP is configured but email sending fails
    """
    # Development mode: SMTP not configured, use console logging
    if not settings.smtp_host or not settings.smtp_domain:
        logger.warning("SMTP not configured. OTP code for console testing:")
        logger.warning(f"Email: {email}")
        logger.warning(f"OTP Code: {otp_code}")
        print(f"\n{'='*60}")
        print(f"OTP CODE FOR {email}: {otp_code}")
        print(f"{'='*60}\n")
        return

    # Prepare template variables
    variables = {
        "otp_code": otp_code,
        "user_email": email,
        "expiry_minutes": settings.otp_expire_minutes,
    }

    # Try to render from database template
    subject = None
    html_content = None
    text_content = None

    try:
        subject, html_content, text_content = await template_service.render_template(
            db=db,
            template_name="otp_email",
            namespace="default",
            variables=variables
        )
        logger.debug("Using database template 'default/otp_email' for OTP email")
    except ValueError:
        # Template not found in database - use fallback
        logger.debug("Template 'default/otp_email' not found, using fallback template")
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
        <p>Best regards,<br>Sinas</p>
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
    except Exception as e:
        # Template rendering failed - use fallback
        logger.error(f"Failed to render template 'otp_email': {e}, using fallback")
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

    # Production mode: SMTP configured, must succeed or raise exception
    try:
        # Run SMTP in thread pool with timeout to avoid blocking the event loop
        await asyncio.wait_for(
            asyncio.to_thread(_send_email_sync, email, subject, html_content, text_content, settings),
            timeout=SMTP_TIMEOUT + 2  # Give a bit more than SMTP timeout
        )

        logger.info(f"OTP email sent successfully to {email}")

    except asyncio.TimeoutError:
        logger.error(f"Timeout sending OTP email to {email} (exceeded {SMTP_TIMEOUT}s)")
        # In production, raise error instead of falling back
        raise Exception(f"Email service timeout after {SMTP_TIMEOUT}s")

    except Exception as e:
        logger.error(f"Failed to send OTP email to {email}: {e}")
        # In production, raise error instead of falling back
        raise Exception(f"Failed to send OTP email: {str(e)}")


__all__ = ['send_otp_email_async']
