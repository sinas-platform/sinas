"""LLM Provider schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class LLMProviderCreate(BaseModel):
    """Schema for creating a new LLM provider."""

    name: str
    provider_type: str  # "openai", "anthropic", "ollama", "azure", etc.
    api_key: Optional[str] = None
    api_endpoint: Optional[str] = None
    default_model: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = False


class LLMProviderUpdate(BaseModel):
    """Schema for updating an LLM provider."""

    name: Optional[str] = None
    provider_type: Optional[str] = None
    api_key: Optional[str] = None
    api_endpoint: Optional[str] = None
    default_model: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class LLMProviderResponse(BaseModel):
    """Schema for LLM provider response (API key is never returned)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider_type: str
    api_endpoint: Optional[str] = None
    default_model: Optional[str] = None
    config: dict[str, Any]
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
