"""Batch submission/poll schemas."""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Function batch ──

class FunctionBatchRequest(BaseModel):
    inputs: list[dict[str, Any]] = Field(..., min_length=1)
    trigger_id_prefix: Optional[str] = None
    delay_seconds: Optional[int] = None
    callback_url: Optional[str] = None
    batch_callback_url: Optional[str] = None


class FunctionBatchResponse(BaseModel):
    batch_id: str
    execution_ids: list[str]
    total: int
    status: str


# ── Agent batch ──

class AgentBatchItem(BaseModel):
    input_variables: dict[str, Any] = Field(default_factory=dict)
    message: str


class AgentBatchRequest(BaseModel):
    inputs: list[AgentBatchItem] = Field(..., min_length=1)
    trigger_id_prefix: Optional[str] = None
    callback_url: Optional[str] = None
    batch_callback_url: Optional[str] = None
    execution_mode: Literal["queue", "provider"] = Field(
        default="queue",
        description=(
            "'queue' runs each invocation through the internal worker queue "
            "(default). 'provider' submits the whole batch to the LLM "
            "provider's batch API (~50% cost, up to 24h turnaround) — only "
            "for agents without tools, on providers that support it. Note: "
            "provider mode renders prompts from the agent config and "
            "input_variables only; per-user context injection (custom "
            "fields) does not apply."
        ),
    )


class AgentBatchResponse(BaseModel):
    batch_id: str
    execution_ids: list[str]
    chat_ids: list[str]
    total: int
    status: str
    execution_mode: str = "queue"


# ── Polling ──

class BatchStatusResponse(BaseModel):
    batch_id: str
    kind: str
    target: dict[str, str]
    user_id: str
    total: int
    completed: int
    failed: int
    cancelled: int
    running: int
    queued: int
    status: str
    started_at: Optional[str]
    finished_at: Optional[str]
    trigger_id_prefix: Optional[str]
    execution_mode: str = "queue"


class BatchExecutionItem(BaseModel):
    execution_id: str
    status: str
    function_name: str
    chat_id: Optional[str]
    trigger_id: str
    started_at: Optional[str]
    completed_at: Optional[str]
    duration_ms: Optional[int]
    error: Optional[str]


class BatchExecutionsResponse(BaseModel):
    executions: list[BatchExecutionItem]


class BatchCancelResponse(BaseModel):
    batch_id: str
    status: str
    cancelled_children: int
