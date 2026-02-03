"""Agent schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class EnabledSkillConfig(BaseModel):
    """Configuration for an enabled skill."""

    skill: str = Field(..., description="Skill identifier in format 'namespace/name'")
    preload: bool = Field(
        default=False, description="If true, inject into system prompt instead of exposing as tool"
    )


class AgentCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9 _-]*$")
    description: Optional[str] = None
    llm_provider_id: Optional[uuid.UUID] = None  # NULL = use default provider
    model: Optional[str] = None  # NULL = use provider's default model
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None  # NULL = use provider's default
    system_prompt: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    initial_messages: Optional[list[dict[str, str]]] = None
    enabled_functions: Optional[list[str]] = None  # List of "namespace/name" strings
    enabled_mcp_tools: Optional[list[str]] = None
    enabled_agents: Optional[list[str]] = None  # List of agent names that can be called as tools
    enabled_skills: Optional[
        list[EnabledSkillConfig]
    ] = None  # List of skill configs with preload option
    function_parameters: Optional[
        dict[str, Any]
    ] = None  # {"namespace/name": {"param": "value or {{template}}"}}
    mcp_tool_parameters: Optional[dict[str, Any]] = None
    state_namespaces_readonly: Optional[list[str]] = None  # Readonly state namespaces
    state_namespaces_readwrite: Optional[list[str]] = None  # Read-write state namespaces


class AgentUpdate(BaseModel):
    namespace: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9 _-]*$"
    )
    description: Optional[str] = None
    llm_provider_id: Optional[uuid.UUID] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    initial_messages: Optional[list[dict[str, str]]] = None
    enabled_functions: Optional[list[str]] = None  # List of "namespace/name" strings
    enabled_mcp_tools: Optional[list[str]] = None
    enabled_agents: Optional[list[str]] = None  # List of agent names that can be called as tools
    enabled_skills: Optional[
        list[EnabledSkillConfig]
    ] = None  # List of skill configs with preload option
    function_parameters: Optional[
        dict[str, Any]
    ] = None  # {"namespace/name": {"param": "value or {{template}}"}}
    mcp_tool_parameters: Optional[dict[str, Any]] = None
    state_namespaces_readonly: Optional[list[str]] = None
    state_namespaces_readwrite: Optional[list[str]] = None
    is_active: Optional[bool] = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    namespace: str
    name: str
    description: Optional[str]
    llm_provider_id: Optional[uuid.UUID]
    model: Optional[str]
    temperature: float
    max_tokens: Optional[int]
    system_prompt: Optional[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    initial_messages: Optional[list[dict[str, str]]]
    enabled_functions: list[str]  # List of "namespace/name" strings
    enabled_mcp_tools: list[str]
    enabled_agents: list[str]  # List of agent names that can be called as tools
    enabled_skills: list[EnabledSkillConfig]  # List of skill configs with preload option
    function_parameters: dict[str, Any]  # {"namespace/name": {"param": "value or {{template}}"}}
    mcp_tool_parameters: dict[str, Any]
    state_namespaces_readonly: list[str]
    state_namespaces_readwrite: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("enabled_skills", mode="before")
    @classmethod
    def convert_enabled_skills(cls, v):
        """Convert dicts from database to EnabledSkillConfig objects."""
        if not v:
            return []

        result = []
        for item in v:
            if isinstance(item, dict):
                result.append(EnabledSkillConfig(**item))
            elif isinstance(item, EnabledSkillConfig):
                result.append(item)
            else:
                # Fallback for unexpected types
                result.append(EnabledSkillConfig(skill=str(item), preload=False))
        return result

    class Config:
        from_attributes = True
