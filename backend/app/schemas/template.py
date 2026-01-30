"""Template schemas."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


class TemplateCreate(BaseModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r'^[a-z][a-z0-9_-]*$')
    name: str = Field(..., min_length=1, max_length=255, pattern=r'^[a-z][a-z0-9_-]*$')
    # Use snake_case or kebab-case for names (e.g., "otp-email", "sales_report_output")
    description: Optional[str] = None
    title: Optional[str] = None  # For emails (subject line), notifications (title), etc.
    html_content: str = Field(..., min_length=1)  # Jinja2 template
    text_content: Optional[str] = None  # Plain text fallback (for emails)
    variable_schema: Optional[Dict[str, Any]] = None  # JSON schema for variables


class TemplateUpdate(BaseModel):
    namespace: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r'^[a-z][a-z0-9_-]*$')
    name: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r'^[a-z][a-z0-9_-]*$')
    description: Optional[str] = None
    title: Optional[str] = None
    html_content: Optional[str] = Field(None, min_length=1)
    text_content: Optional[str] = None
    variable_schema: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class TemplateResponse(BaseModel):
    id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    title: Optional[str]
    html_content: str
    text_content: Optional[str]
    variable_schema: Dict[str, Any]
    is_active: bool
    user_id: Optional[uuid.UUID]
    created_by: Optional[uuid.UUID]
    updated_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    managed_by: Optional[str]
    config_name: Optional[str]
    config_checksum: Optional[str]

    model_config = {"from_attributes": True}


class TemplateRenderRequest(BaseModel):
    """Request to render a template with variables."""
    variables: Dict[str, Any] = Field(default_factory=dict)


class TemplateRenderResponse(BaseModel):
    """Rendered template output."""
    title: Optional[str]
    html_content: str
    text_content: Optional[str]
