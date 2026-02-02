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
  api_key: string;
}

// Chats
export interface Chat {
  id: string;
  user_id: string;
  agent_id: string | null;
  agent_namespace: string | null;
  agent_name: string | null;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatCreate {
  title?: string;
  input?: Record<string, any>;
}

export interface ChatUpdate {
  title?: string;
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

export type UniversalContent = TextContent | ImageContent | AudioContent | FileContent;

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

// Assistants (Agents)
export interface Assistant {
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
  enabled_mcp_tools: string[];
  enabled_agents: string[];
  enabled_skills: EnabledSkillConfig[];
  function_parameters: Record<string, any>;
  mcp_tool_parameters: Record<string, any>;
  state_namespaces_readonly: string[] | null;
  state_namespaces_readwrite: string[] | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AssistantCreate {
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
  enabled_mcp_tools?: string[];
  enabled_agents?: string[];
  enabled_skills?: EnabledSkillConfig[];
  function_parameters?: Record<string, any>;
  mcp_tool_parameters?: Record<string, any>;
  state_namespaces_readonly?: string[];
  state_namespaces_readwrite?: string[];
}

export interface AssistantUpdate {
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
  enabled_mcp_tools?: string[];
  enabled_agents?: string[];
  enabled_skills?: EnabledSkillConfig[];
  function_parameters?: Record<string, any>;
  mcp_tool_parameters?: Record<string, any>;
  state_namespaces_readonly?: string[];
  state_namespaces_readwrite?: string[];
  is_active?: boolean;
}

// Memories
export interface Memory {
  id: string;
  user_id: string;
  key: string;
  value: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryCreate {
  key: string;
  value: string;
}

export interface MemoryUpdate {
  value: string;
}

// MCP Servers
export interface MCPServer {
  id: string;
  name: string;
  url: string;
  protocol: string;
  is_active: boolean;
  last_connected: string | null;
  connection_status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface MCPServerCreate {
  name: string;
  url: string;
  protocol?: string;
  api_key?: string;
}

export interface MCPServerUpdate {
  url?: string;
  protocol?: string;
  api_key?: string;
  is_active?: boolean;
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
  role: string | null;
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
  requirements: string[];
  enabled_namespaces: string[];
  shared_pool: boolean;
  requires_approval: boolean;
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
  requirements?: string[];
  enabled_namespaces?: string[];
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
  requirements?: string[];
  enabled_namespaces?: string[];
  shared_pool?: boolean;
  requires_approval?: boolean;
  is_active?: boolean;
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
}

export interface WebhookUpdate {
  function_namespace?: string;
  function_name?: string;
  http_method?: string;
  description?: string;
  default_values?: Record<string, any>;
  is_active?: boolean;
  requires_auth?: boolean;
}

// Schedules
export interface Schedule {
  id: string;
  name: string;
  cron_expression: string;
  function_id: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
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
