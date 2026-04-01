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


class RolePermissionConfig(BaseModel):
    """Role permission configuration"""

    key: str
    value: bool


class RoleConfig(BaseModel):
    """Role configuration"""

    name: str
    description: Optional[str] = None
    emailDomain: Optional[str] = None
    permissions: list[RolePermissionConfig] = Field(default_factory=list)


class UserPermissionConfig(BaseModel):
    """User permission configuration"""

    key: str
    value: bool


class UserConfig(BaseModel):
    """User configuration"""

    email: str
    isActive: bool = True
    roles: list[str] = Field(default_factory=list)
    permissions: list[UserPermissionConfig] = Field(default_factory=list)


class LLMProviderConfig(BaseModel):
    """LLM provider configuration"""

    name: str
    type: str  # openai, ollama, anthropic, etc.
    apiKey: Optional[str] = None
    endpoint: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    isActive: bool = True


class DatabaseAnnotationConfig(BaseModel):
    """Table/column annotation for semantic layer"""

    schemaName: str = "public"
    tableName: str
    columnName: Optional[str] = None
    displayName: Optional[str] = None
    description: Optional[str] = None


class DatabaseConnectionConfig(BaseModel):
    """Database connection configuration"""

    name: str
    connectionType: str  # postgresql, clickhouse, snowflake
    host: str
    port: int
    database: str
    username: str
    password: Optional[str] = None  # Supports ${ENV_VAR}
    sslMode: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    annotations: list[DatabaseAnnotationConfig] = Field(default_factory=list)


class QueryConfig(BaseModel):
    """Query configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    connectionName: str  # Ref to DatabaseConnection by name
    operation: str  # "read" or "write"
    sql: str
    inputSchema: Optional[dict[str, Any]] = None
    outputSchema: Optional[dict[str, Any]] = None
    timeoutMs: int = 5000
    maxRows: int = 1000


class FunctionConfig(BaseModel):
    """Function configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    code: str
    inputSchema: Optional[dict[str, Any]] = None
    outputSchema: Optional[dict[str, Any]] = None
    icon: Optional[str] = None
    timeout: Optional[int] = None
    sharedPool: Optional[bool] = None
    requiresApproval: Optional[bool] = None


class SkillConfig(BaseModel):
    """Skill configuration"""

    namespace: str = "default"
    name: str
    description: str  # What this skill helps with (shown to LLM)
    content: str  # Markdown instructions (retrieved on demand)


class EnabledStoreConfigYaml(BaseModel):
    """Configuration for an enabled store in agent/component config"""

    store: str = Field(..., description="Store identifier in format 'namespace/name'")
    access: str = Field(default="readonly", description="Access mode: 'readonly' or 'readwrite'")


class ComponentConfig(BaseModel):
    """Component configuration"""

    namespace: str = "default"
    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    sourceCode: str
    inputSchema: Optional[dict[str, Any]] = None
    enabledAgents: list[str] = Field(default_factory=list)
    enabledFunctions: list[str] = Field(default_factory=list)
    enabledQueries: list[str] = Field(default_factory=list)
    enabledComponents: list[str] = Field(default_factory=list)
    enabledStores: list[Union[str, EnabledStoreConfigYaml]] = Field(default_factory=list)
    cssOverrides: Optional[str] = None
    visibility: str = "private"


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
    llmProviderName: Optional[str] = None  # NULL = use default provider
    model: Optional[str] = None  # NULL = use provider's default model
    temperature: float = 0.7
    maxTokens: Optional[int] = None
    systemPrompt: Optional[str] = None
    enabledFunctions: list[str] = Field(default_factory=list)  # List of "namespace/name" strings
    functionParameters: dict[str, Any] = Field(
        default_factory=dict
    )  # {"namespace/name": {"param": "value or {{template}}"}}
    statusTemplates: dict[str, str] = Field(
        default_factory=dict
    )  # {"function:web/search": "Searching for {{query}}...", "agent:support/helper": "Asking support..."}
    enabledAgents: list[str] = Field(default_factory=list)  # Other agents this agent can call
    enabledSkills: list[Union[str, EnabledSkillConfigYaml]] = Field(
        default_factory=list
    )  # List of skill configs (string for backward compat, dict for preload)
    enabledStores: list[Union[str, EnabledStoreConfigYaml]] = Field(default_factory=list)
    enabledQueries: list[str] = Field(default_factory=list)  # List of "namespace/name" query refs
    queryParameters: dict[str, Any] = Field(
        default_factory=dict
    )  # {"namespace/name": {"param": "value or {{template}}"}}
    enabledCollections: list[str] = Field(default_factory=list)  # List of "namespace/name" collection refs
    enabledComponents: list[str] = Field(default_factory=list)  # List of "namespace/name" component refs
    enabledConnectors: list[dict[str, Any]] = Field(default_factory=list)  # [{"connector": "ns/name", "operations": [...]}]
    inputSchema: Optional[dict[str, Any]] = None
    outputSchema: Optional[dict[str, Any]] = None
    initialMessages: Optional[list[dict[str, str]]] = None
    hooks: Optional[dict[str, Any]] = None  # {"onUserMessage": [...], "onAssistantMessage": [...]}
    icon: Optional[str] = None
    isDefault: bool = False
    defaultJobTimeout: Optional[int] = None
    defaultKeepAlive: bool = False
    enableCodeExecution: bool = False


class WebhookDedupConfig(BaseModel):
    """Webhook deduplication configuration"""

    key: str
    ttlSeconds: int = 300


class WebhookConfig(BaseModel):
    """Webhook configuration"""

    path: str
    functionName: str
    httpMethod: str = "POST"
    description: Optional[str] = None
    requiresAuth: bool = True
    defaultValues: dict[str, Any] = Field(default_factory=dict)
    responseMode: str = "sync"
    dedup: Optional[WebhookDedupConfig] = None


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


class ManifestResourceRef(BaseModel):
    """Resource reference in manifest config"""

    type: str = Field(..., description="Resource type: agent, function, skill, collection")
    namespace: str = "default"
    name: str


class ManifestConfig(BaseModel):
    """Manifest registration configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    requiredResources: list[ManifestResourceRef] = Field(default_factory=list)
    requiredPermissions: list[str] = Field(default_factory=list)
    optionalPermissions: list[str] = Field(default_factory=list)
    exposedNamespaces: dict[str, list[str]] = Field(default_factory=dict)
    storeDependencies: list[dict] = Field(default_factory=list)


class CollectionConfig(BaseModel):
    """Collection configuration"""

    namespace: str = "default"
    name: str
    metadataSchema: Optional[dict[str, Any]] = None
    contentFilterFunction: Optional[str] = None  # "namespace/name" format
    postUploadFunction: Optional[str] = None  # "namespace/name" format
    maxFileSizeMb: int = 100
    maxTotalSizeGb: int = 10
    isPublic: bool = False
    allowSharedFiles: bool = True
    allowPrivateFiles: bool = True


class StoreConfig(BaseModel):
    """Store configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    schema: Optional[dict[str, Any]] = None
    strict: bool = False
    defaultVisibility: str = "private"
    encrypted: bool = False


class TemplateConfig(BaseModel):
    """Template configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    title: Optional[str] = None
    htmlContent: str
    textContent: Optional[str] = None
    variableSchema: Optional[dict[str, Any]] = None


class DatabaseTriggerConfig(BaseModel):
    """Database trigger (CDC) configuration"""

    name: str
    connectionName: str  # Reference DatabaseConnection by name
    schemaName: str = "public"
    tableName: str
    operations: list[str] = Field(default=["INSERT", "UPDATE"])
    functionName: str  # "namespace/name" format
    pollColumn: str
    pollIntervalSeconds: int = 10
    batchSize: int = 100
    isActive: bool = True

    @validator("operations")
    def validate_operations(cls, v):
        valid = {"INSERT", "UPDATE"}
        invalid = set(v) - valid
        if invalid:
            raise ValueError(f"Invalid operations: {invalid}. Must be subset of {valid}")
        return v


class SecretConfig(BaseModel):
    """Secret configuration"""

    name: str
    value: Optional[str] = None  # Omit to skip value update on re-apply
    description: Optional[str] = None


class ConnectorOperationConfig(BaseModel):
    """Connector operation configuration"""

    name: str
    method: str
    path: str
    description: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    requestBodyMapping: str = "json"
    responseMapping: str = "json"


class ConnectorAuthConfig(BaseModel):
    """Connector auth configuration"""

    type: str = "none"
    secret: Optional[str] = None
    header: Optional[str] = None
    position: Optional[str] = None
    paramName: Optional[str] = None


class ConnectorRetryConfig(BaseModel):
    """Connector retry configuration"""

    maxAttempts: int = 1
    backoff: str = "none"


class ConnectorConfig(BaseModel):
    """Connector configuration"""

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    baseUrl: str
    auth: ConnectorAuthConfig = Field(default_factory=ConnectorAuthConfig)
    headers: dict[str, str] = Field(default_factory=dict)
    retry: ConnectorRetryConfig = Field(default_factory=ConnectorRetryConfig)
    timeoutSeconds: int = 30
    operations: list[ConnectorOperationConfig] = Field(default_factory=list)


class DependencyConfig(BaseModel):
    """Python dependency configuration"""

    packageName: str
    version: Optional[str] = None


class ConfigSpec(BaseModel):
    """Configuration specification"""

    roles: list[RoleConfig] = Field(default_factory=list)
    users: list[UserConfig] = Field(default_factory=list)
    llmProviders: list[LLMProviderConfig] = Field(default_factory=list)
    databaseConnections: list[DatabaseConnectionConfig] = Field(default_factory=list)
    dependencies: list[DependencyConfig] = Field(default_factory=list)
    secrets: list[SecretConfig] = Field(default_factory=list)
    connectors: list[ConnectorConfig] = Field(default_factory=list)

    skills: list[SkillConfig] = Field(default_factory=list)
    components: list[ComponentConfig] = Field(default_factory=list)
    functions: list[FunctionConfig] = Field(default_factory=list)
    queries: list[QueryConfig] = Field(default_factory=list)
    collections: list[CollectionConfig] = Field(default_factory=list)
    templates: list[TemplateConfig] = Field(default_factory=list)
    stores: list[StoreConfig] = Field(default_factory=list)
    manifests: list[ManifestConfig] = Field(default_factory=list)
    agents: list[AgentConfig] = Field(default_factory=list)
    webhooks: list[WebhookConfig] = Field(default_factory=list)
    schedules: list[ScheduleConfig] = Field(default_factory=list)
    databaseTriggers: list[DatabaseTriggerConfig] = Field(default_factory=list)


class PackageMetadataConfig(BaseModel):
    """Package metadata for SinasPackage kind"""

    name: str
    version: str = "1.0.0"
    description: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None


class SinasConfig(BaseModel):
    """Root configuration schema"""

    apiVersion: str = Field(..., pattern=r"^sinas\.co/v\d+$")
    kind: str = Field(..., pattern=r"^(SinasConfig|SinasPackage)$")
    metadata: Optional[ConfigMetadata] = None
    package: Optional[PackageMetadataConfig] = None
    spec: ConfigSpec

    @validator("apiVersion")
    def validate_api_version(cls, v):
        if v != "sinas.co/v1":
            raise ValueError("Only apiVersion 'sinas.co/v1' is currently supported")
        return v

    @validator("metadata", always=True)
    def validate_metadata(cls, v, values):
        kind = values.get("kind")
        if kind == "SinasConfig" and v is None:
            raise ValueError("'metadata' is required for SinasConfig kind")
        return v

    @validator("package", always=True)
    def validate_package(cls, v, values):
        kind = values.get("kind")
        if kind == "SinasPackage" and v is None:
            raise ValueError("'package' is required for SinasPackage kind")
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
