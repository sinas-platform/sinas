// Shapes mirror the Sinas API (snake_case). Only fields Studio uses.

export interface InstanceInfo {
  auth_mode: 'otp' | 'password' | 'password+otp';
  version: string;
  features?: Record<string, unknown>;
}

export interface AuthUser {
  id: string;
  email: string;
  is_active: boolean;
}

export interface LoginResponse {
  access_token?: string;
  refresh_token?: string;
  user?: AuthUser;
  session_id?: string; // present when an OTP step follows
}

export interface ManifestResourceRef {
  type: string;
  namespace: string;
  name: string;
}

export interface Manifest {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  required_resources: ManifestResourceRef[];
  is_active: boolean;
  created_at: string;
}

export interface EnabledSkill { skill: string; preload: boolean }
export interface EnabledConnector { connector: string; operations: string[] }
export interface EnabledCollection { collection: string; access: 'readonly' | 'readwrite' }
export interface EnabledStore { store: string; access: 'readonly' | 'readwrite' }

export interface Agent {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  system_prompt: string | null;
  input_schema: Record<string, any>;
  enabled_functions: string[];
  enabled_agents: string[];
  enabled_skills: EnabledSkill[];
  enabled_queries: string[];
  enabled_connectors: EnabledConnector[];
  enabled_collections: EnabledCollection[];
  enabled_stores: EnabledStore[];
  icon_url: string | null;
  is_active: boolean;
}

export type AgentUpdate = Partial<
  Pick<
    Agent,
    | 'description'
    | 'system_prompt'
    | 'input_schema'
    | 'enabled_skills'
    | 'enabled_connectors'
    | 'enabled_collections'
    | 'enabled_stores'
  >
>;

export interface ConnectorOperation { name: string; description: string | null }
export interface Connector {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  base_url: string;
  auth: { type?: string; secret?: string } | null;
  operations: ConnectorOperation[];
}

export interface Skill {
  id: string;
  namespace: string;
  name: string;
  description: string;
  content: string;
}

export interface Collection {
  id: string;
  namespace: string;
  name: string;
  is_public?: boolean;
}

export interface Store {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
}

export interface Secret { name: string }

export interface Schedule {
  id: string;
  name: string;
  schedule_type: 'function' | 'agent';
  target_namespace: string;
  target_name: string;
  description: string | null;
  cron_expression: string;
  timezone: string;
  content: string | null;
  is_active: boolean;
  last_run: string | null;
  next_run: string | null;
}

export interface Webhook {
  id: string;
  path: string;
  function_namespace: string;
  function_name: string;
  http_method: string;
  description: string | null;
  is_active: boolean;
  requires_auth: boolean;
  default_values: Record<string, any> | null;
}

export interface InstalledPackage { id: string; name: string; version: string }

export interface SinasFunction {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  input_schema: Record<string, any>;
}

export interface Execution {
  execution_id: string;
  function_name: string;
  status: string;
  trigger_type: string | null;
  trigger_id: string | null;
  error: string | null;
  started_at: string | null;
  created_at?: string;
  duration_ms: number | null;
}

export interface Chat { id: string; title: string }

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string | Array<{ type: string; text?: string }> | null;
  name: string | null;
  tool_calls: any[] | null;
  created_at: string;
}

/** Extract plain text from a message content union. */
export function messageText(content: Message['content']): string {
  if (content == null) return '';
  if (typeof content === 'string') return content;
  return content.map((part) => (part.type === 'text' ? part.text || '' : '')).join('');
}
