"""Chat and message schemas."""
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class ChatCreate(BaseModel):
    title: str
    agent_id: Optional[uuid.UUID] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = None
    archived: Optional[bool] = None


class ChatResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str  # Creator's email address
    agent_id: Optional[uuid.UUID]
    agent_namespace: Optional[str]
    agent_name: Optional[str]
    title: str
    archived: bool = False
    expires_at: Optional[datetime] = None
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
    tool_calls: Optional[list[dict[str, Any]]]
    tool_call_id: Optional[str]
    name: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# Multimodal content part schemas (for OpenAPI documentation)
# ============================================================================


class TextContentPart(BaseModel):
    """Plain text content."""

    type: Literal["text"]
    text: str


class ImageContentPart(BaseModel):
    """Image content via URL or inline base64 data URL."""

    type: Literal["image"]
    image: str = Field(
        description='HTTPS URL (e.g. "https://example.com/photo.jpg") '
        'or data URL (e.g. "data:image/png;base64,iVBOR...")'
    )
    detail: Optional[Literal["low", "high", "auto"]] = Field(
        None, description="Resolution hint (OpenAI-specific, ignored by other providers)"
    )


class AudioContentPart(BaseModel):
    """Base64-encoded audio content."""

    type: Literal["audio"]
    data: str = Field(description="Base64-encoded audio data")
    format: Literal["wav", "mp3", "m4a", "ogg"]


class FileContentPart(BaseModel):
    """File or document content. Provide at least one of file_data, file_url, or file_id."""

    type: Literal["file"]
    file_data: Optional[str] = Field(None, description="Base64-encoded file content")
    file_url: Optional[str] = Field(None, description="HTTPS URL pointing to the file")
    file_id: Optional[str] = Field(None, description="Previously uploaded file ID (OpenAI)")
    filename: Optional[str] = Field(None, description="Original filename (recommended)")
    mime_type: Optional[str] = Field(None, description="MIME type, e.g. application/pdf")


ContentPart = Annotated[
    Union[TextContentPart, ImageContentPart, AudioContentPart, FileContentPart],
    Field(discriminator="type"),
]


class AgentChatCreateRequest(BaseModel):
    """Create chat with agent using system prompt templating."""

    # System prompt templating (validated against agent.input_schema)
    input: Optional[dict[str, Any]] = None

    # Optional title for the chat
    title: Optional[str] = None

    # Optional TTL in seconds — chat will be hard-deleted after this duration
    expires_in: Optional[int] = Field(None, gt=0)


class MessageSendRequest(BaseModel):
    """
    Send message to existing chat.

    All agent behavior (LLM, tools, context) is defined by the agent.
    This request only contains the message content.

    Supports multimodal content: text, images, audio, and files.
    Universal format - automatically converted to provider-specific format.
    """

    content: Union[str, list[ContentPart]] = Field(
        description="Plain string for text-only messages, "
        "or an array of content parts for multimodal messages.",
        examples=[
            "Hello, how can you help me?",
            [
                {"type": "text", "text": "What is in this image?"},
                {"type": "image", "image": "https://example.com/photo.jpg"},
            ],
        ],
    )


class ChatWithMessages(ChatResponse):
    messages: list[MessageResponse]


class ToolApprovalRequest(BaseModel):
    """Approve or reject a tool call that requires user approval."""

    approved: bool


class ToolApprovalResponse(BaseModel):
    """Response from approving/rejecting a tool call."""

    status: str  # "approved", "rejected"
    tool_call_id: str
    message: Optional[str] = None
