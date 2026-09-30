"""Pipeline schemas."""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.services.pipeline_validation import validate_pipeline_definition

NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_-]*$"


class PipelineBase(BaseModel):
    """Shared create/update fields. Steps are free-form dicts validated by
    validate_pipeline_definition (camelCase keys and `.$` mapping keys are data
    and stored verbatim — one representation across YAML/API/DB)."""

    description: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    per_user: Optional[dict[str, Any]] = None
    as_tool: bool = False
    tool_description: Optional[str] = None
    sync_timeout_seconds: int = Field(default=120, ge=1, le=600)
    concurrency: Optional[Literal["single", "parallel"]] = None
    disable_after_failures: Optional[int] = Field(default=None, ge=1)
    output_mapping: Optional[dict[str, Any]] = None


class PipelineCreate(PipelineBase):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=NAME_PATTERN)
    name: str = Field(..., min_length=1, max_length=255, pattern=NAME_PATTERN)

    @model_validator(mode="after")
    def validate_definition(self):
        errors = validate_pipeline_definition(
            self.steps,
            per_user=self.per_user,
            as_tool=self.as_tool,
            input_schema=self.input_schema,
            description=self.description,
            tool_description=self.tool_description,
            concurrency=self.concurrency,
            output_mapping=self.output_mapping,
        )
        if errors:
            raise ValueError("; ".join(errors))
        return self


class PipelineUpdate(BaseModel):
    namespace: Optional[str] = Field(None, min_length=1, max_length=255, pattern=NAME_PATTERN)
    name: Optional[str] = Field(None, min_length=1, max_length=255, pattern=NAME_PATTERN)
    description: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    steps: Optional[list[dict[str, Any]]] = None
    per_user: Optional[dict[str, Any]] = None
    as_tool: Optional[bool] = None
    tool_description: Optional[str] = None
    sync_timeout_seconds: Optional[int] = Field(None, ge=1, le=600)
    concurrency: Optional[Literal["single", "parallel"]] = None
    disable_after_failures: Optional[int] = Field(None, ge=1)
    output_mapping: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    # Note: the merged (existing + update) definition is re-validated in the endpoint,
    # since partial updates can't be validated in isolation.


class PipelineResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    input_schema: dict[str, Any]
    steps: list[dict[str, Any]]
    per_user: Optional[dict[str, Any]]
    as_tool: bool
    tool_description: Optional[str]
    sync_timeout_seconds: int
    concurrency: Optional[str]
    disable_after_failures: Optional[int]
    output_mapping: Optional[dict[str, Any]]
    cursor_value: Optional[str]
    error_message: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class PipelineRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["sync", "async"] = "sync"


class PipelineRunResponse(BaseModel):
    run_id: str
    status: str
    output: Optional[Any] = None
    error: Optional[str] = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: Optional[int] = None


class PipelineRunEnqueuedResponse(BaseModel):
    run_id: str
    status: str = "queued"


class PipelineFanOutResponse(BaseModel):
    """Response for ?allUsers=true fan-out runs."""
    runs: list[PipelineRunEnqueuedResponse]
    users: int


class PipelineRunRecord(BaseModel):
    id: uuid.UUID
    run_id: str
    pipeline_id: uuid.UUID
    user_id: uuid.UUID
    trigger_type: str
    trigger_id: Optional[str]
    status: str
    input: Optional[dict[str, Any]]
    output: Optional[Any] = None
    steps: list[dict[str, Any]]
    error: Optional[str]
    cursor_before: Optional[str]
    cursor_after: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    duration_ms: Optional[int]

    class Config:
        from_attributes = True
