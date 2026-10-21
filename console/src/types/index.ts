import type { AuthUser } from '@sinas/sdk';
import type { ToolApprovals } from '../lib/toolApprovals';

/**
 * Per-agent provider behaviour overrides. Absent key = inherit the provider's
 * setting. The backend validates keys and values (providers/factory.py
 * AGENT_OVERRIDABLE); anything else is rejected.
 */
export type EffortLevel = 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export interface ProviderOverrides {
  prompt_caching?: boolean;
  /** Anthropic `output_config.effort` — how much the model thinks and spends. */
  effort?: EffortLevel;
}

// Authentication
export interface User {
  id: string;
  email: string;
  is_active: boolean;
  external_auth_provider?: string | null;
  external_auth_id?: string | null;
  created_at: string;
}

export interface UserCreate {
  email: string;
}

export type AuthMode = 'otp' | 'password' | 'password+otp';

export interface InstanceInfo {
  auth_mode: AuthMode;
  version: string;
  features: Record<string, boolean>;
}

export interface LoginRequest {
  email: string;
  password?: string;
}

export interface LoginResponse {
  message: string;
  session_id?: string;
  // Returned only for password-only mode (no OTP step)
  access_token?: string;
  refresh_token?: string;
  token_type?: string;
  expires_in?: number;
  user?: User;
}

export interface OTPVerifyRequest {
  session_id: string;
  otp_code: string;
}

export interface OTPVerifyResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface ResetPasswordRequest {
  reset_token: string;
  new_password: string;
}

export interface AdminCreateResetLinkResponse {
  user_id: string;
  reset_token: string;
  expires_at: string;
}

/** POST /auth/setup-link/exchange: the link is spent; `setup_token` sets a
 * password until `expires_at`. */
export interface SetupLinkExchangeResponse {
  setup_token: string;
  email: string;
  has_password: boolean;
  expires_at: string;
}

/** POST /auth/setup-link/complete: tokens (password mode) or an OTP
 * session_id (password+otp). */
export interface SetupLinkCompleteResponse {
  message: string;
  session_id?: string;
  access_token?: string;
  refresh_token?: string;
  token_type?: string;
  expires_in?: number;
  user?: AuthUser;
}

// API Keys
export interface APIKeyRoleRef {
  id: string;
  name: string;
}

export interface APIKey {
  id: string;
  user_id: string;
  user_email?: string;  // Owner's email (only for admins)
  name: string;
  key_prefix: string;
  permissions: Record<string, boolean>;
  roles?: APIKeyRoleRef[];
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
  /** Attribution context the key's requests carry (copied from the creating session, or set by an admin). */
  context?: Record<string, string | number | boolean> | null;
}

export interface APIKeyCreate {
  name: string;
  permissions: Record<string, boolean>;
  role_ids?: string[];
  expires_at?: string;
}

export interface APIKeyCreatedResponse extends APIKey {
  key: string;
}

// Chats
export interface Chat {
  id: string;
  user_id: string;
  user_email: string;
  agent_id: string | null;
  agent_namespace: string | null;
  agent_name: string | null;
  title: string;
  archived: boolean;
  session_key: string | null;
  expires_at: string | null;
  keep_alive: boolean | null;
  active_channel_id: string | null;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  workbench_enabled?: boolean | null;
}

export interface WorkbenchFile {
  name: string;
  content_type: string;
  current_version: number;
  size_bytes: number | null;
  file_metadata: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface WorkbenchFileContent {
  name: string;
  content_base64: string;
  content_type: string;
  version: number;
  file_metadata: Record<string, any>;
}

export interface ChatCreate {
  title?: string;
  input?: Record<string, any>;
  job_timeout?: number;
  keep_alive?: boolean;
}

export interface ChatUpdate {
  title?: string;
  archived?: boolean;
}

// Multimodal content types
export type TextContent = {
  type: "text";
  text: string;
};

export type ImageContent = {
  type: "image";
  image: string;  // URL or data URL
  detail?: "low" | "high" | "auto";
};

export type AudioContent = {
  type: "audio";
  data: string;  // base64
  format: "wav" | "mp3" | "m4a" | "ogg";
};

export type FileContent = {
  type: "file";
  file_data?: string;  // base64
  file_url?: string;   // HTTPS URL
  file_id?: string;    // OpenAI file ID
  filename?: string;
  mime_type?: string;
};

export type ComponentContent = {
  type: "component";
  namespace: string;
  name: string;
  title?: string;
  input?: Record<string, unknown>;
  render_token?: string;
};

export type UniversalContent = TextContent | ImageContent | AudioContent | FileContent | ComponentContent;

export type MessageContent = string | UniversalContent[];

export interface Message {
  id: string;
  chat_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: MessageContent | null;
  tool_calls: any[] | null;
  tool_call_id: string | null;
  name: string | null;
  created_at: string;
}

export interface MessageSendRequest {
  content: MessageContent;
}

export interface ChatWithMessages extends Chat {
  messages: Message[];
}

// Tool Approvals
export interface ToolApprovalRequest {
  approved: boolean;
  /** Approve, and don't ask again for this tool in this chat (session grant). */
  always_allow?: boolean;
}

export interface ToolApprovalResponse {
  status: string;  // "approved" | "rejected"
  tool_call_id: string;
  channel_id?: string | null;
  /** "waiting" until the round's last asked call is decided, then "queued". */
  resume?: 'waiting' | 'queued';
  message?: string;
}

/** An ask_user question the conversation is suspended on. */
export interface PendingInputResponse {
  tool_call_id: string;
  question: string;
  options?: string[] | null;
  expires_at?: string | null;
  created_at?: string | null;
}

export interface PendingInputAnswerResponse {
  status: string;  // "answered"
  tool_call_id: string;
  /** The answer was the round's last outstanding completion: it resumes on channel_id. */
  resumed: boolean;
  /** The resume could not be queued yet; it is retried on channel_id. */
  resume_pending: boolean;
  channel_id: string | null;
  message?: string;
}

export interface ApprovalRequiredEvent {
  type: "approval_required";
  tool_call_id: string;
  function_namespace: string;
  function_name: string;
  arguments: Record<string, any>;
}

// Skills configuration for agents
export interface EnabledSkillConfig {
  skill: string;  // "namespace/name"
  preload: boolean;  // If true, inject into system prompt instead of exposing as tool
}

// Store access configuration for agents
export interface EnabledStoreConfig {
  store: string;  // "namespace/name"
  access: 'readonly' | 'readwrite';
}

// Collection access configuration for agents
export interface EnabledCollectionConfig {
  collection: string;  // "namespace/name"
  access: 'readonly' | 'readwrite';
}

// Function parameter configuration (supports both legacy and new format)
export type FunctionParameterValue =
  | string  // Legacy format: simple string value (treated as overridable)
  | {       // New format: object with value and locked flag
      value: string;
      locked: boolean;  // If true, hidden from LLM and cannot be overridden
    };

export type FunctionParameters = Record<string, Record<string, FunctionParameterValue>>;

// Agents
export interface Agent {
  id: string;
  user_id: string | null;
  namespace: string;
  name: string;
  description: string | null;
  llm_provider_id: string | null;
  provider_overrides?: ProviderOverrides | null;
  model: string | null;
  temperature: number;
  max_tokens: number | null;
  system_prompt: string | null;
  input_schema: Record<string, any>;
  output_schema: Record<string, any>;
  initial_messages: Array<{role: string; content: string}> | null;
  enabled_functions: string[];

  enabled_agents: string[];
  enabled_skills: EnabledSkillConfig[];
  function_parameters: FunctionParameters;
  enabled_queries: string[];
  query_parameters: FunctionParameters;
  enabled_stores: EnabledStoreConfig[];
  enabled_collections: EnabledCollectionConfig[];
  enabled_components: string[];
  enabled_connectors: EnabledConnectorConfig[];
  enabled_mcp_servers: EnabledMcpServerConfig[];
  enabled_pipelines: string[];
  hooks: AgentHooks | null;
  status_templates: Record<string, string>;
  icon: string | null;
  icon_url: string | null;
  is_active: boolean;
  is_default: boolean;
  default_job_timeout: number | null;
  default_keep_alive: boolean;
  /** Chat retention override in days: null = the deployment default, 0 = keep forever. */
  retention_days: number | null;
  system_tools: Array<string | { name: string; connections?: string[] }>;
  /** Permission mode: which tool calls stop for approval. null = unset (run everything). */
  tool_approvals?: ToolApprovals | null;
  created_at: string;
  updated_at: string;
}

export interface AgentCreate {
  namespace?: string;
  name: string;
  description?: string;
  llm_provider_id?: string;
  provider_overrides?: ProviderOverrides;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  system_prompt?: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  initial_messages?: Array<{role: string; content: string}>;
  enabled_functions?: string[];
  enabled_agents?: string[];
  enabled_skills?: EnabledSkillConfig[];
  function_parameters?: FunctionParameters;
  enabled_queries?: string[];
  query_parameters?: FunctionParameters;
  enabled_stores?: EnabledStoreConfig[];
  enabled_collections?: EnabledCollectionConfig[];
  enabled_connectors?: EnabledConnectorConfig[];
  enabled_mcp_servers?: EnabledMcpServerConfig[];
  enabled_pipelines?: string[];
  hooks?: AgentHooks;
  icon?: string;
  is_default?: boolean;
  default_job_timeout?: number;
  default_keep_alive?: boolean;
  /** null = the deployment default (on update: back to it), 0 = keep forever. */
  retention_days?: number | null;
  system_tools?: Array<string | { name: string; connections?: string[] }>;
}

export interface AgentUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  llm_provider_id?: string;
  provider_overrides?: ProviderOverrides;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  system_prompt?: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  initial_messages?: Array<{role: string; content: string}>;
  enabled_functions?: string[];
  enabled_agents?: string[];
  enabled_skills?: EnabledSkillConfig[];
  function_parameters?: FunctionParameters;
  enabled_queries?: string[];
  query_parameters?: FunctionParameters;
  enabled_stores?: EnabledStoreConfig[];
  enabled_collections?: EnabledCollectionConfig[];
  enabled_components?: string[];
  enabled_connectors?: EnabledConnectorConfig[];
  enabled_mcp_servers?: EnabledMcpServerConfig[];
  enabled_pipelines?: string[];
  hooks?: AgentHooks;
  status_templates?: Record<string, string>;
  icon?: string;
  is_active?: boolean;
  is_default?: boolean;
  default_job_timeout?: number;
  default_keep_alive?: boolean;
  /** null = the deployment default (on update: back to it), 0 = keep forever. */
  retention_days?: number | null;
  system_tools?: Array<string | { name: string; connections?: string[] }>;
  /** Left out = unchanged (the API ignores null). */
  tool_approvals?: ToolApprovals;
}

// Roles & Users
export interface Role {
  id: string;
  name: string;
  description: string | null;
  // "system" for roles the platform owns (e.g. the built-in Deployers).
  managed_by?: string | null;
  created_at: string;
}

export interface RoleCreate {
  name: string;
  description?: string;
}

export interface UserRole {
  id: string;
  role_id: string;
  user_id: string;
  user_email: string;
  active: boolean;
  added_at: string;
}

export interface RolePermission {
  id: string;
  role_id: string;
  permission_key: string;
  permission_value: boolean;
  created_at: string;
  updated_at: string;
}

export interface RolePermissionUpdate {
  permission_key: string;
  permission_value: boolean;
}

// Functions
export interface Function {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  code: string;
  input_schema: Record<string, any>;
  output_schema: Record<string, any>;
  icon: string | null;
  icon_url: string | null;
  shared_pool: boolean;
  requires_approval: boolean;
  timeout: number | null;
  max_retries: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface FunctionCreate {
  namespace?: string;
  name: string;
  description?: string;
  code: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  icon?: string;
  shared_pool?: boolean;
  requires_approval?: boolean;
  max_retries?: number;
}

export interface FunctionUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  code?: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  icon?: string;
  shared_pool?: boolean;
  requires_approval?: boolean;
  max_retries?: number;
  is_active?: boolean;
}

// Connectors
export interface ConnectorOperation {
  name: string;
  method: string;
  path: string;
  description: string | null;
  parameters: Record<string, any>;
  request_body_mapping: string;
  response_mapping: string;
}

export interface ConnectorAuth {
  type: string;
  secret?: string;
  header?: string;
  position?: string;
  param_name?: string;
}

export interface ConnectorRetry {
  max_attempts: number;
  backoff: string;
}

export interface Connector {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  base_url: string;
  auth: ConnectorAuth;
  headers: Record<string, string>;
  retry: ConnectorRetry;
  timeout_seconds: number;
  operations: ConnectorOperation[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConnectorCreate {
  namespace?: string;
  name: string;
  description?: string;
  base_url: string;
  auth?: ConnectorAuth;
  headers?: Record<string, string>;
  retry?: ConnectorRetry;
  timeout_seconds?: number;
  operations?: ConnectorOperation[];
}

export interface ConnectorUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  base_url?: string;
  auth?: ConnectorAuth;
  headers?: Record<string, string>;
  retry?: ConnectorRetry;
  timeout_seconds?: number;
  operations?: ConnectorOperation[];
  is_active?: boolean;
}

// Hook config for agents
export interface HookConfig {
  function: string;  // "namespace/name"
  async: boolean;
  on_timeout: 'block' | 'passthrough';
}

export interface AgentHooks {
  on_user_message: HookConfig[];
  on_assistant_message: HookConfig[];
}

// MCP servers
export interface McpServer {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  url: string;
  transport: 'streamable_http' | 'sse' | string;
  auth: { type: string; secret?: string; header?: string };
  headers: Record<string, string>;
  tool_allow: string[];
  tool_deny: string[];
  timeout_seconds: number;
  connect_timeout_seconds: number;
  is_active: boolean;
  managed_by: string | null;
  created_at: string;
  updated_at: string;
}

// Enabled MCP server config for agents
export interface EnabledMcpServerConfig {
  server: string;  // "namespace/name"
  tools?: string[];  // glob patterns; empty = every tool the server allows
}

// Enabled connector config for agents
export interface EnabledConnectorConfig {
  connector: string;  // "namespace/name"
  operations: string[];
  parameters?: Record<string, Record<string, string>>;
}

// Webhooks
export interface Webhook {
  id: string;
  path: string;
  target_type: 'function' | 'agent' | 'pipeline';
  function_namespace: string;
  function_name: string | null;
  agent_namespace: string | null;
  agent_name: string | null;
  pipeline_namespace?: string | null;
  pipeline_name?: string | null;
  message_template: string | null;
  session_key_template: string | null;
  http_method: string;
  description: string | null;
  default_values: Record<string, any> | null;
  is_active: boolean;
  requires_auth: boolean;
  response_mode: string;
  dedup: { key: string; ttl_seconds: number } | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookCreate {
  path: string;
  target_type?: 'function' | 'agent' | 'pipeline';
  function_namespace?: string;
  function_name?: string;
  agent_namespace?: string;
  agent_name?: string;
  message_template?: string;
  session_key_template?: string;
  http_method?: string;
  description?: string;
  default_values?: Record<string, any>;
  requires_auth?: boolean;
  response_mode?: string;
  dedup?: { key: string; ttl_seconds: number } | null;
}

export interface WebhookUpdate {
  target_type?: 'function' | 'agent' | 'pipeline';
  function_namespace?: string;
  function_name?: string;
  agent_namespace?: string;
  agent_name?: string;
  message_template?: string;
  session_key_template?: string;
  http_method?: string;
  description?: string;
  default_values?: Record<string, any>;
  is_active?: boolean;
  requires_auth?: boolean;
  response_mode?: string;
  dedup?: { key: string; ttl_seconds: number } | null;
}

// Schedules
export interface Schedule {
  id: string;
  name: string;
  schedule_type: string;
  target_namespace: string;
  target_name: string;
  description: string | null;
  cron_expression: string;
  timezone: string;
  input_data: Record<string, any>;
  content: string | null;
  is_active: boolean;
  last_run: string | null;
  next_run: string | null;
  created_at: string;
}

export interface ScheduleCreate {
  name: string;
  cron_expression: string;
  function_id: string;
  is_active?: boolean;
}

export interface ScheduleUpdate {
  name?: string;
  cron_expression?: string;
  function_id?: string;
  is_active?: boolean;
}

// Executions
export interface Execution {
  id: string;
  function_id: string;
  status: string;
  result: any;
  error: string | null;
  started_at: string;
  completed_at: string | null;
  created_at: string;
}

// Packages
export interface Package {
  id: string;
  package_name: string;
  version: string;
  installed_at: string;
  installed_by?: string;
}

export interface PackageInstall {
  package_name: string;
  version?: string;
}

// Installed integration package (GET /packages, GET /packages/{name})
export interface InstalledPackage {
  id: string;
  name: string;                       // install name
  package_name?: string | null;       // declared name; differs from name for a named instance
  version: string;
  description?: string | null;
  author?: string | null;
  installed_at: string;
  source_repository?: string | null;  // the repository it follows for updates
  latest_version?: string | null;
  update_available: boolean;
  /** What it requires (package.requires), each with its status now. */
  requires?: PackageRequirementStatus[];
  /** Install names of the installed packages that require it. */
  required_by?: string[];
  /** Installed by a dependency plan rather than asked for directly. */
  installed_as_dependency?: boolean;
  /** Installed as a dependency, and nothing requires it any more. */
  orphaned?: boolean;
}

/** A package another package requires (package.requires). */
export interface PackageRequirement {
  name: string;
  /** A version range, pyproject style: ">=1.2,<2". Absent: any version. */
  version?: string | null;
  repository?: string | null;
  /** The install of a multi-instance package; absent: the package's own name. */
  instance?: string | null;
}

export interface PackageRequirementStatus extends PackageRequirement {
  install_name: string;
  status: 'satisfied' | 'missing' | 'incompatible';
  /** Why it's incompatible: out of range, or the install name holds another package. */
  reason?: 'version' | 'other_package' | null;
  installed_version?: string | null;
  /** The declared name of what is installed under install_name. */
  installed_package?: string | null;
}

/** One entry of a preview's dependency plan, dependencies first. */
export interface DependencyPlanEntry {
  install_name: string;
  package_name: string;
  status: 'satisfied' | 'install' | 'missing' | 'incompatible' | 'cycle' | 'invalid';
  /** Installed version (satisfied) or the version that will be installed. */
  version?: string | null;
  installed_version?: string | null;
  /** Where an 'install' entry comes from. */
  repository?: string | null;
  constraints: { required_by: string; version?: string | null }[];
  required_by: string[];
  requires: string[];
  /** An 'install' entry whose own requirements wait for its variables:
   * fill them in and preview again to resolve what it requires. */
  needs_input?: boolean;
  message?: string | null;
  /** For 'install' entries: its own variables and dry run. */
  variables?: any[];
  requires_input?: boolean;
  preview?: { success: boolean; changes: ApplyChange[]; errors: string[]; warnings: string[] };
}

/** An installed package an upgrade would leave outside its stated range. */
export interface DependencyBreak {
  dependent: string;
  requires: string;
  version?: string | null;
}

/** Where an install or preview takes its package from: pasted YAML, or a
 * package repository. */
export type PackageSource =
  | { source: string }
  | { repository: string; package: string };

export interface PackageInstallOptions {
  variables?: Record<string, any>;
  instance?: string;
  allowBroadRolePermissions?: boolean;
  /** Take over declared resources that were created or edited by hand
   * (skipped otherwise). Uninstalling the package then deletes them. */
  adoptUnmanaged?: boolean;
  /** Variable values for the required packages the install installs, by install name. */
  dependencyVariables?: Record<string, Record<string, any>>;
  /** Upgrade although installed packages requiring it don't accept the new version. */
  allowBreakingDependents?: boolean;
}

/** One resource in a config apply or package install/preview result. */
export interface ApplyChange {
  action: 'create' | 'update' | 'unchanged' | 'skipped' | 'delete';
  resourceType: string;
  resourceName: string;
  details?: string | null;
  changes?: Record<string, { from: unknown; to: unknown }> | null;
  /** 'edited_by_hand': a package skip that adoptUnmanaged would take over;
   * 'adopted': the update that takes it over. */
  reason?: 'edited_by_hand' | 'adopted' | null;
}

// Package repositories
export type PackageRepositoryAuthType = 'none' | 'bearer' | 'header';

export interface PackageRepositoryAuth {
  type: PackageRepositoryAuthType;
  secret?: string | null;   // name of a shared Secret
  header?: string | null;
}

export interface PackageRepository {
  id: string;
  name: string;
  url: string;
  description: string | null;
  auth: PackageRepositoryAuth;
  is_active: boolean;
  managed_by: string | null;
  last_refreshed_at: string | null;
  last_error: string | null;
  package_count: number;
  created_at: string;
  updated_at: string | null;
}

export interface PackageRepositoryCreate {
  name: string;
  url: string;
  description?: string;
  auth?: PackageRepositoryAuth;
  is_active?: boolean;
}

/** Repositories can't be renamed: PUT refuses `name`. */
export interface PackageRepositoryUpdate {
  url?: string;
  description?: string;
  auth?: PackageRepositoryAuth;
  is_active?: boolean;
}

// Identity providers (SSO via OpenID Connect)

/** One rule: exactly one matcher (group or email_domain), the roles it grants. */
export interface RoleMapping {
  group?: string | null;
  email_domain?: string | null;
  roles: string[];
}

/** Copies a claim (a dotted path) into a user custom field at every sign-in. */
export interface ClaimMapping {
  claim: string;
  field: string;
  /** 'session': into the login's session context instead of a custom field. */
  target?: 'user' | 'session' | null;
}

export interface IdentityProvider {
  id: string;
  name: string;
  display_name: string;
  issuer: string;
  client_id: string;
  client_secret: string | null;  // name of a shared Secret
  scopes: string[];
  email_claim: string;
  groups_claim: string;
  role_mappings: RoleMapping[];
  claim_mappings?: ClaimMapping[];
  allowed_domains: string[];
  auto_provision: boolean;
  default_role: string | null;
  enforce_for_domains: string[];
  session_hours: number;
  is_active: boolean;
  managed_by: string | null;
  redirect_uri: string;  // to register at the IdP
  created_at: string;
  updated_at: string | null;
}

export interface IdentityProviderCreate {
  name: string;
  display_name: string;
  issuer: string;
  client_id: string;
  client_secret?: string | null;
  scopes?: string[];
  email_claim?: string;
  groups_claim?: string;
  role_mappings?: RoleMapping[];
  claim_mappings?: ClaimMapping[];
  allowed_domains?: string[];
  auto_provision?: boolean;
  default_role?: string | null;
  enforce_for_domains?: string[];
  session_hours?: number;
  is_active?: boolean;
}

/** Providers can't be renamed: the name is in the redirect URI. "" clears
 * client_secret or default_role. */
export type IdentityProviderUpdate = Partial<Omit<IdentityProviderCreate, 'name'>>;

/** What the login page sees of a provider. */
export interface SsoProvider {
  name: string;
  display_name: string;
}

export interface SsoExchangeResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
  return_to: string;  // a console path to continue on
}

export interface PackageCatalogInstall {
  name: string;  // install name (differs from the package name for a named instance)
  version: string;
  update_available: boolean;
}

export interface PackageCatalogEntry {
  repository: string;
  name: string;
  version: string;
  description?: string | null;
  author?: string | null;
  readme_url?: string | null;
  /** The index says it may be installed more than once (package.multiInstance). */
  multi_instance?: boolean;
  /** The packages it requires, from the repository index, each with its status now. */
  requires?: PackageRequirementStatus[];
  installed: PackageCatalogInstall[];
  update_available: boolean;
}


// LLM Providers
// Per-token USD prices, in LiteLLM's field names
export interface ModelPricing {
  input_cost_per_token?: number;
  output_cost_per_token?: number;
  cache_read_input_token_cost?: number;
  cache_creation_input_token_cost?: number;
  input_cost_per_token_batches?: number;
  output_cost_per_token_batches?: number;
}

export interface LLMProvider {
  id: string;
  name: string;
  provider_type: string;
  api_endpoint: string | null;
  default_model: string | null;
  config: Record<string, any>;
  is_default: boolean;
  is_active: boolean;
  pricing: Record<string, ModelPricing>;
  model_aliases: Record<string, string>;
  discount: number | null;
  // The LiteLLM provider family models are priced as; null: the provider type's
  pricing_provider: string | null;
  created_at: string;
  updated_at: string;
}

export interface LLMProviderCreate {
  name: string;
  provider_type: string;
  api_key?: string;
  api_endpoint?: string;
  default_model?: string;
  config?: Record<string, any>;
  is_default?: boolean;
  is_active?: boolean;
  pricing?: Record<string, ModelPricing>;
  model_aliases?: Record<string, string>;
  discount?: number | null;
  pricing_provider?: string | null;
}

export interface LLMProviderUpdate {
  name?: string;
  provider_type?: string;
  api_key?: string;
  api_endpoint?: string;
  default_model?: string;
  config?: Record<string, any>;
  is_default?: boolean;
  is_active?: boolean;
  pricing?: Record<string, ModelPricing>;
  model_aliases?: Record<string, string>;
  // null clears these
  discount?: number | null;
  pricing_provider?: string | null;
}

// LLM usage and cost (/api/v1/usage)
export interface UsageMetrics {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number;
  unpriced_calls: number;
  errors: number;
}

export type UsageDimension =
  | 'agent' | 'provider' | 'model' | 'user' | 'source' | 'chat' | 'batch' | 'customer' | 'day' | 'hour';

export interface UsageFilters {
  from?: string;
  to?: string;
  agent?: string[];
  provider?: string[];
  model?: string[];
  user_id?: string[];
  source?: string[];
  chat_id?: string[];
  /** The USAGE_CUSTOMER_FIELD custom field of the user a call was for. */
  customer?: string[];
}

export interface UsageSummary {
  from: string | null;
  to: string;
  group_by: UsageDimension[];
  totals: UsageMetrics;
  groups: Array<UsageMetrics & { key: Record<string, string | boolean | null> }>;
  truncated: boolean;
}

export interface UsageTimeseries {
  from: string;
  to: string;
  interval: 'hour' | 'day';
  points: Array<UsageMetrics & { bucket: string }>;
}

// One LLM call (GET /usage/calls), as recorded
export interface UsageCall {
  id: string;
  created_at: string | null;
  user_id: string | null;
  user_email?: string | null;
  chat_id: string | null;
  message_id: string | null;
  agent: string | null;
  source: string | null;
  provider_name: string | null;
  provider_type: string | null;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number | null;
  price_source: string | null;
  batch: boolean | null;
  streamed: boolean | null;
  latency_ms: number | null;
  error: string | null;
  // Why generation stopped: stop | length | tool_calls | content_filter |
  // error | other (null for older calls), and the provider's own value.
  finish_reason?: string | null;
  finish_reason_raw?: string | null;
  customer?: string | null;
}

export interface UsageCallsPage {
  items: UsageCall[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface RatecardSource {
  source: 'json' | 'file' | 'url';
  configured: boolean;
  models: number;
  error: string | null;
  path?: string | null;
  url?: string | null;
  fetched_at?: string | null;
  checked_at?: string | null;
}

export interface RatecardModelInUse {
  provider: string | null;
  provider_type: string | null;
  model: string;
  calls: number;
  unpriced_calls: number;
  last_used: string | null;
  priced_as: string | null;
  matched_key: string | null;
  source: 'provider' | 'deployment' | null;
  origin: string | null;
  discount: number | null;
  price: ModelPricing | null;
}

export interface RatecardProvider {
  name: string;
  provider_type: string;
  is_active: boolean;
  pricing: Record<string, ModelPricing>;
  model_aliases: Record<string, string>;
  discount: number | null;
  pricing_provider: string | null;
  // The families its models are looked up under (pricing_provider, or the type's)
  priced_as_families: string[];
}

export interface Ratecard {
  sources: RatecardSource[];
  deployment_models: number;
  // The litellm_provider families of the deployment ratecard
  provider_families: Array<{ family: string; models: number }>;
  providers: RatecardProvider[];
  days: number;
  models_in_use: RatecardModelInUse[];
  unpriced_models: RatecardModelInUse[];
  deployment_entries: Array<{ model: string; origin: string; litellm_provider: string | null; price: ModelPricing }>;
}

// Templates
export interface Template {
  id: string;
  namespace: string;
  name: string;
  description?: string;
  title?: string;
  html_content: string;
  text_content?: string;
  variable_schema: Record<string, any>;
  is_active: boolean;
  user_id?: string;
  created_by?: string;
  updated_by?: string;
  created_at: string;
  updated_at: string;
  managed_by?: string;
  config_name?: string;
  config_checksum?: string;
}

export interface TemplateCreate {
  namespace?: string;
  name: string;
  description?: string;
  title?: string;
  html_content: string;
  text_content?: string;
  variable_schema?: Record<string, any>;
}

export interface TemplateUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  title?: string;
  html_content?: string;
  text_content?: string;
  variable_schema?: Record<string, any>;
  is_active?: boolean;
}

export interface TemplateRenderRequest {
  variables: Record<string, any>;
}

export interface TemplateRenderResponse {
  title?: string;
  html_content: string;
  text_content?: string;
}

// Skills
export interface Skill {
  id: string;
  namespace: string;
  name: string;
  description: string;
  content: string;
  user_id: string;
  is_active: boolean;
  created_at: string;
  updated_at?: string;
  managed_by?: string;
  config_name?: string;
  config_checksum?: string;
}

export interface SkillCreate {
  namespace?: string;
  name: string;
  description: string;
  content: string;
}

export interface SkillUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  content?: string;
  is_active?: boolean;
}

// Components
export interface Component {
  id: string;
  user_id?: string;
  namespace: string;
  name: string;
  title?: string;
  description?: string;
  source_code: string;
  input_schema?: Record<string, unknown>;
  enabled_agents: string[];
  enabled_functions: string[];
  enabled_queries: string[];
  enabled_components: string[];
  enabled_stores: EnabledStoreConfig[];
  visibility: string;
  is_active: boolean;
  render_token?: string;
  created_at: string;
  updated_at: string;
}

export type ComponentShareMode = 'snapshot' | 'viewer' | 'creator';

export interface ComponentShare {
  id: string;
  token: string;
  component_id: string;
  input_data?: Record<string, unknown> | null;
  expires_at?: string | null;
  max_views?: number | null;
  view_count: number;
  label?: string | null;
  mode: ComponentShareMode;
  allow_writes: boolean;
  created_at: string;
  share_url: string;
}

export interface ComponentShareCreate {
  mode: ComponentShareMode;
  allow_writes?: boolean;
  input_data?: Record<string, unknown>;
  expires_at?: string;
  max_views?: number;
  label?: string;
}

/** What the console gets to render a "viewer" share link for the signed-in user. */
export interface SharedComponent {
  namespace: string;
  name: string;
  title: string;
  input: Record<string, unknown>;
  render_token: string;
}

export interface ComponentCreate {
  namespace?: string;
  name: string;
  title?: string;
  description?: string;
  source_code: string;
  input_schema?: Record<string, unknown>;
  enabled_agents?: string[];
  enabled_functions?: string[];
  enabled_queries?: string[];
  enabled_components?: string[];
  enabled_stores?: EnabledStoreConfig[];
  visibility?: string;
}

export interface ComponentUpdate {
  namespace?: string;
  name?: string;
  title?: string;
  description?: string;
  source_code?: string;
  input_schema?: Record<string, unknown>;
  enabled_agents?: string[];
  enabled_functions?: string[];
  enabled_queries?: string[];
  enabled_components?: string[];
  enabled_stores?: EnabledStoreConfig[];
  visibility?: string;
  is_active?: boolean;
}

// Collections
export interface Collection {
  id: string;
  namespace: string;
  name: string;
  user_id: string;
  metadata_schema: Record<string, any>;
  content_filter_function: string | null;
  post_upload_function: string | null;
  max_file_size_mb: number;
  max_total_size_gb: number;
  is_public: boolean;
  allow_shared_files: boolean;
  allow_private_files: boolean;
  managed_by?: string | null;
  config_name?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CollectionCreate {
  namespace?: string;
  name: string;
  metadata_schema?: Record<string, any>;
  content_filter_function?: string;
  post_upload_function?: string;
  max_file_size_mb?: number;
  max_total_size_gb?: number;
  is_public?: boolean;
  allow_shared_files?: boolean;
  allow_private_files?: boolean;
}

export interface CollectionUpdate {
  metadata_schema?: Record<string, any>;
  content_filter_function?: string | null;
  post_upload_function?: string | null;
  max_file_size_mb?: number;
  max_total_size_gb?: number;
  is_public?: boolean;
  allow_shared_files?: boolean;
  allow_private_files?: boolean;
}

// Files
export interface FileVersion {
  id: string;
  file_id: string;
  version_number: number;
  size_bytes: number;
  hash_sha256: string;
  uploaded_by: string | null;
  actor_type: 'user' | 'agent';
  agent_id: string | null;
  created_at: string;
}

export interface FileInfo {
  id: string;
  namespace: string;
  name: string;
  user_id: string;
  content_type: string;
  current_version: number;
  file_metadata: Record<string, any>;
  visibility: string;
  created_at: string;
  updated_at: string;
}

export interface FileWithVersions extends FileInfo {
  versions: FileVersion[];
}

export interface FileUploadRequest {
  name: string;
  content_base64: string;
  content_type: string;
  visibility?: string;
  file_metadata?: Record<string, any>;
  /** What to do when the name is taken; the server defaults to 'rename'. */
  on_conflict?: 'rename' | 'return_existing' | 'new_version' | 'error';
}

export interface FileDownloadResponse {
  content_base64: string;
  content_type: string;
  file_metadata: Record<string, any>;
  version: number;
}

// Manifests
export interface ManifestResourceRef {
  type: string;
  namespace: string;
  name: string;
}

export interface ManifestStoreDependency {
  store: string;
  key?: string | null;
}

export interface ManifestStoreDependencyStatus {
  store: string;
  key?: string | null;
  exists: boolean;
}

export interface Manifest {
  id: string;
  user_id: string;
  namespace: string;
  name: string;
  description: string | null;
  required_resources: ManifestResourceRef[];
  required_permissions: string[];
  optional_permissions: string[];
  exposed_namespaces: Record<string, string[]>;
  store_dependencies: ManifestStoreDependency[];
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface ManifestCreate {
  namespace: string;
  name: string;
  description?: string;
  required_resources?: ManifestResourceRef[];
  required_permissions?: string[];
  optional_permissions?: string[];
  exposed_namespaces?: Record<string, string[]>;
  store_dependencies?: ManifestStoreDependency[];
}

export interface ManifestUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  required_resources?: ManifestResourceRef[];
  required_permissions?: string[];
  optional_permissions?: string[];
  exposed_namespaces?: Record<string, string[]>;
  store_dependencies?: ManifestStoreDependency[];
  is_active?: boolean;
}

export interface ManifestStatus {
  ready: boolean;
  resources: { satisfied: ManifestResourceRef[]; missing: ManifestResourceRef[] };
  permissions: {
    required: { granted: string[]; missing: string[] };
    optional: { granted: string[]; missing: string[] };
  };
  stores: { satisfied: ManifestStoreDependencyStatus[]; missing: ManifestStoreDependencyStatus[] };
}

// Database Connections
export interface DatabaseConnection {
  id: string;
  name: string;
  connection_type: string;
  host: string;
  port: number;
  database: string;
  username: string;
  ssl_mode: string | null;
  config: Record<string, any>;
  is_active: boolean;
  read_only: boolean;
  created_at: string;
  updated_at: string;
}

export interface DatabaseConnectionCreate {
  name: string;
  connection_type: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password?: string;
  ssl_mode?: string;
  config?: Record<string, any>;
  is_active?: boolean;
  read_only?: boolean;
}

export interface DatabaseConnectionUpdate {
  name?: string;
  connection_type?: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  password?: string;
  ssl_mode?: string;
  config?: Record<string, any>;
  is_active?: boolean;
  read_only?: boolean;
}

export interface DatabaseConnectionTestResponse {
  success: boolean;
  message: string;
  latency_ms?: number;
}

// Database Schema Browser
export interface SchemaInfo {
  schema_name: string;
}

export interface ColumnInfo {
  column_name: string;
  data_type: string;
  udt_name: string;
  is_nullable: string;
  column_default: string | null;
  character_maximum_length: number | null;
  numeric_precision: number | null;
  numeric_scale: number | null;
  ordinal_position: number;
  is_primary_key: boolean;
  display_name: string | null;
  description: string | null;
}

export interface ConstraintInfo {
  constraint_name: string;
  constraint_type: string;
  columns: string[];
  definition: string | null;
  ref_schema: string | null;
  ref_table: string | null;
  ref_columns: string[] | null;
}

export interface IndexInfo {
  index_name: string;
  definition: string;
}

export interface DbTableInfo {
  table_name: string;
  table_type: string;
  estimated_rows: number;
  size_bytes: number;
  display_name: string | null;
  description: string | null;
}

export interface DbTableDetail {
  table_name: string;
  schema_name: string;
  columns: ColumnInfo[];
  constraints: ConstraintInfo[];
  indexes: IndexInfo[];
  display_name: string | null;
  description: string | null;
}

export interface DbViewInfo {
  view_name: string;
  view_definition: string | null;
}

export interface BrowseRowsResponse {
  rows: Record<string, any>[];
  total_count: number;
}

export interface FilterCondition {
  column: string;
  operator: string;
  value?: any;
}

export interface ColumnDefinition {
  name: string;
  type: string;
  nullable?: boolean;
  default?: string;
  primary_key?: boolean;
}

export interface CreateTableRequest {
  table_name: string;
  schema_name?: string;
  columns: ColumnDefinition[];
  if_not_exists?: boolean;
}

export interface AlterTableRequest {
  schema_name?: string;
  add_columns?: ColumnDefinition[];
  drop_columns?: string[];
  rename_columns?: Record<string, string>;
}

export interface CreateViewRequest {
  name: string;
  schema_name?: string;
  sql: string;
  or_replace?: boolean;
}

export interface AnnotationItem {
  schema_name: string;
  table_name: string;
  column_name?: string | null;
  display_name?: string | null;
  description?: string | null;
}

// Queries
export interface Query {
  id: string;
  user_id: string;
  namespace: string;
  name: string;
  description: string | null;
  database_connection_id: string;
  operation: string;
  sql: string;
  input_schema: Record<string, any>;
  output_schema: Record<string, any>;
  timeout_ms: number;
  max_rows: number;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface QueryCreate {
  namespace?: string;
  name: string;
  description?: string;
  database_connection_id: string;
  operation: string;
  sql: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  timeout_ms?: number;
  max_rows?: number;
}

export interface QueryUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  database_connection_id?: string;
  operation?: string;
  sql?: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  timeout_ms?: number;
  max_rows?: number;
  is_active?: boolean;
}

export interface QueryExecuteRequest {
  input: Record<string, any>;
}

export interface QueryExecuteResponse {
  success: boolean;
  operation: string;
  data?: Record<string, any>[];
  row_count?: number;
  affected_rows?: number;
  duration_ms: number;
}

// File Search
export interface FileSearchRequest {
  query?: string;
  metadata_filter?: Record<string, any>;
  limit?: number;
}

export interface DatabaseTrigger {
  id: string;
  name: string;
  database_connection_id: string;
  schema_name: string;
  table_name: string;
  operations: string[];
  target_type?: 'function' | 'pipeline';
  function_namespace: string;
  function_name: string;
  pipeline_namespace?: string | null;
  pipeline_name?: string | null;
  poll_column: string;
  poll_interval_seconds: number;
  batch_size: number;
  is_active: boolean;
  last_poll_value: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface DatabaseTriggerCreate {
  name: string;
  database_connection_id: string;
  schema_name?: string;
  table_name: string;
  operations: string[];
  function_namespace?: string;
  function_name: string;
  poll_column: string;
  poll_interval_seconds?: number;
  batch_size?: number;
}

export interface DatabaseTriggerUpdate {
  name?: string;
  operations?: string[];
  function_namespace?: string;
  function_name?: string;
  poll_column?: string;
  poll_interval_seconds?: number;
  batch_size?: number;
  is_active?: boolean;
}

export interface FileSearchMatch {
  line: number;
  text: string;
  context: string[];
}

export interface FileSearchResult {
  file_id: string;
  filename: string;
  version: number;
  matches: FileSearchMatch[];
}

// Pipelines
export interface PipelineStep {
  name: string;
  type: 'connector' | 'function' | 'agent' | 'query' | 'load';
  [key: string]: any; // per-type fields + `.$` mapping keys, edited as JSON
}

export interface Pipeline {
  id: string;
  user_id: string;
  namespace: string;
  name: string;
  description?: string | null;
  input_schema: Record<string, any>;
  steps: PipelineStep[];
  per_user?: { connector: string; disableAfterFailures?: number } | null;
  as_tool: boolean;
  tool_description?: string | null;
  sync_timeout_seconds: number;
  concurrency?: 'single' | 'parallel' | null;
  disable_after_failures?: number | null;
  output_mapping?: Record<string, any> | null;
  cursor_value?: string | null;
  error_message?: string | null;
  is_active: boolean;
  created_at: string;
  updated_at?: string | null;
}

export interface PipelineCreate {
  namespace: string;
  name: string;
  description?: string | null;
  input_schema?: Record<string, any> | null;
  steps: PipelineStep[];
  per_user?: { connector: string; disableAfterFailures?: number } | null;
  as_tool?: boolean;
  tool_description?: string | null;
  sync_timeout_seconds?: number;
  concurrency?: 'single' | 'parallel' | null;
  disable_after_failures?: number | null;
  output_mapping?: Record<string, any> | null;
}

export interface PipelineUpdate extends Partial<PipelineCreate> {
  is_active?: boolean;
}

export interface PipelineRunStepSummary {
  name: string;
  type: string;
  status: string;
  startedAt?: string;
  durationMs?: number;
  executionId?: string;
  chatId?: string;
  error?: string;
}

export interface PipelineRun {
  id: string;
  run_id: string;
  pipeline_id: string;
  user_id: string;
  trigger_type: string;
  trigger_id?: string | null;
  status: 'running' | 'succeeded' | 'failed' | 'timed_out' | string;
  input?: Record<string, any> | null;
  steps: PipelineRunStepSummary[];
  error?: string | null;
  cursor_before?: string | null;
  cursor_after?: string | null;
  started_at: string;
  completed_at?: string | null;
  duration_ms?: number | null;
}

export interface PipelineRunOutcome {
  run_id: string;
  status: string;
  output?: any;
  error?: string | null;
  steps?: PipelineRunStepSummary[];
  duration_ms?: number | null;
}

// Change history of configurable resources (GET /config/history)
export interface ConfigRevision {
  id: number;
  resource_kind: string;
  resource_key: string;
  resource_id: string | null;
  action: 'create' | 'update' | 'delete' | string;
  origin: 'api' | 'config' | 'package' | 'startup' | string;
  actor_user_id: string | null;
  actor_email: string | null;
  managed_by: string | null;
  config_name: string | null;
  restored_from_id: number | null;
  changed_fields: string[];
  // Only on the detail endpoint: {field: {from, to}} and the state after.
  changes?: Record<string, { from: unknown; to: unknown }> | null;
  spec?: Record<string, unknown> | null;
  created_at: string;
}

export interface ConfigRestoreResult {
  action: 'create' | 'update' | 'unchanged' | string;
  resource_kind: string;
  resource_key: string;
  resource_id: string | null;
  revision: ConfigRevision | null;
}

// Chat retention (GET /retention/status, POST /retention/run)

export interface RetentionPolicy {
  enabled: boolean;
  default_days: number | null;
  action: 'archive' | 'delete';
  schedule: string;
  batch_size: number;
  max_chats_per_run: number | null;
  max_run_seconds: number | null;
}

/** Where archives go. Never credentials: only whether static keys or the default chain are used. */
export interface RetentionDestination {
  configured: boolean;
  bucket?: string;
  prefix?: string;
  endpoint?: string;
  region?: string | null;
  sse_kms?: boolean;
  credentials?: string;
}

export interface RetentionRunCounts {
  candidates: number;
  archived: number;
  deleted: number;
  messages_deleted: number;
  bytes_archived: number;
  skipped: Record<string, number>;
  failed: Record<string, number>;
  more_remaining: boolean;
}

export interface RetentionRun {
  id: string;
  trigger: 'schedule' | 'api' | 'cli';
  status: 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'refused' | 'interrupted';
  actor_email: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  counts: RetentionRunCounts | null;
  per_agent: Record<string, { archived: number; deleted: number; messages_deleted: number }> | null;
  error: string | null;
}

export interface RetentionStatus {
  policy: RetentionPolicy;
  destination: RetentionDestination;
  agent_overrides: Array<{ agent: string; retention_days: number }>;
  schedule_error: string | null;
  next_run: string | null;
  refusal: string | null;
  in_progress: RetentionRun | null;
  last_run: RetentionRun | null;
}

export interface RetentionPreviewAgent {
  agent: string;
  retention_days: number;
  source: 'agent' | 'global';
  chats: number;
  messages: number;
  blocked: Record<string, number>;
}

export interface RetentionPreview {
  dry_run: true;
  as_of: string;
  policy: RetentionPolicy;
  destination: RetentionDestination;
  would_archive: boolean;
  refusal: string | null;
  agents: RetentionPreviewAgent[];
  totals: { chats: number; messages: number; blocked: Record<string, number> };
  notes: string[];
}
