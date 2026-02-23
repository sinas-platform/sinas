"""
Pydantic schemas for declarative configuration
"""
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, validator


class ConfigMetadata(BaseModel):
    """Configuration metadata"""

    name: str
    description: Optional[str] = None
    labels: Optional[dict[str, str]] = Field(default_factory=dict)


class GroupPermissionConfig(BaseModel):
    """Group permission configuration"""

    key: str
    value: bool


class GroupConfig(BaseModel):
    """Group configuration"""

    name: str
    description: Optional[str] = None
    emailDomain: Optional[str] = None
    permissions: list[GroupPermissionConfig] = Field(default_factory=list)


class UserPermissionConfig(BaseModel):
    """User permission configuration"""

    key: str
    value: bool


class UserConfig(BaseModel):
    """User configuration"""

    email: str
    isActive: bool = True
    groups: list[str] = Field(default_factory=list)
    permissions: list[UserPermissionConfig] = Field(default_factory=list)


class LLMProviderConfig(BaseModel):
    """LLM provider configuration"""

    name: str
    type: str  # openai, ollama, anthropic, etc.
    apiKey: Optional[str] = None
    endpoint: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    isActive: bool = True


class FunctionConfig(BaseModel):
    """Function configuration"""

    name: str
    description: Optional[str] = None
    groupName: str
    code: str
    inputSchema: Optional[dict[str, Any]] = None
    outputSchema: Optional[dict[str, Any]] = None
    requirements: list[str] = Field(default_factory=list)
    enabledNamespaces: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SkillConfig(BaseModel):
    """Skill configuration"""

    namespace: str = "default"
    name: str
    description: str  # What this skill helps with (shown to LLM)
    content: str  # Markdown instructions (retrieved on demand)


class EnabledSkillConfigYaml(BaseModel):
    """Configuration for an enabled skill in agent config"""

    skill: str = Field(..., description="Skill identifier in format 'namespace/name'")
    preload: bool = Field(
        default=False, description="If true, inject into system prompt instead of exposing as tool"
    )


class AgentConfig(BaseModel):
    """Agent configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    groupName: str
    llmProviderName: Optional[str] = None  # NULL = use default provider
    model: Optional[str] = None  # NULL = use provider's default model
    temperature: float = 0.7
    maxTokens: Optional[int] = None
    systemPrompt: Optional[str] = None
    enabledFunctions: list[str] = Field(default_factory=list)  # List of "namespace/name" strings
    functionParameters: dict[str, Any] = Field(
        default_factory=dict
    )  # {"namespace/name": {"param": "value or {{template}}"}}
    enabledAgents: list[str] = Field(default_factory=list)  # Other agents this agent can call
    enabledSkills: list[Union[str, EnabledSkillConfigYaml]] = Field(
        default_factory=list
    )  # List of skill configs (string for backward compat, dict for preload)
    stateNamespacesReadonly: list[str] = Field(default_factory=list)  # Readonly state namespaces
    stateNamespacesReadwrite: list[str] = Field(default_factory=list)  # Read-write state namespaces
    enabledCollections: list[str] = Field(default_factory=list)  # List of "namespace/name" collection refs
    isDefault: bool = False


class WebhookConfig(BaseModel):
    """Webhook configuration"""

    path: str
    functionName: str
    httpMethod: str = "POST"
    description: Optional[str] = None
    requiresAuth: bool = True
    groupName: str
    defaultValues: dict[str, Any] = Field(default_factory=dict)


class ScheduleConfig(BaseModel):
    """Schedule configuration"""

    name: str
    scheduleType: str = "function"  # "function" or "agent"
    functionName: Optional[str] = None  # for function schedules
    agentName: Optional[str] = None  # for agent schedules (namespace/name)
    content: Optional[str] = None  # message content for agent schedules
    description: Optional[str] = None
    cronExpression: str
    timezone: str = "UTC"
    groupName: str
    inputData: dict[str, Any] = Field(default_factory=dict)
    isActive: bool = True

    @validator("isActive", always=True)
    def validate_target(cls, v, values):
        schedule_type = values.get("scheduleType", "function")
        if schedule_type == "function" and not values.get("functionName"):
            raise ValueError("functionName is required for function schedules")
        if schedule_type == "agent" and not values.get("agentName"):
            raise ValueError("agentName is required for agent schedules")
        if schedule_type == "agent" and not values.get("content"):
            raise ValueError("content is required for agent schedules")
        return v


class AppResourceRef(BaseModel):
    """Resource reference in app config"""

    type: str = Field(..., description="Resource type: agent, function, skill, collection")
    namespace: str = "default"
    name: str


class AppConfig(BaseModel):
    """App registration configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    requiredResources: list[AppResourceRef] = Field(default_factory=list)
    requiredPermissions: list[str] = Field(default_factory=list)
    optionalPermissions: list[str] = Field(default_factory=list)
    exposedNamespaces: dict[str, list[str]] = Field(default_factory=dict)


class CollectionConfig(BaseModel):
    """Collection configuration"""

    namespace: str = "default"
    name: str
    groupName: str
    metadataSchema: Optional[dict[str, Any]] = None
    contentFilterFunction: Optional[str] = None  # "namespace/name" format
    postUploadFunction: Optional[str] = None  # "namespace/name" format
    maxFileSizeMb: int = 100
    maxTotalSizeGb: int = 10
    allowSharedFiles: bool = True
    allowPrivateFiles: bool = True


class ConfigSpec(BaseModel):
    """Configuration specification"""

    groups: list[GroupConfig] = Field(default_factory=list)
    users: list[UserConfig] = Field(default_factory=list)
    llmProviders: list[LLMProviderConfig] = Field(default_factory=list)

    skills: list[SkillConfig] = Field(default_factory=list)
    functions: list[FunctionConfig] = Field(default_factory=list)
    collections: list[CollectionConfig] = Field(default_factory=list)
    apps: list[AppConfig] = Field(default_factory=list)
    agents: list[AgentConfig] = Field(default_factory=list)
    webhooks: list[WebhookConfig] = Field(default_factory=list)
    schedules: list[ScheduleConfig] = Field(default_factory=list)


class SinasConfig(BaseModel):
    """Root configuration schema"""

    apiVersion: str = Field(..., pattern=r"^sinas\.co/v\d+$")
    kind: str = Field(..., pattern=r"^SinasConfig$")
    metadata: ConfigMetadata
    spec: ConfigSpec

    @validator("apiVersion")
    def validate_api_version(cls, v):
        if v != "sinas.co/v1":
            raise ValueError("Only apiVersion 'sinas.co/v1' is currently supported")
        return v


# Response schemas
class ResourceChange(BaseModel):
    """A single resource change"""

    action: str  # create, update, delete, unchanged
    resourceType: str
    resourceName: str
    details: Optional[str] = None
    changes: Optional[dict[str, Any]] = None


class ConfigApplySummary(BaseModel):
    """Summary of config application"""

    created: dict[str, int] = Field(default_factory=dict)
    updated: dict[str, int] = Field(default_factory=dict)
    unchanged: dict[str, int] = Field(default_factory=dict)
    deleted: dict[str, int] = Field(default_factory=dict)


class ConfigApplyResponse(BaseModel):
    """Response from config apply"""

    success: bool
    summary: ConfigApplySummary
    changes: list[ResourceChange]
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConfigApplyRequest(BaseModel):
    """Request to apply config"""

    config: str  # YAML content
    dryRun: bool = False
    force: bool = False


class ConfigValidateRequest(BaseModel):
    """Request to validate config"""

    config: str  # YAML content


class ValidationError(BaseModel):
    """Validation error"""

    path: str
    message: str


class ConfigValidateResponse(BaseModel):
    """Response from config validation"""

    valid: bool
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)
