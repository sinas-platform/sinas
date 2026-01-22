"""Default template initialization."""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template

logger = logging.getLogger(__name__)


async def initialize_default_templates(db: AsyncSession):
    """
    Initialize default templates (OTP email, etc.) if they don't exist.

    Called during application startup.
    """
    # OTP Email Template
    otp_template_name = "otp_email"
    result = await db.execute(
        select(Template).where(Template.name == otp_template_name)
    )
    existing = result.scalar_one_or_none()

    if not existing:
        otp_template = Template(
            name=otp_template_name,
            description="OTP verification email template",
            title="Your Login Code",
            html_content="""
<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <h2 style="color: #333;">Your Login Code</h2>
    <p>Hello,</p>
    <p>Your login verification code is:</p>
    <div style="background-color: #f8f9fa; padding: 20px; text-align: center; margin: 20px 0; border-radius: 8px;">
        <h1 style="color: #007bff; font-size: 32px; margin: 0; letter-spacing: 8px;">{{ otp_code }}</h1>
    </div>
    <p>This code will expire in {{ expiry_minutes }} minutes.</p>
    <p>If you didn't request this code, please ignore this email.</p>
    <p>Best regards,<br>Sinas</p>
</div>
            """.strip(),
            text_content="""
Your Login Code

Hello,

Your login verification code is: {{ otp_code }}

This code will expire in {{ expiry_minutes }} minutes.

If you didn't request this code, please ignore this email.

Best regards,
SINAS Team
            """.strip(),
            variable_schema={
                "type": "object",
                "properties": {
                    "otp_code": {
                        "type": "string",
                        "description": "6-digit OTP verification code"
                    },
                    "user_email": {
                        "type": "string",
                        "format": "email",
                        "description": "User's email address"
                    },
                    "expiry_minutes": {
                        "type": "integer",
                        "description": "Minutes until OTP expires"
                    }
                },
                "required": ["otp_code", "user_email", "expiry_minutes"]
            },
            is_active=True,
        )
        db.add(otp_template)
        await db.commit()
        logger.info(f"✅ Created default template: {otp_template_name}")
    else:
        logger.debug(f"Default template already exists: {otp_template_name}")
