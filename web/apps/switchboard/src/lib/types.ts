export type ToolType = 'function' | 'query' | 'pipeline';

/** A resource a switchboard serves to agents as a tool. */
export interface ExposedTool {
  type: ToolType;
  namespace: string;
  name: string;
  tool_name?: string | null;
  description?: string | null;
}

export interface SwitchboardLimits {
  max_calls: number;
  window_seconds: number;
}

/** A switchboard is a manifest that exposes tools. */
export interface Manifest {
  id: string;
  namespace: string;
  name: string;
  description: string | null;
  exposed_tools: ExposedTool[];
  switchboard_limits: SwitchboardLimits | null;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface ManifestUpdate {
  description?: string | null;
  exposed_tools?: ExposedTool[];
  /** null clears the override (the deployment default applies). */
  switchboard_limits?: SwitchboardLimits | null;
  is_active?: boolean;
}

export interface ResourceStatus {
  type: string;
  namespace: string;
  name: string;
  exists: boolean;
}

export interface ManifestStatus {
  ready: boolean;
  resources: { satisfied: ResourceStatus[]; missing: ResourceStatus[] };
}

/** What the tool picker lists, from functions, queries and pipelines. */
export interface Resource {
  type: ToolType;
  namespace: string;
  name: string;
  description: string | null;
  /** Why the switchboard would not serve it, if it wouldn't. */
  unavailable: string | null;
}

export interface SwitchboardCall {
  id: string;
  manifest_id: string | null;
  manifest_namespace: string;
  manifest_name: string;
  user_id: string;
  api_key_id: string | null;
  tool_name: string;
  resource_type: string;
  resource_ref: string;
  arguments: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  is_error: boolean;
  duration_ms: number | null;
  created_at: string;
}

export interface CallPage {
  items: SwitchboardCall[];
  total: number;
  limit: number;
  offset: number;
}

export interface CallFilters {
  manifest?: string;
  tool?: string;
  is_error?: boolean;
  since?: string;
  limit?: number;
  offset?: number;
}

export interface Role {
  id: string;
  name: string;
  description: string | null;
}

export interface APIKey {
  id: string;
  user_id: string;
  user_email?: string | null;
  name: string;
  key_prefix: string;
  roles: { id: string; name: string }[];
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface IdentityProvider {
  id: string;
  name: string;
  display_name: string;
  issuer: string;
  role_mappings: { group?: string | null; email_domain?: string | null; roles: string[] }[];
  is_active?: boolean;
}
