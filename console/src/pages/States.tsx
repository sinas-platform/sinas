import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Plus, Search, Tag, Users, Lock, Trash2, Edit, X } from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';

export function States() {
  const queryClient = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editingState, setEditingState] = useState<any>(null);
  const [filters, setFilters] = useState({
    namespace: '',
    visibility: '',
    search: '',
  });

  const { data: states, isLoading } = useQuery({
    queryKey: ['states', filters],
    queryFn: () => {
      const params: any = {};
      if (filters.namespace) params.namespace = filters.namespace;
      if (filters.visibility) params.visibility = filters.visibility;
      if (filters.search) params.search = filters.search;
      return apiClient.listStates(params);
    },
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (stateId: string) => apiClient.deleteState(stateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['states'] });
    },
  });

  const handleDelete = (state: any) => {
    if (confirm(`Delete state "${state.namespace}.${state.key}"?`)) {
      deleteMutation.mutate(state.id);
    }
  };

  const handleEdit = (state: any) => {
    setEditingState(state);
    setShowModal(true);
  };

  const uniqueNamespaces = Array.from(new Set(states?.map((c: any) => c.namespace) || []));

  const getVisibilityIcon = (visibility: string) => {
    switch (visibility) {
      case 'shared':
        return <Users className="w-4 h-4" />;
      case 'private':
        return <Lock className="w-4 h-4" />;
      default:
        return <Lock className="w-4 h-4" />;
    }
  };

  const getVisibilityColor = (visibility: string) => {
    switch (visibility) {
      case 'shared':
        return 'text-blue-600 bg-blue-50';
      case 'private':
        return 'text-gray-600 bg-gray-50';
      default:
        return 'text-gray-600 bg-gray-50';
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">States</h1>
          <p className="text-gray-600 mt-1">Runtime state data accessible to agents</p>
        </div>
        <button
          onClick={() => {
            setEditingState(null);
            setShowModal(true);
          }}
          className="btn btn-primary"
        >
          <Plus className="w-4 h-4" />
          New State
        </button>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="label">Namespace</label>
            <select
              value={filters.namespace}
              onChange={(e) => setFilters({ ...filters, namespace: e.target.value })}
              className="input"
            >
              <option value="">All namespaces</option>
              {uniqueNamespaces.map((ns: string) => (
                <option key={ns} value={ns}>
                  {ns}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Visibility</label>
            <select
              value={filters.visibility}
              onChange={(e) => setFilters({ ...filters, visibility: e.target.value })}
              className="input"
            >
              <option value="">All</option>
              <option value="private">Private</option>
              <option value="shared">Shared</option>
            </select>
          </div>
          <div>
            <label className="label">Search</label>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              <input
                type="text"
                value={filters.search}
                onChange={(e) => setFilters({ ...filters, search: e.target.value })}
                placeholder="Search keys, descriptions..."
                className="input !pl-11"
              />
            </div>
          </div>
        </div>
      </div>

      {/* States List */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">States</h2>
          <span className="text-sm text-gray-500">{states?.length || 0} states</span>
        </div>

        {isLoading ? (
          <div className="text-center py-8 text-gray-500">Loading states...</div>
        ) : !states || states.length === 0 ? (
          <div className="text-center py-8 text-gray-500">No states found</div>
        ) : (
          <div className="space-y-2">
            {states.map((state: any) => (
              <div
                key={state.id}
                className="border border-gray-200 rounded-lg p-4 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-sm font-mono font-semibold text-gray-900">
                        {state.namespace}.{state.key}
                      </span>
                      <span className={`px-2 py-0.5 text-xs font-medium rounded flex items-center gap-1 ${getVisibilityColor(state.visibility)}`}>
                        {getVisibilityIcon(state.visibility)}
                        {state.visibility}
                      </span>
                      <span className="px-2 py-0.5 bg-gray-100 text-gray-600 text-xs font-medium rounded">
                        Score: {state.relevance_score}
                      </span>
                    </div>
                    {state.description && (
                      <p className="text-sm text-gray-600 mb-2">{state.description}</p>
                    )}
                    {state.tags && state.tags.length > 0 && (
                      <div className="flex items-center gap-2 flex-wrap">
                        {state.tags.map((tag: string, idx: number) => (
                          <span key={idx} className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs rounded flex items-center gap-1">
                            <Tag className="w-3 h-3" />
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="mt-2">
                      <details className="text-xs">
                        <summary className="cursor-pointer text-gray-500 hover:text-gray-700">
                          View value
                        </summary>
                        <pre className="mt-2 p-2 bg-gray-50 rounded border border-gray-200 overflow-x-auto">
                          {JSON.stringify(state.value, null, 2)}
                        </pre>
                      </details>
                    </div>
                    <div className="mt-2 text-xs text-gray-500">
                      Updated {new Date(state.updated_at).toLocaleString()}
                      {state.expires_at && (
                        <span className="ml-2">
                          • Expires {new Date(state.expires_at).toLocaleString()}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-4">
                    <button
                      onClick={() => handleEdit(state)}
                      className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(state)}
                      className="p-2 text-red-600 hover:text-red-900 hover:bg-red-50 rounded"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Modal */}
      {showModal && (
        <StateModal
          state={editingState}
          onClose={() => {
            setShowModal(false);
            setEditingState(null);
          }}
        />
      )}
    </div>
  );
}

function StateModal({
  state,
  onClose,
}: {
  state: any;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState({
    namespace: state?.namespace || '',
    key: state?.key || '',
    value: state?.value ? JSON.stringify(state.value, null, 2) : '{}',
    visibility: state?.visibility || 'private',
    description: state?.description || '',
    tags: state?.tags?.join(', ') || '',
    relevance_score: state?.relevance_score || 1.0,
    expires_at: state?.expires_at ? new Date(state.expires_at).toISOString().slice(0, 16) : '',
  });

  const saveMutation = useMutation({
    mutationFn: async (data: any) => {
      const payload = {
        ...data,
        value: JSON.parse(data.value),
        tags: data.tags ? data.tags.split(',').map((t: string) => t.trim()).filter(Boolean) : [],
        relevance_score: parseFloat(data.relevance_score),
        expires_at: data.expires_at || null,
      };

      if (state) {
        // Update - only send updatable fields
        const { namespace, key, ...updatePayload } = payload;
        return apiClient.updateState(state.id, updatePayload);
      } else {
        return apiClient.createState(payload);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['states'] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-gray-900">
            {state ? 'Edit State' : 'New State'}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
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
                disabled={!!state}
                placeholder="e.g. user_preferences"
              />
            </div>
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
          </div>

          <div>
            <label className="label">Value (JSON) *</label>
            <CodeEditor
              value={formData.value}
              language="json"
              placeholder='{"example": "value"}'
              onChange={(e) => setFormData({ ...formData, value: e.target.value })}
              padding={15}
              style={{
                backgroundColor: '#f9fafb',
                fontFamily: 'ui-monospace, monospace',
                fontSize: 13,
                border: '1px solid #d1d5db',
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
            <p className="text-xs text-gray-500 mt-1">
              Shared states can be accessed by users with namespace permissions
            </p>
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
                onChange={(e) => setFormData({ ...formData, relevance_score: e.target.value })}
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

          <div className="flex gap-2 justify-end pt-4 border-t border-gray-200">
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
            <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-800">
              Error: {(saveMutation.error as any)?.message || 'Failed to save state'}
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
