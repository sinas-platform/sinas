import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { AppWindow, Plus, Trash2, Edit2, CheckCircle, XCircle, RefreshCw } from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import type { App, AppCreate, AppUpdate, AppStatus, AppResourceRef } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';

const RESOURCE_TYPES = ['agents', 'functions', 'skills', 'templates', 'collections'];

const DEFAULT_EXPOSED_NAMESPACES: Record<string, string[]> = Object.fromEntries(
  RESOURCE_TYPES.map((t) => [t, ['*']])
);

function ExposedNamespacesEditor({
  value,
  onChange,
}: {
  value: Record<string, string[]>;
  onChange: (v: Record<string, string[]>) => void;
}) {
  return (
    <div className="space-y-2">
      {RESOURCE_TYPES.map((type) => (
        <div key={type} className="flex items-center space-x-3">
          <label className="w-28 text-sm font-medium text-gray-700 capitalize">{type}</label>
          <input
            type="text"
            className="input flex-1"
            placeholder="* = all, or comma-separated: default, shared"
            value={(value[type] || []).join(', ')}
            onChange={(e) => {
              const namespaces = e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean);
              const next = { ...value };
              if (namespaces.length > 0) {
                next[type] = namespaces;
              } else {
                delete next[type];
              }
              onChange(next);
            }}
          />
        </div>
      ))}
    </div>
  );
}

function ResourceListEditor({
  value,
  onChange,
}: {
  value: AppResourceRef[];
  onChange: (v: AppResourceRef[]) => void;
}) {
  const addResource = () => {
    onChange([...value, { type: 'agents', namespace: 'default', name: '' }]);
  };

  const removeResource = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const updateResource = (idx: number, field: keyof AppResourceRef, val: string) => {
    const next = [...value];
    next[idx] = { ...next[idx], [field]: val };
    onChange(next);
  };

  return (
    <div className="space-y-2">
      {value.length > 0 && (
        <div className="grid grid-cols-[2fr_2fr_3fr_auto] gap-2 text-xs font-medium text-gray-500">
          <span>Type</span>
          <span>Namespace</span>
          <span>Name</span>
          <span className="w-8" />
        </div>
      )}
      {value.map((res, idx) => (
        <div key={idx} className="grid grid-cols-[2fr_2fr_3fr_auto] gap-2 items-center">
          <select
            className="input"
            value={res.type}
            onChange={(e) => updateResource(idx, 'type', e.target.value)}
          >
            {RESOURCE_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <input
            type="text"
            className="input"
            placeholder="namespace"
            value={res.namespace}
            onChange={(e) => updateResource(idx, 'namespace', e.target.value)}
          />
          <input
            type="text"
            className="input"
            placeholder="name"
            value={res.name}
            onChange={(e) => updateResource(idx, 'name', e.target.value)}
          />
          <button
            type="button"
            onClick={() => removeResource(idx)}
            className="text-red-500 hover:text-red-700 p-1"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      ))}
      <button type="button" onClick={addResource} className="btn btn-sm btn-secondary">
        <Plus className="w-4 h-4 mr-1" /> Add Resource
      </button>
    </div>
  );
}

function PermissionListEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  return (
    <textarea
      className="input font-mono text-sm"
      rows={3}
      placeholder={placeholder || 'One permission per line'}
      value={value.join('\n')}
      onChange={(e) => {
        onChange(
          e.target.value
            .split('\n')
            .map((s) => s.trim())
            .filter(Boolean)
        );
      }}
    />
  );
}

function StatusBanner({ status }: { status?: AppStatus }) {
  if (!status) return null;

  if (status.ready) {
    return (
      <div className="flex items-center gap-2 mt-2 px-3 py-1.5 bg-green-50 border border-green-200 rounded text-sm text-green-700">
        <CheckCircle className="w-4 h-4 shrink-0" />
        All resources and permissions satisfied
      </div>
    );
  }

  const lines: string[] = [];
  for (const r of status.resources.missing) {
    lines.push(`Resource: ${r.type}/${r.namespace}/${r.name}`);
  }
  for (const p of status.permissions.required.missing) {
    lines.push(`Required: ${p}`);
  }
  for (const p of status.permissions.optional.missing) {
    lines.push(`Optional: ${p}`);
  }

  return (
    <div className="mt-2 px-3 py-1.5 bg-red-50 border border-red-200 rounded text-sm text-red-700">
      <div className="flex items-center gap-2 font-medium">
        <XCircle className="w-4 h-4 shrink-0" />
        Missing dependencies
      </div>
      <ul className="mt-1 ml-6 list-disc text-xs space-y-0.5">
        {lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

export function Apps() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedApp, setSelectedApp] = useState<App | null>(null);
  const [appStatuses, setAppStatuses] = useState<Record<string, AppStatus>>({});
  const [refreshingStatuses, setRefreshingStatuses] = useState(false);

  const [createFormData, setCreateFormData] = useState<AppCreate>({
    namespace: 'default',
    name: '',
    description: '',
    required_resources: [],
    required_permissions: [],
    optional_permissions: [],
    exposed_namespaces: { ...DEFAULT_EXPOSED_NAMESPACES },
  });
  const [editFormData, setEditFormData] = useState<AppUpdate>({});

  const { data: apps, isLoading, error } = useQuery({
    queryKey: ['apps'],
    queryFn: () => apiClient.listApps(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: AppCreate) => apiClient.createApp(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ namespace, name, data }: { namespace: string; name: string; data: AppUpdate }) =>
      apiClient.updateApp(namespace, name, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] });
      setShowEditModal(false);
      setSelectedApp(null);
      setEditFormData({});
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteApp(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] });
    },
  });

  const resetCreateForm = () => {
    setCreateFormData({
      namespace: 'default',
      name: '',
      description: '',
      required_resources: [],
      required_permissions: [],
      optional_permissions: [],
      exposed_namespaces: { ...DEFAULT_EXPOSED_NAMESPACES },
    });
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (createFormData.name.trim()) {
      createMutation.mutate(createFormData);
    }
  };

  const handleEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedApp) {
      updateMutation.mutate({
        namespace: selectedApp.namespace,
        name: selectedApp.name,
        data: editFormData,
      });
    }
  };

  const openEditModal = (app: App) => {
    setSelectedApp(app);
    setEditFormData({
      namespace: app.namespace,
      name: app.name,
      description: app.description || '',
      required_resources: app.required_resources || [],
      required_permissions: app.required_permissions || [],
      optional_permissions: app.optional_permissions || [],
      exposed_namespaces: app.exposed_namespaces || {},
      is_active: app.is_active,
    });
    setShowEditModal(true);
  };

  const handleDelete = (app: App) => {
    if (confirm(`Are you sure you want to delete app "${app.namespace}/${app.name}"?`)) {
      deleteMutation.mutate({ namespace: app.namespace, name: app.name });
    }
  };

  const fetchAllStatuses = useCallback(async (appList: App[]) => {
    setRefreshingStatuses(true);
    const results: Record<string, AppStatus> = {};
    await Promise.all(
      appList.map(async (app) => {
        try {
          const status = await apiClient.getAppStatus(app.namespace, app.name);
          results[`${app.namespace}/${app.name}`] = status;
        } catch {
          // skip failed ones
        }
      })
    );
    setAppStatuses(results);
    setRefreshingStatuses(false);
  }, []);

  useEffect(() => {
    if (apps && apps.length > 0) {
      fetchAllStatuses(apps);
    }
  }, [apps, fetchAllStatuses]);

  if (error) {
    return <ErrorDisplay error={error} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Apps</h1>
          <p className="text-gray-600 mt-1">Manage application packages that bundle resources and permissions</p>
        </div>
        <div className="flex items-center space-x-2">
          {apps && apps.length > 0 && (
            <button
              onClick={() => fetchAllStatuses(apps)}
              className="btn btn-secondary flex items-center"
              disabled={refreshingStatuses}
              title="Refresh all statuses"
            >
              <RefreshCw className={`w-4 h-4 mr-2 ${refreshingStatuses ? 'animate-spin' : ''}`} />
              Status
            </button>
          )}
          <button
            onClick={() => {
              resetCreateForm();
              setShowCreateModal(true);
            }}
            className="btn btn-primary flex items-center"
          >
            <Plus className="w-5 h-5 mr-2" />
            Create App
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
        </div>
      ) : apps && apps.length > 0 ? (
        <div className="grid gap-4">
          {apps.map((app) => {
            const key = `${app.namespace}/${app.name}`;
            const status = appStatuses[key];
            return (
              <div key={app.id} className="card hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center space-x-2">
                      <AppWindow className="w-5 h-5 text-blue-500" />
                      <h3 className="text-lg font-semibold text-gray-900">
                        {app.namespace}/{app.name}
                      </h3>
                      {!app.is_active && (
                        <span className="px-2 py-1 text-xs bg-gray-200 text-gray-600 rounded">
                          Inactive
                        </span>
                      )}
                    </div>
                    {app.description && (
                      <p className="text-gray-600 mt-2">{app.description}</p>
                    )}
                    <StatusBanner status={status} />
                    <div className="flex flex-wrap gap-2 mt-3">
                      {(app.required_resources || []).length > 0 && (
                        <span className="px-2 py-1 text-xs bg-blue-100 text-blue-700 rounded">
                          {app.required_resources.length} resource{app.required_resources.length !== 1 ? 's' : ''}
                        </span>
                      )}
                      {(app.required_permissions || []).length > 0 && (
                        <span className="px-2 py-1 text-xs bg-purple-100 text-purple-700 rounded">
                          {app.required_permissions.length} required perm{app.required_permissions.length !== 1 ? 's' : ''}
                        </span>
                      )}
                      {(app.optional_permissions || []).length > 0 && (
                        <span className="px-2 py-1 text-xs bg-yellow-100 text-yellow-700 rounded">
                          {app.optional_permissions.length} optional perm{app.optional_permissions.length !== 1 ? 's' : ''}
                        </span>
                      )}
                      {Object.entries(app.exposed_namespaces || {}).map(([type, namespaces]) => (
                        <span key={type} className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded">
                          {type}: {namespaces.join(', ')}
                        </span>
                      ))}
                    </div>
                    <div className="text-xs text-gray-500 mt-2">
                      {new Date(app.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="flex items-center space-x-2 ml-4">
                    <button
                      onClick={() => openEditModal(app)}
                      className="btn btn-sm btn-secondary"
                      title="Edit"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(app)}
                      className="btn btn-sm btn-danger"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <AppWindow className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No apps yet</h3>
          <p className="text-gray-600 mb-4">Create your first app to get started</p>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <>
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50" onClick={() => setShowCreateModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <h2 className="text-2xl font-bold mb-6">Create App</h2>
              <form onSubmit={handleCreate} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="label">Namespace</label>
                    <input
                      type="text"
                      className="input"
                      value={createFormData.namespace}
                      onChange={(e) => setCreateFormData({ ...createFormData, namespace: e.target.value })}
                      required
                      pattern="[a-z0-9_-]+"
                      title="Lowercase letters, numbers, hyphens, and underscores only"
                    />
                  </div>
                  <div>
                    <label className="label">Name</label>
                    <input
                      type="text"
                      className="input"
                      value={createFormData.name}
                      onChange={(e) => setCreateFormData({ ...createFormData, name: e.target.value })}
                      required
                      pattern="[a-z0-9_-]+"
                      title="Lowercase letters, numbers, hyphens, and underscores only"
                    />
                  </div>
                </div>

                <div>
                  <label className="label">Description</label>
                  <input
                    type="text"
                    className="input"
                    placeholder="What this app does"
                    value={createFormData.description || ''}
                    onChange={(e) => setCreateFormData({ ...createFormData, description: e.target.value })}
                  />
                </div>

                <div>
                  <label className="label">Exposed Namespaces</label>
                  <p className="text-xs text-gray-500 mb-2">
                    Namespaces this app exposes for each resource type
                  </p>
                  <ExposedNamespacesEditor
                    value={createFormData.exposed_namespaces || {}}
                    onChange={(v) => setCreateFormData({ ...createFormData, exposed_namespaces: v })}
                  />
                </div>

                <div>
                  <label className="label">Required Resources</label>
                  <ResourceListEditor
                    value={createFormData.required_resources || []}
                    onChange={(v) => setCreateFormData({ ...createFormData, required_resources: v })}
                  />
                </div>

                <div>
                  <label className="label">Required Permissions</label>
                  <PermissionListEditor
                    value={createFormData.required_permissions || []}
                    onChange={(v) => setCreateFormData({ ...createFormData, required_permissions: v })}
                    placeholder="e.g. sinas.agents/default/my-agent.read:own (one per line)"
                  />
                </div>

                <div>
                  <label className="label">Optional Permissions</label>
                  <PermissionListEditor
                    value={createFormData.optional_permissions || []}
                    onChange={(v) => setCreateFormData({ ...createFormData, optional_permissions: v })}
                    placeholder="e.g. sinas.states/preferences.read:own (one per line)"
                  />
                </div>

                <div className="flex justify-end space-x-2 pt-4">
                  <button
                    type="button"
                    onClick={() => setShowCreateModal(false)}
                    className="btn btn-secondary"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={createMutation.isPending}
                  >
                    {createMutation.isPending ? 'Creating...' : 'Create'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </>
      )}

      {/* Edit Modal */}
      {showEditModal && selectedApp && (
        <>
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50" onClick={() => setShowEditModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <h2 className="text-2xl font-bold mb-6">Edit App</h2>
              <form onSubmit={handleEdit} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="label">Namespace</label>
                    <input
                      type="text"
                      className="input"
                      value={editFormData.namespace ?? selectedApp.namespace}
                      onChange={(e) => setEditFormData({ ...editFormData, namespace: e.target.value })}
                      pattern="[a-z0-9_-]+"
                    />
                  </div>
                  <div>
                    <label className="label">Name</label>
                    <input
                      type="text"
                      className="input"
                      value={editFormData.name ?? selectedApp.name}
                      onChange={(e) => setEditFormData({ ...editFormData, name: e.target.value })}
                      pattern="[a-z0-9_-]+"
                    />
                  </div>
                </div>

                <div>
                  <label className="label">Description</label>
                  <input
                    type="text"
                    className="input"
                    value={editFormData.description ?? selectedApp.description ?? ''}
                    onChange={(e) => setEditFormData({ ...editFormData, description: e.target.value })}
                  />
                </div>

                <div>
                  <label className="label">Exposed Namespaces</label>
                  <ExposedNamespacesEditor
                    value={editFormData.exposed_namespaces ?? selectedApp.exposed_namespaces ?? {}}
                    onChange={(v) => setEditFormData({ ...editFormData, exposed_namespaces: v })}
                  />
                </div>

                <div>
                  <label className="label">Required Resources</label>
                  <ResourceListEditor
                    value={editFormData.required_resources ?? selectedApp.required_resources ?? []}
                    onChange={(v) => setEditFormData({ ...editFormData, required_resources: v })}
                  />
                </div>

                <div>
                  <label className="label">Required Permissions</label>
                  <PermissionListEditor
                    value={editFormData.required_permissions ?? selectedApp.required_permissions ?? []}
                    onChange={(v) => setEditFormData({ ...editFormData, required_permissions: v })}
                    placeholder="e.g. sinas.agents/default/my-agent.read:own (one per line)"
                  />
                </div>

                <div>
                  <label className="label">Optional Permissions</label>
                  <PermissionListEditor
                    value={editFormData.optional_permissions ?? selectedApp.optional_permissions ?? []}
                    onChange={(v) => setEditFormData({ ...editFormData, optional_permissions: v })}
                    placeholder="e.g. sinas.states/preferences.read:own (one per line)"
                  />
                </div>

                <div>
                  <label className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      checked={editFormData.is_active ?? selectedApp.is_active}
                      onChange={(e) => setEditFormData({ ...editFormData, is_active: e.target.checked })}
                    />
                    <span>Active</span>
                  </label>
                </div>

                <div className="flex justify-end space-x-2 pt-4">
                  <button
                    type="button"
                    onClick={() => setShowEditModal(false)}
                    className="btn btn-secondary"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={updateMutation.isPending}
                  >
                    {updateMutation.isPending ? 'Saving...' : 'Save'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
