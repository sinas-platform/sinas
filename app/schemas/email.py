from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.email import EmailStatus
import uuid


class EmailTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=500)
    html_content: str
    text_content: Optional[str] = None
    example_variables: Optional[Dict[str, Any]] = None


class EmailTemplateCreate(EmailTemplateBase):
    pass


class EmailTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    subject: Optional[str] = Field(None, min_length=1, max_length=500)
    html_content: Optional[str] = None
    text_content: Optional[str] = None
    example_variables: Optional[Dict[str, Any]] = None


class EmailTemplateResponse(EmailTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class EmailSend(BaseModel):
    to_email: EmailStr
    from_email: Optional[EmailStr] = None
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    subject: Optional[str] = None
    html_content: Optional[str] = None
    text_content: Optional[str] = None
    template_name: Optional[str] = None
    template_variables: Optional[Dict[str, Any]] = None
    attachments: Optional[List[Dict[str, Any]]] = None


class EmailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: str
    from_email: str
    to_email: str
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    subject: str
    html_content: Optional[str] = None
    text_content: Optional[str] = None
    status: EmailStatus
    direction: str
    inbox_id: Optional[int] = None
    template_id: Optional[int] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    error_message: Optional[str] = None


class EmailListResponse(BaseModel):
    emails: List[EmailResponse]
    total: int
    page: int = 1
    per_page: int = 50


class EmailInboxBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email_address: EmailStr
    description: Optional[str] = None
    active: bool = True
    webhook_id: Optional[uuid.UUID] = None


class EmailInboxCreate(EmailInboxBase):
    pass


class EmailInboxUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email_address: Optional[EmailStr] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    webhook_id: Optional[uuid.UUID] = None


class EmailInboxResponse(EmailInboxBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class EmailInboxRuleBase(BaseModel):
    inbox_id: int
    name: str = Field(..., min_length=1, max_length=255)
    from_pattern: Optional[str] = Field(None, max_length=500)
    subject_pattern: Optional[str] = Field(None, max_length=500)
    body_pattern: Optional[str] = Field(None, max_length=500)
    webhook_id: Optional[uuid.UUID] = None
    priority: int = 0
    active: bool = True


class EmailInboxRuleCreate(EmailInboxRuleBase):
    pass


class EmailInboxRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    from_pattern: Optional[str] = Field(None, max_length=500)
    subject_pattern: Optional[str] = Field(None, max_length=500)
    body_pattern: Optional[str] = Field(None, max_length=500)
    webhook_id: Optional[uuid.UUID] = None
    priority: Optional[int] = None
    active: Optional[bool] = None


class EmailInboxRuleResponse(EmailInboxRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
