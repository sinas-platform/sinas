import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Link, useNavigate } from 'react-router-dom';
import { Bot, Plus, Trash2, Edit, Copy, MessageSquare, Brain, Search } from 'lucide-react';
import { useState, useMemo } from 'react';
import { useToast } from '../lib/toast-context';
import { SchemaFormField } from '../components/SchemaFormField';
import type { AgentCreate, ChatCreate } from '../types';

export function Agents() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showError } = useToast();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showChatModal, setShowChatModal] = useState(false);
  const [chatAgent, setChatAgent] = useState<any>(null);
  const [chatInputParams, setChatInputParams] = useState<Record<string, any>>({});
  const [searchFilter, setSearchFilter] = useState('');
  const [formData, setFormData] = useState<AgentCreate>({
    namespace: 'default',
    name: '',
    description: '',
    system_prompt: '',
  });

  const { data: agents, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAgents(),
    retry: false,
  });

  const { data: llmProviders } = useQuery({
    queryKey: ['llmProviders'],
    queryFn: () => apiClient.listLLMProviders(),
    retry: false,
  });

  const activeProviders = llmProviders?.filter((p) => p.is_active) || [];

  const filteredAgents = useMemo(() => {
    if (!agents) return [];
    if (!searchFilter.trim()) return agents;
    const search = searchFilter.toLowerCase();
    return agents.filter((a) =>
      `${a.namespace}/${a.name}`.toLowerCase().includes(search) ||
      a.description?.toLowerCase().includes(search)
    );
  }, [agents, searchFilter]);

  const createMutation = useMutation({
    mutationFn: (data: AgentCreate) => apiClient.createAgent(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      setShowCreateModal(false);
      setFormData({ namespace: 'default', name: '', description: '', system_prompt: '' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) => apiClient.deleteAgent(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: async ({ namespace, name }: { namespace: string; name: string }) => {
      const agent = await apiClient.getAgent(namespace, name);
      const now = new Date();
      const timestamp = now.toISOString().replace('T', ' ').substring(0, 19);
      const duplicateData: AgentCreate = {
        namespace: agent.namespace,
        name: `${agent.name} (copy - ${timestamp})`,
        description: agent.description || undefined,
        llm_provider_id: agent.llm_provider_id || undefined,
        model: agent.model || undefined,
        temperature: agent.temperature ?? undefined,
        max_tokens: agent.max_tokens ?? undefined,
        system_prompt: agent.system_prompt || undefined,
        input_schema: agent.input_schema,
        output_schema: agent.output_schema,
        initial_messages: agent.initial_messages || undefined,
        enabled_functions: agent.enabled_functions,
        enabled_agents: agent.enabled_agents || undefined,
        enabled_skills: agent.enabled_skills || undefined,
        function_parameters: agent.function_parameters,
        state_namespaces_readonly: agent.state_namespaces_readonly || undefined,
        state_namespaces_readwrite: agent.state_namespaces_readwrite || undefined,
      };
      return apiClient.createAgent(duplicateData);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  });

  const startChatMutation = useMutation({
    mutationFn: ({ namespace, name, data }: { namespace: string; name: string; data: ChatCreate }) =>
      apiClient.createChatWithAgent(namespace, name, data),
    onSuccess: (chat: any) => {
      queryClient.invalidateQueries({ queryKey: ['chats'] });
      setShowChatModal(false);
      setChatAgent(null);
      setChatInputParams({});
      navigate(`/chats/${chat.id}`);
    },
    onError: (error: any) => {
      showError(error?.response?.data?.detail || 'Failed to create chat');
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (formData.name.trim()) {
      createMutation.mutate(formData);
    }
  };

  const handleStartChat = (agent: any) => {
    const inputSchema = agent.input_schema;
    const hasInputParams = inputSchema?.properties && Object.keys(inputSchema.properties).length > 0;
    if (hasInputParams) {
      setChatAgent(agent);
      setChatInputParams({});
      setShowChatModal(true);
    } else {
      startChatMutation.mutate({
        namespace: agent.namespace,
        name: agent.name,
        data: { title: `Chat with ${agent.name}` },
      });
    }
  };

  const handleChatModalSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatAgent) return;
    const data: ChatCreate = { title: `Chat with ${chatAgent.name}` };
    if (Object.keys(chatInputParams).length > 0) {
      data.input = chatInputParams;
    }
    startChatMutation.mutate({
      namespace: chatAgent.namespace,
      name: chatAgent.name,
      data,
    });
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

      {/* Search */}
      {agents && agents.length > 0 && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none" />
          <input
            type="text"
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            placeholder="Search agents by name, namespace, or description..."
            className="input w-full !pl-11"
          />
        </div>
      )}

      {/* Agents List */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
          <p className="text-gray-600 mt-2">Loading agents...</p>
        </div>
      ) : agents && agents.length > 0 ? (
        <div className="space-y-3">
          {filteredAgents.map((agent) => (
            <div key={agent.id} className="card hover:shadow-md transition-shadow">
              <div className="flex items-center gap-4">
                {/* Icon + status */}
                <div className="flex-shrink-0 relative">
                  <Bot className="w-8 h-8 text-primary-600" />
                  <div className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white ${agent.is_active ? 'bg-green-500' : 'bg-gray-300'}`} />
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-gray-900 truncate">
                      <span className="text-gray-500">{agent.namespace}/</span>{agent.name}
                    </h3>
                    {agent.is_default && (
                      <span className="text-xs font-medium bg-primary-100 text-primary-700 px-2 py-0.5 rounded flex-shrink-0">Default</span>
                    )}
                  </div>
                  <p className="text-sm text-gray-600 truncate mt-0.5">
                    {agent.description || 'No description'}
                  </p>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1">
                    {agent.model && (
                      <span className="text-xs text-gray-500">{agent.model}</span>
                    )}
                    {agent.enabled_functions.length > 0 && (
                      <span className="text-xs text-gray-500">{agent.enabled_functions.length} functions</span>
                    )}
                    {agent.enabled_agents && agent.enabled_agents.length > 0 && (
                      <span className="text-xs text-gray-500">{agent.enabled_agents.length} agents</span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button
                    onClick={() => handleStartChat(agent)}
                    disabled={startChatMutation.isPending}
                    className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-green-700 bg-green-50 hover:bg-green-100 rounded-md transition-colors"
                    title="Start chat"
                  >
                    <MessageSquare className="w-4 h-4 mr-1.5" />
                    Chat
                  </button>
                  <Link
                    to={`/agents/${agent.namespace}/${agent.name}`}
                    className="p-2 text-gray-500 hover:text-primary-600 hover:bg-gray-100 rounded-md transition-colors"
                    title="Edit"
                  >
                    <Edit className="w-4 h-4" />
                  </Link>
                  <button
                    onClick={() => {
                      if (confirm('Create a duplicate of this agent?')) {
                        duplicateMutation.mutate({ namespace: agent.namespace, name: agent.name });
                      }
                    }}
                    className="p-2 text-gray-500 hover:text-blue-600 hover:bg-gray-100 rounded-md transition-colors"
                    disabled={duplicateMutation.isPending}
                    title="Duplicate"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this agent?')) {
                        deleteMutation.mutate({ namespace: agent.namespace, name: agent.name });
                      }
                    }}
                    className="p-2 text-gray-500 hover:text-red-600 hover:bg-gray-100 rounded-md transition-colors"
                    disabled={deleteMutation.isPending}
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
          {searchFilter && filteredAgents.length === 0 && (
            <div className="text-center py-8 text-gray-500">No agents match your search</div>
          )}
        </div>
      ) : activeProviders.length === 0 ? (
        <div className="text-center py-12 card">
          <Brain className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">Configure an LLM Provider first</h3>
          <p className="text-gray-600 mb-4">You need at least one active LLM provider before creating agents</p>
          <Link to="/llm-providers" className="btn btn-primary">
            Configure LLM Provider
          </Link>
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
                <p className="text-xs text-gray-500 mt-1">
                  Allowed: letters, numbers, spaces, underscores, hyphens, parentheses, colons
                </p>
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

      {/* Start Chat Modal (for agents with input_schema) */}
      {showChatModal && chatAgent && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-900 mb-1">Start Chat</h2>
            <p className="text-sm text-gray-500 mb-4">
              Configure input parameters for <span className="font-medium">{chatAgent.namespace}/{chatAgent.name}</span>
            </p>
            <form onSubmit={handleChatModalSubmit} className="space-y-4">
              {(() => {
                const inputSchema = chatAgent.input_schema;
                const properties = inputSchema?.properties || {};
                const requiredFields = inputSchema?.required || [];
                return Object.entries(properties).map(([key, prop]: [string, any]) => (
                  <SchemaFormField
                    key={key}
                    name={key}
                    schema={prop}
                    value={chatInputParams[key]}
                    onChange={(value) => setChatInputParams({ ...chatInputParams, [key]: value })}
                    required={requiredFields.includes(key)}
                  />
                ));
              })()}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowChatModal(false);
                    setChatAgent(null);
                    setChatInputParams({});
                  }}
                  className="btn btn-secondary"
                  disabled={startChatMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={startChatMutation.isPending}
                >
                  {startChatMutation.isPending ? 'Starting...' : 'Start Chat'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
