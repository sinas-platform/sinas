import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Plus, Trash2, Edit2, KeyRound, X, Eye, EyeOff } from 'lucide-react';

interface Secret {
  id: string;
  name: string;
  description: string | null;
  visibility: string;
  user_id: string;
  created_at: string;
  updated_at: string;
}

interface SecretFormData {
  name: string;
  value: string;
  description: string;
  visibility: string;
}

export function Secrets() {
  const queryClient = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editingSecret, setEditingSecret] = useState<Secret | null>(null);
  const [formData, setFormData] = useState<SecretFormData>({ name: '', value: '', description: '', visibility: 'shared' });
  const [showValue, setShowValue] = useState(false);

  const { data: secrets, isLoading } = useQuery({
    queryKey: ['secrets'],
    queryFn: () => apiClient.listSecrets(),
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: (data: SecretFormData) => {
      if (editingSecret) {
        return apiClient.updateSecret(editingSecret.name, {
          value: data.value || undefined,
          description: data.description || undefined,
        });
      }
      return apiClient.createSecret(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
      closeModal();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => apiClient.deleteSecret(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
  });

  const openCreateModal = () => {
    setEditingSecret(null);
    setFormData({ name: '', value: '', description: '', visibility: 'shared' });
    setShowValue(false);
    setShowModal(true);
  };

  const openEditModal = (secret: Secret) => {
    setEditingSecret(secret);
    setFormData({ name: secret.name, value: '', description: secret.description || '', visibility: secret.visibility });
    setShowValue(false);
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingSecret(null);
    setFormData({ name: '', value: '', description: '', visibility: 'shared' });
    setShowValue(false);
    saveMutation.reset();
  };

  const handleDelete = (secret: Secret) => {
    if (confirm(`Are you sure you want to delete secret "${secret.name}"?`)) {
      deleteMutation.mutate(secret.name);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    saveMutation.mutate(formData);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Secrets</h1>
          <p className="text-gray-400 mt-1">Encrypted credentials for connectors and functions</p>
        </div>
        <button onClick={openCreateModal} className="btn btn-primary flex items-center">
          <Plus className="w-5 h-5 mr-2" />
          New Secret
        </button>
      </div>

      {isLoading ? (
        <div className="text-gray-400">Loading...</div>
      ) : !secrets?.length ? (
        <div className="card text-center py-12">
          <KeyRound className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-300">No secrets yet</h3>
          <p className="text-gray-500 mt-1">Create a secret to store API keys, tokens, and other credentials</p>
          <button onClick={openCreateModal} className="btn btn-primary mt-4">
            <Plus className="w-4 h-4 mr-2 inline" />
            Create Secret
          </button>
        </div>
      ) : (
        <div className="grid gap-4">
          {secrets.map((secret: Secret) => (
            <div key={secret.id} className="card flex items-center justify-between">
              <div className="flex items-center gap-4 min-w-0">
                <KeyRound className="w-5 h-5 text-primary-400 flex-shrink-0" />
                <div className="min-w-0">
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-sm text-gray-200">{secret.name}</span>
                    <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${
                      secret.visibility === 'private'
                        ? 'text-purple-400 bg-purple-900/20'
                        : 'text-blue-400 bg-blue-900/20'
                    }`}>
                      {secret.visibility}
                    </span>
                    <span className="text-xs text-gray-600 font-mono">••••••••</span>
                  </div>
                  {secret.description && (
                    <p className="text-xs text-gray-500 mt-0.5 truncate">{secret.description}</p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className="text-xs text-gray-600">
                  {new Date(secret.updated_at).toLocaleDateString()}
                </span>
                <button
                  onClick={() => openEditModal(secret)}
                  className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors"
                  title="Edit"
                >
                  <Edit2 className="w-4 h-4" />
                </button>
                <button
                  onClick={() => handleDelete(secret)}
                  className="p-1.5 text-gray-500 hover:text-red-400 transition-colors"
                  title="Delete"
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-gray-100">
                {editingSecret ? 'Update Secret' : 'Create Secret'}
              </h2>
              <button onClick={closeModal} className="p-1 text-gray-500 hover:text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="label">Name</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="SLACK_BOT_TOKEN"
                  className="input w-full font-mono"
                  disabled={!!editingSecret}
                  required={!editingSecret}
                  pattern="^[A-Za-z_][A-Za-z0-9_]*$"
                />
                {!editingSecret && (
                  <p className="text-xs text-gray-600 mt-1">Letters, numbers, and underscores only</p>
                )}
              </div>

              <div>
                <label className="label">Value</label>
                <div className="relative">
                  <input
                    type={showValue ? 'text' : 'password'}
                    value={formData.value}
                    onChange={(e) => setFormData({ ...formData, value: e.target.value })}
                    placeholder={editingSecret ? 'Leave empty to keep current value' : 'Enter secret value'}
                    className="input w-full font-mono pr-10"
                    required={!editingSecret}
                  />
                  <button
                    type="button"
                    onClick={() => setShowValue(!showValue)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-500 hover:text-gray-400"
                  >
                    {showValue ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div>
                <label className="label">Description</label>
                <textarea
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="What is this secret used for?"
                  className="input w-full"
                  rows={2}
                />
              </div>

              {!editingSecret && (
                <div>
                  <label className="label">Visibility</label>
                  <div className="flex gap-3">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="radio" name="visibility" value="shared"
                        checked={formData.visibility === 'shared'}
                        onChange={() => setFormData({ ...formData, visibility: 'shared' })}
                        className="text-primary-600" />
                      <div>
                        <span className="text-sm text-gray-200">Shared</span>
                        <p className="text-xs text-gray-500">Available to all users and connectors</p>
                      </div>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="radio" name="visibility" value="private"
                        checked={formData.visibility === 'private'}
                        onChange={() => setFormData({ ...formData, visibility: 'private' })}
                        className="text-primary-600" />
                      <div>
                        <span className="text-sm text-gray-200">Private</span>
                        <p className="text-xs text-gray-500">Only used when you trigger a connector</p>
                      </div>
                    </label>
                  </div>
                </div>
              )}

              {saveMutation.isError && (
                <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg">
                  <p className="text-sm text-red-400">
                    {(saveMutation.error as any)?.response?.data?.detail || 'Failed to save secret'}
                  </p>
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={closeModal} className="btn btn-secondary">
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saveMutation.isPending}
                  className="btn btn-primary"
                >
                  {saveMutation.isPending ? 'Saving...' : editingSecret ? 'Update' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
