"""
SMTP server for receiving incoming emails.
Integrates with SINAS webhook system to trigger functions on email receipt.
"""

import asyncio
import email
import re
from email.message import EmailMessage
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPServer
from datetime import datetime
import uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.email import Email, EmailStatus, EmailInbox, EmailInboxRule
from app.models.execution import Execution, ExecutionStatus
from app.models.function import Function
from app.services.execution_engine import executor
import logging

logger = logging.getLogger(__name__)


class EmailHandler:
    """Handles incoming SMTP email messages"""

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        """Accept all recipient addresses"""
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(self, server, session, envelope):
        """Process incoming email data"""
        try:
            message_data = envelope.content.decode('utf8', errors='replace')
            msg = email.message_from_string(message_data)

            from_email = envelope.mail_from
            to_emails = envelope.rcpt_tos

            message_id = msg.get('Message-ID', f"<{uuid.uuid4()}>")
            subject = msg.get('Subject', 'No Subject')

            # Extract content
            html_content = None
            text_content = None
            attachments = []

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))

                    if "attachment" in content_disposition:
                        attachments.append({
                            'filename': part.get_filename(),
                            'content_type': content_type,
                            'size': len(part.get_payload())
                        })
                    elif content_type == "text/plain":
                        text_content = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    elif content_type == "text/html":
                        html_content = part.get_payload(decode=True).decode('utf-8', errors='replace')
            else:
                content_type = msg.get_content_type()
                payload = msg.get_payload(decode=True)
                if payload:
                    content = payload.decode('utf-8', errors='replace')
                    if content_type == "text/html":
                        html_content = content
                    else:
                        text_content = content

            # Extract headers
            headers = {}
            for key, value in msg.items():
                if key.lower() not in ['from', 'to', 'subject', 'message-id']:
                    headers[key] = value

            async with AsyncSessionLocal() as db:
                for to_email in to_emails:
                    # Find matching inbox
                    inbox = await self._find_inbox(db, to_email)

                    # Create email record
                    email_record = Email(
                        message_id=f"{message_id}-{to_email}",
                        from_email=from_email,
                        to_email=to_email,
                        subject=subject,
                        html_content=html_content,
                        text_content=text_content,
                        raw_content=message_data,
                        headers=headers,
                        attachments=attachments if attachments else None,
                        status=EmailStatus.RECEIVED,
                        direction="inbound",
                        inbox_id=inbox.id if inbox else None,
                        received_at=datetime.utcnow()
                    )
                    db.add(email_record)
                    await db.commit()
                    await db.refresh(email_record)

                    logger.info(f"Email received from {from_email} to {to_email}")

                    # Trigger webhook/function if inbox configured
                    if inbox and inbox.active:
                        await self._trigger_email_webhook(db, email_record, inbox)

            return '250 Message accepted for delivery'

        except Exception as e:
            logger.error(f"Error processing email: {str(e)}", exc_info=True)
            return '554 Transaction failed'

    async def _find_inbox(self, db: AsyncSession, email_address: str) -> Optional[EmailInbox]:
        """Find inbox matching email address"""
        result = await db.execute(
            select(EmailInbox).filter(
                EmailInbox.email_address == email_address,
                EmailInbox.active == True
            )
        )
        return result.scalar_one_or_none()

    async def _trigger_email_webhook(self, db: AsyncSession, email_record: Email, inbox: EmailInbox):
        """Trigger webhook function for incoming email"""
        try:
            # Check if any rules match
            webhook_id = inbox.webhook_id

            if inbox.rules:
                # Get rules sorted by priority
                result = await db.execute(
                    select(EmailInboxRule).filter(
                        EmailInboxRule.inbox_id == inbox.id,
                        EmailInboxRule.active == True
                    ).order_by(EmailInboxRule.priority.desc())
                )
                rules = result.scalars().all()

                # Find first matching rule
                for rule in rules:
                    if await self._rule_matches(email_record, rule):
                        webhook_id = rule.webhook_id or webhook_id
                        logger.info(f"Email matched rule: {rule.name}")
                        break

            if not webhook_id:
                logger.info(f"No webhook configured for inbox {inbox.name}")
                return

            # Get webhook to find function
            from app.models.webhook import Webhook
            result = await db.execute(
                select(Webhook).filter(Webhook.id == webhook_id)
            )
            webhook = result.scalar_one_or_none()

            if not webhook:
                logger.error(f"Webhook {webhook_id} not found")
                return

            # Get function
            result = await db.execute(
                select(Function).filter(Function.name == webhook.function_name)
            )
            function = result.scalar_one_or_none()

            if not function:
                logger.error(f"Function {webhook.function_name} not found")
                return

            # Prepare email data for function input
            email_data = {
                "id": email_record.id,
                "message_id": email_record.message_id,
                "from": email_record.from_email,
                "to": email_record.to_email,
                "subject": email_record.subject,
                "text_content": email_record.text_content,
                "html_content": email_record.html_content,
                "headers": email_record.headers,
                "attachments": email_record.attachments,
                "received_at": email_record.received_at.isoformat() if email_record.received_at else None
            }

            # Merge with webhook default values
            input_data = {**(webhook.default_values or {}), "email": email_data}

            # Execute function asynchronously
            asyncio.create_task(self._execute_webhook_function(
                function=function,
                input_data=input_data,
                email_id=email_record.id
            ))

            logger.info(f"Triggered webhook function {webhook.function_name} for email {email_record.id}")

        except Exception as e:
            logger.error(f"Error triggering webhook for email {email_record.id}: {e}", exc_info=True)

    async def _execute_webhook_function(self, function, input_data: dict, email_id: int):
        """Execute webhook function in background"""
        try:
            async with AsyncSessionLocal() as db:
                await executor.execute_function(
                    db=db,
                    function=function,
                    input_data=input_data,
                    user_id=function.user_id,
                    trigger_type="email_webhook",
                    trigger_metadata={"email_id": email_id}
                )
        except Exception as e:
            logger.error(f"Error executing webhook function for email {email_id}: {e}", exc_info=True)

    async def _rule_matches(self, email_record: Email, rule: EmailInboxRule) -> bool:
        """Check if email matches rule conditions"""
        try:
            if rule.from_pattern:
                if not re.search(rule.from_pattern, email_record.from_email, re.IGNORECASE):
                    return False

            if rule.subject_pattern:
                if not re.search(rule.subject_pattern, email_record.subject, re.IGNORECASE):
                    return False

            if rule.body_pattern:
                body = email_record.text_content or email_record.html_content or ""
                if not re.search(rule.body_pattern, body, re.IGNORECASE):
                    return False

            return True
        except re.error as e:
            logger.error(f"Invalid regex in rule {rule.id}: {e}")
            return False


class SMTPServerManager:
    """Manages SMTP server lifecycle"""

    def __init__(self, host: str = "0.0.0.0", port: int = 2525):
        self.host = host
        self.port = port
        self.controller = None

    def start(self):
        """Start SMTP server"""
        handler = EmailHandler()
        self.controller = Controller(
            handler,
            hostname=self.host,
            port=self.port
        )
        self.controller.start()
        logger.info(f"SMTP server started on {self.host}:{self.port}")

    def stop(self):
        """Stop SMTP server"""
        if self.controller:
            self.controller.stop()
            logger.info("SMTP server stopped")


from app.core.config import settings

smtp_server = SMTPServerManager(host=settings.smtp_server_host, port=settings.smtp_server_port)
