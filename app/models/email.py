from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base, GUID
import enum
import uuid as uuid_lib


class EmailStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RECEIVED = "received"


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    subject = Column(String(500), nullable=False)
    html_content = Column(Text, nullable=False)
    text_content = Column(Text, nullable=True)
    example_variables = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    emails = relationship("Email", back_populates="template")


class Email(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String(255), unique=True, index=True)
    from_email = Column(String(255), nullable=False, index=True)
    to_email = Column(String(255), nullable=False, index=True)
    cc = Column(JSON, nullable=True)
    bcc = Column(JSON, nullable=True)
    subject = Column(String(500), nullable=False)
    html_content = Column(Text, nullable=True)
    text_content = Column(Text, nullable=True)
    raw_content = Column(Text, nullable=True)
    headers = Column(JSON, nullable=True)
    attachments = Column(JSON, nullable=True)
    status = Column(SQLEnum(EmailStatus), default=EmailStatus.PENDING, index=True)
    direction = Column(String(10), nullable=False, index=True)  # inbound/outbound
    inbox_id = Column(Integer, ForeignKey("email_inboxes.id"), nullable=True, index=True)
    template_id = Column(Integer, ForeignKey("email_templates.id"), nullable=True)
    template_variables = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    template = relationship("EmailTemplate", back_populates="emails")
    inbox = relationship("EmailInbox", back_populates="emails")


class EmailInbox(Base):
    __tablename__ = "email_inboxes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email_address = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    active = Column(Boolean, default=True)

    # Webhook integration - uses existing SINAS webhook system
    # When email received, triggers this webhook's function
    webhook_id = Column(GUID(), ForeignKey("webhooks.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    emails = relationship("Email", back_populates="inbox")
    rules = relationship("EmailInboxRule", back_populates="inbox", cascade="all, delete-orphan")
    webhook = relationship("Webhook")


class EmailInboxRule(Base):
    __tablename__ = "email_inbox_rules"

    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("email_inboxes.id"), nullable=False)
    name = Column(String(255), nullable=False)

    # Rule conditions (all must match for rule to trigger)
    from_pattern = Column(String(500), nullable=True)  # Regex pattern
    subject_pattern = Column(String(500), nullable=True)  # Regex pattern
    body_pattern = Column(String(500), nullable=True)  # Regex pattern

    # Action: execute specific webhook's function instead of default inbox webhook
    webhook_id = Column(GUID(), ForeignKey("webhooks.id"), nullable=True)

    priority = Column(Integer, default=0)  # Higher priority rules evaluated first
    active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    inbox = relationship("EmailInbox", back_populates="rules")
    webhook = relationship("Webhook")
