"""Email utility with template support and managed mailer fallback."""
import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.template_service import template_service

logger = logging.getLogger(__name__)

# SMTP timeout in seconds (fail fast instead of waiting 2 minutes)
SMTP_TIMEOUT = 10

# Managed mailer endpoint (used when no SMTP is configured)
MANAGED_MAILER_URL = "https://mail.sinas.cloud/send-otp"


def _send_email_sync(
    email: str, subject: str, html_content: str, text_content: str, settings
) -> None:
    """Synchronous SMTP email sending (to be run in thread pool)."""
    from_email = f"login@{settings.smtp_domain}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = email

    part1 = MIMEText(text_content, "plain")
    part2 = MIMEText(html_content, "html")
    msg.attach(part1)
    msg.attach(part2)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT) as server:
        server.starttls()
        if settings.smtp_user and settings.smtp_password:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


async def _send_via_managed_mailer(email: str, otp_code: str) -> None:
    """Send OTP via the managed Sinas mailer service."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            MANAGED_MAILER_URL,
            json={
                "email": email,
                "otp_code": otp_code,
                "domain": settings.domain,
                "reply_to": settings.superadmin_email,
            },
        )
        resp.raise_for_status()

    logger.info(f"OTP email sent via managed mailer to {email}")


async def send_otp_email_async(db: AsyncSession, email: str, otp_code: str) -> None:
    """
    Send OTP code to the user.

    Priority:
    1. User-configured SMTP — uses their server + database template
    2. Managed mailer (mail.sinas.cloud) — no config needed
    3. Console logging — development fallback
    """
    # 1. User-configured SMTP: use their server + template
    if settings.smtp_host and settings.smtp_domain:
        await _send_via_user_smtp(db, email, otp_code)
        return

    # 2. Managed mailer: no SMTP configured, use Sinas mail service
    if settings.domain and settings.domain not in ("localhost", "127.0.0.1"):
        try:
            await _send_via_managed_mailer(email, otp_code)
            return
        except Exception as e:
            logger.error(f"Managed mailer failed: {e}")
            raise Exception(
                "Email delivery failed. Configure SMTP_HOST in .env for self-hosted email, "
                "or check https://status.sinas.cloud for service status."
            )

    # 3. Development mode: log to console
    logger.warning("No SMTP configured and no domain set. OTP code for console testing:")
    logger.warning(f"Email: {email}")
    logger.warning(f"OTP Code: {otp_code}")
    print(f"\n{'='*60}")
    print(f"OTP CODE FOR {email}: {otp_code}")
    print(f"{'='*60}\n")


async def _send_via_user_smtp(db: AsyncSession, email: str, otp_code: str) -> None:
    """Send OTP via user-configured SMTP with database template support."""
    variables = {
        "otp_code": otp_code,
        "user_email": email,
        "expiry_minutes": settings.otp_expire_minutes,
    }

    # Try database template
    subject = None
    html_content = None
    text_content = None

    try:
        subject, html_content, text_content = await template_service.render_template(
            db=db, template_name="otp_email", namespace="default", variables=variables
        )
    except ValueError:
        pass
    except Exception as e:
        logger.error(f"Failed to render template 'otp_email': {e}")

    # Fallback template
    if not subject:
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
    </div>
        """
        text_content = f"Your login code is: {otp_code}\n\nThis code expires in {settings.otp_expire_minutes} minutes."

    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                _send_email_sync, email, subject, html_content, text_content, settings
            ),
            timeout=SMTP_TIMEOUT + 2,
        )
        logger.info(f"OTP email sent via user SMTP to {email}")
    except asyncio.TimeoutError:
        raise Exception(f"Email service timeout after {SMTP_TIMEOUT}s")
    except Exception as e:
        raise Exception(f"Failed to send OTP email: {str(e)}")


__all__ = ["send_otp_email_async"]
