import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, getApiErrorMessage } from '../lib/api';
import { useToast } from '../lib/toast-context';
import { Save, ArrowLeft, Trash2, Play, Check, X } from 'lucide-react';

const TRANSPORTS = [
  { value: 'streamable_http', label: 'Streamable HTTP (recommended)' },
  { value: 'sse', label: 'HTTP + SSE (legacy)' },
];
const AUTH_TYPES = [
  { value: 'none', label: 'No Auth' },
  { value: 'bearer', label: 'Bearer Token (Authorization header)' },
  { value: 'header', label: 'API Key in a custom header' },
];

// Keep only the auth fields that matter for the chosen type, so saving a
// server never writes stale fields from an earlier choice.
function pruneAuthForSave(auth: any): Record<string, any> {
  const type = auth?.type || 'none';
  const out: Record<string, any> = { type };
  if (type === 'none') return out;
  if (auth.secret) out.secret = auth.secret;
  if (type === 'header') out.header = auth.header || 'X-Api-Key';
  return out;
}

const splitPatterns = (text: string): string[] =>
  text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

export function McpServerEditor() {
  const { namespace, name } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = namespace === 'new' && name === 'new';
  const { showSuccess } = useToast();

  const [formData, setFormData] = useState({
    namespace: 'default', name: '', description: '', url: '',
    transport: 'streamable_http',
    auth: { type: 'none' as string, secret: '', header: 'X-Api-Key' },
    headers: {} as Record<string, string>,
    tool_allow: '' as string,
    tool_deny: '' as string,
    timeout_seconds: 60,
    connect_timeout_seconds: 10,
    is_active: true,
  });
  const [newHeaderKey, setNewHeaderKey] = useState('');
  const [newHeaderValue, setNewHeaderValue] = useState('');
  const [toolsResult, setToolsResult] = useState<any>(null);

  const { data: server, isLoading } = useQuery({
    queryKey: ['mcp-server', namespace, name],
    queryFn: () => apiClient.getMcpServer(namespace!, name!),
    enabled: !isNew,
    retry: false,
  });

  const { data: secrets } = useQuery({
    queryKey: ['secrets'],
    queryFn: () => apiClient.listSecrets(),
    retry: false,
  });

  useEffect(() => {
    if (server) {
      setFormData({
        namespace: server.namespace,
        name: server.name,
        description: server.description || '',
        url: server.url,
        transport: server.transport || 'streamable_http',
        auth: { type: 'none', secret: '', header: 'X-Api-Key', ...server.auth },
        headers: server.headers || {},
        tool_allow: (server.tool_allow || []).join('\n'),
        tool_deny: (server.tool_deny || []).join('\n'),
        timeout_seconds: server.timeout_seconds,
        connect_timeout_seconds: server.connect_timeout_seconds,
        is_active: server.is_active,
      });
    }
  }, [server]);

  const payload = () => ({
    ...formData,
    auth: pruneAuthForSave(formData.auth),
    tool_allow: splitPatterns(formData.tool_allow),
    tool_deny: splitPatterns(formData.tool_deny),
  });

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      if (isNew) return apiClient.createMcpServer(data);
      return apiClient.updateMcpServer(namespace!, name!, data);
    },
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] });
      if (isNew) {
        showSuccess('MCP server created');
        navigate(`/mcp-servers/${data.namespace}/${data.name}`, { replace: true });
      } else {
        showSuccess('MCP server saved');
        queryClient.invalidateQueries({ queryKey: ['mcp-server', namespace, name] });
      }
    },
  });

  const toolsMutation = useMutation({
    mutationFn: () => apiClient.listMcpServerTools(formData.namespace, formData.name),
    onSuccess: (data: any) => setToolsResult(data),
    onError: (err: any) => setToolsResult({ error: getApiErrorMessage(err, 'Could not reach the server') }),
  });

  const addHeader = () => {
    if (newHeaderKey.trim()) {
      setFormData({ ...formData, headers: { ...formData.headers, [newHeaderKey.trim()]: newHeaderValue } });
      setNewHeaderKey('');
      setNewHeaderValue('');
    }
  };

  const removeHeader = (key: string) => {
    const h = { ...formData.headers };
    delete h[key];
    setFormData({ ...formData, headers: h });
  };

  if (!isNew && isLoading) return <div className="text-gray-400">Loading...</div>;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/mcp-servers')} className="p-1.5 text-gray-500 hover:text-gray-300">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-gray-100">
              {isNew ? 'New MCP Server' : `${formData.namespace}/${formData.name}`}
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {!isNew && (
            <button
              onClick={() => { setToolsResult(null); toolsMutation.mutate(); }}
              disabled={toolsMutation.isPending}
              className="btn btn-secondary flex items-center"
              title="Connect to the saved server and list its tools"
            >
              <Play className="w-4 h-4 mr-2" />
              {toolsMutation.isPending ? 'Connecting...' : 'Test & List Tools'}
            </button>
          )}
          <button onClick={() => saveMutation.mutate(payload())} disabled={saveMutation.isPending} className="btn btn-primary flex items-center">
            <Save className="w-4 h-4 mr-2" />
            {saveMutation.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {saveMutation.isError && (
        <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg">
          <p className="text-sm text-red-400">
            {getApiErrorMessage(saveMutation.error, 'Failed to save')}
          </p>
        </div>
      )}

      {/* General */}
      <div className="card space-y-4">
        <h2 className="text-lg font-semibold text-gray-100">General</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Namespace</label>
            <input type="text" value={formData.namespace} onChange={e => setFormData({ ...formData, namespace: e.target.value })}
              className="input w-full" disabled={!isNew} />
          </div>
          <div>
            <label className="label">Name</label>
            <input type="text" value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })}
              className="input w-full" disabled={!isNew} />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-4">
          <div className="col-span-2">
            <label className="label">Endpoint URL</label>
            <input type="text" value={formData.url} onChange={e => setFormData({ ...formData, url: e.target.value })}
              placeholder="https://mcp.example.com/mcp" className="input w-full font-mono" />
          </div>
          <div>
            <label className="label">Transport</label>
            <select value={formData.transport} onChange={e => setFormData({ ...formData, transport: e.target.value })}
              className="input w-full">
              {TRANSPORTS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className="label">Description</label>
          <textarea value={formData.description} onChange={e => setFormData({ ...formData, description: e.target.value })}
            className="input w-full" rows={2} />
        </div>
        <div className="flex items-center gap-4">
          <div className="w-40">
            <label className="label">Call timeout (s)</label>
            <input type="number" value={formData.timeout_seconds}
              onChange={e => setFormData({ ...formData, timeout_seconds: parseInt(e.target.value) || 60 })}
              className="input w-full" min={1} max={600} />
          </div>
          <div className="w-40">
            <label className="label">Connect timeout (s)</label>
            <input type="number" value={formData.connect_timeout_seconds}
              onChange={e => setFormData({ ...formData, connect_timeout_seconds: parseInt(e.target.value) || 10 })}
              className="input w-full" min={1} max={120} />
          </div>
          <label className="flex items-center gap-2 mt-6">
            <input type="checkbox" checked={formData.is_active} onChange={e => setFormData({ ...formData, is_active: e.target.checked })}
              className="rounded border-gray-600 bg-gray-800 text-primary-600" />
            <span className="text-sm text-gray-300">Active</span>
          </label>
        </div>
      </div>

      {/* Auth */}
      <div className="card space-y-4">
        <h2 className="text-lg font-semibold text-gray-100">Authentication</h2>
        <p className="text-xs text-gray-500">
          The credential is a Secret, resolved and decrypted in the backend at call time. The sandbox never sees it.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Auth Type</label>
            <select value={formData.auth.type} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, type: e.target.value } })}
              className="input w-full">
              {AUTH_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
          {formData.auth.type !== 'none' && (
            <div>
              <label className="label">Secret</label>
              <select value={formData.auth.secret} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, secret: e.target.value } })}
                className="input w-full">
                <option value="">Select a secret...</option>
                {secrets?.map((s: any) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </div>
          )}
        </div>
        {formData.auth.type === 'header' && (
          <div className="w-1/2">
            <label className="label">Header Name</label>
            <input type="text" value={formData.auth.header} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, header: e.target.value } })}
              className="input w-full" placeholder="X-Api-Key" />
          </div>
        )}
      </div>

      {/* Headers */}
      <div className="card space-y-4">
        <h2 className="text-lg font-semibold text-gray-100">Static Headers</h2>
        {Object.entries(formData.headers).map(([key, value]) => (
          <div key={key} className="flex items-center gap-2">
            <span className="font-mono text-sm text-gray-300 w-48">{key}</span>
            <span className="font-mono text-sm text-gray-500 flex-1">{value}</span>
            <button onClick={() => removeHeader(key)} className="text-gray-500 hover:text-red-400">
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        ))}
        <div className="flex gap-2">
          <input type="text" value={newHeaderKey} onChange={e => setNewHeaderKey(e.target.value)}
            placeholder="Header name" className="input !w-48 shrink-0" />
          <input type="text" value={newHeaderValue} onChange={e => setNewHeaderValue(e.target.value)}
            placeholder="Value" className="input !flex-1" />
          <button onClick={addHeader} disabled={!newHeaderKey.trim()} className="btn btn-secondary">Add</button>
        </div>
      </div>

      {/* Tool filter */}
      <div className="card space-y-4">
        <h2 className="text-lg font-semibold text-gray-100">Tool Filter</h2>
        <p className="text-xs text-gray-500">
          Glob patterns on the server's tool names, one per line. An empty allow list exposes every tool; deny wins over allow.
          Agents can narrow this further per binding.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Allow</label>
            <textarea value={formData.tool_allow} onChange={e => setFormData({ ...formData, tool_allow: e.target.value })}
              className="input w-full font-mono" rows={4} placeholder={'search_*\nget_*'} />
          </div>
          <div>
            <label className="label">Deny</label>
            <textarea value={formData.tool_deny} onChange={e => setFormData({ ...formData, tool_deny: e.target.value })}
              className="input w-full font-mono" rows={4} placeholder={'delete_*'} />
          </div>
        </div>
      </div>

      {/* Live tools */}
      {toolsResult && (
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-100">Tools on the server</h2>
            {toolsResult.elapsed_ms !== undefined && (
              <span className="text-xs text-gray-500">{toolsResult.elapsed_ms} ms</span>
            )}
          </div>
          {toolsResult.error ? (
            <p className="text-sm text-red-400">{toolsResult.error}</p>
          ) : toolsResult.tools?.length ? (
            <div className="space-y-2">
              {toolsResult.tools.map((tool: any) => (
                <div key={tool.name} className="flex items-start gap-3 p-2 rounded border border-line-soft">
                  {tool.allowed
                    ? <Check className="w-4 h-4 text-green-400 mt-0.5 shrink-0" />
                    : <X className="w-4 h-4 text-gray-600 mt-0.5 shrink-0" />}
                  <div className="min-w-0">
                    <span className={`font-mono text-sm ${tool.allowed ? 'text-gray-200' : 'text-gray-500 line-through'}`}>
                      {tool.name}
                    </span>
                    {tool.description && <p className="text-xs text-gray-500 mt-0.5">{tool.description}</p>}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-500">The server reports no tools.</p>
          )}
        </div>
      )}
    </div>
  );
}
