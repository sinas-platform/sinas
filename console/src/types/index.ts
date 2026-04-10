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

export interface LoginRequest {
  email: string;
}

export interface LoginResponse {
  message: string;
  session_id: string;
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

// API Keys
export interface APIKey {
  id: string;
  user_id: string;
  user_email?: string;  // Owner's email (only for admins)
  name: string;
  key_prefix: string;
  permissions: Record<string, boolean>;
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface APIKeyCreate {
  name: string;
  permissions: Record<string, boolean>;
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
  compile_status?: string;
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
}

export interface ToolApprovalResponse {
  status: string;  // "approved" | "rejected"
  tool_call_id: string;
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
  hooks: AgentHooks | null;
  status_templates: Record<string, string>;
  icon: string | null;
  icon_url: string | null;
  is_active: boolean;
  is_default: boolean;
  default_job_timeout: number | null;
  default_keep_alive: boolean;
  system_tools: string[];
  created_at: string;
  updated_at: string;
}

export interface AgentCreate {
  namespace?: string;
  name: string;
  description?: string;
  llm_provider_id?: string;
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
  hooks?: AgentHooks;
  icon?: string;
  is_default?: boolean;
  default_job_timeout?: number;
  default_keep_alive?: boolean;
  system_tools?: string[];
}

export interface AgentUpdate {
  namespace?: string;
  name?: string;
  description?: string;
  llm_provider_id?: string;
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
  hooks?: AgentHooks;
  status_templates?: Record<string, string>;
  icon?: string;
  is_active?: boolean;
  is_default?: boolean;
  default_job_timeout?: number;
  default_keep_alive?: boolean;
  system_tools?: string[];
}

// Roles & Users
export interface Role {
  id: string;
  name: string;
  description: string | null;
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
  function_namespace: string;
  function_name: string;
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
  function_namespace?: string;
  function_name: string;
  http_method?: string;
  description?: string;
  default_values?: Record<string, any>;
  requires_auth?: boolean;
  response_mode?: string;
  dedup?: { key: string; ttl_seconds: number } | null;
}

export interface WebhookUpdate {
  function_namespace?: string;
  function_name?: string;
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


// LLM Providers
export interface LLMProvider {
  id: string;
  name: string;
  provider_type: string;
  api_endpoint: string | null;
  default_model: string | null;
  config: Record<string, any>;
  is_default: boolean;
  is_active: boolean;
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
  compiled_bundle?: string;
  source_map?: string;
  compile_status: string;
  compile_errors?: Array<{ text: string; location?: { line: number; column: number } | null }>;
  input_schema?: Record<string, unknown>;
  enabled_agents: string[];
  enabled_functions: string[];
  enabled_queries: string[];
  enabled_components: string[];
  enabled_stores: EnabledStoreConfig[];
  css_overrides?: string;
  visibility: string;
  version: number;
  is_published: boolean;
  is_active: boolean;
  render_token?: string;
  created_at: string;
  updated_at: string;
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
  css_overrides?: string;
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
  css_overrides?: string;
  visibility?: string;
  is_active?: boolean;
  is_published?: boolean;
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
  function_namespace: string;
  function_name: string;
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
