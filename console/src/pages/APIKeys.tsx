import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Key, Plus, Trash2, Copy, Check, X } from 'lucide-react';
import { useState } from 'react';
import type { APIKeyCreate } from '../types';
import { useToast } from '../lib/toast-context';

export function APIKeys() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createdKey, setCreatedKey] = useState<{ id: string; api_key: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [customPermission, setCustomPermission] = useState('');
  const [formData, setFormData] = useState<APIKeyCreate>({
    name: '',
    permissions: {},
  });

  const { data: apiKeys, isLoading } = useQuery({
    queryKey: ['apiKeys'],
    queryFn: () => apiClient.listAPIKeys(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: APIKeyCreate) => apiClient.createAPIKey(data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
      setCreatedKey({ id: data.id, api_key: data.api_key });
      setShowCreateModal(false);
      setFormData({ name: '', permissions: {} });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (keyId: string) => apiClient.revokeAPIKey(keyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
      showToast('API key revoked successfully', 'success');
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (formData.name.trim()) {
      createMutation.mutate(formData);
    }
  };

  const handleCopyKey = (key: string) => {
    navigator.clipboard.writeText(key);
    setCopiedKey(key);
    showToast('API key copied to clipboard', 'success');
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const togglePermission = (permissionKey: string) => {
    setFormData({
      ...formData,
      permissions: {
        ...formData.permissions,
        [permissionKey]: !formData.permissions[permissionKey],
      },
    });
  };

  const addCustomPermission = () => {
    const trimmed = customPermission.trim();
    if (trimmed) {
      setFormData({
        ...formData,
        permissions: {
          ...formData.permissions,
          [trimmed]: true,
        },
      });
      setCustomPermission('');
    }
  };

  const removePermission = (permissionKey: string) => {
    const newPermissions = { ...formData.permissions };
    delete newPermissions[permissionKey];
    setFormData({
      ...formData,
      permissions: newPermissions,
    });
  };

  // Common permission categories
  const permissionCategories = [
    {
      name: 'Chats',
      permissions: [
        { key: 'sinas.chats.read:all', label: 'Read Chats' },
        { key: 'sinas.chats.write:all', label: 'Write Chats' },
        { key: 'sinas.chats.delete:all', label: 'Delete Chats' },
      ],
    },
    {
      name: 'Agents',
      permissions: [
        { key: 'sinas.assistants.read:all', label: 'Read Agents' },
        { key: 'sinas.assistants.write:all', label: 'Write Agents' },
        { key: 'sinas.assistants.delete:all', label: 'Delete Agents' },
      ],
    },
    {
      name: 'Functions',
      permissions: [
        { key: 'sinas.functions.read:all', label: 'Read Functions' },
        { key: 'sinas.functions.write:all', label: 'Write Functions' },
        { key: 'sinas.functions.delete:all', label: 'Delete Functions' },
        { key: 'sinas.functions.execute:all', label: 'Execute Functions' },
      ],
    },
    {
      name: 'Webhooks',
      permissions: [
        { key: 'sinas.webhooks.read:all', label: 'Read Webhooks' },
        { key: 'sinas.webhooks.write:all', label: 'Write Webhooks' },
        { key: 'sinas.webhooks.delete:all', label: 'Delete Webhooks' },
      ],
    },
    {
      name: 'Ontology',
      permissions: [
        { key: 'sinas.ontology.read:all', label: 'Read Ontology' },
        { key: 'sinas.ontology.write:all', label: 'Write Ontology' },
        { key: 'sinas.ontology.delete:all', label: 'Delete Ontology' },
      ],
    },
    {
      name: 'State Store',
      permissions: [
        { key: 'sinas.contexts.read:all', label: 'Read States' },
        { key: 'sinas.contexts.write:all', label: 'Write States' },
        { key: 'sinas.contexts.delete:all', label: 'Delete States' },
      ],
    },
    {
      name: 'Documents',
      permissions: [
        { key: 'sinas.documents.read:all', label: 'Read Documents' },
        { key: 'sinas.documents.write:all', label: 'Write Documents' },
        { key: 'sinas.documents.delete:all', label: 'Delete Documents' },
      ],
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">API Keys</h1>
          <p className="text-gray-600 mt-1">Manage your API keys for programmatic access</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Create API Key
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : apiKeys && apiKeys.length > 0 ? (
        <div className="grid gap-4">
          {apiKeys.map((key) => (
            <div key={key.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex items-start flex-1">
                  <Key className="w-6 h-6 text-primary-600 mr-3 flex-shrink-0 mt-1" />
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-gray-900">{key.name}</h3>
                    <p className="text-sm text-gray-600 font-mono mt-1">{key.key_prefix}***</p>
                    <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
                      <span>Created: {new Date(key.created_at).toLocaleDateString()}</span>
                      {key.last_used_at && (
                        <>
                          <span className="text-gray-300">•</span>
                          <span>Last used: {new Date(key.last_used_at).toLocaleDateString()}</span>
                        </>
                      )}
                      {key.expires_at && (
                        <>
                          <span className="text-gray-300">•</span>
                          <span>Expires: {new Date(key.expires_at).toLocaleDateString()}</span>
                        </>
                      )}
                    </div>
                    {key.permissions && Object.keys(key.permissions).length > 0 && (
                      <div className="mt-2">
                        <details className="text-xs">
                          <summary className="cursor-pointer text-gray-600 hover:text-gray-900 font-medium">
                            View permissions ({Object.keys(key.permissions).filter((k) => key.permissions[k]).length})
                          </summary>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {Object.entries(key.permissions)
                              .filter(([, enabled]) => enabled)
                              .map(([permission]) => (
                                <span
                                  key={permission}
                                  className="px-2 py-0.5 bg-blue-100 text-blue-800 text-xs rounded"
                                >
                                  {permission}
                                </span>
                              ))}
                          </div>
                        </details>
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-4">
                  <span
                    className={`px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap ${
                      key.is_active
                        ? 'bg-green-100 text-green-800'
                        : 'bg-gray-100 text-gray-800'
                    }`}
                  >
                    {key.is_active ? 'Active' : 'Inactive'}
                  </span>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to revoke this API key? This action cannot be undone.')) {
                        revokeMutation.mutate(key.id);
                      }
                    }}
                    className="text-red-600 hover:text-red-700 p-2"
                    disabled={revokeMutation.isPending}
                    title="Revoke key"
                  >
                    <Trash2 className="w-5 h-5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Key className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No API keys</h3>
          <p className="text-gray-600 mb-4">Create API keys for programmatic access to SINAS</p>
          <button onClick={() => setShowCreateModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Create API Key
          </button>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <>
          <div
            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50"
            onClick={() => {
              setShowCreateModal(false);
              setFormData({ name: '', permissions: {} });
            }}
          />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto pointer-events-auto">
            <div className="sticky top-0 bg-white border-b border-gray-200 p-6">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-gray-900">Create API Key</h2>
                <button
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ name: '', permissions: {} });
                  }}
                  className="text-gray-400 hover:text-gray-600"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <form onSubmit={handleCreate} className="p-6 space-y-6">
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-2">
                  Key Name *
                </label>
                <input
                  id="name"
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="My API Key"
                  required
                  className="input"
                  autoFocus
                />
                <p className="text-xs text-gray-500 mt-1">
                  A descriptive name to identify this API key
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-3">
                  Permissions
                </label>
                <p className="text-xs text-gray-500 mb-3">
                  Add custom permissions or select from common examples below. Format: <code className="bg-gray-100 px-1 rounded">resource.action:scope</code>
                </p>

                {/* Selected Permissions */}
                {Object.keys(formData.permissions).length > 0 && (
                  <div className="mb-4 border border-gray-200 rounded-lg p-3 bg-gray-50">
                    <div className="text-xs font-medium text-gray-700 mb-2">Selected Permissions:</div>
                    <div className="flex flex-wrap gap-2">
                      {Object.keys(formData.permissions).map((permission) => (
                        <span
                          key={permission}
                          className="inline-flex items-center gap-1 px-2 py-1 bg-blue-100 text-blue-800 text-xs rounded font-mono"
                        >
                          {permission}
                          <button
                            type="button"
                            onClick={() => removePermission(permission)}
                            className="hover:text-blue-900"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Add Custom Permission */}
                <div className="mb-4">
                  <label className="block text-xs font-medium text-gray-700 mb-2">Add Custom Permission</label>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={customPermission}
                      onChange={(e) => setCustomPermission(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          addCustomPermission();
                        }
                      }}
                      placeholder="e.g., sinas.chats.read:all"
                      className="input flex-1 font-mono text-sm"
                    />
                    <button
                      type="button"
                      onClick={addCustomPermission}
                      disabled={!customPermission.trim()}
                      className="btn btn-secondary"
                    >
                      Add
                    </button>
                  </div>
                </div>

                {/* Common Permission Examples */}
                <details className="border border-gray-200 rounded-lg">
                  <summary className="cursor-pointer p-3 text-sm font-medium text-gray-700 hover:bg-gray-50">
                    Common Permission Examples
                  </summary>
                  <div className="p-3 pt-0 space-y-3 max-h-64 overflow-y-auto">
                    {permissionCategories.map((category) => (
                      <div key={category.name}>
                        <h4 className="text-xs font-semibold text-gray-900 mb-1">{category.name}</h4>
                        <div className="flex flex-wrap gap-1 ml-2">
                          {category.permissions.map((permission) => (
                            <button
                              key={permission.key}
                              type="button"
                              onClick={() => togglePermission(permission.key)}
                              className={`px-2 py-1 text-xs rounded font-mono transition-colors ${
                                formData.permissions[permission.key]
                                  ? 'bg-blue-100 text-blue-800'
                                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                              }`}
                            >
                              {permission.key}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              </div>

              {createMutation.isError && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                  Failed to create API key. Please try again.
                </div>
              )}

              <div className="flex justify-end space-x-3 pt-4 border-t border-gray-200">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ name: '', permissions: {} });
                  }}
                  className="btn btn-secondary"
                  disabled={createMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createMutation.isPending || !formData.name.trim()}
                >
                  {createMutation.isPending ? 'Creating...' : 'Create API Key'}
                </button>
              </div>
            </form>
          </div>
        </div>
        </>
      )}

      {/* Created Key Modal */}
      {createdKey && (
        <>
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50" onClick={() => setCreatedKey(null)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-6 pointer-events-auto">
            <div className="text-center mb-6">
              <div className="inline-flex items-center justify-center w-12 h-12 bg-green-100 rounded-full mb-4">
                <Check className="w-6 h-6 text-green-600" />
              </div>
              <h2 className="text-xl font-semibold text-gray-900 mb-2">API Key Created!</h2>
              <p className="text-sm text-gray-600">
                Make sure to copy your API key now. You won't be able to see it again!
              </p>
            </div>

            <div className="bg-gray-50 rounded-lg p-4 mb-6">
              <label className="block text-xs font-medium text-gray-700 mb-2">Your API Key</label>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-white border border-gray-300 rounded text-sm font-mono break-all">
                  {createdKey.api_key}
                </code>
                <button
                  onClick={() => handleCopyKey(createdKey.api_key)}
                  className="btn btn-secondary flex-shrink-0"
                  title="Copy to clipboard"
                >
                  {copiedKey === createdKey.api_key ? (
                    <Check className="w-5 h-5 text-green-600" />
                  ) : (
                    <Copy className="w-5 h-5" />
                  )}
                </button>
              </div>
            </div>

            <div className="flex justify-end">
              <button
                onClick={() => setCreatedKey(null)}
                className="btn btn-primary"
              >
                Done
              </button>
            </div>
          </div>
        </div>
        </>
      )}
    </div>
  );
}
