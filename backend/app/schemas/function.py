"""Function schemas."""
import ast
import json
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, validator


class FunctionCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: Optional[str] = None
    code: str = Field(..., min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    icon: Optional[str] = None  # "collection:ns/coll/file" or "url:https://..."
    shared_pool: bool = Field(
        default=False, description="Use shared worker pool instead of isolated container"
    )
    requires_approval: bool = Field(
        default=False, description="Require user approval before execution"
    )
    timeout: Optional[int] = Field(
        default=None, description="Per-function timeout in seconds (null = use global default)"
    )

    @validator("code")
    def validate_code(cls, v):
        try:
            ast.parse(v)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python syntax: {e}")
        return v

    @validator("input_schema", "output_schema")
    def validate_schema(cls, v):
        try:
            # Try to serialize and deserialize to validate JSON schema format
            json.dumps(v)
            # Basic check for required JSON Schema fields
            if not isinstance(v, dict):
                raise ValueError("Schema must be a dictionary")
            if "type" not in v:
                raise ValueError("Schema must have a 'type' field")
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid JSON schema: {e}")
        return v


class FunctionUpdate(BaseModel):
    namespace: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    description: Optional[str] = None
    code: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    icon: Optional[str] = None  # "collection:ns/coll/file" or "url:https://..."
    shared_pool: Optional[bool] = None
    requires_approval: Optional[bool] = None
    timeout: Optional[int] = None
    is_active: Optional[bool] = None

    @validator("code")
    def validate_code(cls, v):
        if v is not None:
            try:
                ast.parse(v)
            except SyntaxError as e:
                raise ValueError(f"Invalid Python syntax: {e}")
        return v

    @validator("input_schema", "output_schema")
    def validate_schema(cls, v):
        if v is not None:
            try:
                json.dumps(v)
                if not isinstance(v, dict):
                    raise ValueError("Schema must be a dictionary")
                if "type" not in v:
                    raise ValueError("Schema must have a 'type' field")
            except (TypeError, ValueError) as e:
                raise ValueError(f"Invalid JSON schema: {e}")
        return v


class FunctionResponse(BaseModel):
    id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    code: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    icon: Optional[str] = None
    icon_url: Optional[str] = None
    shared_pool: bool
    requires_approval: bool
    timeout: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FunctionVersionResponse(BaseModel):
    id: uuid.UUID
    function_id: uuid.UUID
    version: int
    code: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    created_at: datetime
    created_by: uuid.UUID

    class Config:
        from_attributes = True


class FunctionExecuteRequest(BaseModel):
    """Request to execute a function."""

    input: dict[str, Any] = {}
    timeout: Optional[int] = None  # Timeout in seconds (sync only)


class FunctionExecuteResponse(BaseModel):
    """Response from function execution."""

    status: str
    execution_id: str
    result: Any = None
    error: Optional[str] = None


class FunctionExecuteAsyncResponse(BaseModel):
    """Response from async function execution."""

    execution_id: str
    status: str = "queued"


