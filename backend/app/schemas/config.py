"""
Pydantic schemas for declarative configuration
"""
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, validator
from datetime import datetime


class ConfigMetadata(BaseModel):
    """Configuration metadata"""
    name: str
    description: Optional[str] = None
    labels: Optional[Dict[str, str]] = Field(default_factory=dict)


class GroupPermissionConfig(BaseModel):
    """Group permission configuration"""
    key: str
    value: bool


class GroupConfig(BaseModel):
    """Group configuration"""
    name: str
    description: Optional[str] = None
    emailDomain: Optional[str] = None
    permissions: List[GroupPermissionConfig] = Field(default_factory=list)


class UserPermissionConfig(BaseModel):
    """User permission configuration"""
    key: str
    value: bool


class UserConfig(BaseModel):
    """User configuration"""
    email: str
    isActive: bool = True
    groups: List[str] = Field(default_factory=list)
    permissions: List[UserPermissionConfig] = Field(default_factory=list)


class LLMProviderConfig(BaseModel):
    """LLM provider configuration"""
    name: str
    type: str  # openai, ollama, anthropic, etc.
    apiKey: Optional[str] = None
    endpoint: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    isActive: bool = True


class FunctionConfig(BaseModel):
    """Function configuration"""
    name: str
    description: Optional[str] = None
    groupName: str
    code: str
    inputSchema: Optional[Dict[str, Any]] = None
    outputSchema: Optional[Dict[str, Any]] = None
    requirements: List[str] = Field(default_factory=list)
    enabledNamespaces: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class SkillConfig(BaseModel):
    """Skill configuration"""
    namespace: str = "default"
    name: str
    description: str  # What this skill helps with (shown to LLM)
    content: str  # Markdown instructions (retrieved on demand)


class EnabledSkillConfigYaml(BaseModel):
    """Configuration for an enabled skill in agent config"""
    skill: str = Field(..., description="Skill identifier in format 'namespace/name'")
    preload: bool = Field(default=False, description="If true, inject into system prompt instead of exposing as tool")


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
    enabledFunctions: List[str] = Field(default_factory=list)  # List of "namespace/name" strings
    functionParameters: Dict[str, Any] = Field(default_factory=dict)  # {"namespace/name": {"param": "value or {{template}}"}}
    enabledMcpTools: List[str] = Field(default_factory=list)
    enabledAgents: List[str] = Field(default_factory=list)  # Other agents this agent can call
    enabledSkills: List[Union[str, EnabledSkillConfigYaml]] = Field(default_factory=list)  # List of skill configs (string for backward compat, dict for preload)
    stateNamespacesReadonly: List[str] = Field(default_factory=list)  # Readonly state namespaces
    stateNamespacesReadwrite: List[str] = Field(default_factory=list)  # Read-write state namespaces


class WebhookConfig(BaseModel):
    """Webhook configuration"""
    path: str
    functionName: str
    httpMethod: str = "POST"
    description: Optional[str] = None
    requiresAuth: bool = True
    groupName: str
    defaultValues: Dict[str, Any] = Field(default_factory=dict)


class ScheduleConfig(BaseModel):
    """Schedule configuration"""
    name: str
    functionName: str
    description: Optional[str] = None
    cronExpression: str
    timezone: str = "UTC"
    groupName: str
    inputData: Dict[str, Any] = Field(default_factory=dict)
    isActive: bool = True


class MCPServerConfig(BaseModel):
    """MCP server configuration"""
    name: str
    url: str
    protocol: str  # websocket or http
    apiKey: Optional[str] = None
    isActive: bool = True
    groupName: str


class ConfigSpec(BaseModel):
    """Configuration specification"""
    groups: List[GroupConfig] = Field(default_factory=list)
    users: List[UserConfig] = Field(default_factory=list)
    llmProviders: List[LLMProviderConfig] = Field(default_factory=list)
    mcpServers: List[MCPServerConfig] = Field(default_factory=list)
    skills: List[SkillConfig] = Field(default_factory=list)
    functions: List[FunctionConfig] = Field(default_factory=list)
    agents: List[AgentConfig] = Field(default_factory=list)
    webhooks: List[WebhookConfig] = Field(default_factory=list)
    schedules: List[ScheduleConfig] = Field(default_factory=list)


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
    changes: Optional[Dict[str, Any]] = None


class ConfigApplySummary(BaseModel):
    """Summary of config application"""
    created: Dict[str, int] = Field(default_factory=dict)
    updated: Dict[str, int] = Field(default_factory=dict)
    unchanged: Dict[str, int] = Field(default_factory=dict)
    deleted: Dict[str, int] = Field(default_factory=dict)


class ConfigApplyResponse(BaseModel):
    """Response from config apply"""
    success: bool
    summary: ConfigApplySummary
    changes: List[ResourceChange]
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


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
    errors: List[ValidationError] = Field(default_factory=list)
    warnings: List[ValidationError] = Field(default_factory=list)
