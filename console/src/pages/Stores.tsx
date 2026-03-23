import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import {
  Plus, Trash2, Edit2, HardDrive, Shield, Lock, Unlock, X,
  ChevronRight, ChevronDown, Search, Tag, Users, Eye, EyeOff, Edit,
} from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';

export function Stores() {
  const queryClient = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editingStore, setEditingStore] = useState<any>(null);
  const [expandedStores, setExpandedStores] = useState<Set<string>>(new Set());

  const { data: stores, isLoading } = useQuery({
    queryKey: ['stores'],
    queryFn: () => apiClient.listStores(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteStore(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stores'] });
    },
  });

  const handleDelete = (store: any) => {
    if (confirm(`Are you sure you want to delete store "${store.namespace}/${store.name}"?`)) {
      deleteMutation.mutate({ namespace: store.namespace, name: store.name });
    }
  };

  const openEditModal = (store: any) => {
    setEditingStore(store);
    setShowModal(true);
  };

  const openCreateModal = () => {
    setEditingStore(null);
    setShowModal(true);
  };

  const toggleExpanded = (storeKey: string) => {
    setExpandedStores(prev => {
      const next = new Set(prev);
      if (next.has(storeKey)) {
        next.delete(storeKey);
      } else {
        next.add(storeKey);
      }
      return next;
    });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">State Stores</h1>
          <p className="text-gray-400 mt-1">Define typed state stores with schemas and explore state data</p>
        </div>
        <button
          onClick={openCreateModal}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          New Store
        </button>
      </div>

      {/* Stores List */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
        </div>
      ) : stores && stores.length > 0 ? (
        <div className="grid gap-4">
          {stores.map((store: any) => {
            const storeKey = `${store.namespace}/${store.name}`;
            const isExpanded = expandedStores.has(storeKey);

            return (
              <div key={store.id} className="card transition-colors">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center space-x-2 mb-2">
                      <button
                        onClick={() => toggleExpanded(storeKey)}
                        className="text-gray-400 hover:text-gray-200 p-0.5 -ml-1"
                      >
                        {isExpanded ? (
                          <ChevronDown className="w-4 h-4" />
                        ) : (
                          <ChevronRight className="w-4 h-4" />
                        )}
                      </button>
                      <HardDrive className="w-5 h-5 text-blue-500" />
                      <h3
                        className="text-lg font-semibold text-gray-100 cursor-pointer hover:text-white"
                        onClick={() => toggleExpanded(storeKey)}
                      >
                        {storeKey}
                      </h3>
                      <span className={`px-2 py-0.5 text-xs font-medium rounded ${
                        store.strict
                          ? 'text-amber-400 bg-amber-900/20'
                          : 'text-green-400 bg-green-900/20'
                      }`}>
                        {store.strict ? 'strict' : 'freeform'}
                      </span>
                      {store.encrypted && (
                        <span className="px-2 py-0.5 text-xs font-medium rounded flex items-center gap-1 text-amber-500 bg-amber-900/20">
                          <Shield className="w-3 h-3" />
                          encrypted
                        </span>
                      )}
                      <span className={`px-2 py-0.5 text-xs font-medium rounded flex items-center gap-1 ${
                        store.default_visibility === 'shared'
                          ? 'text-blue-400 bg-blue-900/20'
                          : 'text-gray-400 bg-[#0d0d0d]'
                      }`}>
                        {store.default_visibility === 'shared' ? (
                          <Unlock className="w-3 h-3" />
                        ) : (
                          <Lock className="w-3 h-3" />
                        )}
                        {store.default_visibility}
                      </span>
                      <StoreStateCount namespace={store.namespace} name={store.name} />
                    </div>
                    {store.description && (
                      <p className="text-sm text-gray-400 mb-2">{store.description}</p>
                    )}
                    {store.schema && Object.keys(store.schema).length > 0 && (
                      <details className="text-xs mt-2">
                        <summary className="cursor-pointer text-gray-500 hover:text-gray-300">
                          View schema
                        </summary>
                        <pre className="mt-2 p-2 bg-[#0d0d0d] rounded border border-white/[0.06] overflow-x-auto text-gray-300">
                          {JSON.stringify(store.schema, null, 2)}
                        </pre>
                      </details>
                    )}
                    <div className="text-xs text-gray-500 mt-2">
                      Created {new Date(store.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="flex items-center space-x-2 ml-4">
                    <button
                      onClick={() => openEditModal(store)}
                      className="btn btn-sm btn-secondary"
                      title="Edit"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(store)}
                      className="btn btn-sm btn-danger"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Expanded states panel */}
                {isExpanded && (
                  <StoreStatesPanel
                    namespace={store.namespace}
                    name={store.name}
                    storeEncrypted={store.encrypted}
                  />
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <HardDrive className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No stores yet</h3>
          <p className="text-gray-400 mb-4">Create your first store to define typed state storage</p>
        </div>
      )}

      {/* Create/Edit Store Modal */}
      {showModal && (
        <StoreModal
          store={editingStore}
          onClose={() => {
            setShowModal(false);
            setEditingStore(null);
          }}
        />
      )}
    </div>
  );
}

/* Small badge showing state count for a store */
function StoreStateCount({ namespace, name }: { namespace: string; name: string }) {
  const { data: states } = useQuery({
    queryKey: ['store-states', namespace, name, 'me'],
    queryFn: () => apiClient.listStoreStates(namespace, name, { owner: 'me' }),
    retry: false,
  });

  const count = states?.length ?? 0;
  if (count === 0) return null;

  return (
    <span className="px-2 py-0.5 text-xs font-medium rounded text-gray-400 bg-[#0d0d0d]">
      {count} state{count !== 1 ? 's' : ''}
    </span>
  );
}

/* Expanded panel showing states inside a store */
function StoreStatesPanel({
  namespace,
  name,
  storeEncrypted,
}: {
  namespace: string;
  name: string;
  storeEncrypted: boolean;
}) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [ownerFilter, setOwnerFilter] = useState<string>('me');
  const [showStateModal, setShowStateModal] = useState(false);
  const [editingState, setEditingState] = useState<any>(null);
  const [revealedValues, setRevealedValues] = useState<Record<string, boolean>>({});

  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: () => apiClient.listUsers(),
    retry: false,
  });

  const { data: states, isLoading } = useQuery({
    queryKey: ['store-states', namespace, name, ownerFilter],
    queryFn: () => apiClient.listStoreStates(namespace, name, { owner: ownerFilter }),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (key: string) => apiClient.deleteStoreState(namespace, name, key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['store-states', namespace, name] });
    },
  });

  const handleDeleteState = (state: any) => {
    if (confirm(`Delete state "${state.key}"?`)) {
      deleteMutation.mutate(state.key);
    }
  };

  const filtered = (states || []).filter((s: any) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      s.key?.toLowerCase().includes(q) ||
      s.description?.toLowerCase().includes(q) ||
      s.user_email?.toLowerCase().includes(q)
    );
  });

  return (
    <div className="mt-4 pt-4 border-t border-white/[0.06]">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3 flex-1">
          <h4 className="text-sm font-semibold text-gray-300">States</h4>
          <select
            value={ownerFilter}
            onChange={e => setOwnerFilter(e.target.value)}
            className="input text-xs py-1 w-auto"
          >
            <option value="me">My states</option>
            <option value="all">All users</option>
            {users && users.length > 0 && (
              <>
                <option disabled>───</option>
                {users.map((u: any) => (
                  <option key={u.id} value={u.id}>{u.email}</option>
                ))}
              </>
            )}
          </select>
          <div className="relative max-w-xs flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500 pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter states..."
              className="input !py-1 !pl-8 !text-xs"
            />
          </div>
        </div>
        <button
          onClick={() => { setEditingState(null); setShowStateModal(true); }}
          className="btn btn-sm btn-primary flex items-center gap-1"
        >
          <Plus className="w-3.5 h-3.5" />
          New State
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-4 text-gray-500 text-sm">Loading states...</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-4 text-gray-500 text-sm">
          {states && states.length > 0 ? 'No states match filter' : 'No states in this store'}
        </div>
      ) : (
        <div className="space-y-1.5">
          {filtered.map((state: any) => (
            <div
              key={state.id || state.key}
              className="border border-white/[0.06] rounded-lg p-3 hover:bg-white/5 transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="text-sm font-mono font-semibold text-gray-100">
                      {state.key}
                    </span>
                    {state.user_email && (
                      <span className="px-2 py-0.5 text-xs font-medium rounded text-gray-400 bg-[#0d0d0d]">
                        {state.user_email}
                      </span>
                    )}
                    <span className={`px-2 py-0.5 text-xs font-medium rounded flex items-center gap-1 ${
                      state.visibility === 'shared'
                        ? 'text-blue-400 bg-blue-900/20'
                        : 'text-gray-400 bg-[#0d0d0d]'
                    }`}>
                      {state.visibility === 'shared' ? (
                        <Users className="w-3 h-3" />
                      ) : (
                        <Lock className="w-3 h-3" />
                      )}
                      {state.visibility}
                    </span>
                    {state.encrypted && (
                      <span className="px-2 py-0.5 text-xs font-medium rounded flex items-center gap-1 text-amber-500 bg-amber-900/20">
                        <Shield className="w-3 h-3" />
                        encrypted
                      </span>
                    )}
                    {state.relevance_score != null && state.relevance_score !== 1 && (
                      <span className="px-2 py-0.5 bg-[#161616] text-gray-400 text-xs font-medium rounded">
                        Score: {state.relevance_score}
                      </span>
                    )}
                  </div>
                  {state.description && (
                    <p className="text-xs text-gray-400 mb-1">{state.description}</p>
                  )}
                  {state.tags && state.tags.length > 0 && (
                    <div className="flex items-center gap-1.5 flex-wrap mb-1">
                      {state.tags.map((tag: string, idx: number) => (
                        <span key={idx} className="px-1.5 py-0.5 bg-blue-900/30 text-blue-400 text-xs rounded flex items-center gap-1">
                          <Tag className="w-2.5 h-2.5" />
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="mt-1">
                    {state.encrypted ? (
                      <div className="text-xs">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-gray-500">
                            {revealedValues[state.id || state.key]
                              ? JSON.stringify(state.value, null, 2).slice(0, 80)
                              : '••••••••'}
                          </span>
                          <button
                            onClick={() => setRevealedValues(prev => ({
                              ...prev,
                              [state.id || state.key]: !prev[state.id || state.key],
                            }))}
                            className="p-1 text-gray-500 hover:text-gray-300 rounded"
                            title={revealedValues[state.id || state.key] ? 'Hide value' : 'Reveal value'}
                          >
                            {revealedValues[state.id || state.key] ? (
                              <EyeOff className="w-3 h-3" />
                            ) : (
                              <Eye className="w-3 h-3" />
                            )}
                          </button>
                        </div>
                        {revealedValues[state.id || state.key] && (
                          <pre className="mt-1.5 p-2 bg-[#0d0d0d] rounded border border-white/[0.06] overflow-x-auto text-gray-300">
                            {JSON.stringify(state.value, null, 2)}
                          </pre>
                        )}
                      </div>
                    ) : (
                      <details className="text-xs">
                        <summary className="cursor-pointer text-gray-500 hover:text-gray-300">
                          View value
                        </summary>
                        <pre className="mt-1.5 p-2 bg-[#0d0d0d] rounded border border-white/[0.06] overflow-x-auto text-gray-300">
                          {JSON.stringify(state.value, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                  <div className="mt-1.5 text-xs text-gray-500">
                    Updated {new Date(state.updated_at).toLocaleString()}
                    {state.expires_at && (
                      <span className="ml-2">
                        Expires {new Date(state.expires_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1.5 ml-3">
                  <button
                    onClick={() => { setEditingState(state); setShowStateModal(true); }}
                    className="p-1.5 text-gray-400 hover:text-gray-100 hover:bg-white/10 rounded"
                    title="Edit state"
                  >
                    <Edit className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => handleDeleteState(state)}
                    className="p-1.5 text-red-600 hover:text-red-400 hover:bg-red-900/20 rounded"
                    title="Delete state"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* State create/edit modal */}
      {showStateModal && (
        <StateModal
          state={editingState}
          storeNamespace={namespace}
          storeName={name}
          storeEncrypted={storeEncrypted}
          onClose={() => {
            setShowStateModal(false);
            setEditingState(null);
          }}
        />
      )}
    </div>
  );
}

/* Modal for creating/editing a state within a store */
function StateModal({
  state,
  storeNamespace,
  storeName,
  storeEncrypted,
  onClose,
}: {
  state: any;
  storeNamespace: string;
  storeName: string;
  storeEncrypted: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState({
    key: state?.key || '',
    value: state?.value ? JSON.stringify(state.value, null, 2) : '{}',
    visibility: state?.visibility || 'private',
    encrypted: state?.encrypted ?? storeEncrypted,
    description: state?.description || '',
    tags: state?.tags?.join(', ') || '',
    relevance_score: state?.relevance_score ?? 1.0,
    expires_at: state?.expires_at ? new Date(state.expires_at).toISOString().slice(0, 16) : '',
  });

  const saveMutation = useMutation({
    mutationFn: async (data: any) => {
      const payload: any = {
        value: JSON.parse(data.value),
        visibility: data.visibility,
        encrypted: data.encrypted,
        description: data.description || undefined,
        tags: data.tags ? data.tags.split(',').map((t: string) => t.trim()).filter(Boolean) : [],
        relevance_score: parseFloat(data.relevance_score),
        expires_at: data.expires_at || null,
      };

      if (state) {
        return apiClient.updateStoreState(storeNamespace, storeName, state.key, payload);
      } else {
        return apiClient.createStoreState(storeNamespace, storeName, {
          key: data.key,
          ...payload,
        });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['store-states', storeNamespace, storeName] });
      onClose();
    },
  });

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={onClose} />
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div
          className="bg-[#161616] rounded-lg max-w-3xl w-full max-h-[90vh] overflow-y-auto pointer-events-auto"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="sticky top-0 bg-[#161616] border-b border-white/[0.06] px-6 py-4 flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-gray-100">
                {state ? 'Edit State' : 'New State'}
              </h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Store: {storeNamespace}/{storeName}
              </p>
            </div>
            <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
              <X className="w-5 h-5" />
            </button>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              saveMutation.mutate(formData);
            }}
            className="p-6 space-y-4"
          >
            <div>
              <label className="label">Key *</label>
              <input
                type="text"
                value={formData.key}
                onChange={(e) => setFormData({ ...formData, key: e.target.value })}
                className="input"
                required
                disabled={!!state}
                placeholder="e.g. theme"
              />
            </div>

            <div>
              <label className="label">Value (JSON) *</label>
              <CodeEditor
                value={formData.value}
                language="json"
                placeholder='{"example": "value"}'
                onChange={(e) => setFormData({ ...formData, value: e.target.value })}
                padding={15}
                data-color-mode="dark"
                style={{
                  backgroundColor: '#111111',
                  fontFamily: 'ui-monospace, monospace',
                  fontSize: 13,
                  color: '#ededed',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: '0.375rem',
                  minHeight: '150px',
                }}
              />
            </div>

            <div>
              <label className="label">Visibility</label>
              <select
                value={formData.visibility}
                onChange={(e) => setFormData({ ...formData, visibility: e.target.value })}
                className="input"
              >
                <option value="private">Private (only you)</option>
                <option value="shared">Shared (with namespace permissions)</option>
              </select>
            </div>

            <div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formData.encrypted}
                  onChange={(e) => setFormData({ ...formData, encrypted: e.target.checked })}
                  className="w-4 h-4 rounded border-white/20 bg-[#111] text-amber-500 focus:ring-amber-500/30"
                />
                <span className="flex items-center gap-1.5 text-sm text-gray-300">
                  <Shield className="w-4 h-4 text-amber-500" />
                  Encrypted
                </span>
              </label>
            </div>

            <div>
              <label className="label">Description</label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                className="input"
                rows={2}
                placeholder="Describe this state..."
              />
            </div>

            <div>
              <label className="label">Tags (comma-separated)</label>
              <input
                type="text"
                value={formData.tags}
                onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                className="input"
                placeholder="user, preferences, theme"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Relevance Score (0-1)</label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="1"
                  value={formData.relevance_score}
                  onChange={(e) => setFormData({ ...formData, relevance_score: e.target.value as any })}
                  className="input"
                />
              </div>
              <div>
                <label className="label">Expires At</label>
                <input
                  type="datetime-local"
                  value={formData.expires_at}
                  onChange={(e) => setFormData({ ...formData, expires_at: e.target.value })}
                  className="input"
                />
              </div>
            </div>

            <div className="flex gap-2 justify-end pt-4 border-t border-white/[0.06]">
              <button type="button" onClick={onClose} className="btn btn-secondary">
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={saveMutation.isPending}
              >
                {saveMutation.isPending ? 'Saving...' : state ? 'Update' : 'Create'}
              </button>
            </div>

            {saveMutation.isError && (
              <div className="p-3 bg-red-900/20 border border-red-800/30 rounded text-sm text-red-300">
                Error: {(saveMutation.error as any)?.message || 'Failed to save state'}
              </div>
            )}
          </form>
        </div>
      </div>
    </>
  );
}

/* Store create/edit modal */
function StoreModal({
  store,
  onClose,
}: {
  store: any;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState({
    namespace: store?.namespace || 'default',
    name: store?.name || '',
    description: store?.description || '',
    schema: store?.schema && Object.keys(store.schema).length > 0 ? store.schema : { type: 'object', properties: {} },
    strict: store?.strict ?? false,
    default_visibility: store?.default_visibility || 'private',
    encrypted: store?.encrypted ?? false,
  });

  const saveMutation = useMutation({
    mutationFn: async (data: any) => {
      const payload: any = {
        ...data,
        schema: data.schema || {},
      };

      // Remove empty schema (no properties defined)
      if (!payload.schema?.properties || Object.keys(payload.schema.properties).length === 0) {
        delete payload.schema;
      }

      if (store) {
        const { namespace, name, ...updatePayload } = payload;
        return apiClient.updateStore(store.namespace, store.name, updatePayload);
      } else {
        return apiClient.createStore(payload);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stores'] });
      onClose();
    },
  });

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={onClose} />
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div
          className="bg-[#161616] rounded-lg max-w-3xl w-full max-h-[90vh] overflow-y-auto pointer-events-auto"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="sticky top-0 bg-[#161616] border-b border-white/[0.06] px-6 py-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold text-gray-100">
              {store ? 'Edit Store' : 'New Store'}
            </h2>
            <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
              <X className="w-5 h-5" />
            </button>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              saveMutation.mutate(formData);
            }}
            className="p-6 space-y-4"
          >
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Namespace *</label>
                <input
                  type="text"
                  value={formData.namespace}
                  onChange={(e) => setFormData({ ...formData, namespace: e.target.value })}
                  className="input"
                  required
                  disabled={!!store}
                  placeholder="default"
                  pattern="[a-zA-Z][a-zA-Z0-9_-]*"
                  title="Must start with a letter, then letters, numbers, hyphens, and underscores"
                />
              </div>
              <div>
                <label className="label">Name *</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="input"
                  required
                  disabled={!!store}
                  placeholder="e.g. user-preferences"
                  pattern="[a-zA-Z][a-zA-Z0-9_-]*"
                  title="Must start with a letter, then letters, numbers, hyphens, and underscores"
                />
              </div>
            </div>

            <div>
              <label className="label">Description</label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                className="input"
                rows={2}
                placeholder="Describe this store..."
              />
            </div>

            <JSONSchemaEditor
              value={formData.schema}
              onChange={(schema) => setFormData({ ...formData, schema })}
              label="Schema (JSON Schema)"
              description="JSON Schema to validate state values. When strict mode is enabled, all values must match this schema."
            />

            <div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formData.strict}
                  onChange={(e) => setFormData({ ...formData, strict: e.target.checked })}
                  className="w-4 h-4 rounded border-white/20 bg-[#111] text-amber-500 focus:ring-amber-500/30"
                />
                <span className="text-sm text-gray-300">
                  Strict mode
                </span>
              </label>
              <p className="text-xs text-gray-500 mt-1 ml-6">
                When enabled, all state values must conform to the defined schema
              </p>
            </div>

            <div>
              <label className="label">Default Visibility</label>
              <select
                value={formData.default_visibility}
                onChange={(e) => setFormData({ ...formData, default_visibility: e.target.value })}
                className="input"
              >
                <option value="private">Private (only owner)</option>
                <option value="shared">Shared (with namespace permissions)</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">
                Default visibility for new states created in this store
              </p>
            </div>

            <div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formData.encrypted}
                  onChange={(e) => setFormData({ ...formData, encrypted: e.target.checked })}
                  className="w-4 h-4 rounded border-white/20 bg-[#111] text-amber-500 focus:ring-amber-500/30"
                />
                <span className="flex items-center gap-1.5 text-sm text-gray-300">
                  <Shield className="w-4 h-4 text-amber-500" />
                  Encrypted
                </span>
              </label>
              <p className="text-xs text-gray-500 mt-1 ml-6">
                Values in this store will be encrypted at rest using Fernet encryption
              </p>
            </div>

            <div className="flex gap-2 justify-end pt-4 border-t border-white/[0.06]">
              <button type="button" onClick={onClose} className="btn btn-secondary">
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={saveMutation.isPending}
              >
                {saveMutation.isPending ? 'Saving...' : store ? 'Update' : 'Create'}
              </button>
            </div>

            {saveMutation.isError && (
              <div className="p-3 bg-red-900/20 border border-red-800/30 rounded text-sm text-red-300">
                Error: {(saveMutation.error as any)?.message || 'Failed to save store'}
              </div>
            )}
          </form>
        </div>
      </div>
    </>
  );
}
