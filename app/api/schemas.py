from pydantic import BaseModel, Field, validator
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid
import ast
import json
from enum import Enum

from app.models.webhook import HTTPMethod
from app.models.execution import TriggerType, ExecutionStatus


class SubtenantCreate(BaseModel):
    description: Optional[str] = None


class SubtenantResponse(BaseModel):
    id: uuid.UUID  # This IS the subtenant_id
    description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class FunctionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    description: Optional[str] = None
    code: str = Field(..., min_length=1)
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    requirements: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

    @validator('code')
    def validate_code(cls, v):
        try:
            ast.parse(v)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python syntax: {e}")
        return v

    @validator('input_schema', 'output_schema')
    def validate_schema(cls, v):
        try:
            # Try to serialize and deserialize to validate JSON schema format
            json.dumps(v)
            # Basic check for required JSON Schema fields
            if not isinstance(v, dict):
                raise ValueError("Schema must be a dictionary")
            if 'type' not in v:
                raise ValueError("Schema must have a 'type' field")
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid JSON schema: {e}")
        return v


class FunctionUpdate(BaseModel):
    description: Optional[str] = None
    code: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    requirements: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None

    @validator('code')
    def validate_code(cls, v):
        if v is not None:
            try:
                ast.parse(v)
            except SyntaxError as e:
                raise ValueError(f"Invalid Python syntax: {e}")
        return v

    @validator('input_schema', 'output_schema')
    def validate_schema(cls, v):
        if v is not None:
            try:
                json.dumps(v)
                if not isinstance(v, dict):
                    raise ValueError("Schema must be a dictionary")
                if 'type' not in v:
                    raise ValueError("Schema must have a 'type' field")
            except (TypeError, ValueError) as e:
                raise ValueError(f"Invalid JSON schema: {e}")
        return v


class FunctionResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    code: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    requirements: List[str]
    tags: List[str]
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
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    created_at: datetime
    created_by: str

    class Config:
        from_attributes = True


class WebhookCreate(BaseModel):
    path: str = Field(..., min_length=1, max_length=255, pattern=r'^[a-zA-Z0-9_/-]+$')
    function_name: str = Field(..., min_length=1, max_length=255)
    http_method: HTTPMethod = HTTPMethod.POST
    description: Optional[str] = None
    default_values: Optional[Dict[str, Any]] = None
    requires_auth: bool = True


class WebhookUpdate(BaseModel):
    function_name: Optional[str] = None
    http_method: Optional[HTTPMethod] = None
    description: Optional[str] = None
    default_values: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    requires_auth: Optional[bool] = None


class WebhookResponse(BaseModel):
    id: uuid.UUID
    path: str
    function_name: str
    http_method: HTTPMethod
    description: Optional[str]
    default_values: Optional[Dict[str, Any]]
    is_active: bool
    requires_auth: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ScheduledJobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    function_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    cron_expression: str = Field(..., min_length=1)
    timezone: str = "UTC"
    input_data: Dict[str, Any]

    @validator('cron_expression')
    def validate_cron(cls, v):
        from croniter import croniter
        if not croniter.is_valid(v):
            raise ValueError("Invalid cron expression")
        return v


class ScheduledJobUpdate(BaseModel):
    function_name: Optional[str] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None

    @validator('cron_expression')
    def validate_cron(cls, v):
        if v is not None:
            from croniter import croniter
            if not croniter.is_valid(v):
                raise ValueError("Invalid cron expression")
        return v


class ScheduledJobResponse(BaseModel):
    id: uuid.UUID
    name: str
    function_name: str
    description: Optional[str]
    cron_expression: str
    timezone: str
    input_data: Dict[str, Any]
    is_active: bool
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class ExecutionResponse(BaseModel):
    id: uuid.UUID
    execution_id: str
    function_name: str
    trigger_type: TriggerType
    trigger_id: uuid.UUID
    status: ExecutionStatus
    input_data: Dict[str, Any]
    output_data: Optional[Any]
    error: Optional[str]
    traceback: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    duration_ms: Optional[int]

    class Config:
        from_attributes = True


class StepExecutionResponse(BaseModel):
    id: uuid.UUID
    execution_id: str
    function_name: str
    status: ExecutionStatus
    input_data: Dict[str, Any]
    output_data: Optional[Any]
    error: Optional[str]
    duration_ms: Optional[int]
    started_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class PackageInstall(BaseModel):
    package_name: str = Field(..., min_length=1, max_length=255)
    version: Optional[str] = None


class PackageResponse(BaseModel):
    id: uuid.UUID
    package_name: str
    version: Optional[str]
    installed_at: datetime
    installed_by: Optional[str]

    class Config:
        from_attributes = True


