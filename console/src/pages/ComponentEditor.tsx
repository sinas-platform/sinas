import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, RefreshCw, ExternalLink, AlertCircle, Settings2, X } from 'lucide-react';
import { apiClient, getComponentRenderUrl } from '../lib/api';
import type { ComponentUpdate } from '../types';

type ResourceTab = 'queries' | 'functions' | 'agents' | 'states';

export function ComponentEditor() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [sourceCode, setSourceCode] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [cssOverrides, setCssOverrides] = useState('');
  const [visibility, setVisibility] = useState('private');
  const [enabledQueries, setEnabledQueries] = useState<string[]>([]);
  const [enabledFunctions, setEnabledFunctions] = useState<string[]>([]);
  const [enabledAgents, setEnabledAgents] = useState<string[]>([]);
  const [stateNamespacesReadonly, setStateNamespacesReadonly] = useState<string[]>([]);
  const [stateNamespacesReadwrite, setStateNamespacesReadwrite] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [showResources, setShowResources] = useState(false);
  const [resourceTab, setResourceTab] = useState<ResourceTab>('queries');

  // Forward auth token to component iframes via postMessage
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type === 'sinas:ready') {
        const token = localStorage.getItem('auth_token');
        if (token && event.source) {
          (event.source as Window).postMessage(
            { type: 'sinas:auth', token },
            '*'
          );
        }
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  const { data: component, isLoading } = useQuery({
    queryKey: ['component', namespace, name],
    queryFn: () => apiClient.getComponent(namespace!, name!),
    enabled: !!namespace && !!name,
    refetchInterval: (query) => {
      const status = query.state.data?.compile_status;
      if (status === 'pending' || status === 'compiling') return 2000;
      return false;
    },
  });

  // Fetch available resources (lazy — only when panel is open)
  const { data: queries } = useQuery({
    queryKey: ['queries'],
    queryFn: () => apiClient.listQueries(),
    enabled: showResources,
    retry: false,
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    enabled: showResources,
    retry: false,
  });

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAgents(),
    enabled: showResources,
    retry: false,
  });

  const { data: states } = useQuery({
    queryKey: ['states'],
    queryFn: () => apiClient.listStates(),
    enabled: showResources,
    retry: false,
  });

  useEffect(() => {
    if (component) {
      setSourceCode(component.source_code);
      setTitle(component.title || '');
      setDescription(component.description || '');
      setCssOverrides(component.css_overrides || '');
      setVisibility(component.visibility);
      setEnabledQueries(component.enabled_queries || []);
      setEnabledFunctions(component.enabled_functions || []);
      setEnabledAgents(component.enabled_agents || []);
      setStateNamespacesReadonly(component.state_namespaces_readonly || []);
      setStateNamespacesReadwrite(component.state_namespaces_readwrite || []);
      setDirty(false);
    }
  }, [component]);

  const updateMutation = useMutation({
    mutationFn: (data: ComponentUpdate) =>
      apiClient.updateComponent(namespace!, name!, data),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ['components'] });
      queryClient.invalidateQueries({ queryKey: ['component', namespace, name] });
      setDirty(false);
      if (updated.namespace !== namespace || updated.name !== name) {
        navigate(`/components/${updated.namespace}/${updated.name}`, { replace: true });
      }
    },
  });

  const compileMutation = useMutation({
    mutationFn: () => apiClient.compileComponent(namespace!, name!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['component', namespace, name] });
    },
  });

  const handleSave = useCallback(() => {
    const data: ComponentUpdate = {};
    if (sourceCode !== component?.source_code) data.source_code = sourceCode;
    if (title !== (component?.title || '')) data.title = title || undefined;
    if (description !== (component?.description || '')) data.description = description || undefined;
    if (cssOverrides !== (component?.css_overrides || '')) data.css_overrides = cssOverrides || undefined;
    if (visibility !== component?.visibility) data.visibility = visibility;

    // Always send resource arrays so they can be updated
    if (JSON.stringify(enabledQueries) !== JSON.stringify(component?.enabled_queries || []))
      data.enabled_queries = enabledQueries;
    if (JSON.stringify(enabledFunctions) !== JSON.stringify(component?.enabled_functions || []))
      data.enabled_functions = enabledFunctions;
    if (JSON.stringify(enabledAgents) !== JSON.stringify(component?.enabled_agents || []))
      data.enabled_agents = enabledAgents;
    if (JSON.stringify(stateNamespacesReadonly) !== JSON.stringify(component?.state_namespaces_readonly || []))
      data.state_namespaces_readonly = stateNamespacesReadonly;
    if (JSON.stringify(stateNamespacesReadwrite) !== JSON.stringify(component?.state_namespaces_readwrite || []))
      data.state_namespaces_readwrite = stateNamespacesReadwrite;

    if (Object.keys(data).length === 0) return;
    updateMutation.mutate(data);
  }, [sourceCode, title, description, cssOverrides, visibility, enabledQueries, enabledFunctions, enabledAgents, stateNamespacesReadonly, stateNamespacesReadwrite, component, updateMutation]);

  // Ctrl+S save shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        if (dirty) handleSave();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [dirty, handleSave]);

  // Count total enabled resources for the badge
  const resourceCount = enabledQueries.length + enabledFunctions.length + enabledAgents.length
    + stateNamespacesReadonly.length + stateNamespacesReadwrite.length;

  const getStatusBadge = (status: string) => {
    const colors: Record<string, string> = {
      success: 'bg-green-900/30 text-green-400 border-green-800',
      pending: 'bg-yellow-900/30 text-yellow-400 border-yellow-800',
      compiling: 'bg-blue-900/30 text-blue-400 border-blue-800',
      error: 'bg-red-900/30 text-red-400 border-red-800',
    };
    return colors[status] || 'bg-gray-900/30 text-gray-400 border-gray-800';
  };

  // Helper to toggle item in array
  const toggleItem = (
    arr: string[],
    setter: (v: string[]) => void,
    item: string,
  ) => {
    const next = arr.includes(item)
      ? arr.filter(i => i !== item)
      : [...arr, item];
    setter(next);
    setDirty(true);
  };

  // Get unique state namespaces from existing states
  const stateNamespaces = states
    ? Array.from(new Set((states as any[]).map((s: any) => s.namespace))).sort()
    : [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (!component) {
    return (
      <div className="p-6 text-gray-400">Component not found</div>
    );
  }

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-gray-800 bg-[#0d0d0d]">
        <div className="flex items-center gap-3">
          <Link to="/components" className="text-gray-400 hover:text-white transition-colors">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-white">{component.title || component.name}</h1>
            <p className="text-xs text-gray-500">{namespace}/{name} &middot; v{component.version}</p>
          </div>
          <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getStatusBadge(component.compile_status)}`}>
            {component.compile_status}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowResources(!showResources)}
            className={`flex items-center gap-1 px-3 py-1.5 text-sm border rounded-lg transition-colors ${
              showResources
                ? 'text-primary-400 border-primary-700 bg-primary-900/20'
                : 'text-gray-400 hover:text-white border-gray-700'
            }`}
          >
            <Settings2 className="w-4 h-4" />
            Resources
            {resourceCount > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-xs bg-primary-600 text-white rounded-full">
                {resourceCount}
              </span>
            )}
          </button>
          {component.compile_status === 'success' && (
            <a
              href={getComponentRenderUrl(component.render_token!, namespace!, name!)}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-400 hover:text-white border border-gray-700 rounded-lg transition-colors"
            >
              <ExternalLink className="w-4 h-4" />
              Preview
            </a>
          )}
          <button
            onClick={() => compileMutation.mutate()}
            disabled={compileMutation.isPending}
            className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-400 hover:text-white border border-gray-700 rounded-lg transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${compileMutation.isPending ? 'animate-spin' : ''}`} />
            Compile
          </button>
          <button
            onClick={handleSave}
            disabled={!dirty || updateMutation.isPending}
            className="flex items-center gap-1 px-3 py-1.5 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
          >
            <Save className="w-4 h-4" />
            {updateMutation.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Compile errors */}
      {component.compile_status === 'error' && component.compile_errors?.length && (
        <div className="px-6 py-2 bg-red-900/10 border-b border-red-900/30">
          <div className="flex items-center gap-2 text-red-400 text-sm">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <div>
              {component.compile_errors.map((err, i) => (
                <div key={i}>
                  {err.location ? `Line ${err.location.line}: ` : ''}{err.text}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Code editor */}
        <div className="flex-1 flex flex-col border-r border-gray-800">
          {/* Metadata bar */}
          <div className="px-4 py-2 border-b border-gray-800 flex gap-4">
            <div className="flex-1">
              <input
                value={title}
                onChange={(e) => { setTitle(e.target.value); setDirty(true); }}
                placeholder="Title"
                className="w-full bg-transparent text-sm text-white focus:outline-none"
              />
            </div>
            <select
              value={visibility}
              onChange={(e) => { setVisibility(e.target.value); setDirty(true); }}
              className="bg-[#0d0d0d] border border-gray-800 rounded text-xs text-gray-400 px-2 py-1"
            >
              <option value="private">Private</option>
              <option value="shared">Shared</option>
              <option value="public">Public</option>
            </select>
          </div>

          {/* Source code textarea */}
          <textarea
            value={sourceCode}
            onChange={(e) => { setSourceCode(e.target.value); setDirty(true); }}
            className="flex-1 w-full bg-[#0a0a0a] text-gray-200 text-sm font-mono p-4 resize-none focus:outline-none"
            spellCheck={false}
          />

          {/* CSS overrides */}
          <div className="border-t border-gray-800">
            <div className="px-4 py-1 text-xs text-gray-500">CSS Overrides</div>
            <textarea
              value={cssOverrides}
              onChange={(e) => { setCssOverrides(e.target.value); setDirty(true); }}
              rows={3}
              className="w-full bg-[#0a0a0a] text-gray-300 text-xs font-mono px-4 py-2 resize-none focus:outline-none"
              placeholder="body { background: #1a1a2e; }"
              spellCheck={false}
            />
          </div>
        </div>

        {/* Resources panel (toggled) */}
        {showResources && (
          <div className="w-80 flex flex-col bg-[#0d0d0d] border-r border-gray-800 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800">
              <span className="text-sm font-medium text-gray-200">Resources</span>
              <button onClick={() => setShowResources(false)} className="text-gray-500 hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Resource tabs */}
            <div className="flex border-b border-gray-800">
              {([
                ['queries', 'Queries'],
                ['functions', 'Functions'],
                ['agents', 'Agents'],
                ['states', 'States'],
              ] as [ResourceTab, string][]).map(([tab, label]) => (
                <button
                  key={tab}
                  onClick={() => setResourceTab(tab)}
                  className={`flex-1 px-2 py-2 text-xs font-medium transition-colors ${
                    resourceTab === tab
                      ? 'text-primary-400 border-b-2 border-primary-600'
                      : 'text-gray-500 hover:text-gray-300'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-y-auto p-3">

              {/* Queries tab */}
              {resourceTab === 'queries' && (
                <div className="space-y-1">
                  <p className="text-xs text-gray-500 mb-2">
                    Select queries this component can execute via the proxy.
                  </p>
                  {queries && (queries as any[]).length > 0 ? (
                    (queries as any[]).map((q: any) => {
                      const ref = `${q.namespace}/${q.name}`;
                      return (
                        <label key={ref} className="flex items-start gap-2 p-2 hover:bg-white/5 rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={enabledQueries.includes(ref)}
                            onChange={() => toggleItem(enabledQueries, setEnabledQueries, ref)}
                            className="mt-0.5 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-mono text-gray-200 truncate">{ref}</div>
                            <span className={`inline-block mt-0.5 px-1.5 py-0.5 text-xs font-medium rounded ${
                              q.operation === 'read'
                                ? 'bg-blue-900/30 text-blue-400'
                                : 'bg-orange-900/30 text-orange-400'
                            }`}>
                              {q.operation}
                            </span>
                          </div>
                        </label>
                      );
                    })
                  ) : (
                    <p className="text-xs text-gray-600">No queries available</p>
                  )}
                </div>
              )}

              {/* Functions tab */}
              {resourceTab === 'functions' && (
                <div className="space-y-1">
                  <p className="text-xs text-gray-500 mb-2">
                    Select functions this component can execute via the proxy.
                  </p>
                  {functions && (functions as any[]).length > 0 ? (
                    (functions as any[]).map((fn: any) => {
                      const ref = `${fn.namespace}/${fn.name}`;
                      return (
                        <label key={ref} className="flex items-start gap-2 p-2 hover:bg-white/5 rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={enabledFunctions.includes(ref)}
                            onChange={() => toggleItem(enabledFunctions, setEnabledFunctions, ref)}
                            className="mt-0.5 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-mono text-gray-200 truncate">{ref}</div>
                            {fn.description && (
                              <p className="text-xs text-gray-500 mt-0.5 truncate">{fn.description}</p>
                            )}
                          </div>
                        </label>
                      );
                    })
                  ) : (
                    <p className="text-xs text-gray-600">No functions available</p>
                  )}
                </div>
              )}

              {/* Agents tab */}
              {resourceTab === 'agents' && (
                <div className="space-y-1">
                  <p className="text-xs text-gray-500 mb-2">
                    Select agents this component can create chats with.
                  </p>
                  {agents && (agents as any[]).length > 0 ? (
                    (agents as any[]).map((a: any) => {
                      const ref = `${a.namespace}/${a.name}`;
                      return (
                        <label key={ref} className="flex items-start gap-2 p-2 hover:bg-white/5 rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={enabledAgents.includes(ref)}
                            onChange={() => toggleItem(enabledAgents, setEnabledAgents, ref)}
                            className="mt-0.5 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-mono text-gray-200 truncate">{ref}</div>
                            {a.description && (
                              <p className="text-xs text-gray-500 mt-0.5 truncate">{a.description}</p>
                            )}
                          </div>
                        </label>
                      );
                    })
                  ) : (
                    <p className="text-xs text-gray-600">No agents available</p>
                  )}
                </div>
              )}

              {/* States tab */}
              {resourceTab === 'states' && (
                <div className="space-y-4">
                  {/* Read-only */}
                  <div>
                    <h4 className="text-xs font-semibold text-gray-300 mb-1">Read-only</h4>
                    <p className="text-xs text-gray-500 mb-2">
                      Component can read states from these namespaces.
                    </p>
                    {stateNamespaces.length > 0 ? (
                      <div className="space-y-1">
                        {stateNamespaces.map((ns: string) => (
                          <label key={`ro-${ns}`} className="flex items-center gap-2 p-2 hover:bg-white/5 rounded cursor-pointer">
                            <input
                              type="checkbox"
                              checked={stateNamespacesReadonly.includes(ns)}
                              onChange={() => toggleItem(stateNamespacesReadonly, setStateNamespacesReadonly, ns)}
                              className="w-4 h-4 text-blue-600 border-white/10 rounded focus:ring-blue-500"
                            />
                            <span className="text-sm font-mono text-gray-200">{ns}</span>
                          </label>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-gray-600">No state namespaces found</p>
                    )}
                  </div>

                  {/* Read-write */}
                  <div>
                    <h4 className="text-xs font-semibold text-gray-300 mb-1">Read-write</h4>
                    <p className="text-xs text-gray-500 mb-2">
                      Component can read, write, and delete states in these namespaces.
                    </p>
                    {stateNamespaces.length > 0 ? (
                      <div className="space-y-1">
                        {stateNamespaces.map((ns: string) => (
                          <label key={`rw-${ns}`} className="flex items-center gap-2 p-2 hover:bg-white/5 rounded cursor-pointer">
                            <input
                              type="checkbox"
                              checked={stateNamespacesReadwrite.includes(ns)}
                              onChange={() => toggleItem(stateNamespacesReadwrite, setStateNamespacesReadwrite, ns)}
                              className="w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                            />
                            <span className="text-sm font-mono text-gray-200">{ns}</span>
                          </label>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-gray-600">No state namespaces found</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Preview iframe */}
        <div className="flex-1 flex flex-col bg-white">
          <div className="px-4 py-2 bg-[#111111] border-b border-gray-800 text-xs text-gray-500">
            Preview {component.compile_status !== 'success' && '(compile required)'}
          </div>
          {component.compile_status === 'success' ? (
            <iframe
              src={getComponentRenderUrl(component.render_token!, namespace!, name!)}
              className="flex-1 w-full border-0"
              title="Component Preview"
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">
              Compile the component to see a preview
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
