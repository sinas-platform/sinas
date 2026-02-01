import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Link } from 'react-router-dom';
import { Bot, Plus, Trash2, Edit, CheckCircle, XCircle } from 'lucide-react';
import { useState } from 'react';
import type { AssistantCreate } from '../types';

export function Agents() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [formData, setFormData] = useState<AssistantCreate>({
    namespace: 'default',
    name: '',
    description: '',
    system_prompt: '',
  });

  const { data: agents, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAssistants(),
    retry: false,
  });

  const { data: llmProviders } = useQuery({
    queryKey: ['llmProviders'],
    queryFn: () => apiClient.listLLMProviders(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: AssistantCreate) => apiClient.createAssistant(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      setShowCreateModal(false);
      setFormData({ namespace: 'default', name: '', description: '', system_prompt: '' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) => apiClient.deleteAssistant(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (formData.name.trim()) {
      createMutation.mutate(formData);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Agents</h1>
          <p className="text-gray-600 mt-1">Manage your AI agents and their configurations</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          New Agent
        </button>
      </div>

      {/* Agents Grid */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
          <p className="text-gray-600 mt-2">Loading agents...</p>
        </div>
      ) : agents && agents.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {agents.map((agent) => (
            <div key={agent.id} className="card hover:shadow-md transition-shadow">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center flex-1 min-w-0">
                  <Bot className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="min-w-0 flex-1">
                    <h3 className="font-semibold text-gray-900 truncate">{agent.name}</h3>
                    <p className="text-xs text-gray-500">
                      {new Date(agent.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="ml-2 flex-shrink-0">
                  {agent.is_active ? (
                    <CheckCircle className="w-5 h-5 text-green-500" />
                  ) : (
                    <XCircle className="w-5 h-5 text-gray-400" />
                  )}
                </div>
              </div>

              <div className="mb-4">
                <p className="text-sm text-gray-600 line-clamp-2 min-h-[40px]">
                  {agent.description || 'No description provided'}
                </p>
              </div>

              <div className="space-y-2 mb-4">
                {agent.llm_provider_id && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">Provider:</span>{' '}
                    {llmProviders?.find(p => p.id === agent.llm_provider_id)?.name || 'Unknown'}
                  </div>
                )}
                {agent.model && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">Model:</span> {agent.model}
                  </div>
                )}
                {agent.system_prompt && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">System Prompt:</span> Configured
                  </div>
                )}
                {agent.input_schema && Object.keys(agent.input_schema).length > 0 && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">Input Schema:</span> Defined
                  </div>
                )}
                {agent.output_schema && Object.keys(agent.output_schema).length > 0 && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">Output Schema:</span> Defined
                  </div>
                )}
                {agent.enabled_agents && agent.enabled_agents.length > 0 && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">Other Agents:</span> {agent.enabled_agents.length}
                  </div>
                )}
                {agent.enabled_functions.length > 0 && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">Functions:</span> {agent.enabled_functions.length}
                  </div>
                )}
                {agent.enabled_mcp_tools.length > 0 && (
                  <div className="text-xs text-gray-600">
                    <span className="font-medium">MCP Tools:</span> {agent.enabled_mcp_tools.length}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between pt-4 border-t border-gray-200">
                <Link
                  to={`/agents/${agent.namespace}/${agent.name}`}
                  className="text-sm text-primary-600 hover:text-primary-700 flex items-center"
                >
                  <Edit className="w-4 h-4 mr-1" />
                  Edit
                </Link>
                <button
                  onClick={() => {
                    if (confirm('Are you sure you want to delete this agent?')) {
                      deleteMutation.mutate({ namespace: agent.namespace, name: agent.name });
                    }
                  }}
                  className="text-sm text-red-600 hover:text-red-700 flex items-center"
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Bot className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No agents yet</h3>
          <p className="text-gray-600 mb-4">Create your first AI agent to get started</p>
          <button onClick={() => setShowCreateModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Create Agent
          </button>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Create New Agent</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label htmlFor="namespace" className="block text-sm font-medium text-gray-700 mb-2">
                  Namespace *
                </label>
                <input
                  id="namespace"
                  type="text"
                  value={formData.namespace}
                  onChange={(e) => setFormData({ ...formData, namespace: e.target.value })}
                  placeholder="default"
                  required
                  className="input"
                  autoFocus
                />
                <p className="text-xs text-gray-500 mt-1">
                  Namespace for organizing agents (e.g., "default", "customer-service")
                </p>
              </div>

              <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-2">
                  Name *
                </label>
                <input
                  id="name"
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="My Agent"
                  required
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="description" className="block text-sm font-medium text-gray-700 mb-2">
                  Description
                </label>
                <input
                  id="description"
                  type="text"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="A helpful assistant that..."
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="system_prompt" className="block text-sm font-medium text-gray-700 mb-2">
                  System Prompt
                </label>
                <textarea
                  id="system_prompt"
                  value={formData.system_prompt}
                  onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                  placeholder="You are a helpful assistant..."
                  rows={4}
                  className="input resize-none"
                />
              </div>

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ namespace: 'default', name: '', description: '', system_prompt: '' });
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
                  {createMutation.isPending ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
