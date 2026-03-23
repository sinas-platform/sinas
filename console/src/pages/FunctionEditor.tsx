import { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, API_BASE_URL } from '../lib/api';
import { ArrowLeft, Save, Trash2, ChevronDown, ChevronRight, Filter, Upload, Info, Play, Loader2 } from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';
import { SchemaFormField } from '../components/SchemaFormField';
import { ApiUsage } from '../components/ApiUsage';

const SCHEMA_PRESETS: Record<string, { label: string; input: any; output: any }> = {
  'pre-upload-filter': {
    label: 'Pre-upload filter',
    input: {
      type: "object",
      properties: {
        content_base64: { type: "string", description: "Base64-encoded file content" },
        namespace: { type: "string", description: "Collection namespace" },
        collection: { type: "string", description: "Collection name" },
        filename: { type: "string", description: "Uploaded file name" },
        content_type: { type: "string", description: "MIME type" },
        size_bytes: { type: "integer", description: "File size in bytes" },
        user_metadata: { type: "object", description: "Metadata provided by uploader" },
        user_id: { type: "string", description: "Uploader's user ID" },
      },
      required: ["content_base64", "namespace", "collection", "filename", "content_type", "size_bytes"],
    },
    output: {
      type: "object",
      properties: {
        approved: { type: "boolean", description: "Whether the file is approved" },
        reason: { type: "string", description: "Rejection reason (if not approved)" },
        modified_content: { type: "string", description: "Base64-encoded replacement content (optional)" },
        metadata: { type: "object", description: "Additional metadata to merge (optional)" },
      },
      required: ["approved"],
    },
  },
  'post-upload': {
    label: 'Post-upload',
    input: {
      type: "object",
      properties: {
        file_id: { type: "string", description: "UUID of the stored file" },
        namespace: { type: "string", description: "Collection namespace" },
        collection: { type: "string", description: "Collection name" },
        filename: { type: "string", description: "File name" },
        version: { type: "integer", description: "Version number" },
        file_path: { type: "string", description: "Storage path" },
        user_id: { type: "string", description: "Uploader's user ID" },
        metadata: { type: "object", description: "Final file metadata" },
      },
      required: ["file_id", "namespace", "collection", "filename", "version"],
    },
    output: {
      type: "object",
      properties: {},
    },
  },
  'cdc': {
    label: 'CDC (Change Data Capture)',
    input: {
      type: "object",
      properties: {
        table: { type: "string", description: "Schema-qualified table name (e.g. public.orders)" },
        operation: { type: "string", description: "Type of change detected (CHANGE)" },
        rows: {
          type: "array",
          items: { type: "object" },
          description: "Array of new/changed rows from the polled table",
        },
        poll_column: { type: "string", description: "Column used for change detection" },
        count: { type: "integer", description: "Number of rows in this batch" },
        timestamp: { type: "string", description: "ISO 8601 timestamp of when the poll occurred" },
      },
      required: ["table", "operation", "rows", "poll_column", "count", "timestamp"],
    },
    output: {
      type: "object",
      properties: {},
    },
  },
  'message-hook': {
    label: 'Message Hook',
    input: {
      type: "object",
      properties: {
        message: {
          type: "object",
          properties: {
            role: { type: "string", description: "Message role: 'user' or 'assistant'" },
            content: { type: "string", description: "Message content" },
          },
          required: ["role", "content"],
          description: "The message being processed",
        },
        chat_id: { type: "string", description: "Chat ID" },
        agent: {
          type: "object",
          properties: {
            namespace: { type: "string" },
            name: { type: "string" },
          },
          description: "Agent that owns this chat",
        },
        session_key: { type: "string", description: "Session key (if present)" },
        user_id: { type: "string", description: "User ID" },
      },
      required: ["message", "chat_id", "agent", "user_id"],
    },
    output: {
      type: "object",
      properties: {
        content: { type: "string", description: "Mutated message content (optional)" },
        block: { type: "boolean", description: "If true, block the pipeline" },
        reply: { type: "string", description: "Reply to send when blocking (optional)" },
      },
    },
  },
};

export function FunctionEditor() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = namespace === 'new';

  const [formData, setFormData] = useState({
    namespace: 'default',
    name: '',
    description: '',
    code: `def handler(input, context):
    # input: dict validated against input_schema
    # context: {user_id, user_email, access_token, execution_id, trigger_type, chat_id}

    return {"result": "success"}`,
    input_schema: {
      type: "object",
      properties: {},
    } as any,
    output_schema: {
      type: "object",
      properties: {
        result: {
          type: "string",
          description: "Output result"
        }
      },
      required: ["result"]
    } as any,
    icon: '' as string,
    shared_pool: false,
    requires_approval: false,
    timeout: null as number | null,
  });

  const [iconMode, setIconMode] = useState<'collection' | 'url'>('collection');
  const [iconCollectionNs, setIconCollectionNs] = useState('');
  const [iconCollectionName, setIconCollectionName] = useState('');
  const [iconCollectionFiles, setIconCollectionFiles] = useState<any[]>([]);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testInputParams, setTestInputParams] = useState<Record<string, any>>({});
  const [testResult, setTestResult] = useState<any>(null);
  const testResultRef = useRef<HTMLDivElement>(null);

  const { data: func, isLoading } = useQuery({
    queryKey: ['function', namespace, name],
    queryFn: () => apiClient.getFunction(namespace!, name!),
    enabled: !isNew && !!namespace && !!name,
  });

  const { data: collections } = useQuery({
    queryKey: ['collections'],
    queryFn: () => apiClient.listCollections(),
    retry: false,
  });

  // Detect if this function is used as a collection trigger
  const triggerRoles = useMemo(() => {
    const funcId = `${formData.namespace}/${formData.name}`;
    const roles: { contentFilter: string[]; postUpload: string[] } = { contentFilter: [], postUpload: [] };
    if (!collections || !formData.name) return roles;
    for (const coll of collections) {
      const collName = `${coll.namespace}/${coll.name}`;
      if (coll.content_filter_function === funcId) roles.contentFilter.push(collName);
      if (coll.post_upload_function === funcId) roles.postUpload.push(collName);
    }
    return roles;
  }, [collections, formData.namespace, formData.name]);

  const isCollectionTrigger = triggerRoles.contentFilter.length > 0 || triggerRoles.postUpload.length > 0;
  const [showTriggerDocs, setShowTriggerDocs] = useState(false);

  // Load function data when available
  useEffect(() => {
    if (func && !isNew) {
      setFormData({
        namespace: func.namespace || 'default',
        name: func.name || '',
        description: func.description || '',
        code: func.code || '',
        input_schema: func.input_schema || {},
        output_schema: func.output_schema || {},
        icon: func.icon || '',
        shared_pool: func.shared_pool || false,
        requires_approval: func.requires_approval || false,
        timeout: func.timeout || null,
      });
    }
  }, [func, isNew]);

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      setSaveError(null);
      return isNew
        ? apiClient.createFunction(data)
        : apiClient.updateFunction(namespace!, name!, data);
    },
    onSuccess: (data) => {
      setSaveError(null);
      queryClient.invalidateQueries({ queryKey: ['functions'] });
      queryClient.invalidateQueries({ queryKey: ['function', namespace, name] });
      if (isNew) {
        navigate(`/functions/${data.namespace}/${data.name}`);
      } else if (data.namespace !== namespace || data.name !== name) {
        navigate(`/functions/${data.namespace}/${data.name}`);
      }
    },
    onError: (error: any) => {
      const detail = error?.response?.data?.detail;
      if (Array.isArray(detail)) {
        const syntaxError = detail.find((d: any) => d.loc?.includes('code') && d.msg);
        if (syntaxError) {
          setSaveError(syntaxError.msg);
          return;
        }
        setSaveError(detail.map((d: any) => d.msg).join('; '));
      } else if (typeof detail === 'string') {
        setSaveError(detail);
      } else {
        setSaveError('Failed to save function. Check your code and schemas.');
      }
    },
  });

  const testMutation = useMutation({
    mutationFn: async (input: any) => {
      const resp = await apiClient.executeFunction(formData.namespace, formData.name, input);
      return resp;
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.deleteFunction(namespace!, name!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['functions'] });
      navigate('/functions');
    },
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();

    // Validate that function definition exists (handler or legacy name-based)
    const handlerRegex = /def\s+handler\s*\(/;
    const legacyRegex = new RegExp(`def\\s+${formData.name}\\s*\\(`);
    if (!handlerRegex.test(formData.code) && !legacyRegex.test(formData.code)) {
      alert(`Code must contain a function definition: def handler(input, context)`);
      return;
    }

    saveMutation.mutate(formData);
  };

  // Check if the entry point function exists in the code (handler or legacy name-based)
  const hasEntryPoint = () => {
    if (!formData.name && !formData.code) return false;
    const handlerRegex = /def\s+handler\s*\(/;
    if (handlerRegex.test(formData.code)) return true;
    if (!formData.name) return false;
    const escapedName = formData.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const functionRegex = new RegExp(`def\\s+${escapedName}\\s*\\(`);
    return functionRegex.test(formData.code);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <Link to="/functions" className="mr-4 text-gray-400 hover:text-gray-100">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-100">
              {isNew ? 'New Function' : (
                <>
                  <span className="text-gray-500">{formData.namespace}/</span>{formData.name || 'Edit Function'}
                </>
              )}
            </h1>
            <p className="text-gray-400 mt-1">
              {isNew ? 'Create a new Python function' : 'Edit function configuration and code'}
            </p>
          </div>
        </div>
        <div className="flex space-x-3">
          {!isNew && (
            <button
              onClick={() => {
                if (confirm('Are you sure you want to delete this function?')) {
                  deleteMutation.mutate();
                }
              }}
              className="btn btn-danger flex items-center"
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="w-4 h-4 mr-2" />
              Delete
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saveMutation.isPending}
            className="btn btn-primary flex items-center"
          >
            <Save className="w-4 h-4 mr-2" />
            {saveMutation.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {!isNew && formData.namespace && formData.name && (
        <ApiUsage
          curl={[
            {
              label: 'Execute function',
              language: 'bash',
              code: `curl -X POST ${API_BASE_URL}/functions/${formData.namespace}/${formData.name}/execute \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '${Object.keys((formData.input_schema as any)?.properties || {}).length > 0
    ? `{"input": {${Object.keys((formData.input_schema as any).properties).map(k => `"${k}": "..."`).join(', ')}}}`
    : '{"input": {}}'}'`,
            },
            {
              label: 'Check execution result',
              language: 'bash',
              code: `curl ${API_BASE_URL}/executions/{execution_id} \\
  -H "Authorization: Bearer $TOKEN"`,
            },
          ]}
          sdk={[
            {
              label: 'Execute and check results',
              language: 'python',
              code: `from sinas import SinasClient

client = SinasClient(base_url="${API_BASE_URL}", api_key="sk-...")

# List executions for this function
executions = client.executions.list(
    function_name="${formData.name}", limit=10
)

# Get execution details
details = client.executions.get(executions[0]["execution_id"])
print(details["status"], details["output_data"])`,
            },
          ]}
        />
      )}

      {/* Success/Error Messages */}
      {saveError && (
        <div className="p-4 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400 font-mono">
          {saveError}
        </div>
      )}

      {saveMutation.isSuccess && !saveError && (
        <div className="p-4 bg-green-900/20 border border-green-800/30 rounded-lg text-sm text-green-400">
          Function saved successfully!
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        {/* Basic Info */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Basic Information</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Namespace *
              </label>
              <input
                type="text"
                value={formData.namespace}
                onChange={(e) => setFormData({ ...formData, namespace: e.target.value })}
                placeholder="default"
                pattern="^[a-z][a-z0-9_-]*$"
                title="Must start with lowercase letter, contain only lowercase letters, numbers, underscores, and hyphens"
                required
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">
                Use lowercase letters, numbers, underscores, and hyphens
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Function Name *
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="my_function"
                pattern="^[a-zA-Z_][a-zA-Z0-9_]*$"
                title="Must start with letter or underscore, contain only letters, numbers, and underscores"
                required
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">
                Must start with letter or underscore, contain only alphanumerics and underscores
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Description
              </label>
              <input
                type="text"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="What does this function do?"
                className="input"
              />
            </div>

            {/* Icon Picker */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Icon</label>
              <div className="flex items-start gap-4">
                <div className="flex-shrink-0 w-[4.5rem] h-[4.5rem] rounded-lg bg-[#1e1e1e] border border-white/[0.06] flex items-center justify-center overflow-hidden">
                  {(func?.icon_url || (formData.icon?.startsWith('url:') && formData.icon.length > 4)) ? (
                    <img
                      src={formData.icon?.startsWith('url:') ? formData.icon.slice(4) : func?.icon_url || ''}
                      alt=""
                      className="w-full h-full object-cover"
                    />
                  ) : formData.icon?.startsWith('collection:') ? (
                    <img
                      src={(() => {
                        const ref = formData.icon.slice(11);
                        const parts = ref.split('/');
                        if (parts.length === 3) return `${window.location.hostname === 'localhost' ? 'http://localhost:8000' : ''}/files/public/${parts[0]}/${parts[1]}/${parts[2]}`;
                        return '';
                      })()}
                      alt=""
                      className="w-full h-full object-cover"
                    />
                  ) : null}
                </div>
                <div className="flex-1 space-y-2">
                  <div className="flex gap-2">
                    <button type="button" onClick={() => setIconMode('collection')} className={`px-3 py-1 text-xs rounded ${iconMode === 'collection' ? 'bg-primary-600 text-white' : 'bg-[#1e1e1e] text-gray-400'}`}>From Collection</button>
                    <button type="button" onClick={() => setIconMode('url')} className={`px-3 py-1 text-xs rounded ${iconMode === 'url' ? 'bg-primary-600 text-white' : 'bg-[#1e1e1e] text-gray-400'}`}>External URL</button>
                  </div>
                  {iconMode === 'url' ? (
                    <input
                      type="url"
                      value={formData.icon?.startsWith('url:') ? formData.icon.slice(4) : ''}
                      onChange={(e) => setFormData({ ...formData, icon: e.target.value ? `url:${e.target.value}` : '' })}
                      placeholder="https://example.com/icon.png"
                      className="input text-sm"
                    />
                  ) : (
                    <div className="space-y-2">
                      <select
                        value={iconCollectionNs && iconCollectionName ? `${iconCollectionNs}/${iconCollectionName}` : ''}
                        onChange={async (e) => {
                          const val = e.target.value;
                          if (!val) { setIconCollectionNs(''); setIconCollectionName(''); setIconCollectionFiles([]); return; }
                          const [ns, cn] = val.split('/');
                          setIconCollectionNs(ns); setIconCollectionName(cn);
                          try {
                            const files = await apiClient.listFiles(ns, cn);
                            setIconCollectionFiles(files.filter((f: any) => f.content_type?.startsWith('image/')));
                          } catch { setIconCollectionFiles([]); }
                        }}
                        className="input text-sm"
                      >
                        <option value="">Select collection...</option>
                        {collections?.map((c: any) => (
                          <option key={c.id} value={`${c.namespace}/${c.name}`}>{c.namespace}/{c.name}</option>
                        ))}
                      </select>
                      {iconCollectionFiles.length > 0 && (
                        <div className="grid grid-cols-6 gap-2 max-h-32 overflow-y-auto">
                          {iconCollectionFiles.map((f: any) => {
                            const ref = `collection:${iconCollectionNs}/${iconCollectionName}/${f.name}`;
                            return (
                              <button key={f.id} type="button" onClick={() => setFormData({ ...formData, icon: ref })}
                                className={`w-10 h-10 rounded border-2 overflow-hidden ${formData.icon === ref ? 'border-primary-500' : 'border-transparent hover:border-gray-500'}`} title={f.name}>
                                <img src={`${window.location.hostname === 'localhost' ? 'http://localhost:8000' : ''}/files/public/${iconCollectionNs}/${iconCollectionName}/${f.name}`} alt={f.name} className="w-full h-full object-cover" />
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                  {formData.icon && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500 truncate">{formData.icon}</span>
                      <button type="button" onClick={() => setFormData({ ...formData, icon: '' })} className="text-xs text-red-400 hover:text-red-300">Remove</button>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="space-y-3 pt-2 border-t border-white/[0.06]">
              <h3 className="text-sm font-medium text-gray-100">Execution Settings</h3>

              <div className="flex items-start">
                <input
                  type="checkbox"
                  id="shared_pool"
                  checked={formData.shared_pool}
                  onChange={(e) => setFormData({ ...formData, shared_pool: e.target.checked })}
                  className="mt-1 h-4 w-4 text-primary-600 focus:ring-primary-500 border-white/10 rounded"
                />
                <label htmlFor="shared_pool" className="ml-3">
                  <span className="block text-sm font-medium text-gray-300">Use Shared Worker Pool</span>
                  <span className="block text-xs text-gray-500 mt-0.5">
                    Run in shared worker pool instead of isolated container. More efficient for trusted functions with high call frequency.
                  </span>
                </label>
              </div>

              <div className="flex items-start">
                <input
                  type="checkbox"
                  id="requires_approval"
                  checked={formData.requires_approval}
                  onChange={(e) => setFormData({ ...formData, requires_approval: e.target.checked })}
                  className="mt-1 h-4 w-4 text-primary-600 focus:ring-primary-500 border-white/10 rounded"
                />
                <label htmlFor="requires_approval" className="ml-3">
                  <span className="block text-sm font-medium text-gray-300">Require Approval Before Execution</span>
                  <span className="block text-xs text-gray-500 mt-0.5">
                    LLM must ask user for approval before calling this function. Use for dangerous operations (delete, send email, etc.).
                  </span>
                </label>
              </div>

              <div>
                <label htmlFor="timeout" className="block text-sm font-medium text-gray-300">
                  Timeout (seconds)
                </label>
                <span className="block text-xs text-gray-500 mt-0.5 mb-1.5">
                  Override the global function timeout. Leave empty to use the default.
                </span>
                <input
                  type="number"
                  id="timeout"
                  min={1}
                  placeholder="Default"
                  value={formData.timeout ?? ''}
                  onChange={(e) => setFormData({ ...formData, timeout: e.target.value ? parseInt(e.target.value, 10) : null })}
                  className="w-32 px-3 py-1.5 bg-white/5 border border-white/10 rounded-md text-gray-100 text-sm focus:ring-primary-500 focus:border-primary-500"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Code Editor */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Python Code *</h2>
          <div className="border border-white/10 rounded-lg overflow-hidden flex" style={{ minHeight: '400px' }}>
            {/* Line numbers */}
            <div
              className="select-none text-right pr-3 pt-[15px] text-gray-600 text-sm leading-[21px]"
              style={{
                backgroundColor: '#0d0d0d',
                fontFamily: 'ui-monospace, SFMono-Regular, SF Mono, Consolas, Liberation Mono, Menlo, monospace',
                fontSize: 14,
                minWidth: '3rem',
                paddingLeft: '0.5rem',
              }}
            >
              {formData.code.split('\n').map((_: string, i: number) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            <div className="flex-1">
              <CodeEditor
                value={formData.code}
                language="python"
                placeholder="Enter your Python code here..."
                onChange={(e) => { setSaveError(null); setFormData({ ...formData, code: e.target.value }); }}
                padding={15}
                data-color-mode="dark"
                style={{
                  fontSize: 14,
                  backgroundColor: '#111111',
                  color: '#ededed',
                  fontFamily: 'ui-monospace, SFMono-Regular, SF Mono, Consolas, Liberation Mono, Menlo, monospace',
                  minHeight: '400px',
                }}
              />
            </div>
          </div>
          {/* Syntax error highlight */}
          {saveError && saveError.includes('syntax') && (
            <div className="mt-2 p-2 bg-red-900/20 border border-red-800/30 rounded text-xs text-red-400 font-mono">
              {saveError}
            </div>
          )}
          <div className="flex items-center justify-between mt-2">
            <p className="text-xs text-gray-500">
              Entry point: <code className="font-mono bg-[#161616] px-1 rounded">def handler(input, context)</code>
            </p>
            {formData.name && (
              <div className="flex items-center ml-4">
                {hasEntryPoint() ? (
                  <span className="flex items-center text-xs text-green-600 font-medium">
                    <svg className="w-4 h-4 mr-1" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                    </svg>
                    Entry point found
                  </span>
                ) : (
                  <span className="flex items-center text-xs text-red-600 font-medium">
                    <svg className="w-4 h-4 mr-1" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                    </svg>
                    Entry point missing
                  </span>
                )}
              </div>
            )}
          </div>

          {/* Test Execution — always visible for saved functions */}
          {!isNew && (
            <div className="mt-4 pt-4 border-t border-white/[0.06]">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-gray-300">Test Execution</h3>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-600">Runs saved code</span>
                  <button
                    type="button"
                    onClick={() => {
                      setTestResult(null);
                      testMutation.mutate(testInputParams);
                      setTimeout(() => testResultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
                    }}
                    disabled={testMutation.isPending}
                    className="btn btn-primary flex items-center text-sm py-1.5 px-3"
                  >
                    {testMutation.isPending ? (
                      <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Running...</>
                    ) : (
                      <><Play className="w-3.5 h-3.5 mr-1.5" /> Run</>
                    )}
                  </button>
                </div>
              </div>
              {/* Schema-based input form */}
              {(() => {
                const properties = formData.input_schema?.properties || {};
                const requiredFields = formData.input_schema?.required || [];
                const hasProps = Object.keys(properties).length > 0;

                return hasProps ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-3">
                    {Object.entries(properties).map(([key, prop]: [string, any]) => (
                      <SchemaFormField
                        key={key}
                        name={key}
                        schema={prop}
                        value={testInputParams[key]}
                        onChange={(value) => setTestInputParams({ ...testInputParams, [key]: value })}
                        required={requiredFields.includes(key)}
                      />
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-gray-600 mb-3">No input parameters defined.</p>
                );
              })()}
              {/* Result */}
              <div ref={testResultRef}>
                {(testMutation.isPending || testMutation.data || testMutation.error || testResult) && (
                  <pre className={`p-3 rounded-lg text-xs font-mono overflow-auto max-h-48 ${
                    testMutation.error || testResult?.error
                      ? 'bg-red-900/10 border border-red-800/30 text-red-400'
                      : testMutation.isPending
                      ? 'bg-[#0d0d0d] border border-white/10 text-gray-500'
                      : 'bg-green-900/10 border border-green-800/30 text-gray-300'
                  }`}>
                    {testMutation.isPending
                      ? 'Running...'
                      : testMutation.error
                      ? JSON.stringify((testMutation.error as any)?.response?.data || 'Execution failed', null, 2)
                      : testResult?.error
                      ? testResult.error
                      : JSON.stringify(testMutation.data, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Function Reference */}
        <div className="card border-blue-800/30 bg-blue-900/20/50">
          <button
            type="button"
            onClick={() => setShowTriggerDocs(!showTriggerDocs)}
            className="flex items-center w-full text-left"
          >
            <Info className="w-5 h-5 text-blue-500 mr-2 flex-shrink-0" />
            <div className="flex-1">
              <span className="text-sm font-medium text-gray-100">Function reference</span>
              {isCollectionTrigger && (
                <span className="text-xs text-gray-500 ml-2">
                  {triggerRoles.contentFilter.length > 0 && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-orange-900/30 text-orange-300 mr-1">
                      <Filter className="w-3 h-3 mr-1" />
                      Content filter for {triggerRoles.contentFilter.join(', ')}
                    </span>
                  )}
                  {triggerRoles.postUpload.length > 0 && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-900/30 text-green-300">
                      <Upload className="w-3 h-3 mr-1" />
                      Post-upload for {triggerRoles.postUpload.join(', ')}
                    </span>
                  )}
                </span>
              )}
            </div>
            {showTriggerDocs ? <ChevronDown className="w-4 h-4 text-gray-500" /> : <ChevronRight className="w-4 h-4 text-gray-500" />}
          </button>
          {showTriggerDocs && (
            <div className="mt-4 space-y-4">
              {/* input_data & context */}
              <div>
                <h4 className="text-sm font-semibold text-gray-100 mb-2">input_data &amp; context</h4>
                <p className="text-xs text-gray-400 mb-2">
                  Every function receives <code className="font-mono bg-[#161616] px-1 rounded">input_data</code> (dict matching the input schema) and <code className="font-mono bg-[#161616] px-1 rounded">context</code> (dict with execution metadata).
                </p>
                <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                  <pre className="text-xs text-gray-100 font-mono">{`def handler(input_data, context):
    # input_data: dict matching your Input Schema (below)

    # context dict — always present:
    # {
    #     "user_id":        str,   # ID of the user who triggered execution
    #     "user_email":     str,   # Email of the triggering user
    #     "access_token":   str,   # Short-lived JWT for calling the SINAS API
    #     "execution_id":   str,   # Unique execution ID
    #     "trigger_type":   str,   # "AGENT" | "API" | "WEBHOOK" | "SCHEDULE" | "CDC" | "HOOK" | "MANUAL"
    #     "chat_id":        str,   # Chat ID (when triggered by an agent, empty otherwise)
    #     "secrets":        dict,  # Decrypted secrets (shared pool only): {"NAME": "value"}
    # }

    return {"result": "..."}  # Must match Output Schema`}</pre>
                </div>
              </div>

              {/* Trigger-specific input_data */}
              <div>
                <h4 className="text-sm font-semibold text-gray-100 mb-2">Trigger-specific input_data</h4>
                <p className="text-xs text-gray-400 mb-2">
                  Depending on how the function is invoked, <code className="font-mono bg-[#161616] px-1 rounded">input_data</code> is populated differently:
                </p>
                <div className="space-y-3">
                  <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                    <p className="text-xs text-blue-400 font-medium mb-1">AGENT / API / MANUAL / SCHEDULE</p>
                    <pre className="text-xs text-gray-100 font-mono">{`# input_data = values matching your Input Schema, provided by
# the caller (agent tool call, API request, schedule config, or UI).`}</pre>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                    <p className="text-xs text-blue-400 font-medium mb-1">WEBHOOK</p>
                    <pre className="text-xs text-gray-100 font-mono">{`# input_data = webhook default_values merged with request body/query params.
# The webhook config determines which HTTP method and path trigger this.`}</pre>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                    <p className="text-xs text-blue-400 font-medium mb-1">CDC (Change Data Capture)</p>
                    <pre className="text-xs text-gray-100 font-mono">{`# input_data = {
#     "table":       str,    # "schema.table" that changed
#     "operation":   str,    # "CHANGE"
#     "rows":        list,   # List of changed row dicts
#     "poll_column": str,    # Column used for change detection
#     "count":       int,    # Number of rows in this batch
#     "timestamp":   str,    # ISO 8601 timestamp
# }`}</pre>
                  </div>
                </div>
              </div>

              {/* Collection triggers */}
              <div>
                <h4 className="text-sm font-semibold text-gray-100 mb-2">Collection triggers</h4>
                <p className="text-xs text-gray-400 mb-2">
                  Functions can be used as collection triggers. Set this in the collection's configuration under Content Filter or Post-Upload function.
                </p>
                <div className="space-y-3">
                  <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                    <div className="flex items-center mb-1">
                      <Filter className="w-3 h-3 text-orange-500 mr-1" />
                      <p className="text-xs text-orange-400 font-medium">Content Filter — runs before file is stored</p>
                    </div>
                    <pre className="text-xs text-gray-100 font-mono">{`# input_data = {
#     "content_base64": str,    # Base64-encoded file content
#     "namespace": str,         # Collection namespace
#     "collection": str,        # Collection name
#     "filename": str,          # Uploaded file name
#     "content_type": str,      # MIME type (e.g. "text/plain")
#     "size_bytes": int,        # File size in bytes
#     "user_metadata": dict,    # Metadata provided by uploader
#     "user_id": str,           # Uploader's user ID
# }
# Return: {"approved": True/False, "reason": "...", "modified_content": "...", "metadata": {}}`}</pre>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                    <div className="flex items-center mb-1">
                      <Upload className="w-3 h-3 text-green-500 mr-1" />
                      <p className="text-xs text-green-400 font-medium">Post-Upload — runs asynchronously after file is stored</p>
                    </div>
                    <pre className="text-xs text-gray-100 font-mono">{`# input_data = {
#     "file_id": str,       # UUID of the stored file
#     "namespace": str,     # Collection namespace
#     "collection": str,    # Collection name
#     "filename": str,      # File name
#     "version": int,       # Version number (1 for new files)
#     "file_path": str,     # Storage path
#     "user_id": str,       # Uploader's user ID
#     "metadata": dict,     # Final file metadata (after filter)
# }
# Return value is ignored (fire-and-forget).`}</pre>
                  </div>
                </div>
              </div>

              {/* Message hooks */}
              <div>
                <h4 className="text-sm font-semibold text-gray-100 mb-2">Message hooks</h4>
                <p className="text-xs text-gray-400 mb-2">
                  Functions can be used as message lifecycle hooks on agents. Configure in the agent's Hooks tab.
                </p>
                <div className="space-y-3">
                  <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                    <p className="text-xs text-blue-400 font-medium mb-1">Hook input</p>
                    <pre className="text-xs text-gray-100 font-mono">{`# input_data = {
#     "message":     {"role": "user"|"assistant", "content": "..."},
#     "chat_id":     str,
#     "agent":       {"namespace": "...", "name": "..."},
#     "session_key": str | None,
#     "user_id":     str,
# }`}</pre>
                  </div>
                  <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                    <p className="text-xs text-blue-400 font-medium mb-1">Hook return values</p>
                    <pre className="text-xs text-gray-100 font-mono">{`# Pass through (no change):
return None

# Mutate message content:
return {"content": "modified content"}

# Block the pipeline (sync hooks only):
return {"block": True, "reply": "Sorry, that message was blocked"}

# Async hooks: return value updates stored message retroactively`}</pre>
                  </div>
                </div>
              </div>

              {/* Secrets */}
              <div>
                <h4 className="text-sm font-semibold text-gray-100 mb-2">Secrets (shared pool only)</h4>
                <p className="text-xs text-gray-400 mb-2">
                  Functions running in the shared pool can access encrypted secrets via <code className="font-mono bg-[#161616] px-1 rounded">context['secrets']</code>.
                </p>
                <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                  <pre className="text-xs text-gray-100 font-mono">{`def handler(input_data, context):
    token = context['secrets']['SLACK_BOT_TOKEN']
    # Use token to call external API...`}</pre>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Schemas side by side */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="card">
            <JSONSchemaEditor
              label="Input Schema *"
              description="Expected input parameters"
              value={formData.input_schema}
              onChange={(schema) => setFormData({ ...formData, input_schema: schema })}
            />
            <div className="mt-2 flex items-center gap-2">
              <span className="text-xs text-gray-500">Preset:</span>
              <select
                className="input text-xs py-1 w-auto"
                value=""
                onChange={(e) => {
                  const preset = SCHEMA_PRESETS[e.target.value];
                  if (preset) setFormData({ ...formData, input_schema: preset.input });
                }}
              >
                <option value="">Select...</option>
                {Object.entries(SCHEMA_PRESETS).map(([key, preset]) => (
                  <option key={key} value={key}>{preset.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="card">
            <JSONSchemaEditor
              label="Output Schema *"
              description="Expected output structure"
              value={formData.output_schema}
              onChange={(schema) => setFormData({ ...formData, output_schema: schema })}
            />
            <div className="mt-2 flex items-center gap-2">
              <span className="text-xs text-gray-500">Preset:</span>
              <select
                className="input text-xs py-1 w-auto"
                value=""
                onChange={(e) => {
                  const preset = SCHEMA_PRESETS[e.target.value];
                  if (preset) setFormData({ ...formData, output_schema: preset.output });
                }}
              >
                <option value="">Select...</option>
                {Object.entries(SCHEMA_PRESETS).map(([key, preset]) => (
                  <option key={key} value={key}>{preset.label}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

      </form>
    </div>
  );
}
