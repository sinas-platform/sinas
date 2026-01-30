"""Chat and message schemas."""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
import uuid


class ChatCreate(BaseModel):
    title: str
    agent_id: Optional[uuid.UUID] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = None


class ChatResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str  # Creator's email address
    agent_id: Optional[uuid.UUID]
    agent_namespace: Optional[str]
    agent_name: Optional[str]
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None  # Timestamp of last message

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


class AgentChatCreateRequest(BaseModel):
    """Create chat with agent using system prompt templating."""

    # System prompt templating (validated against agent.input_schema)
    input: Optional[Dict[str, Any]] = None

    # Optional title for the chat
    title: Optional[str] = None


class MessageSendRequest(BaseModel):
    """
    Send message to existing chat.

    All agent behavior (LLM, tools, context) is defined by the agent.
    This request only contains the message content.

    Supports multimodal content: text, images, audio, and files.
    Universal format - automatically converted to provider-specific format.
    """

    content: Union[str, List[Dict[str, Any]]]
    # String: "Hello world"
    # Multimodal:
    # [
    #   {"type": "text", "text": "..."},
    #   {"type": "image", "image": "https://... or data:image/png;base64,..."},
    #   {"type": "audio", "data": "base64...", "format": "wav"},
    #   {"type": "file", "file_data": "base64...", "filename": "doc.pdf"}
    # ]


class ChatWithMessages(ChatResponse):
    messages: List[MessageResponse]


class ToolApprovalRequest(BaseModel):
    """Approve or reject a tool call that requires user approval."""
    approved: bool


class ToolApprovalResponse(BaseModel):
    """Response from approving/rejecting a tool call."""
    status: str  # "approved", "rejected"
    tool_call_id: str
    message: Optional[str] = None
