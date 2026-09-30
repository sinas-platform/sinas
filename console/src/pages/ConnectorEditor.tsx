import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, getApiErrorMessage, API_BASE_URL } from '../lib/api';
import { useToast } from '../lib/toast-context';
import {
  Save, ArrowLeft, Plus, Trash2, Play, X, ChevronDown, ChevronRight,
  Upload, AlertCircle, Globe, FileText,
} from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
const AUTH_TYPES = [
  { value: 'none', label: 'No Auth' },
  { value: 'bearer', label: 'Bearer Token' },
  { value: 'basic', label: 'Basic Auth' },
  { value: 'api_key', label: 'API Key' },
  { value: 'oauth2_client_credentials', label: 'OAuth 2.0 (Client Credentials)' },
  { value: 'oauth2_authorization_code', label: 'OAuth 2.0 (Authorization Code)' },
  { value: 'sinas_token', label: 'Sinas Token' },
];
// Auth types that resolve a stored Secret (for OAuth this is the client secret).
const SECRET_AUTH_TYPES = ['bearer', 'basic', 'api_key', 'oauth2_client_credentials', 'oauth2_authorization_code'];

// Keep only the auth fields that matter for the chosen type. The editor carries a full
// set of auth defaults in state for convenience, but persisting them all would write
// empty OAuth fields (tokenUrl:"", scopes:[], clientAuthMethod:"body", ...) onto every
// connector — polluting config export / GitOps diffs for non-OAuth connectors.
function pruneAuthForSave(auth: any): Record<string, any> {
  const type = auth?.type || 'none';
  const out: Record<string, any> = { type };
  if (type === 'none' || type === 'sinas_token') return out;
  if (auth.secret) out.secret = auth.secret;
  if (type === 'bearer' || type === 'basic') return out;
  if (type === 'api_key') {
    const position = auth.position === 'query' ? 'query' : 'header';
    out.position = position;
    if (position === 'query') out.param_name = auth.param_name || 'api_key';
    else out.header = auth.header || 'X-Api-Key';
    return out;
  }
  if (type === 'oauth2_client_credentials' || type === 'oauth2_authorization_code') {
    if (auth.token_url) out.token_url = auth.token_url;
    if (auth.client_id) out.client_id = auth.client_id;
    if (auth.scopes?.length) out.scopes = auth.scopes;
    if (auth.client_auth_method) out.client_auth_method = auth.client_auth_method;
    if (auth.token_params && Object.keys(auth.token_params).length) out.token_params = auth.token_params;
    if (type === 'oauth2_authorization_code' && auth.authorize_url) out.authorize_url = auth.authorize_url;
    return out;
  }
  return out;
}
const methodColors: Record<string, string> = {
  GET: 'bg-green-900/30 text-green-400',
  POST: 'bg-blue-900/30 text-blue-400',
  PUT: 'bg-yellow-900/30 text-yellow-400',
  PATCH: 'bg-orange-900/30 text-orange-400',
  DELETE: 'bg-red-900/30 text-red-400',
};

interface Operation {
  name: string;
  method: string;
  path: string;
  description: string;
  parameters: any;
  request_body_mapping: string;
  response_mapping: string;
}

const emptyOp = (): Operation => ({
  name: '', method: 'GET', path: '/', description: '',
  parameters: { type: 'object', properties: {} },
  request_body_mapping: 'json', response_mapping: 'json',
});

export function ConnectorEditor() {
  const { namespace, name } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = namespace === 'new' && name === 'new';
  const { showSuccess, showError } = useToast();

  const [formData, setFormData] = useState({
    namespace: 'default', name: '', description: '', base_url: '',
    auth: {
      type: 'none' as string, secret: '', header: 'X-Api-Key', position: 'header', param_name: 'api_key',
      token_url: '', client_id: '', scopes: [] as string[], client_auth_method: 'body', authorize_url: '',
    },
    headers: {} as Record<string, string>,
    retry: { max_attempts: 1, backoff: 'none' },
    timeout_seconds: 30,
    operations: [] as Operation[],
    is_active: true,
  });

  const [expandedOps, setExpandedOps] = useState<Set<number>>(new Set());
  const [newHeaderKey, setNewHeaderKey] = useState('');
  const [newHeaderValue, setNewHeaderValue] = useState('');

  // OpenAPI import
  const [showImportModal, setShowImportModal] = useState(false);
  const [importSpecMode, setImportSpecMode] = useState<'paste' | 'url'>('paste');
  const [importSpec, setImportSpec] = useState('');
  const [importSpecUrl, setImportSpecUrl] = useState('');
  const [importPreview, setImportPreview] = useState<any>(null);
  const [importSelected, setImportSelected] = useState<Set<string>>(new Set());

  // Test modal
  const [showTestModal, setShowTestModal] = useState(false);
  const [testOpIndex, setTestOpIndex] = useState<number>(0);
  const [testParams, setTestParams] = useState<Record<string, any>>({});
  const [testResult, setTestResult] = useState<any>(null);

  const { data: connector, isLoading } = useQuery({
    queryKey: ['connector', namespace, name],
    queryFn: () => apiClient.getConnector(namespace!, name!),
    enabled: !isNew,
    retry: false,
  });

  const { data: secrets } = useQuery({
    queryKey: ['secrets'],
    queryFn: () => apiClient.listSecrets(),
    retry: false,
  });

  useEffect(() => {
    if (connector) {
      setFormData({
        namespace: connector.namespace,
        name: connector.name,
        description: connector.description || '',
        base_url: connector.base_url,
        auth: {
          type: 'none', secret: '', header: 'X-Api-Key', position: 'header', param_name: 'api_key',
          token_url: '', client_id: '', scopes: [], client_auth_method: 'body', authorize_url: '', ...connector.auth,
        },
        headers: connector.headers || {},
        retry: { max_attempts: 1, backoff: 'none', ...connector.retry },
        timeout_seconds: connector.timeout_seconds,
        operations: connector.operations || [],
        is_active: connector.is_active,
      });
    }
  }, [connector]);

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      if (isNew) return apiClient.createConnector(data);
      return apiClient.updateConnector(namespace!, name!, data);
    },
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
      if (isNew) {
        showSuccess('Connector created');
        navigate(`/connectors/${data.namespace}/${data.name}`, { replace: true });
      } else {
        showSuccess('Connector saved');
        queryClient.invalidateQueries({ queryKey: ['connector', namespace, name] });
      }
    },
  });

  const importParseMutation = useMutation({
    mutationFn: (data: any) => apiClient.parseConnectorOpenAPI(data),
    onSuccess: (data: any) => {
      setImportPreview(data);
      const allNames = new Set<string>(data.operations?.map((op: any) => op.name as string) || []);
      setImportSelected(allNames);

      // Auto-populate connector fields from spec metadata (only if empty)
      const updates: any = {};
      if (data.spec_base_url && !formData.base_url) updates.base_url = data.spec_base_url;
      if (data.spec_title && !formData.name) {
        // Slugify title to a valid connector name
        const slug = data.spec_title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
        if (slug) updates.name = slug;
      }
      if (data.spec_description && !formData.description) updates.description = data.spec_description;
      const hasAuthSuggestion = !!data.suggested_auth;
      if (Object.keys(updates).length > 0 || hasAuthSuggestion) {
        setFormData(prev => {
          const next = { ...prev, ...updates };
          // Prefill auth from the spec's securitySchemes, but never clobber a chosen type.
          if (hasAuthSuggestion && prev.auth.type === 'none') {
            next.auth = { ...prev.auth, ...data.suggested_auth };
          }
          return next;
        });
      }
    },
  });

  const testMutation = useMutation({
    mutationFn: ({ op, params }: { op: string; params: any }) =>
      apiClient.testConnectorOperation(formData.namespace, formData.name, op, params),
    onSuccess: (data: any) => setTestResult(data),
    onError: (err: any) => setTestResult({ error: getApiErrorMessage(err, 'Request failed') }),
  });

  const handleSave = () => {
    saveMutation.mutate({ ...formData, auth: pruneAuthForSave(formData.auth) });
  };

  // OAuth (authorization-code) per-user connection status
  const isAuthCode = formData.auth.type === 'oauth2_authorization_code';
  const { data: oauthStatus, refetch: refetchOAuthStatus } = useQuery({
    queryKey: ['connector-oauth-status', namespace, name],
    queryFn: () => apiClient.getConnectorOAuthStatus(namespace!, name!),
    enabled: !isNew && isAuthCode,
    retry: false,
  });

  // Refresh status when the popup reports back that authorization finished.
  useEffect(() => {
    // The callback page is served by the API (which may be a different origin than the
    // console in local dev). Trust the message only from the API origin or our own.
    let apiOrigin = window.location.origin;
    try { apiOrigin = new URL(API_BASE_URL, window.location.origin).origin; } catch { /* keep default */ }
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin && e.origin !== apiOrigin) return;
      if (e.data?.type === 'connector-oauth') {
        if (e.data.status === 'success') showSuccess('Account connected');
        else if (e.data.status === 'error') showError('Authorization was not completed');
        refetchOAuthStatus();
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [refetchOAuthStatus, showSuccess, showError]);

  const connectMutation = useMutation({
    mutationFn: () => apiClient.beginConnectorOAuth(formData.namespace, formData.name),
  });

  // Open the popup synchronously in the click handler (a popup opened later, from the
  // mutation's async onSuccess, is outside the user-gesture window and gets blocked).
  // We pre-open a blank window now and point it at the provider once the URL arrives.
  const handleConnect = () => {
    const popup = window.open('', 'connector-oauth', 'width=600,height=760');
    if (!popup) {
      showError('Please allow popups for this site, then click Connect again.');
      return;
    }
    connectMutation.mutate(undefined, {
      onSuccess: (data: { authorize_url: string }) => {
        popup.location.href = data.authorize_url;
      },
      onError: (err: any) => {
        popup.close();
        showError(getApiErrorMessage(err, 'Could not start the connection.'));
      },
    });
  };

  const disconnectMutation = useMutation({
    mutationFn: () => apiClient.disconnectConnectorOAuth(formData.namespace, formData.name),
    onSuccess: () => { showSuccess('Account disconnected'); refetchOAuthStatus(); },
    onError: (err: any) => showError(getApiErrorMessage(err, 'Could not disconnect the account.')),
  });

  const addOperation = () => {
    const ops = [...formData.operations, emptyOp()];
    setFormData({ ...formData, operations: ops });
    setExpandedOps(new Set([...expandedOps, ops.length - 1]));
  };

  const removeOperation = (index: number) => {
    const ops = formData.operations.filter((_, i) => i !== index);
    setFormData({ ...formData, operations: ops });
  };

  const updateOperation = (index: number, field: string, value: any) => {
    const ops = [...formData.operations];
    ops[index] = { ...ops[index], [field]: value };
    setFormData({ ...formData, operations: ops });
  };

  const toggleOpExpand = (index: number) => {
    const next = new Set(expandedOps);
    if (next.has(index)) next.delete(index); else next.add(index);
    setExpandedOps(next);
  };

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

  const handleImportParse = () => {
    importParseMutation.mutate({
      spec: importSpecMode === 'paste' ? importSpec : undefined,
      spec_url: importSpecMode === 'url' ? importSpecUrl : undefined,
    });
  };

  const handleImportApply = () => {
    if (!importPreview?.operations) return;
    const selected = importPreview.operations.filter((op: any) => importSelected.has(op.name));

    // Merge into local form state: add new, update existing by name
    const merged = [...formData.operations];
    for (const op of selected) {
      const idx = merged.findIndex(e => e.name === op.name);
      if (idx >= 0) {
        merged[idx] = op;
      } else {
        merged.push(op);
      }
    }
    setFormData({ ...formData, operations: merged });
    setShowImportModal(false);
    setImportPreview(null);
  };

  const openTestModal = (index: number) => {
    setTestOpIndex(index);
    setTestParams({});
    setTestResult(null);
    setShowTestModal(true);
  };

  if (!isNew && isLoading) return <div className="text-gray-400">Loading...</div>;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/connectors')} className="p-1.5 text-gray-500 hover:text-gray-300">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-gray-100">
              {isNew ? 'New Connector' : `${formData.namespace}/${formData.name}`}
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowImportModal(true)}
            className="btn btn-secondary flex items-center"
          >
            <Upload className="w-4 h-4 mr-2" />
            Import OpenAPI
          </button>
          <button onClick={handleSave} disabled={saveMutation.isPending} className="btn btn-primary flex items-center">
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
        <div>
          <label className="label">Base URL</label>
          <input type="text" value={formData.base_url} onChange={e => setFormData({ ...formData, base_url: e.target.value })}
            placeholder="https://api.example.com" className="input w-full" />
        </div>
        <div>
          <label className="label">Description</label>
          <textarea value={formData.description} onChange={e => setFormData({ ...formData, description: e.target.value })}
            className="input w-full" rows={2} />
        </div>
        <div className="flex items-center gap-4">
          <div className="w-40">
            <label className="label">Timeout (s)</label>
            <input type="number" value={formData.timeout_seconds} onChange={e => setFormData({ ...formData, timeout_seconds: parseInt(e.target.value) || 30 })}
              className="input w-full" min={1} max={300} />
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
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Auth Type</label>
            <select value={formData.auth.type} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, type: e.target.value } })}
              className="input w-full">
              {AUTH_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
          {SECRET_AUTH_TYPES.includes(formData.auth.type) && (
            <div>
              <label className="label">{formData.auth.type === 'oauth2_client_credentials' ? 'Client Secret' : 'Secret'}</label>
              <select value={formData.auth.secret} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, secret: e.target.value } })}
                className="input w-full">
                <option value="">Select a secret...</option>
                {secrets?.map((s: any) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </div>
          )}
        </div>
        {formData.auth.type === 'api_key' && (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Position</label>
              <select value={formData.auth.position} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, position: e.target.value } })}
                className="input w-full">
                <option value="header">Header</option>
                <option value="query">Query Parameter</option>
              </select>
            </div>
            {formData.auth.position === 'query' ? (
              <div>
                <label className="label">Query Param Name</label>
                <input type="text" value={formData.auth.param_name} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, param_name: e.target.value } })}
                  className="input w-full" placeholder="api_key" />
              </div>
            ) : (
              <div>
                <label className="label">Header Name</label>
                <input type="text" value={formData.auth.header} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, header: e.target.value } })}
                  className="input w-full" placeholder="X-Api-Key" />
              </div>
            )}
          </div>
        )}
        {formData.auth.type === 'oauth2_client_credentials' && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Token URL</label>
                <input type="text" value={formData.auth.token_url} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, token_url: e.target.value } })}
                  className="input w-full font-mono" placeholder="https://idp.example.com/oauth/token" />
              </div>
              <div>
                <label className="label">Client ID</label>
                <input type="text" value={formData.auth.client_id} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, client_id: e.target.value } })}
                  className="input w-full font-mono" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Scopes</label>
                <input type="text"
                  value={(formData.auth.scopes || []).join(' ')}
                  onChange={e => setFormData({ ...formData, auth: { ...formData.auth, scopes: e.target.value.split(/\s+/).filter(Boolean) } })}
                  className="input w-full font-mono" placeholder="read write (space-separated)" />
              </div>
              <div>
                <label className="label">Client Auth Method</label>
                <select value={formData.auth.client_auth_method} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, client_auth_method: e.target.value } })}
                  className="input w-full">
                  <option value="body">Request Body (client_secret_post)</option>
                  <option value="basic">HTTP Basic (client_secret_basic)</option>
                </select>
              </div>
            </div>
            <p className="text-xs text-gray-500">
              The client secret is exchanged for a short-lived access token, cached until shortly before it expires, and sent as a Bearer token.
            </p>
          </div>
        )}
        {isAuthCode && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Authorize URL</label>
                <input type="text" value={formData.auth.authorize_url} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, authorize_url: e.target.value } })}
                  className="input w-full font-mono" placeholder="https://provider.com/oauth/authorize" />
              </div>
              <div>
                <label className="label">Token URL</label>
                <input type="text" value={formData.auth.token_url} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, token_url: e.target.value } })}
                  className="input w-full font-mono" placeholder="https://provider.com/oauth/token" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Client ID</label>
                <input type="text" value={formData.auth.client_id} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, client_id: e.target.value } })}
                  className="input w-full font-mono" />
              </div>
              <div>
                <label className="label">Client Auth Method</label>
                <select value={formData.auth.client_auth_method} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, client_auth_method: e.target.value } })}
                  className="input w-full">
                  <option value="body">Request Body (client_secret_post)</option>
                  <option value="basic">HTTP Basic (client_secret_basic)</option>
                </select>
              </div>
            </div>
            <div>
              <label className="label">Scopes</label>
              <input type="text"
                value={(formData.auth.scopes || []).join(' ')}
                onChange={e => setFormData({ ...formData, auth: { ...formData.auth, scopes: e.target.value.split(/\s+/).filter(Boolean) } })}
                className="input w-full font-mono" placeholder="openid email profile (space-separated)" />
            </div>
            <p className="text-xs text-gray-500">
              Each user authorizes their own account; tokens are stored per-user and refreshed automatically.
              Register this redirect URI with the provider: <span className="font-mono text-gray-400">https://&lt;your-domain&gt;/auth/connectors/oauth/callback</span>
            </p>
            {isNew ? (
              <p className="text-xs text-yellow-500/80">Save the connector before connecting an account.</p>
            ) : (
              <div className="flex items-center gap-3 pt-1">
                {oauthStatus?.connected ? (
                  <>
                    <span className="text-sm text-green-400">
                      Connected{oauthStatus.expires_at ? ` · expires ${new Date(oauthStatus.expires_at).toLocaleString()}` : ''}
                    </span>
                    <button onClick={handleConnect} disabled={connectMutation.isPending} className="btn btn-secondary btn-sm">
                      Reconnect
                    </button>
                    <button onClick={() => disconnectMutation.mutate()} disabled={disconnectMutation.isPending} className="btn btn-secondary btn-sm text-red-400">
                      Disconnect
                    </button>
                  </>
                ) : (
                  <>
                    <span className="text-sm text-gray-500">Not connected</span>
                    <button onClick={handleConnect} disabled={connectMutation.isPending} className="btn btn-primary btn-sm">
                      {connectMutation.isPending ? 'Opening...' : 'Connect Account'}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        )}
        {formData.auth.type === 'sinas_token' && (
          <p className="text-xs text-gray-500">The calling user's Sinas JWT will be forwarded as a Bearer token.</p>
        )}
      </div>

      {/* Headers */}
      <div className="card space-y-4">
        <h2 className="text-lg font-semibold text-gray-100">Default Headers</h2>
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

      {/* Retry */}
      <div className="card space-y-4">
        <h2 className="text-lg font-semibold text-gray-100">Retry</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Max Attempts</label>
            <input type="number" value={formData.retry.max_attempts}
              onChange={e => setFormData({ ...formData, retry: { ...formData.retry, max_attempts: parseInt(e.target.value) || 1 } })}
              className="input w-full" min={1} max={10} />
          </div>
          <div>
            <label className="label">Backoff</label>
            <select value={formData.retry.backoff}
              onChange={e => setFormData({ ...formData, retry: { ...formData.retry, backoff: e.target.value } })}
              className="input w-full">
              <option value="none">None</option>
              <option value="exponential">Exponential</option>
              <option value="linear">Linear</option>
            </select>
          </div>
        </div>
      </div>

      {/* Operations */}
      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-100">
            Operations <span className="text-gray-500 font-normal text-sm">({formData.operations.length})</span>
          </h2>
          <button onClick={addOperation} className="btn btn-secondary btn-sm flex items-center">
            <Plus className="w-4 h-4 mr-1" />
            Add Operation
          </button>
        </div>

        {formData.operations.map((op, i) => (
          <div key={i} className="border border-line-soft rounded-lg">
            <div className="flex items-center gap-3 p-3 cursor-pointer" onClick={() => toggleOpExpand(i)}>
              {expandedOps.has(i) ? <ChevronDown className="w-4 h-4 text-gray-500" /> : <ChevronRight className="w-4 h-4 text-gray-500" />}
              <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase ${methodColors[op.method] || 'bg-gray-800 text-gray-400'}`}>
                {op.method}
              </span>
              <span className="font-mono text-sm text-gray-200">{op.name || '(unnamed)'}</span>
              <span className="text-xs text-gray-500">{op.path}</span>
              <div className="flex-1" />
              {!isNew && (
                <button onClick={(e) => { e.stopPropagation(); openTestModal(i); }}
                  className="p-1 text-gray-500 hover:text-green-400" title="Test">
                  <Play className="w-4 h-4" />
                </button>
              )}
              <button onClick={(e) => { e.stopPropagation(); removeOperation(i); }}
                className="p-1 text-gray-500 hover:text-red-400" title="Remove">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>

            {expandedOps.has(i) && (
              <div className="border-t border-line-soft p-4 space-y-3">
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="label">Name</label>
                    <input type="text" value={op.name} onChange={e => updateOperation(i, 'name', e.target.value)}
                      className="input w-full font-mono" placeholder="operation_name" />
                  </div>
                  <div>
                    <label className="label">Method</label>
                    <select value={op.method} onChange={e => updateOperation(i, 'method', e.target.value)} className="input w-full">
                      {METHODS.map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="label">Path</label>
                    <input type="text" value={op.path} onChange={e => updateOperation(i, 'path', e.target.value)}
                      className="input w-full font-mono" placeholder="/endpoint/{{ id }}" />
                  </div>
                </div>
                <div>
                  <label className="label">Description</label>
                  <input type="text" value={op.description || ''} onChange={e => updateOperation(i, 'description', e.target.value)}
                    className="input w-full" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="label">Request Mapping</label>
                    <select value={op.request_body_mapping} onChange={e => updateOperation(i, 'request_body_mapping', e.target.value)}
                      className="input w-full">
                      <option value="json">JSON Body</option>
                      <option value="query">Query Params</option>
                      <option value="path_and_json">Path + JSON Body</option>
                      <option value="path_and_query">Path + Query Params</option>
                    </select>
                  </div>
                  <div>
                    <label className="label">Response Mapping</label>
                    <select value={op.response_mapping} onChange={e => updateOperation(i, 'response_mapping', e.target.value)}
                      className="input w-full">
                      <option value="json">JSON</option>
                      <option value="text">Text</option>
                    </select>
                  </div>
                </div>
                <JSONSchemaEditor
                  label="Parameters"
                  description="Input parameters for this operation"
                  value={op.parameters || { type: 'object', properties: {} }}
                  onChange={schema => updateOperation(i, 'parameters', schema)}
                />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* OpenAPI Import Modal */}
      {showImportModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-surface-1 rounded-lg max-w-3xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-gray-100">Import from OpenAPI</h2>
              <button onClick={() => { setShowImportModal(false); setImportPreview(null); }} className="p-1 text-gray-500 hover:text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            {!importPreview ? (
              <div className="space-y-4">
                <div className="flex gap-2">
                  <button onClick={() => setImportSpecMode('paste')}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm ${importSpecMode === 'paste' ? 'bg-primary-600/20 text-primary-400 border border-primary-600/40' : 'bg-surface-0 text-gray-400 border border-line-soft'}`}>
                    <FileText className="w-4 h-4" /> Paste Spec
                  </button>
                  <button onClick={() => setImportSpecMode('url')}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm ${importSpecMode === 'url' ? 'bg-primary-600/20 text-primary-400 border border-primary-600/40' : 'bg-surface-0 text-gray-400 border border-line-soft'}`}>
                    <Globe className="w-4 h-4" /> From URL
                  </button>
                </div>
                {importSpecMode === 'paste' ? (
                  <textarea value={importSpec} onChange={e => setImportSpec(e.target.value)}
                    placeholder="Paste OpenAPI v3 spec (JSON or YAML)" className="input font-mono text-sm w-full" rows={10} />
                ) : (
                  <input type="text" value={importSpecUrl} onChange={e => setImportSpecUrl(e.target.value)}
                    placeholder="https://api.example.com/openapi.json" className="input w-full" />
                )}
                {importParseMutation.isError && (
                  <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg">
                    <p className="text-sm text-red-400">{getApiErrorMessage(importParseMutation.error, 'Failed to parse spec')}</p>
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <button onClick={() => setShowImportModal(false)} className="btn btn-secondary">Cancel</button>
                  <button onClick={handleImportParse} disabled={importParseMutation.isPending}
                    className="btn btn-primary">{importParseMutation.isPending ? 'Parsing...' : 'Parse Spec'}</button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {importPreview.warnings?.length > 0 && (
                  <div className="p-3 bg-yellow-900/20 border border-yellow-800 rounded-lg">
                    {importPreview.warnings.map((w: string, i: number) => (
                      <p key={i} className="text-sm text-yellow-400 flex items-center gap-2">
                        <AlertCircle className="w-4 h-4 flex-shrink-0" />{w}
                      </p>
                    ))}
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-400">{importPreview.operations?.length || 0} operations found</span>
                  <button onClick={() => {
                    const all = new Set<string>(importPreview.operations?.map((op: any) => op.name as string) || []);
                    setImportSelected(importSelected.size === all.size ? new Set<string>() : all);
                  }} className="text-sm text-primary-400 hover:text-primary-300">
                    {importSelected.size === (importPreview.operations?.length || 0) ? 'Deselect All' : 'Select All'}
                  </button>
                </div>
                <div className="space-y-2 max-h-[50vh] overflow-y-auto">
                  {importPreview.operations?.map((op: any) => (
                    <div key={op.name} className="flex items-center gap-3 p-2 border border-line-soft rounded">
                      <input type="checkbox" checked={importSelected.has(op.name)}
                        onChange={() => {
                          const s = new Set(importSelected);
                          s.has(op.name) ? s.delete(op.name) : s.add(op.name);
                          setImportSelected(s);
                        }}
                        className="rounded border-gray-600 bg-gray-800 text-primary-600" />
                      <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase ${methodColors[op.method] || 'bg-gray-800 text-gray-400'}`}>
                        {op.method}
                      </span>
                      <span className="font-mono text-sm text-gray-200">{op.name}</span>
                      <span className="text-xs text-gray-500">{op.path}</span>
                    </div>
                  ))}
                </div>
                <div className="flex justify-between">
                  <button onClick={() => setImportPreview(null)} className="btn btn-secondary">Back</button>
                  <button onClick={handleImportApply} disabled={importSelected.size === 0}
                    className="btn btn-primary">
                    {`Import ${importSelected.size} Operations`}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Test Modal */}
      {showTestModal && formData.operations[testOpIndex] && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-surface-1 rounded-lg max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-gray-100">
                Test: {formData.operations[testOpIndex].name}
              </h2>
              <button onClick={() => setShowTestModal(false)} className="p-1 text-gray-500 hover:text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="label">Parameters (JSON)</label>
                <CodeEditor
                  value={JSON.stringify(testParams, null, 2)}
                  language="json"
                  onChange={e => { try { setTestParams(JSON.parse(e.target.value)); } catch {} }}
                  padding={12}
                  style={{ fontSize: 12, backgroundColor: 'var(--surface-0)', borderRadius: 8, fontFamily: 'monospace' }}
                  minHeight={60}
                />
              </div>
              <button onClick={() => testMutation.mutate({ op: formData.operations[testOpIndex].name, params: testParams })}
                disabled={testMutation.isPending} className="btn btn-primary w-full">
                {testMutation.isPending ? 'Sending...' : 'Send Request'}
              </button>
              {testResult && (
                <div className="space-y-2">
                  {testResult.error ? (
                    <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg">
                      <p className="text-sm text-red-400">{testResult.error}</p>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center gap-3">
                        <span className={`text-sm font-bold ${testResult.status_code < 400 ? 'text-green-400' : 'text-red-400'}`}>
                          {testResult.status_code}
                        </span>
                        <span className="text-xs text-gray-500">{testResult.elapsed_ms}ms</span>
                      </div>
                      <div className="bg-surface-0 rounded-lg p-3 overflow-x-auto max-h-[40vh] overflow-y-auto">
                        <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap">
                          {typeof testResult.body === 'string' ? testResult.body : JSON.stringify(testResult.body, null, 2)}
                        </pre>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
