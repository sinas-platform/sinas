"""Chat and message schemas."""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid


class ChatCreate(BaseModel):
    title: str
    assistant_id: Optional[uuid.UUID] = None
    group_id: Optional[uuid.UUID] = None
    enabled_webhooks: Optional[List[str]] = None
    enabled_mcp_tools: Optional[List[str]] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = None
    enabled_webhooks: Optional[List[str]] = None
    enabled_mcp_tools: Optional[List[str]] = None


class ChatResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    group_id: Optional[uuid.UUID]
    assistant_id: Optional[uuid.UUID]
    title: str
    enabled_webhooks: List[str]
    enabled_mcp_tools: List[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    role: str
    content: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]
    tool_call_id: Optional[str]
    name: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class MessageSendRequest(BaseModel):
    content: str
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    enabled_webhooks: Optional[List[str]] = None
    disabled_webhooks: Optional[List[str]] = None
    enabled_mcp_tools: Optional[List[str]] = None
    disabled_mcp_tools: Optional[List[str]] = None
    inject_context: bool = True  # Auto-inject relevant context into prompts
    context_namespaces: Optional[List[str]] = None  # Filter which namespaces to inject
    context_limit: int = 5  # Max number of context entries to inject


class ChatWithMessages(ChatResponse):
    messages: List[MessageResponse]
