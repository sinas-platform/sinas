import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Key, Plus, Trash2, Copy, Check, X } from 'lucide-react';
import { useState } from 'react';
import type { APIKeyCreate } from '../types';
import { useToast } from '../lib/toast-context';
import { PermissionEditor } from '../components/PermissionEditor';

export function APIKeys() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createdKey, setCreatedKey] = useState<{ id: string; key: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [formData, setFormData] = useState<APIKeyCreate>({
    name: '',
    permissions: {},
  });

  const { data: apiKeys, isLoading } = useQuery({
    queryKey: ['apiKeys'],
    queryFn: () => apiClient.listAPIKeys(),
    retry: false,
  });

  const filteredApiKeys = apiKeys?.filter(key => showInactive || key.is_active);

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-GB'); // European format (dd/MM/yyyy)
  };

  const isExpired = (dateString: string | null) => {
    if (!dateString) return false;
    return new Date(dateString) < new Date();
  };

  const isExpiringSoon = (dateString: string | null) => {
    if (!dateString) return false;
    const expiryDate = new Date(dateString);
    const now = new Date();
    const daysUntilExpiry = (expiryDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
    return daysUntilExpiry > 0 && daysUntilExpiry <= 7;
  };

  const createMutation = useMutation({
    mutationFn: (data: APIKeyCreate) => apiClient.createAPIKey(data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
      setCreatedKey({ id: data.id, key: data.key });
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


  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">API Keys</h1>
          <p className="text-gray-400 mt-1">Manage your API keys for programmatic access</p>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="rounded border-white/10 text-primary-600 focus:ring-primary-500"
            />
            Show inactive
          </label>
          <button
            onClick={() => setShowCreateModal(true)}
            className="btn btn-primary flex items-center"
          >
            <Plus className="w-5 h-5 mr-2" />
            Create API Key
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : filteredApiKeys && filteredApiKeys.length > 0 ? (
        <div className="grid gap-4">
          {filteredApiKeys.map((key) => (
            <div key={key.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex items-start flex-1">
                  <Key className="w-6 h-6 text-primary-600 mr-3 flex-shrink-0 mt-1" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-100">{key.name}</h3>
                      {key.user_email && (
                        <span className="text-xs text-gray-500">({key.user_email})</span>
                      )}
                    </div>
                    <p className="text-sm text-gray-400 font-mono mt-1">{key.key_prefix}***</p>
                    <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
                      <span>Created: {formatDate(key.created_at)}</span>
                      {key.last_used_at && (
                        <>
                          <span className="text-gray-300">•</span>
                          <span>Last used: {formatDate(key.last_used_at)}</span>
                        </>
                      )}
                      {key.expires_at && (
                        <>
                          <span className="text-gray-300">•</span>
                          <span className={
                            isExpired(key.expires_at)
                              ? 'text-red-600 font-medium'
                              : isExpiringSoon(key.expires_at)
                              ? 'text-orange-400 font-medium'
                              : ''
                          }>
                            {isExpired(key.expires_at) ? 'Expired: ' : 'Expires: '}{formatDate(key.expires_at)}
                          </span>
                        </>
                      )}
                    </div>
                    {key.permissions && Object.keys(key.permissions).length > 0 && (
                      <div className="mt-2">
                        <details className="text-xs">
                          <summary className="cursor-pointer text-gray-400 hover:text-gray-100 font-medium">
                            View permissions ({Object.keys(key.permissions).filter((k) => key.permissions[k]).length})
                          </summary>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {Object.entries(key.permissions)
                              .filter(([, enabled]) => enabled)
                              .map(([permission]) => (
                                <span
                                  key={permission}
                                  className="px-2 py-0.5 bg-blue-900/30 text-blue-300 text-xs rounded"
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
                  {isExpired(key.expires_at) && (
                    <span className="px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap bg-red-900/30 text-red-300">
                      Expired
                    </span>
                  )}
                  {isExpiringSoon(key.expires_at) && !isExpired(key.expires_at) && (
                    <span className="px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap bg-orange-900/30 text-orange-300">
                      Expiring Soon
                    </span>
                  )}
                  <span
                    className={`px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap ${
                      key.is_active
                        ? 'bg-green-900/30 text-green-300'
                        : 'bg-[#161616] text-gray-200'
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
                    className="text-red-600 hover:text-red-400 p-2"
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
      ) : apiKeys && apiKeys.length > 0 ? (
        <div className="text-center py-12 card">
          <Key className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No active API keys</h3>
          <p className="text-gray-400 mb-4">
            {showInactive ? 'All API keys are filtered out' : 'Enable "Show inactive" to see inactive keys'}
          </p>
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Key className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No API keys</h3>
          <p className="text-gray-400 mb-4">Create API keys for programmatic access to SINAS</p>
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
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50"
            onClick={() => {
              setShowCreateModal(false);
              setFormData({ name: '', permissions: {} });
            }}
          />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto pointer-events-auto">
            <div className="sticky top-0 bg-[#161616] border-b border-white/[0.06] p-6">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-gray-100">Create API Key</h2>
                <button
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ name: '', permissions: {} });
                  }}
                  className="text-gray-500 hover:text-gray-400"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <form onSubmit={handleCreate} className="p-6 space-y-6">
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-300 mb-2">
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
                <label htmlFor="expires_at" className="block text-sm font-medium text-gray-300 mb-2">
                  Expiry Date (Optional)
                </label>
                <input
                  id="expires_at"
                  type="date"
                  value={formData.expires_at || ''}
                  onChange={(e) => setFormData({ ...formData, expires_at: e.target.value || undefined })}
                  min={new Date().toISOString().split('T')[0]}
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Leave empty for no expiration. Key will be automatically revoked after this date.
                </p>
              </div>

              <PermissionEditor
                mode="dict"
                label="Permissions"
                value={formData.permissions}
                onChange={(permissions) => setFormData({ ...formData, permissions })}
              />

              {createMutation.isError && (
                <div className="p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
                  Failed to create API key. Please try again.
                </div>
              )}

              <div className="flex justify-end space-x-3 pt-4 border-t border-white/[0.06]">
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
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={() => setCreatedKey(null)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 pointer-events-auto">
            <div className="text-center mb-6">
              <div className="inline-flex items-center justify-center w-12 h-12 bg-green-900/30 rounded-full mb-4">
                <Check className="w-6 h-6 text-green-600" />
              </div>
              <h2 className="text-xl font-semibold text-gray-100 mb-2">API Key Created!</h2>
              <p className="text-sm text-gray-400">
                Make sure to copy your API key now. You won't be able to see it again!
              </p>
            </div>

            <div className="bg-[#0d0d0d] rounded-lg p-4 mb-6">
              <label className="block text-xs font-medium text-gray-300 mb-2">Your API Key</label>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-[#161616] border border-white/10 rounded text-sm font-mono break-all">
                  {createdKey.key}
                </code>
                <button
                  onClick={() => handleCopyKey(createdKey.key)}
                  className="btn btn-secondary flex-shrink-0"
                  title="Copy to clipboard"
                >
                  {copiedKey === createdKey.key ? (
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
