import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, ExternalLink, Settings2, X } from 'lucide-react';
import { apiClient, COMPONENT_SANDBOX, getComponentRenderUrl } from '../lib/api';
import { useFrameTheme } from '../components/chat/frameTheme';
import type { ComponentUpdate, EnabledStoreConfig } from '../types';

type ResourceTab = 'queries' | 'functions' | 'agents' | 'stores';

export function ComponentEditor() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [sourceCode, setSourceCode] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState('private');
  const [enabledQueries, setEnabledQueries] = useState<string[]>([]);
  const [enabledFunctions, setEnabledFunctions] = useState<string[]>([]);
  const [enabledAgents, setEnabledAgents] = useState<string[]>([]);
  const [enabledStores, setEnabledStores] = useState<EnabledStoreConfig[]>([]);
  const [dirty, setDirty] = useState(false);
  const [showResources, setShowResources] = useState(false);
  // Bumped on every save: remounts the preview even when the new render
  // token happens to equal the last one (two saves within a second).
  const [saveCount, setSaveCount] = useState(0);
  const [resourceTab, setResourceTab] = useState<ResourceTab>('queries');

  const { data: component, isLoading } = useQuery({
    queryKey: ['component', namespace, name],
    queryFn: () => apiClient.getComponent(namespace!, name!),
    enabled: !!namespace && !!name,
  });

  // Fixed per render token, so a theme switch reaches the preview by message
  // (useFrameTheme) instead of reloading it.
  const previewRef = useRef<HTMLIFrameElement>(null);
  const previewUrl = useMemo(
    () => (component?.render_token ? getComponentRenderUrl(component.render_token, namespace!, name!) : ''),
    [component?.render_token, namespace, name],
  );
  const onPreviewLoad = useFrameTheme(previewRef);

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

  const { data: stores } = useQuery({
    queryKey: ['stores'],
    queryFn: () => apiClient.listStores(),
    enabled: showResources,
    retry: false,
  });

  useEffect(() => {
    if (component) {
      setSourceCode(component.source_code);
      setTitle(component.title || '');
      setDescription(component.description || '');
      setVisibility(component.visibility);
      setEnabledQueries(component.enabled_queries || []);
      setEnabledFunctions(component.enabled_functions || []);
      setEnabledAgents(component.enabled_agents || []);
      setEnabledStores(component.enabled_stores || []);
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
      setSaveCount((n) => n + 1);
      if (updated.namespace !== namespace || updated.name !== name) {
        navigate(`/components/${updated.namespace}/${updated.name}`, { replace: true });
      }
    },
  });

  const handleSave = useCallback(() => {
    const data: ComponentUpdate = {};
    if (sourceCode !== component?.source_code) data.source_code = sourceCode;
    if (title !== (component?.title || '')) data.title = title || undefined;
    if (description !== (component?.description || '')) data.description = description || undefined;
    if (visibility !== component?.visibility) data.visibility = visibility;

    // Always send resource arrays so they can be updated
    if (JSON.stringify(enabledQueries) !== JSON.stringify(component?.enabled_queries || []))
      data.enabled_queries = enabledQueries;
    if (JSON.stringify(enabledFunctions) !== JSON.stringify(component?.enabled_functions || []))
      data.enabled_functions = enabledFunctions;
    if (JSON.stringify(enabledAgents) !== JSON.stringify(component?.enabled_agents || []))
      data.enabled_agents = enabledAgents;
    if (JSON.stringify(enabledStores) !== JSON.stringify(component?.enabled_stores || []))
      data.enabled_stores = enabledStores;

    if (Object.keys(data).length === 0) return;
    updateMutation.mutate(data);
  }, [sourceCode, title, description, visibility, enabledQueries, enabledFunctions, enabledAgents, enabledStores, component, updateMutation]);

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
    + enabledStores.length;

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
      <div className="flex items-center justify-between px-6 py-3 border-b border-gray-800 bg-surface-0">
        <div className="flex items-center gap-3">
          <Link to="/components" className="text-gray-400 hover:text-gray-100 transition-colors">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-lg font-semibold text-gray-100">{component.title || component.name}</h1>
            <p className="text-xs text-gray-500">{namespace}/{name}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowResources(!showResources)}
            className={`flex items-center gap-1 px-3 py-1.5 text-sm border rounded-lg transition-colors ${
              showResources
                ? 'text-primary-400 border-primary-700 bg-primary-900/20'
                : 'text-gray-400 hover:text-gray-100 border-gray-700'
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
          <a
            href={previewUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-400 hover:text-gray-100 border border-gray-700 rounded-lg transition-colors"
          >
            <ExternalLink className="w-4 h-4" />
            Open
          </a>
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
                className="w-full bg-transparent text-sm text-gray-100 focus:outline-none"
              />
            </div>
            <div className="flex-1">
              <input
                value={description}
                onChange={(e) => { setDescription(e.target.value); setDirty(true); }}
                placeholder="Description"
                className="w-full bg-transparent text-sm text-gray-400 focus:outline-none"
              />
            </div>
            <select
              value={visibility}
              onChange={(e) => { setVisibility(e.target.value); setDirty(true); }}
              className="bg-surface-0 border border-gray-800 rounded text-xs text-gray-400 px-2 py-1"
            >
              <option value="private">Private</option>
              <option value="shared">Shared</option>
              <option value="public">Public</option>
            </select>
          </div>

          {/* The page's HTML: markup, <style> and <script> (window.sinas is there) */}
          <textarea
            value={sourceCode}
            onChange={(e) => { setSourceCode(e.target.value); setDirty(true); }}
            className="flex-1 w-full bg-surface-page text-gray-200 text-sm font-mono p-4 resize-none focus:outline-none"
            spellCheck={false}
          />

        </div>

        {/* Resources panel (toggled) */}
        {showResources && (
          <div className="w-80 flex flex-col bg-surface-0 border-r border-gray-800 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800">
              <span className="text-sm font-medium text-gray-200">Resources</span>
              <button onClick={() => setShowResources(false)} className="text-gray-500 hover:text-gray-100">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Resource tabs */}
            <div className="flex border-b border-gray-800">
              {([
                ['queries', 'Queries'],
                ['functions', 'Functions'],
                ['agents', 'Agents'],
                ['stores', 'Stores'],
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
                        <label key={ref} className="flex items-start gap-2 p-2 hover:bg-hover rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={enabledQueries.includes(ref)}
                            onChange={() => toggleItem(enabledQueries, setEnabledQueries, ref)}
                            className="mt-0.5 w-4 h-4 text-primary-600 border-line rounded focus:ring-primary-500"
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
                        <label key={ref} className="flex items-start gap-2 p-2 hover:bg-hover rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={enabledFunctions.includes(ref)}
                            onChange={() => toggleItem(enabledFunctions, setEnabledFunctions, ref)}
                            className="mt-0.5 w-4 h-4 text-primary-600 border-line rounded focus:ring-primary-500"
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
                        <label key={ref} className="flex items-start gap-2 p-2 hover:bg-hover rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={enabledAgents.includes(ref)}
                            onChange={() => toggleItem(enabledAgents, setEnabledAgents, ref)}
                            className="mt-0.5 w-4 h-4 text-primary-600 border-line rounded focus:ring-primary-500"
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

              {/* Stores tab */}
              {resourceTab === 'stores' && (
                <div className="space-y-1">
                  <p className="text-xs text-gray-500 mb-2">
                    Configure which stores this component can access and the access level for each.
                  </p>
                  {stores && (stores as any[]).length > 0 ? (
                    <div className="space-y-1">
                      {(stores as any[]).map((store: any) => {
                        const ref = `${store.namespace}/${store.name}`;
                        const currentStores = enabledStores;
                        const existing = currentStores.find((s: any) => s.store === ref);
                        const access = existing?.access || 'none';
                        return (
                          <div key={ref} className="flex items-center justify-between p-2 hover:bg-hover rounded">
                            <div className="flex-1 min-w-0">
                              <div className="text-sm font-mono text-gray-200 truncate">{ref}</div>
                              {store.description && (
                                <p className="text-xs text-gray-500 mt-0.5 truncate">{store.description}</p>
                              )}
                            </div>
                            <select
                              value={access}
                              onChange={(e) => {
                                const newAccess = e.target.value as 'readonly' | 'readwrite';
                                let updated: EnabledStoreConfig[];
                                if (e.target.value === 'none') {
                                  updated = currentStores.filter((s) => s.store !== ref);
                                } else if (existing) {
                                  updated = currentStores.map((s) => s.store === ref ? { ...s, access: newAccess } : s);
                                } else {
                                  updated = [...currentStores, { store: ref, access: newAccess }];
                                }
                                setEnabledStores(updated);
                                setDirty(true);
                              }}
                              className="bg-surface-0 border border-gray-800 rounded text-xs text-gray-400 px-2 py-1 ml-2"
                            >
                              <option value="none">None</option>
                              <option value="readonly">Read-only</option>
                              <option value="readwrite">Read-write</option>
                            </select>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-600">No stores available. Create stores first.</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Preview iframe */}
        <div className="flex-1 flex flex-col bg-surface-0">
          <div className="px-4 py-2 bg-surface-input border-b border-gray-800 text-xs text-gray-500">
            Preview {dirty && '(save to update)'}
          </div>
          <iframe
            key={saveCount}
            ref={previewRef}
            onLoad={onPreviewLoad}
            src={previewUrl}
            sandbox={COMPONENT_SANDBOX}
            className="flex-1 w-full border-0"
            title="Component Preview"
          />
        </div>
      </div>
    </div>
  );
}
