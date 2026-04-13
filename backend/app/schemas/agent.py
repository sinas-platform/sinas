"""Agent schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator


class EnabledSkillConfig(BaseModel):
    """Configuration for an enabled skill."""

    skill: str = Field(..., description="Skill identifier in format 'namespace/name'")
    preload: bool = Field(
        default=False, description="If true, inject into system prompt instead of exposing as tool"
    )


class EnabledStoreConfig(BaseModel):
    """Configuration for an enabled store."""

    store: str = Field(..., description="Store identifier in format 'namespace/name'")
    access: str = Field(
        default="readonly", description="Access mode: 'readonly' or 'readwrite'", pattern=r"^(readonly|readwrite)$"
    )


class EnabledCollectionConfig(BaseModel):
    """Configuration for an enabled collection."""

    collection: str = Field(..., description="Collection identifier in format 'namespace/name'")
    access: str = Field(
        default="readonly", description="Access mode: 'readonly' or 'readwrite'", pattern=r"^(readonly|readwrite)$"
    )


class SystemToolConfig(BaseModel):
    """Configuration for a system tool that requires parameters."""

    name: str = Field(..., description="System tool name (e.g. 'databaseIntrospection')")
    connections: Optional[list[str]] = Field(
        None, description="Allowed database connection names (for databaseIntrospection)"
    )

    # Extensible: add more optional config fields for future system tools here


class HookConfig(BaseModel):
    """Configuration for a single message hook."""

    function: str = Field(..., description="Function reference in format 'namespace/name'")
    async_: bool = Field(default=False, alias="async", description="If true, fire-and-forget")
    on_timeout: str = Field(
        default="passthrough", pattern=r"^(block|passthrough)$",
        description="Behavior on timeout: 'block' stops the pipeline, 'passthrough' continues"
    )

    model_config = {"populate_by_name": True}


class AgentHooks(BaseModel):
    """Message lifecycle hooks configuration."""

    on_user_message: list[HookConfig] = Field(default_factory=list)
    on_assistant_message: list[HookConfig] = Field(default_factory=list)


class AgentCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9 _\-():]*$")
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

    enabled_agents: Optional[list[str]] = None  # List of agent names that can be called as tools
    enabled_skills: Optional[
        list[EnabledSkillConfig]
    ] = None  # List of skill configs with preload option
    function_parameters: Optional[
        dict[str, Any]
    ] = None  # {"namespace/name": {"param": "value or {{template}}"}}
    status_templates: Optional[
        dict[str, str]
    ] = None  # {"function:web/search": "Searching for {{query}}...", "agent:support/helper": "Asking support..."}

    enabled_queries: Optional[list[str]] = None  # List of "namespace/name" query references
    query_parameters: Optional[
        dict[str, Any]
    ] = None  # {"namespace/name": {"param": "value or {{template}}"}}

    enabled_stores: Optional[list[EnabledStoreConfig]] = None  # Store access configs
    enabled_collections: Optional[list[EnabledCollectionConfig]] = None  # Collection access configs
    enabled_components: Optional[list[str]] = None  # List of "namespace/name" component references
    enabled_connectors: Optional[list[dict[str, Any]]] = None  # [{"connector": "ns/name", "operations": [...], "parameters": {...}}]
    hooks: Optional[AgentHooks] = None
    icon: Optional[str] = None  # "collection:ns/coll/file" or "url:https://..."
    is_default: Optional[bool] = False

    # Long-running workflow settings
    default_job_timeout: Optional[int] = Field(None, gt=0, description="Default job timeout in seconds for chats with this agent")
    default_keep_alive: Optional[bool] = Field(False, description="Default keep_alive for chats with this agent")
    system_tools: Optional[list[Union[str, SystemToolConfig]]] = Field(
        default=None,
        description=(
            "Opt-in Sinas platform tools. Simple string for tools with no config, "
            "or {name, ...config} for tools that need parameters. "
            "Supported: 'codeExecution', 'packageManagement', 'configIntrospection', "
            "'databaseIntrospection' (requires connections list)."
        ),
    )


class AgentUpdate(BaseModel):
    namespace: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9 _\-():]*$"
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

    enabled_agents: Optional[list[str]] = None  # List of agent names that can be called as tools
    enabled_skills: Optional[
        list[EnabledSkillConfig]
    ] = None  # List of skill configs with preload option
    function_parameters: Optional[
        dict[str, Any]
    ] = None  # {"namespace/name": {"param": "value or {{template}}"}}
    status_templates: Optional[
        dict[str, str]
    ] = None  # {"function:web/search": "Searching for {{query}}...", "agent:support/helper": "Asking support..."}

    enabled_queries: Optional[list[str]] = None  # List of "namespace/name" query references
    query_parameters: Optional[
        dict[str, Any]
    ] = None  # {"namespace/name": {"param": "value or {{template}}"}}

    enabled_stores: Optional[list[EnabledStoreConfig]] = None  # Store access configs
    enabled_collections: Optional[list[EnabledCollectionConfig]] = None  # Collection access configs
    enabled_components: Optional[list[str]] = None  # List of "namespace/name" component references
    enabled_connectors: Optional[list[dict[str, Any]]] = None  # [{"connector": "ns/name", "operations": [...], "parameters": {...}}]
    hooks: Optional[AgentHooks] = None
    icon: Optional[str] = None  # "collection:ns/coll/file" or "url:https://..."
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None

    # Long-running workflow settings
    default_job_timeout: Optional[int] = Field(None, gt=0)
    default_keep_alive: Optional[bool] = None
    system_tools: Optional[list[Union[str, SystemToolConfig]]] = None


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
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}
    initial_messages: Optional[list[dict[str, str]]]
    enabled_functions: list[str] = []
    enabled_agents: list[str] = []
    enabled_skills: list[EnabledSkillConfig] = []
    function_parameters: dict[str, Any] = {}
    status_templates: dict[str, str] = {}
    enabled_queries: list[str] = []
    query_parameters: dict[str, Any] = {}
    enabled_stores: list[EnabledStoreConfig] = []
    enabled_collections: list[EnabledCollectionConfig] = []
    enabled_components: list[str] = []
    enabled_connectors: list[dict[str, Any]] = []
    hooks: Optional[dict[str, Any]] = None
    icon: Optional[str] = None
    icon_url: Optional[str] = None
    is_active: bool
    is_default: bool
    default_job_timeout: Optional[int] = None
    default_keep_alive: bool = False
    system_tools: list[Union[str, SystemToolConfig]] = []
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

    @field_validator("enabled_stores", mode="before")
    @classmethod
    def convert_enabled_stores(cls, v):
        """Convert dicts from database to EnabledStoreConfig objects."""
        if not v:
            return []

        result = []
        for item in v:
            if isinstance(item, dict):
                result.append(EnabledStoreConfig(**item))
            elif isinstance(item, EnabledStoreConfig):
                result.append(item)
            else:
                result.append(EnabledStoreConfig(store=str(item), access="readonly"))
        return result

    @field_validator("enabled_collections", mode="before")
    @classmethod
    def convert_enabled_collections(cls, v):
        """Convert dicts/strings from database to EnabledCollectionConfig objects."""
        if not v:
            return []

        result = []
        for item in v:
            if isinstance(item, dict):
                result.append(EnabledCollectionConfig(**item))
            elif isinstance(item, EnabledCollectionConfig):
                result.append(item)
            else:
                # Backward compat: plain string = readonly
                result.append(EnabledCollectionConfig(collection=str(item), access="readonly"))
        return result

    @field_validator("system_tools", mode="before")
    @classmethod
    def convert_system_tools(cls, v):
        """Convert dicts from database to SystemToolConfig objects."""
        if not v:
            return []

        result = []
        for item in v:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                result.append(SystemToolConfig(**item))
            elif isinstance(item, SystemToolConfig):
                result.append(item)
        return result

    class Config:
        from_attributes = True
