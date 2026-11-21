import { configApi, runtimeApi } from '@sinas/ui';
import type {
  APIKey, CallFilters, CallPage, IdentityProvider, Manifest, ManifestStatus, ManifestUpdate, Resource, Role,
} from './types';
import { unavailableReason } from './tools';

const path = (namespace: string, name: string) =>
  `/manifests/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}`;

export async function listManifests(): Promise<Manifest[]> {
  // Switched-off ones too: the app is where they are switched back on.
  return (await configApi.get<Manifest[]>('/manifests', { params: { include_inactive: true } })).data;
}

export async function getManifest(namespace: string, name: string): Promise<Manifest> {
  return (await configApi.get<Manifest>(path(namespace, name))).data;
}

export async function createManifest(body: { namespace: string; name: string; description?: string }): Promise<Manifest> {
  return (await configApi.post<Manifest>('/manifests', { ...body, exposed_tools: [] })).data;
}

export async function updateManifest(namespace: string, name: string, body: ManifestUpdate): Promise<Manifest> {
  return (await configApi.put<Manifest>(path(namespace, name), body)).data;
}

export async function deleteManifest(namespace: string, name: string): Promise<void> {
  await configApi.delete(path(namespace, name));
}

export async function getManifestStatus(namespace: string, name: string): Promise<ManifestStatus> {
  return (await runtimeApi.get<ManifestStatus>(`${path(namespace, name)}/status`)).data;
}

interface RawResource {
  namespace: string;
  name: string;
  description?: string | null;
  tool_description?: string | null;
  is_active?: boolean;
  as_tool?: boolean;
  requires_approval?: boolean;
}

/** Every function, query and pipeline the signed-in user can see. */
export async function listResources(): Promise<Resource[]> {
  const quiet = { quietStatuses: [403] };
  const [functions, queries, pipelines] = await Promise.all([
    configApi.get<RawResource[]>('/functions', quiet).then((r) => r.data).catch(() => []),
    configApi.get<RawResource[]>('/queries', quiet).then((r) => r.data).catch(() => []),
    configApi.get<RawResource[]>('/pipelines', quiet).then((r) => r.data).catch(() => []),
  ]);
  const map = (type: Resource['type']) => (r: RawResource): Resource => ({
    type,
    namespace: r.namespace,
    name: r.name,
    description: (type === 'pipeline' ? r.tool_description || r.description : r.description) ?? null,
    unavailable: unavailableReason(type, r),
  });
  return [...queries.map(map('query')), ...functions.map(map('function')), ...pipelines.map(map('pipeline'))];
}

export async function listCalls(filters: CallFilters): Promise<CallPage> {
  return (await configApi.get<CallPage>('/switchboard/calls', { params: filters })).data;
}

export async function listApiKeys(): Promise<APIKey[]> {
  return (await configApi.get<APIKey[]>('/api-keys')).data;
}

export async function createApiKey(body: { name: string; role_ids: string[]; expires_at?: string }): Promise<APIKey & { key: string }> {
  return (await configApi.post<APIKey & { key: string }>('/api-keys', { ...body, permissions: {} })).data;
}

export async function revokeApiKey(id: string): Promise<void> {
  await configApi.delete(`/api-keys/${id}`);
}

export async function listRoles(): Promise<Role[]> {
  return (await configApi.get<Role[]>('/roles', { quietStatuses: [403] })).data;
}

export async function listIdentityProviders(): Promise<IdentityProvider[]> {
  return (await configApi.get<IdentityProvider[]>('/identity-providers', { quietStatuses: [403] })).data;
}

export interface UserRef {
  id: string;
  email: string;
}

/** Everyone, for naming callers in the audit log. Admins only; others get []. */
export async function listUsers(): Promise<UserRef[]> {
  return configApi.get<UserRef[]>('/users', { params: { limit: 1000 }, quietStatuses: [403] })
    .then((r) => r.data)
    .catch(() => []);
}
