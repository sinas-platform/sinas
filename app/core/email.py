"""Email utility - imports from email service for backward compatibility."""

# Re-export from email service for backward compatibility
from app.services.email_service import send_otp_email_async

__all__ = ['send_otp_email_async']
