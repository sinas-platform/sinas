// Thin fetch client for the Sinas API. Management endpoints live under
// /api/v1, runtime endpoints (auth, chats, executions) at the root.
// 401s trigger one refresh-and-retry; a failed refresh disconnects.
import { clearConnection, getConnection, updateTokens } from './connection';
import type {
  Agent,
  AgentUpdate,
  AuthUser,
  Chat,
  Collection,
  Connector,
  Execution,
  InstalledPackage,
  SinasFunction,
  InstanceInfo,
  LoginResponse,
  Manifest,
  ManifestResourceRef,
  Message,
  Schedule,
  Secret,
  Skill,
  Store,
  Webhook,
} from './types';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === 'string') return data.detail;
    if (data?.detail) return JSON.stringify(data.detail);
  } catch {
    /* non-JSON body */
  }
  return `Request failed (${res.status})`;
}

let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const conn = getConnection();
  if (!conn) return false;
  refreshing ??= (async () => {
    try {
      const res = await fetch(`${conn.baseUrl}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: conn.refreshToken }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      updateTokens(data.access_token, data.refresh_token ?? conn.refreshToken);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function request<T>(
  path: string,
  opts: { method?: string; body?: unknown; retried?: boolean } = {},
): Promise<T> {
  const conn = getConnection();
  if (!conn) throw new ApiError(401, 'Not connected to a workspace');

  const res = await fetch(`${conn.baseUrl}${path}`, {
    method: opts.method ?? 'GET',
    headers: {
      Authorization: `Bearer ${getConnection()!.accessToken}`,
      ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });

  if (res.status === 401 && !opts.retried) {
    if (await refreshTokens()) {
      return request<T>(path, { ...opts, retried: true });
    }
    clearConnection();
    window.location.assign(`${import.meta.env.BASE_URL}connect`);
    throw new ApiError(401, 'Session expired');
  }

  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---- Pre-connection (no stored tokens) ----

export async function fetchInstanceInfo(baseUrl: string): Promise<InstanceInfo> {
  const res = await fetch(`${baseUrl}/info`);
  if (!res.ok) throw new ApiError(res.status, 'Could not reach this workspace');
  return res.json();
}

export async function login(baseUrl: string, email: string, password?: string): Promise<LoginResponse> {
  const res = await fetch(`${baseUrl}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(password !== undefined ? { email, password } : { email }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

export async function verifyOtp(baseUrl: string, sessionId: string, otpCode: string): Promise<Required<Omit<LoginResponse, 'session_id'>>> {
  const res = await fetch(`${baseUrl}/auth/verify-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, otp_code: otpCode }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json();
}

// ---- Authenticated API ----

export const api = {
  me: () => request<AuthUser>('/auth/me'),

  // Projects (manifests)
  listProjects: () => request<Manifest[]>('/api/v1/manifests'),
  getProject: (ns: string, name: string) => request<Manifest>(`/api/v1/manifests/${ns}/${name}`),
  createProject: (data: { namespace: string; name: string; description?: string; required_resources: ManifestResourceRef[] }) =>
    request<Manifest>('/api/v1/manifests', { method: 'POST', body: data }),
  updateProject: (ns: string, name: string, data: { description?: string; required_resources?: ManifestResourceRef[] }) =>
    request<Manifest>(`/api/v1/manifests/${ns}/${name}`, { method: 'PUT', body: data }),
  deleteProject: (ns: string, name: string) =>
    request<void>(`/api/v1/manifests/${ns}/${name}`, { method: 'DELETE' }),

  // Assistants (agents)
  listAgents: () => request<Agent[]>('/api/v1/agents'),
  getAgent: (ns: string, name: string) => request<Agent>(`/api/v1/agents/${ns}/${name}`),
  createAgent: (data: { namespace: string; name: string; description?: string; system_prompt?: string }) =>
    request<Agent>('/api/v1/agents', { method: 'POST', body: data }),
  updateAgent: (ns: string, name: string, data: AgentUpdate) =>
    request<Agent>(`/api/v1/agents/${ns}/${name}`, { method: 'PUT', body: data }),

  // Capabilities
  listConnectors: () => request<Connector[]>('/api/v1/connectors'),
  listSkills: () => request<Skill[]>('/api/v1/skills'),
  createSkill: (data: { namespace: string; name: string; description: string; content: string }) =>
    request<Skill>('/api/v1/skills', { method: 'POST', body: data }),
  listCollections: () => request<Collection[]>('/api/v1/collections'),
  createCollection: (data: { namespace: string; name: string }) =>
    request<Collection>('/api/v1/collections', { method: 'POST', body: data }),
  listStores: () => request<Store[]>('/api/v1/stores'),
  createStore: (data: { namespace: string; name: string; description?: string }) =>
    request<Store>('/api/v1/stores', { method: 'POST', body: data }),
  listSecrets: () => request<Secret[]>('/api/v1/secrets'),

  // Workflows
  listSchedules: () => request<Schedule[]>('/api/v1/schedules'),
  createSchedule: (data: {
    name: string;
    schedule_type: 'function' | 'agent';
    target_namespace: string;
    target_name: string;
    description?: string;
    cron_expression: string;
    timezone?: string;
    content?: string;
  }) => request<Schedule>('/api/v1/schedules', { method: 'POST', body: data }),
  updateSchedule: (name: string, data: Partial<{ description: string; cron_expression: string; content: string; is_active: boolean; target_namespace: string; target_name: string }>) =>
    request<Schedule>(`/api/v1/schedules/${encodeURIComponent(name)}`, { method: 'PATCH', body: data }),
  listWebhooks: () => request<Webhook[]>('/api/v1/webhooks'),
  createWebhook: (data: {
    path: string;
    function_namespace: string;
    function_name: string;
    http_method?: string;
    description?: string;
    default_values?: Record<string, any>;
    requires_auth?: boolean;
  }) => request<Webhook>('/api/v1/webhooks', { method: 'POST', body: data }),
  updateWebhook: (path: string, data: Partial<{ description: string; default_values: Record<string, any>; is_active: boolean; requires_auth: boolean }>) =>
    request<Webhook>(`/api/v1/webhooks/${path}`, { method: 'PUT', body: data }),

  listFunctions: () => request<SinasFunction[]>('/api/v1/functions'),
  listExecutions: (params: { trigger_type?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.trigger_type) qs.set('trigger_type', params.trigger_type);
    qs.set('limit', String(params.limit ?? 50));
    return request<Execution[]>(`/executions?${qs}`);
  },

  // Companion package detection + in-app setup (see components/SetupStudio.tsx)
  listPackages: () => request<InstalledPackage[]>('/api/v1/packages'),
  installPackage: (yamlContent: string, variables: Record<string, string>) =>
    request<unknown>('/api/v1/packages/install', {
      method: 'POST',
      body: { source: yamlContent, variables },
    }),

  // One-shot agent invocation (used for studio/copilot AI assist)
  invokeAgent: (ns: string, name: string, data: { message: string; session_key?: string }) =>
    request<{ reply: string }>(`/agents/${ns}/${name}/invoke`, { method: 'POST', body: data }),

  // Test chat (runtime)
  createChat: (ns: string, name: string, data: { title?: string; input?: Record<string, unknown> }) =>
    request<Chat>(`/agents/${ns}/${name}/chats`, { method: 'POST', body: data }),
  sendMessage: (chatId: string, content: string) =>
    request<Message>(`/chats/${chatId}/messages`, { method: 'POST', body: { content } }),
  listMessages: (chatId: string) => request<Message[]>(`/chats/${chatId}/messages`),
};
