import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useToast } from '../lib/toast-context';
import {
  Save, ArrowLeft, Plus, Trash2, Play, X, ChevronDown, ChevronRight,
  Upload, AlertCircle, Globe, FileText,
} from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
const AUTH_TYPES = [
  { value: 'none', label: 'No Auth' },
  { value: 'bearer', label: 'Bearer Token' },
  { value: 'basic', label: 'Basic Auth' },
  { value: 'api_key', label: 'API Key' },
  { value: 'sinas_token', label: 'Sinas Token' },
];
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
  const { showSuccess } = useToast();

  const [formData, setFormData] = useState({
    namespace: 'default', name: '', description: '', base_url: '',
    auth: { type: 'none' as string, secret: '', header: 'X-Api-Key', position: 'header', param_name: 'api_key' },
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
        auth: { type: 'none', secret: '', header: 'X-Api-Key', position: 'header', param_name: 'api_key', ...connector.auth },
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
      if (Object.keys(updates).length > 0) setFormData(prev => ({ ...prev, ...updates }));
    },
  });

  const testMutation = useMutation({
    mutationFn: ({ op, params }: { op: string; params: any }) =>
      apiClient.testConnectorOperation(formData.namespace, formData.name, op, params),
    onSuccess: (data: any) => setTestResult(data),
    onError: (err: any) => setTestResult({ error: err?.response?.data?.detail || 'Request failed' }),
  });

  const handleSave = () => {
    saveMutation.mutate(formData);
  };

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
            {(saveMutation.error as any)?.response?.data?.detail || 'Failed to save'}
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
          {['bearer', 'basic', 'api_key'].includes(formData.auth.type) && (
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
        {formData.auth.type === 'api_key' && (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Header Name</label>
              <input type="text" value={formData.auth.header} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, header: e.target.value } })}
                className="input w-full" />
            </div>
            <div>
              <label className="label">Position</label>
              <select value={formData.auth.position} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, position: e.target.value } })}
                className="input w-full">
                <option value="header">Header</option>
                <option value="query">Query Parameter</option>
              </select>
            </div>
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
            placeholder="Header name" className="input w-48" />
          <input type="text" value={newHeaderValue} onChange={e => setNewHeaderValue(e.target.value)}
            placeholder="Value" className="input flex-1" />
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
          <div key={i} className="border border-white/[0.06] rounded-lg">
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
              <div className="border-t border-white/[0.06] p-4 space-y-3">
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
                <div>
                  <label className="label">Parameters (JSON Schema)</label>
                  <CodeEditor
                    value={typeof op.parameters === 'string' ? op.parameters : JSON.stringify(op.parameters, null, 2)}
                    language="json"
                    onChange={e => {
                      try { updateOperation(i, 'parameters', JSON.parse(e.target.value)); } catch {}
                    }}
                    padding={12}
                    style={{ fontSize: 12, backgroundColor: '#0d0d0d', borderRadius: 8, fontFamily: 'monospace' }}
                    minHeight={80}
                  />
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* OpenAPI Import Modal */}
      {showImportModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-3xl w-full p-6 max-h-[90vh] overflow-y-auto">
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
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm ${importSpecMode === 'paste' ? 'bg-primary-600/20 text-primary-400 border border-primary-600/40' : 'bg-[#0d0d0d] text-gray-400 border border-white/[0.06]'}`}>
                    <FileText className="w-4 h-4" /> Paste Spec
                  </button>
                  <button onClick={() => setImportSpecMode('url')}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm ${importSpecMode === 'url' ? 'bg-primary-600/20 text-primary-400 border border-primary-600/40' : 'bg-[#0d0d0d] text-gray-400 border border-white/[0.06]'}`}>
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
                    <p className="text-sm text-red-400">{(importParseMutation.error as any)?.response?.data?.detail || 'Failed to parse spec'}</p>
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
                    <div key={op.name} className="flex items-center gap-3 p-2 border border-white/[0.06] rounded">
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
          <div className="bg-[#161616] rounded-lg max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
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
                  style={{ fontSize: 12, backgroundColor: '#0d0d0d', borderRadius: 8, fontFamily: 'monospace' }}
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
                      <div className="bg-[#0d0d0d] rounded-lg p-3 overflow-x-auto max-h-[40vh] overflow-y-auto">
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
