"""Chat and message schemas."""
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, SkipValidation


class ChatCreate(BaseModel):
    title: str
    agent_id: Optional[uuid.UUID] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = None
    archived: Optional[bool] = None


class ProviderCredentialInput(BaseModel):
    """A run's own credential for one LLM provider (write-only)."""

    model_config = ConfigDict(extra="forbid")

    credential: str = Field(
        description="The credential, presented on this chat's calls to the provider. Never returned."
    )
    expires_in: Optional[int] = Field(
        86400,
        description="Seconds until it expires (1 to 2592000, default 86400). Sending it again "
        "replaces it, which also extends it.",
    )


# Validated by the endpoint (app/services/provider_credentials.py), not here:
# a malformed entry is a 400 `invalid_request` whose message never quotes the
# credential, where a validation error here would be a 422 echoing its input.
ProviderCredentials = SkipValidation[Optional[dict[str, Optional[ProviderCredentialInput]]]]

PROVIDER_CREDENTIALS_DESCRIPTION = (
    "Write-only. The run's own credential per LLM provider name, presented on every LLM call "
    "of this chat to that provider (later messages, approvals, resumes, background jobs, "
    "sub-agents' chats) instead of the provider's configured key, or in a header the provider "
    "names (its config.run_credential). Sending a provider again replaces its credential; null "
    "removes it. Needs sinas.chats.assign_provider_credential:own; the provider must enable "
    "run_credential. 400 invalid_request for an unknown provider, one that hasn't enabled it, "
    "an empty credential, or expires_in out of range."
)


class ProviderCredentialSet(BaseModel):
    """A provider this chat has an unexpired run credential for (never the credential)."""

    provider: str
    expires_at: datetime


class ChatResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str  # Creator's email address
    agent_id: Optional[uuid.UUID]
    agent_namespace: Optional[str]
    agent_name: Optional[str]
    title: str
    archived: bool = False
    session_key: Optional[str] = None
    expires_at: Optional[datetime] = None
    keep_alive: Optional[bool] = None
    active_channel_id: Optional[str] = None
    # Whether this chat's agent has the workbench tool: what a client needs
    # to show the workbench panel, available with chat permission alone
    # (reading the agent itself may be a permission the user lacks).
    workbench_enabled: Optional[bool] = None
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None  # Timestamp of last message
    # Read-only: the providers this chat has an unexpired run credential for.
    provider_credentials_set: list[ProviderCredentialSet] = Field(default_factory=list)

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
    """Audio content via base64 data or URL."""

    type: Literal["audio"]
    data: Optional[str] = Field(None, description="Base64-encoded audio data")
    url: Optional[str] = Field(None, description="URL pointing to the audio file (e.g., from a collection)")
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

    # Long-running workflow overrides (defaults from agent if not provided)
    job_timeout: Optional[int] = Field(None, gt=0, description="Job timeout override in seconds")
    keep_alive: Optional[bool] = Field(None, description="Keep agent job alive after HTTP disconnect")

    provider_credentials: ProviderCredentials = Field(
        None, repr=False, description=PROVIDER_CREDENTIALS_DESCRIPTION
    )


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
    include_thinking: bool = Field(
        default=False,
        description="Streaming only: send the model's thinking text in `thinking`. "
        "Without it, a stream still gets one empty `thinking` when the model starts thinking.",
    )
    provider_credentials: ProviderCredentials = Field(
        None, repr=False, description=PROVIDER_CREDENTIALS_DESCRIPTION
    )


class PendingApprovalResponse(BaseModel):
    """A tool call awaiting user approval."""
    tool_call_id: str
    function_namespace: str
    function_name: str
    arguments: dict[str, Any] = {}


class PendingInputResponse(BaseModel):
    """An ask_user question the conversation is suspended on."""

    tool_call_id: str
    question: str
    options: Optional[list[str]] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class PendingInputAnswer(BaseModel):
    """Answer to a pending ask_user question; becomes the tool result the
    suspended round resumes with."""

    answer: str
    wait: bool = Field(
        default=False,
        description=(
            "Wait for the resumed turn and return its result (200, the same "
            "status/reply/pending_inputs/usage shape as invoke) instead of "
            "a channel to stream it from (202)."
        ),
    )


TurnStatus = Literal["completed", "input_required", "in_progress", "injected"]


class TurnState(BaseModel):
    """How a synchronously answered turn ended, and what to do next."""

    status: TurnStatus = Field(
        default="completed",
        description=(
            "`completed`: the turn's final reply is in this response. "
            "`input_required`: the agent asked the user (ask_user) and the turn "
            "waits for the answer: see `pending_inputs`, answer with "
            "POST /chats/{chat_id}/pending-input/{tool_call_id}. "
            "`in_progress`: the turn goes on without this request (a background "
            "run it waits for outlasted the request's time budget); follow it on "
            "`channel_id` (GET /chats/{chat_id}/stream/{channel_id}) or read the "
            "chat later. `injected`: the chat was busy (a turn running, or one "
            "waiting on the user or on background work), so the message was "
            "delivered into that turn, which reads it at its next step; nothing "
            "was answered by this request."
        ),
    )
    pending_inputs: list[PendingInputResponse] = Field(
        default_factory=list,
        description="The chat's open ask_user questions (input_required, injected).",
    )
    channel_id: Optional[str] = Field(
        default=None,
        description=(
            "Stream channel of the turn still going on (in_progress), or of the "
            "parked turn an injected message went to."
        ),
    )


class ChatWithMessages(ChatResponse):
    messages: list[MessageResponse]
    pending_approvals: list[PendingApprovalResponse] = []
    pending_inputs: list[PendingInputResponse] = []


class InvokeRequest(BaseModel):
    """Synchronous agent invoke — send message, get reply."""

    message: Union[str, list[ContentPart]] = Field(
        description="Message to send to the agent"
    )
    session_key: Optional[str] = Field(
        None, description="Session key for conversation continuity"
    )
    reset: Optional[bool] = Field(
        False, description="If true, archive existing session and start fresh"
    )
    input: Optional[dict[str, Any]] = Field(
        None, description="Agent input variables (only used when creating a new chat)"
    )
    provider_credentials: ProviderCredentials = Field(
        None, repr=False, description=PROVIDER_CREDENTIALS_DESCRIPTION
    )


class TurnUsage(BaseModel):
    """LLM usage of one request: every LLM call made while answering it.

    Covers the reply and its tool rounds. Not included: earlier turns of the
    same chat, calls made by delegated sub-agents (they run as separate
    jobs, under their own chats), and background compaction. Field names
    match the `/api/v1/usage/summary` metrics; each call is also an
    `llm_usage` row, committed before the response is returned. A call
    whose row failed to save (or took over 10s) is counted with its tokens
    but as unpriced, so `cost_usd` never includes a cost /usage lacks.
    """

    calls: int = Field(description="LLM calls made")
    prompt_tokens: int = Field(description="Input tokens, cache reads and writes included")
    completion_tokens: int = Field(description="Output tokens")
    total_tokens: int = Field(description="prompt_tokens + completion_tokens")
    cache_read_tokens: int = Field(description="Part of prompt_tokens read from the prompt cache")
    cache_write_tokens: int = Field(description="Part of prompt_tokens written to the prompt cache")
    cost_usd: Optional[float] = Field(
        description=(
            "Cost of the priced calls in USD. Null when calls were made but none "
            "could be priced; when unpriced_calls > 0 it covers the priced calls only."
        )
    )
    unpriced_calls: int = Field(description="Calls without a known price, not in cost_usd")
    errors: int = Field(description="Calls that failed")


class InvokeResponse(TurnState):
    """Response from synchronous agent invoke."""

    reply: str = Field(
        description=(
            "The agent's final reply (completed). For input_required / "
            "in_progress, whatever the agent said before it asked or started "
            "waiting (often empty); for injected, empty."
        )
    )
    chat_id: uuid.UUID
    session_key: Optional[str] = None
    usage: Optional[TurnUsage] = Field(
        None,
        description=(
            "LLM usage of this invoke only (not of earlier invokes in the same "
            "session, nor of a later answer's continuation): the calls made "
            "while this request ran, including a continuation it waited for. "
            "Null only if it could not be determined."
        ),
    )


class MessageSendResponse(MessageResponse, TurnState):
    """The reply to a non-streaming message send, with the turn's LLM usage.

    The message is the final reply (completed), the assistant message that
    asked or started waiting (input_required / in_progress), or the user
    message as stored (injected)."""

    usage: Optional[TurnUsage] = Field(
        None,
        description=(
            "LLM usage of this send only (not of earlier turns, nor of a later "
            "answer's continuation). Null only if it could not be determined."
        ),
    )


class PendingInputAnswerAccepted(BaseModel):
    """The answer was recorded (202, without `wait`)."""

    status: Literal["answered"] = "answered"
    tool_call_id: str
    resumed: bool = Field(description="The answer was the round's last completion; its resume is queued.")
    resume_pending: bool = Field(
        description="The last completion, but the resume could not be queued yet; it is retried shortly."
    )
    channel_id: Optional[str] = Field(
        None,
        description="Where the resumed turn streams (GET /chats/{chat_id}/stream/{channel_id}), "
        "when resumed or resume_pending.",
    )
    message: str


class PendingInputAnswerResult(TurnState):
    """The resumed turn's result, for an answer posted with `wait: true`."""

    tool_call_id: str
    chat_id: uuid.UUID
    reply: str = Field(description="The final reply (completed); see InvokeResponse.reply.")
    message: Optional[MessageResponse] = Field(
        None, description="The message `reply` comes from, when there is one."
    )
    usage: Optional[TurnUsage] = Field(
        None,
        description=(
            "LLM usage of the continuation this answer resumed, as far as this "
            "request waited for it. Null only if it could not be determined."
        ),
    )


class ToolApprovalRequest(BaseModel):
    """Approve or reject a tool call that requires user approval."""

    approved: bool
    # "Always allow" for the rest of this chat: approving with this flag
    # records a session grant so the same tool won't ask again.
    always_allow: bool = False


class ToolApprovalResponse(BaseModel):
    """Response from approving/rejecting a tool call."""

    status: str  # "approved", "rejected"
    tool_call_id: str
    message: Optional[str] = None
