"""Assistant schemas."""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid


class AssistantCreate(BaseModel):
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    group_id: Optional[uuid.UUID] = None
    enabled_webhooks: Optional[List[str]] = None
    enabled_mcp_tools: Optional[List[str]] = None
    webhook_parameters: Optional[Dict[str, Any]] = None
    mcp_tool_parameters: Optional[Dict[str, Any]] = None
    context_namespaces: Optional[List[str]] = None  # None = all namespaces
    ontology_namespaces: Optional[List[str]] = None  # None = all namespaces
    ontology_concepts: Optional[List[str]] = None  # None = all concepts (format: namespace.concept)


class AssistantUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    enabled_webhooks: Optional[List[str]] = None
    enabled_mcp_tools: Optional[List[str]] = None
    webhook_parameters: Optional[Dict[str, Any]] = None
    mcp_tool_parameters: Optional[Dict[str, Any]] = None
    context_namespaces: Optional[List[str]] = None
    ontology_namespaces: Optional[List[str]] = None
    ontology_concepts: Optional[List[str]] = None
    is_active: Optional[bool] = None


class AssistantResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    group_id: Optional[uuid.UUID]
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    enabled_webhooks: List[str]
    enabled_mcp_tools: List[str]
    webhook_parameters: Dict[str, Any]
    mcp_tool_parameters: Dict[str, Any]
    context_namespaces: Optional[List[str]]
    ontology_namespaces: Optional[List[str]]
    ontology_concepts: Optional[List[str]]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
