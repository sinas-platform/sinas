import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Brain, Plus, Trash2, Edit2 } from 'lucide-react';
import { useState } from 'react';
import type { LLMProvider, LLMProviderCreate, LLMProviderUpdate } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';

export function LLMProviders() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<LLMProvider | null>(null);
  const [createFormData, setCreateFormData] = useState<LLMProviderCreate>({
    name: '',
    provider_type: 'openai',
    is_active: true,
    is_default: false,
  });
  const [editFormData, setEditFormData] = useState<LLMProviderUpdate>({});
  const [editConfigText, setEditConfigText] = useState('{}');
  const [createConfigText, setCreateConfigText] = useState('{}');

  const { data: providers, isLoading } = useQuery({
    queryKey: ['llmProviders'],
    queryFn: () => apiClient.listLLMProviders(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: LLMProviderCreate) => apiClient.createLLMProvider(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llmProviders'] });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: LLMProviderUpdate }) =>
      apiClient.updateLLMProvider(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llmProviders'] });
      setShowEditModal(false);
      setSelectedProvider(null);
      setEditFormData({});
      setEditConfigText('{}');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (providerId: string) => apiClient.deleteLLMProvider(providerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llmProviders'] });
    },
  });

  const resetCreateForm = () => {
    setCreateFormData({
      name: '',
      provider_type: 'openai',
      is_active: true,
      is_default: false,
    });
    setCreateConfigText('{}');
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (createFormData.name.trim() && createFormData.provider_type.trim()) {
      createMutation.mutate(createFormData);
    }
  };

  const handleEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedProvider && editFormData.name?.trim()) {
      updateMutation.mutate({
        id: selectedProvider.id,
        data: editFormData,
      });
    }
  };

  const openEditModal = (provider: LLMProvider) => {
    setSelectedProvider(provider);
    const configJson = JSON.stringify(provider.config || {}, null, 2);
    setEditConfigText(configJson);
    setEditFormData({
      name: provider.name,
      provider_type: provider.provider_type,
      api_endpoint: provider.api_endpoint || '',
      default_model: provider.default_model || '',
      config: provider.config || {},
      is_default: provider.is_default,
      is_active: provider.is_active,
    });
    setShowEditModal(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">LLM Providers</h1>
          <p className="text-gray-600 mt-1">Manage language model provider configurations</p>
        </div>
        <button
          onClick={() => {
            resetCreateForm();
            setShowCreateModal(true);
          }}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Add Provider
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : providers && providers.length > 0 ? (
        <div className="grid gap-6">
          {providers.map((provider) => (
            <div key={provider.id} className={`card ${!provider.is_active ? 'opacity-60 bg-gray-50' : ''}`}>
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <Brain className={`w-8 h-8 mr-3 flex-shrink-0 ${provider.is_active ? 'text-primary-600' : 'text-gray-400'}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-900">{provider.name}</h3>
                      {provider.is_default && (
                        <span className="text-xs font-medium bg-primary-100 text-primary-700 px-2 py-0.5 rounded">Default</span>
                      )}
                      {provider.is_active ? (
                        <span className="px-2 py-0.5 bg-green-100 text-green-800 text-xs font-medium rounded">Active</span>
                      ) : (
                        <span className="px-2 py-0.5 bg-gray-200 text-gray-600 text-xs font-medium rounded">Inactive</span>
                      )}
                    </div>
                    <p className="text-sm text-gray-600">
                      Provider Type: <span className="font-medium">{provider.provider_type}</span>
                    </p>
                    {provider.api_endpoint && (
                      <p className="text-sm text-gray-600 truncate">
                        Endpoint: {provider.api_endpoint}
                      </p>
                    )}
                    {provider.default_model && (
                      <p className="text-sm text-gray-600">
                        Default Model: <span className="font-medium">{provider.default_model}</span>
                      </p>
                    )}
                    <p className="text-xs text-gray-500 mt-1">
                      Created: {new Date(provider.created_at).toLocaleString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center space-x-2 ml-4 flex-shrink-0">
                  <button
                    onClick={() => openEditModal(provider)}
                    className="text-blue-600 hover:text-blue-700"
                    disabled={updateMutation.isPending}
                  >
                    <Edit2 className="w-5 h-5" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this LLM provider?')) {
                        deleteMutation.mutate(provider.id);
                      }
                    }}
                    className="text-red-600 hover:text-red-700"
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="w-5 h-5" />
                  </button>
                </div>
              </div>
              {provider.config && Object.keys(provider.config).length > 0 && (
                <div className="mt-4 pt-4 border-t border-gray-200">
                  <p className="text-sm font-medium text-gray-700 mb-2">Configuration:</p>
                  <pre className="text-xs bg-gray-50 p-3 rounded overflow-x-auto">
                    {JSON.stringify(provider.config, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Brain className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No LLM providers configured</h3>
          <p className="text-gray-600 mb-4">Add language model providers to enable AI capabilities</p>
          <button onClick={() => setShowCreateModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Add Provider
          </button>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Add LLM Provider</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-2">
                  Provider Name *
                </label>
                <input
                  id="name"
                  type="text"
                  value={createFormData.name}
                  onChange={(e) => setCreateFormData({ ...createFormData, name: e.target.value })}
                  placeholder="OpenAI GPT-4"
                  required
                  className="input"
                  autoFocus
                />
              </div>

              <div>
                <label htmlFor="provider_type" className="block text-sm font-medium text-gray-700 mb-2">
                  Provider Type *
                </label>
                <select
                  id="provider_type"
                  value={createFormData.provider_type}
                  onChange={(e) => setCreateFormData({ ...createFormData, provider_type: e.target.value })}
                  className="input"
                  required
                >
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="mistral">Mistral AI</option>
                  <option value="ollama">Ollama</option>
                </select>
              </div>

              <div>
                <label htmlFor="api_key" className="block text-sm font-medium text-gray-700 mb-2">
                  API Key
                </label>
                <input
                  id="api_key"
                  type="password"
                  value={createFormData.api_key || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, api_key: e.target.value })}
                  placeholder="sk-..."
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="api_endpoint" className="block text-sm font-medium text-gray-700 mb-2">
                  API Endpoint
                </label>
                <input
                  id="api_endpoint"
                  type="text"
                  value={createFormData.api_endpoint || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, api_endpoint: e.target.value })}
                  placeholder="https://api.openai.com/v1"
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="default_model" className="block text-sm font-medium text-gray-700 mb-2">
                  Default Model
                </label>
                <input
                  id="default_model"
                  type="text"
                  value={createFormData.default_model || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, default_model: e.target.value })}
                  placeholder="gpt-4, mistral-large, llama3, etc."
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Default model to use with this provider
                </p>
              </div>

              <div>
                <label htmlFor="config" className="block text-sm font-medium text-gray-700 mb-2">
                  Configuration (JSON)
                </label>
                <textarea
                  id="config"
                  value={createConfigText}
                  onChange={(e) => {
                    setCreateConfigText(e.target.value);
                    try {
                      const parsed = JSON.parse(e.target.value);
                      setCreateFormData({ ...createFormData, config: parsed });
                    } catch {
                      // Invalid JSON, don't update formData yet
                    }
                  }}
                  placeholder='{"model": "gpt-4", "temperature": 0.7}'
                  rows={6}
                  className="input resize-none font-mono text-xs"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Provider-specific configuration as JSON
                </p>
              </div>

              <div className="flex items-center space-x-4">
                <div className="flex items-center">
                  <input
                    id="is_active"
                    type="checkbox"
                    checked={createFormData.is_active}
                    onChange={(e) => setCreateFormData({ ...createFormData, is_active: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
                  />
                  <label htmlFor="is_active" className="ml-2 block text-sm text-gray-700">
                    Active
                  </label>
                </div>

                <div className="flex items-center">
                  <input
                    id="is_default"
                    type="checkbox"
                    checked={createFormData.is_default}
                    onChange={(e) => setCreateFormData({ ...createFormData, is_default: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
                  />
                  <label htmlFor="is_default" className="ml-2 block text-sm text-gray-700">
                    Set as Default
                  </label>
                </div>
              </div>

              {createMutation.isError && (
                <ErrorDisplay error={createMutation.error} title="Failed to add provider" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    resetCreateForm();
                  }}
                  className="btn btn-secondary"
                  disabled={createMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createMutation.isPending || !createFormData.name.trim() || !createFormData.provider_type.trim()}
                >
                  {createMutation.isPending ? 'Adding...' : 'Add Provider'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {showEditModal && selectedProvider && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Edit LLM Provider</h2>
            <form onSubmit={handleEdit} className="space-y-4">
              <div>
                <label htmlFor="edit_name" className="block text-sm font-medium text-gray-700 mb-2">
                  Provider Name *
                </label>
                <input
                  id="edit_name"
                  type="text"
                  value={editFormData.name || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, name: e.target.value })}
                  placeholder="OpenAI GPT-4"
                  required
                  className="input"
                  autoFocus
                />
              </div>

              <div>
                <label htmlFor="edit_provider_type" className="block text-sm font-medium text-gray-700 mb-2">
                  Provider Type *
                </label>
                <select
                  id="edit_provider_type"
                  value={editFormData.provider_type || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, provider_type: e.target.value })}
                  className="input"
                  required
                >
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="mistral">Mistral AI</option>
                  <option value="ollama">Ollama</option>
                </select>
              </div>

              <div>
                <label htmlFor="edit_api_key" className="block text-sm font-medium text-gray-700 mb-2">
                  API Key
                </label>
                <input
                  id="edit_api_key"
                  type="password"
                  value={editFormData.api_key || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, api_key: e.target.value })}
                  placeholder="Leave empty to keep current key"
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  API key is never returned for security. Enter new key to update.
                </p>
              </div>

              <div>
                <label htmlFor="edit_api_endpoint" className="block text-sm font-medium text-gray-700 mb-2">
                  API Endpoint
                </label>
                <input
                  id="edit_api_endpoint"
                  type="text"
                  value={editFormData.api_endpoint || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, api_endpoint: e.target.value })}
                  placeholder="https://api.openai.com/v1"
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="edit_default_model" className="block text-sm font-medium text-gray-700 mb-2">
                  Default Model
                </label>
                <input
                  id="edit_default_model"
                  type="text"
                  value={editFormData.default_model || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, default_model: e.target.value })}
                  placeholder="gpt-4, mistral-large, llama3, etc."
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Default model to use with this provider
                </p>
              </div>

              <div>
                <label htmlFor="edit_config" className="block text-sm font-medium text-gray-700 mb-2">
                  Configuration (JSON)
                </label>
                <textarea
                  id="edit_config"
                  value={editConfigText}
                  onChange={(e) => {
                    setEditConfigText(e.target.value);
                    try {
                      const parsed = JSON.parse(e.target.value);
                      setEditFormData({ ...editFormData, config: parsed });
                    } catch {
                      // Invalid JSON, don't update formData yet
                    }
                  }}
                  placeholder='{"model": "gpt-4", "temperature": 0.7}'
                  rows={6}
                  className="input resize-none font-mono text-xs"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Provider-specific configuration as JSON
                </p>
              </div>

              <div className="flex items-center space-x-4">
                <div className="flex items-center">
                  <input
                    id="edit_is_active"
                    type="checkbox"
                    checked={editFormData.is_active ?? false}
                    onChange={(e) => setEditFormData({ ...editFormData, is_active: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
                  />
                  <label htmlFor="edit_is_active" className="ml-2 block text-sm text-gray-700">
                    Active
                  </label>
                </div>

                <div className="flex items-center">
                  <input
                    id="edit_is_default"
                    type="checkbox"
                    checked={editFormData.is_default ?? false}
                    onChange={(e) => setEditFormData({ ...editFormData, is_default: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
                  />
                  <label htmlFor="edit_is_default" className="ml-2 block text-sm text-gray-700">
                    Set as Default
                  </label>
                </div>
              </div>

              {updateMutation.isError && (
                <ErrorDisplay error={updateMutation.error} title="Failed to update provider" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowEditModal(false);
                    setSelectedProvider(null);
                    setEditFormData({});
                    setEditConfigText('{}');
                  }}
                  className="btn btn-secondary"
                  disabled={updateMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={updateMutation.isPending || !editFormData.name?.trim()}
                >
                  {updateMutation.isPending ? 'Updating...' : 'Update Provider'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
