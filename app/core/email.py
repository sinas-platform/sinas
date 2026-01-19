"""Simple email utility for OTP authentication."""
import smtplib
import logging
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# SMTP timeout in seconds (fail fast instead of waiting 2 minutes)
SMTP_TIMEOUT = 10


def _send_email_sync(email: str, otp_code: str, settings) -> None:
    """Synchronous SMTP email sending (to be run in thread pool)."""
    subject = "Your Login Code"
    from_email = f"login@{settings.smtp_domain}"

    # HTML content
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

    # Text content
    text_content = f"""
Your Login Code

Hello,

Your login verification code is: {otp_code}

This code will expire in {settings.otp_expire_minutes} minutes.

If you didn't request this code, please ignore this email.

Best regards,
SINAS Team
    """

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
    Send OTP code via SMTP (non-blocking).
    Falls back to console logging if SMTP is not configured (development mode).
    Raises exception if SMTP is configured but fails (production mode).

    Args:
        db: Database session (not used, kept for compatibility)
        email: Recipient email address
        otp_code: The OTP code to send

    Raises:
        Exception: If SMTP is configured but email sending fails
    """
    from app.core.config import settings

    # Development mode: SMTP not configured, use console logging
    if not settings.smtp_host or not settings.smtp_domain:
        logger.warning("SMTP not configured. OTP code for console testing:")
        logger.warning(f"Email: {email}")
        logger.warning(f"OTP Code: {otp_code}")
        print(f"\n{'='*60}")
        print(f"OTP CODE FOR {email}: {otp_code}")
        print(f"{'='*60}\n")
        return

    # Production mode: SMTP configured, must succeed or raise exception
    try:
        # Run SMTP in thread pool with timeout to avoid blocking the event loop
        await asyncio.wait_for(
            asyncio.to_thread(_send_email_sync, email, otp_code, settings),
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
