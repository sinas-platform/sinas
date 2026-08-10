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


class UserIdentityConfig(BaseModel):
    """External identity linked to a user"""

    provider: str
    subject: str
    metadata: Optional[dict[str, Any]] = None


class UserConfig(BaseModel):
    """User configuration"""

    email: str
    isActive: bool = True
    roles: list[str] = Field(default_factory=list)
    permissions: list[UserPermissionConfig] = Field(default_factory=list)
    customFields: Optional[dict[str, Any]] = None
    identities: list[UserIdentityConfig] = Field(default_factory=list)


class LLMProviderConfig(BaseModel):
    """LLM provider configuration"""

    name: str
    type: str  # openai, azure, ollama, anthropic, etc.
    apiKey: Optional[str] = None
    endpoint: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    defaultModel: Optional[str] = None
    isDefault: bool = False
    # Passthrough for provider-specific config (merged into the DB `config`
    # column, not overwritten). For Azure: api_version, azure_deployment,
    # max_tokens_param, drop_params, extra_params.
    config: dict[str, Any] = Field(default_factory=dict)
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


class EnabledCollectionConfigYaml(BaseModel):
    """Configuration for an enabled collection in agent/component config"""

    collection: str = Field(..., description="Collection identifier in format 'namespace/name'")
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
    enabledCollections: list[Union[str, EnabledCollectionConfigYaml]] = Field(default_factory=list)
    enabledComponents: list[str] = Field(default_factory=list)  # List of "namespace/name" component refs
    enabledConnectors: list[dict[str, Any]] = Field(default_factory=list)  # [{"connector": "ns/name", "operations": [...]}]
    enabledPipelines: list[str] = Field(default_factory=list)  # List of "namespace/name" pipeline refs (asTool)
    inputSchema: Optional[dict[str, Any]] = None
    outputSchema: Optional[dict[str, Any]] = None
    initialMessages: Optional[list[dict[str, str]]] = None
    hooks: Optional[dict[str, Any]] = None  # {"onUserMessage": [...], "onAssistantMessage": [...]}
    icon: Optional[str] = None
    isDefault: bool = False
    defaultJobTimeout: Optional[int] = None
    defaultKeepAlive: bool = False
    systemTools: list[Any] = Field(
        default_factory=list,
        description=(
            "Opt-in Sinas platform tools. Simple string or {name, ...config}. "
            "Supported: 'codeExecution', 'packageManagement', 'configIntrospection', "
            "'databaseIntrospection' (requires connections list)."
        ),
    )


class WebhookDedupConfig(BaseModel):
    """Webhook deduplication configuration"""

    key: str
    ttlSeconds: int = 300


class WebhookConfig(BaseModel):
    """Webhook configuration"""

    path: str
    targetType: str = "function"  # "function", "agent", or "pipeline"
    functionName: Optional[str] = None  # for function targets ("namespace/name" or "name")
    agentName: Optional[str] = None  # for agent targets (namespace/name)
    pipelineName: Optional[str] = None  # for pipeline targets (namespace/name)
    messageTemplate: Optional[str] = None  # Jinja2, rendered against the request payload
    sessionKeyTemplate: Optional[str] = None  # Jinja2, optional conversation continuity key
    httpMethod: str = "POST"
    description: Optional[str] = None
    requiresAuth: bool = True
    defaultValues: dict[str, Any] = Field(default_factory=dict)
    responseMode: str = "sync"  # "sync", "async", or "raw" (raw: function targets only)
    dedup: Optional[WebhookDedupConfig] = None

    @validator("dedup", always=True)
    def validate_target(cls, v, values):
        target_type = values.get("targetType", "function")
        if target_type not in ("function", "agent", "pipeline"):
            raise ValueError("targetType must be 'function', 'agent', or 'pipeline'")
        if values.get("responseMode") not in ("sync", "async", "raw"):
            raise ValueError("responseMode must be 'sync', 'async', or 'raw'")
        if target_type == "function":
            if not values.get("functionName"):
                raise ValueError("functionName is required for function-target webhooks")
        elif target_type == "pipeline":
            if not values.get("pipelineName"):
                raise ValueError("pipelineName is required for pipeline-target webhooks")
            if values.get("responseMode") == "raw":
                raise ValueError("responseMode 'raw' is only supported for function-target webhooks")
        else:
            if not values.get("agentName"):
                raise ValueError("agentName is required for agent-target webhooks")
            if not values.get("messageTemplate"):
                raise ValueError("messageTemplate is required for agent-target webhooks")
            if values.get("responseMode") == "raw":
                raise ValueError("responseMode 'raw' is only supported for function-target webhooks")
        return v


class ScheduleConfig(BaseModel):
    """Schedule configuration"""

    name: str
    scheduleType: str = "function"  # "function", "agent", or "pipeline"
    functionName: Optional[str] = None  # for function schedules
    agentName: Optional[str] = None  # for agent schedules (namespace/name)
    pipelineName: Optional[str] = None  # for pipeline schedules (namespace/name)
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
        if schedule_type == "pipeline" and not values.get("pipelineName"):
            raise ValueError("pipelineName is required for pipeline schedules")
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
    targetType: str = "function"  # "function" or "pipeline"
    functionName: Optional[str] = None  # "namespace/name" format (function targets)
    pipelineName: Optional[str] = None  # "namespace/name" format (pipeline targets)
    pollColumn: str
    pollIntervalSeconds: int = 10
    batchSize: int = 100
    isActive: bool = True

    @validator("targetType")
    def validate_target_type(cls, v):
        if v not in ("function", "pipeline"):
            raise ValueError("targetType must be 'function' or 'pipeline'")
        return v

    @validator("isActive", always=True)
    def validate_target(cls, v, values):
        if values.get("targetType", "function") == "pipeline":
            if not values.get("pipelineName"):
                raise ValueError("pipelineName is required for pipeline-target triggers")
        elif not values.get("functionName"):
            raise ValueError("functionName is required for function-target triggers")
        return v

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


class TokenResponsePathsConfig(BaseModel):
    """Dot-paths into a nonstandard OAuth token response (issue #109)."""

    accessToken: Optional[str] = None
    refreshToken: Optional[str] = None
    expiresIn: Optional[str] = None
    scope: Optional[str] = None
    successFlag: Optional[str] = None
    error: Optional[str] = None
    errorDescription: Optional[str] = None


# camelCase config key ↔ snake_case stored key for the nested paths object.
# The outer CONNECTOR_AUTH_FIELD_MAP only renames top-level fields; this one
# handles the object's inner keys in both directions.
TOKEN_RESPONSE_PATH_FIELD_MAP: list[tuple[str, str]] = [
    ("accessToken", "access_token"),
    ("refreshToken", "refresh_token"),
    ("expiresIn", "expires_in"),
    ("scope", "scope"),
    ("successFlag", "success_flag"),
    ("error", "error"),
    ("errorDescription", "error_description"),
]


class ConnectorAuthConfig(BaseModel):
    """Connector auth configuration"""

    type: str = "none"
    secret: Optional[str] = None
    header: Optional[str] = None
    position: Optional[str] = None
    paramName: Optional[str] = None
    # OAuth 2.0 (client-credentials and authorization-code) fields.
    tokenUrl: Optional[str] = None
    clientId: Optional[str] = None
    scopes: Optional[list[str]] = None
    clientAuthMethod: Optional[str] = None
    authorizeUrl: Optional[str] = None
    tokenParams: Optional[dict[str, str]] = None
    tokenResponsePaths: Optional[TokenResponsePathsConfig] = None


# Single source of truth for connector auth field names across the config round-trip:
# (camelCase config key, snake_case stored-auth key). Used by config-apply (camel→snake)
# and the serializer (snake→camel) so a new auth field is added in exactly one place here
# plus the two schema models above/`ConnectorAuth`, not in four hand-kept lists.
CONNECTOR_AUTH_FIELD_MAP: list[tuple[str, str]] = [
    ("type", "type"),
    ("secret", "secret"),
    ("header", "header"),
    ("position", "position"),
    ("paramName", "param_name"),
    ("tokenUrl", "token_url"),
    ("clientId", "client_id"),
    ("scopes", "scopes"),
    ("clientAuthMethod", "client_auth_method"),
    ("authorizeUrl", "authorize_url"),
    ("tokenParams", "token_params"),
    ("tokenResponsePaths", "token_response_paths"),
]


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


class PipelineConfig(BaseModel):
    """Pipeline configuration (see ADR 2026-07-28-pipelines-triggers-and-linear-steps).

    `steps` and `perUser` are passed through verbatim — camelCase keys and `.$`
    mapping keys are data (one representation across YAML/API/DB); shape is
    validated by validate_pipeline_definition at apply time.
    """

    model_config = {"populate_by_name": True}

    namespace: str = "default"
    name: str
    description: Optional[str] = None
    inputSchema: Optional[dict[str, Any]] = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    perUser: Optional[dict[str, Any]] = None
    asTool: bool = False
    toolDescription: Optional[str] = None
    syncTimeoutSeconds: int = 120
    concurrency: Optional[str] = None  # "single" | "parallel" | None (auto)
    disableAfterFailures: Optional[int] = None
    output: Optional[Any] = None
    outputExpr: Optional[str] = Field(default=None, alias="output.$")
    isActive: bool = True

    def output_mapping(self) -> Optional[dict[str, Any]]:
        """Build the stored output-mapping dict from the YAML-level fields."""
        if self.outputExpr is not None:
            return {"output.$": self.outputExpr}
        if self.output is not None:
            return {"output": self.output}
        return None


class DependencyConfig(BaseModel):
    """Python dependency configuration"""

    packageName: str
    version: Optional[str] = None


class VariableConfig(BaseModel):
    """Install-time variable declaration for packages.

    Variables are resolved before resource persistence via ${{ vars.NAME }}
    substitution in the raw YAML. NOT Jinja2 — simple regex replace.
    """

    name: str = Field(..., pattern=r"^[A-Z][A-Z0-9_]*$", description="Variable name (UPPER_SNAKE_CASE)")
    type: str = Field(
        ...,
        description="Variable type: text, boolean, enum, resource_ref, secret",
    )
    description: Optional[str] = None
    default: Optional[Any] = None
    required: bool = True
    example: Optional[str] = None

    # Type-specific fields
    pattern: Optional[str] = Field(None, description="Regex pattern for 'text' type validation")
    choices: Optional[list[str]] = Field(None, description="Allowed values for 'enum' type")
    resource: Optional[str] = Field(
        None,
        description="Resource type for 'resource_ref': llm_providers, database_connections, roles, secrets, collections",
    )


class ConfigSpec(BaseModel):
    """Configuration specification"""

    model_config = {"extra": "forbid"}

    variables: list[VariableConfig] = Field(default_factory=list)
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
    pipelines: list[PipelineConfig] = Field(default_factory=list)
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
