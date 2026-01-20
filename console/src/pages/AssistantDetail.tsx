import { useParams, Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useState, useEffect } from 'react';
import { ArrowLeft, Save, Trash2, Loader2, Bot } from 'lucide-react';
import type { AssistantUpdate } from '../types';

export function AssistantDetail() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: assistant, isLoading } = useQuery({
    queryKey: ['assistant', namespace, name],
    queryFn: () => apiClient.getAssistant(namespace!, name!),
    enabled: !!namespace && !!name,
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });

  const { data: states } = useQuery({
    queryKey: ['states'],
    queryFn: () => apiClient.listStates(),
    retry: false,
  });

  const { data: groups } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listGroups(),
    retry: false,
  });

  const { data: assistants } = useQuery({
    queryKey: ['assistants'],
    queryFn: () => apiClient.listAssistants(),
    retry: false,
  });

  const { data: llmProviders } = useQuery({
    queryKey: ['llmProviders'],
    queryFn: () => apiClient.listLLMProviders(),
    retry: false,
  });

  const { data: mcpTools } = useQuery({
    queryKey: ['mcpTools'],
    queryFn: () => apiClient.listMCPTools(),
    retry: false,
  });

  const [formData, setFormData] = useState<AssistantUpdate>({});
  const [toolsTab, setToolsTab] = useState<'assistants' | 'functions' | 'mcp' | 'states'>('assistants');

  // Initialize form data when assistant loads
  useEffect(() => {
    if (assistant) {
      setFormData({
        name: assistant.name,
        description: assistant.description || '',
        llm_provider_id: assistant.llm_provider_id || undefined,
        model: assistant.model || undefined,
        temperature: assistant.temperature,
        max_tokens: assistant.max_tokens ?? undefined,
        system_prompt: assistant.system_prompt || undefined,
        input_schema: assistant.input_schema || {},
        output_schema: assistant.output_schema || {},
        initial_messages: assistant.initial_messages || [],
        is_active: assistant.is_active,
        enabled_functions: assistant.enabled_functions || [],
        enabled_mcp_tools: assistant.enabled_mcp_tools || [],
        enabled_agents: assistant.enabled_agents || [],
        state_namespaces_readonly: assistant.state_namespaces_readonly || [],
        state_namespaces_readwrite: assistant.state_namespaces_readwrite || [],
      });
    }
  }, [assistant]);

  const updateMutation = useMutation({
    mutationFn: (data: AssistantUpdate) => apiClient.updateAssistant(namespace!, name!, data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['assistant', namespace, name] });
      queryClient.invalidateQueries({ queryKey: ['assistants'] });
      if (data.namespace !== namespace || data.name !== name) {
        // Name or namespace changed, navigate to new URL
        navigate(`/agents/${data.namespace}/${data.name}`);
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.deleteAssistant(namespace!, name!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assistants'] });
      navigate('/agents');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    updateMutation.mutate(formData);
  };

  const handleDelete = () => {
    if (confirm('Are you sure you want to delete this assistant? This action cannot be undone.')) {
      deleteMutation.mutate();
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    );
  }

  if (!assistant) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold text-gray-900">Agent not found</h2>
        <Link to="/agents" className="text-primary-600 hover:text-primary-700 mt-2 inline-block">
          Back to agents
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <Link to="/agents" className="mr-4 text-gray-600 hover:text-gray-900">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-900">{assistant.name}</h1>
            <p className="text-gray-600 mt-1">Configure your AI agent</p>
          </div>
        </div>
        <button
          onClick={handleDelete}
          disabled={deleteMutation.isPending}
          className="btn btn-danger flex items-center"
        >
          <Trash2 className="w-4 h-4 mr-2" />
          Delete
        </button>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Basic Information</h2>

          <div className="space-y-4">
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-2">
                Name *
              </label>
              <input
                id="name"
                type="text"
                value={formData.name || assistant.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="input"
                required
              />
            </div>

            <div>
              <label htmlFor="description" className="block text-sm font-medium text-gray-700 mb-2">
                Description
              </label>
              <input
                id="description"
                type="text"
                value={formData.description ?? assistant.description ?? ''}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="A helpful agent that..."
                className="input"
              />
            </div>

            <div>
              <label htmlFor="group_id" className="block text-sm font-medium text-gray-700 mb-2">
                Group
              </label>
              <select
                id="group_id"
                value={formData.group_id ?? assistant.group_id ?? ''}
                onChange={(e) => setFormData({ ...formData, group_id: e.target.value || undefined })}
                className="input"
              >
                <option value="">No group (Personal)</option>
                {groups?.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
              <p className="text-xs text-gray-500 mt-1">
                Assign to a group to share with team members
              </p>
            </div>

            <div>
              <label htmlFor="system_prompt" className="block text-sm font-medium text-gray-700 mb-2">
                System Prompt
              </label>
              <textarea
                id="system_prompt"
                value={formData.system_prompt ?? assistant.system_prompt ?? ''}
                onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                placeholder="You are a helpful agent that..."
                rows={8}
                className="input resize-none font-mono text-sm"
              />
              <p className="text-xs text-gray-500 mt-1">
                This prompt defines the agent's behavior and personality. Supports Jinja2 templates.
              </p>
            </div>

            <div className="flex items-center">
              <input
                id="is_active"
                type="checkbox"
                checked={formData.is_active ?? assistant.is_active}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
              />
              <label htmlFor="is_active" className="ml-2 text-sm text-gray-700">
                Active (agent can be used in chats)
              </label>
            </div>
          </div>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">LLM Configuration</h2>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="llm_provider_id" className="block text-sm font-medium text-gray-700 mb-2">
                  LLM Provider
                </label>
                <select
                  id="llm_provider_id"
                  value={formData.llm_provider_id ?? assistant.llm_provider_id ?? ''}
                  onChange={(e) => {
                    const providerId = e.target.value || undefined;
                    setFormData({
                      ...formData,
                      llm_provider_id: providerId,
                    });
                  }}
                  className="input"
                >
                  <option value="">No provider (use default)</option>
                  {llmProviders?.filter(p => p.is_active).map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name} ({provider.provider_type})
                    </option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  Select a configured LLM provider
                </p>
              </div>

              <div>
                <label htmlFor="model" className="block text-sm font-medium text-gray-700 mb-2">
                  Model
                </label>
                <input
                  id="model"
                  type="text"
                  value={formData.model ?? assistant.model ?? ''}
                  onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                  placeholder="gpt-4o, claude-3-opus, etc."
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Enter the model name to use with the selected provider
                </p>
              </div>
            </div>

            <div>
              <label htmlFor="temperature" className="block text-sm font-medium text-gray-700 mb-2">
                Temperature ({formData.temperature ?? assistant.temperature})
              </label>
              <input
                id="temperature"
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={formData.temperature ?? assistant.temperature}
                onChange={(e) => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                className="w-full"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>Precise (0)</span>
                <span>Balanced (1)</span>
                <span>Creative (2)</span>
              </div>
            </div>

            <div>
              <label htmlFor="max_tokens" className="block text-sm font-medium text-gray-700 mb-2">
                Max Tokens (optional)
              </label>
              <input
                id="max_tokens"
                type="number"
                min="1"
                max="200000"
                value={formData.max_tokens ?? assistant.max_tokens ?? ''}
                onChange={(e) => setFormData({ ...formData, max_tokens: e.target.value ? parseInt(e.target.value) : undefined })}
                placeholder="Leave empty for provider default"
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">
                Maximum number of tokens to generate in the response
              </p>
            </div>
          </div>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Input/Output Schemas</h2>
          <p className="text-sm text-gray-600 mb-4">
            Define JSON schemas for input variables and expected output structure
          </p>

          <div className="space-y-4">
            <div>
              <label htmlFor="input_schema" className="block text-sm font-medium text-gray-700 mb-2">
                Input Schema (JSON)
              </label>
              <textarea
                id="input_schema"
                value={JSON.stringify(formData.input_schema ?? assistant.input_schema ?? {}, null, 2)}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value);
                    setFormData({ ...formData, input_schema: parsed });
                  } catch {
                    // Invalid JSON, don't update
                  }
                }}
                placeholder='{"type": "object", "properties": {"variable_name": {"type": "string"}}}'
                rows={6}
                className="input resize-none font-mono text-xs"
              />
              <p className="text-xs text-gray-500 mt-1">
                JSON Schema defining input variables (for use in system prompt templates)
              </p>
            </div>

            <div>
              <label htmlFor="output_schema" className="block text-sm font-medium text-gray-700 mb-2">
                Output Schema (JSON)
              </label>
              <textarea
                id="output_schema"
                value={JSON.stringify(formData.output_schema ?? assistant.output_schema ?? {}, null, 2)}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value);
                    setFormData({ ...formData, output_schema: parsed });
                  } catch {
                    // Invalid JSON, don't update
                  }
                }}
                placeholder='{"type": "object", "properties": {"result": {"type": "string"}}}'
                rows={6}
                className="input resize-none font-mono text-xs"
              />
              <p className="text-xs text-gray-500 mt-1">
                JSON Schema defining expected response structure (empty = no enforcement)
              </p>
            </div>

            <div>
              <label htmlFor="initial_messages" className="block text-sm font-medium text-gray-700 mb-2">
                Initial Messages (JSON)
              </label>
              <textarea
                id="initial_messages"
                value={JSON.stringify(formData.initial_messages ?? assistant.initial_messages ?? [], null, 2)}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value);
                    setFormData({ ...formData, initial_messages: parsed });
                  } catch {
                    // Invalid JSON, don't update
                  }
                }}
                placeholder='[{"role": "user", "content": "Example"}, {"role": "assistant", "content": "Response"}]'
                rows={6}
                className="input resize-none font-mono text-xs"
              />
              <p className="text-xs text-gray-500 mt-1">
                Few-shot learning: initial message history for context
              </p>
            </div>
          </div>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Tools & Access</h2>

          {/* Tabs */}
          <div className="flex border-b border-gray-200 mb-4">
            <button
              type="button"
              onClick={() => setToolsTab('assistants')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'assistants'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Other Agents
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('functions')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'functions'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Functions
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('mcp')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'mcp'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              MCP Tools
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('states')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'states'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              States
            </button>
          </div>

          {/* Tab Content */}
          <div className="space-y-4">
            {/* Other Agents Tab */}
            {toolsTab === 'assistants' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Enable other agents to be called as tools by this agent
                </p>
                {assistants && assistants.length > 1 ? (
                  <div className="space-y-2 border border-gray-200 rounded-lg p-3 max-h-96 overflow-y-auto">
                    {assistants
                      .filter((a: any) => a.id !== assistant.id)
                      .map((otherAssistant: any) => (
                        <label
                          key={otherAssistant.id}
                          className="flex items-start p-2 hover:bg-gray-50 rounded cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={(formData.enabled_agents || assistant.enabled_agents || []).includes(otherAssistant.id)}
                            onChange={(e) => {
                              const current = formData.enabled_agents || assistant.enabled_agents || [];
                              const updated = e.target.checked
                                ? [...current, otherAssistant.id]
                                : current.filter((id: string) => id !== otherAssistant.id);
                              setFormData({ ...formData, enabled_agents: updated });
                            }}
                            className="mt-1 w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                          />
                          <div className="ml-3 flex-1">
                            <div className="flex items-center gap-2">
                              <Bot className="w-4 h-4 text-primary-600" />
                              <span className="text-sm font-medium text-gray-900">{otherAssistant.name}</span>
                            </div>
                            {otherAssistant.description && (
                              <p className="text-xs text-gray-500 mt-0.5">{otherAssistant.description}</p>
                            )}
                            {otherAssistant.provider && otherAssistant.model && (
                              <p className="text-xs text-gray-400 mt-0.5">
                                {otherAssistant.provider}/{otherAssistant.model}
                              </p>
                            )}
                          </div>
                        </label>
                      ))}
                  </div>
                ) : (
                  <div className="bg-gray-50 rounded-lg p-3 border border-gray-200">
                    <p className="text-sm text-gray-500">No other agents available.</p>
                  </div>
                )}
              </div>
            )}

            {/* Functions Tab */}
            {toolsTab === 'functions' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Select which functions this agent can call
                </p>
                {functions && functions.length > 0 ? (
                  <div className="space-y-2 border border-gray-200 rounded-lg p-3 max-h-96 overflow-y-auto">
                    {functions.map((func: any) => {
                      const funcIdentifier = `${func.namespace}/${func.name}`;
                      return (
                        <label
                          key={func.id}
                          className="flex items-start p-2 hover:bg-gray-50 rounded cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={(formData.enabled_functions || assistant.enabled_functions || []).includes(funcIdentifier)}
                            onChange={(e) => {
                              const currentFunctions = formData.enabled_functions || assistant.enabled_functions || [];
                              const newFunctions = e.target.checked
                                ? [...currentFunctions, funcIdentifier]
                                : currentFunctions.filter((id: string) => id !== funcIdentifier);
                              setFormData({ ...formData, enabled_functions: newFunctions });
                            }}
                            className="mt-1 w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                          />
                          <div className="ml-3 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-gray-900 font-mono">
                                {funcIdentifier}
                              </span>
                              {!func.is_active && (
                                <span className="px-2 py-0.5 bg-gray-100 text-gray-600 text-xs font-medium rounded">
                                  Inactive
                                </span>
                              )}
                            </div>
                            {func.description && (
                              <p className="text-xs text-gray-600 mt-0.5">{func.description}</p>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-gray-50 rounded-lg p-3 border border-gray-200">
                    <p className="text-sm text-gray-500">No functions available. Create functions first.</p>
                  </div>
                )}
              </div>
            )}

            {/* MCP Tools Tab */}
            {toolsTab === 'mcp' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Select MCP tools that this agent can use
                </p>
                {mcpTools && mcpTools.length > 0 ? (
                  <div className="space-y-2 border border-gray-200 rounded-lg p-3 max-h-96 overflow-y-auto">
                    {mcpTools.map((tool: any) => {
                      const toolName = tool.name || tool.tool_name;
                      const isEnabled = (formData.enabled_mcp_tools || assistant.enabled_mcp_tools || []).includes(toolName);

                      return (
                        <label
                          key={toolName}
                          className="flex items-start p-2 hover:bg-gray-50 rounded cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={isEnabled}
                            onChange={(e) => {
                              const current = formData.enabled_mcp_tools || assistant.enabled_mcp_tools || [];
                              const updated = e.target.checked
                                ? [...current, toolName]
                                : current.filter((name: string) => name !== toolName);
                              setFormData({ ...formData, enabled_mcp_tools: updated });
                            }}
                            className="mt-1 w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                          />
                          <div className="ml-3 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-gray-900 font-mono">{toolName}</span>
                              {tool.server_name && (
                                <span className="text-xs text-gray-500">({tool.server_name})</span>
                              )}
                            </div>
                            {tool.description && (
                              <p className="text-xs text-gray-500 mt-0.5">{tool.description}</p>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-gray-50 rounded-lg p-3 border border-gray-200">
                    <p className="text-sm text-gray-500">No MCP tools available. Configure and activate MCP servers first.</p>
                  </div>
                )}
              </div>
            )}

            {/* States Tab */}
            {toolsTab === 'states' && (
              <div className="space-y-4">
                {/* Read-only Namespaces */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 mb-2">Read-only State Namespaces</h3>
                  <p className="text-xs text-gray-500 mb-3">
                    This agent can retrieve states from these namespaces (read-only)
                  </p>
                  {states && states.length > 0 ? (
                    <div className="space-y-2 border border-gray-200 rounded-lg p-3 max-h-64 overflow-y-auto">
                      {Array.from(new Set(states.map((c: any) => c.namespace))).map((namespace: string) => (
                        <label
                          key={namespace}
                          className="flex items-start p-2 hover:bg-gray-50 rounded cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={(formData.state_namespaces_readonly || assistant.state_namespaces_readonly || []).includes(namespace)}
                            onChange={(e) => {
                              const current = formData.state_namespaces_readonly || assistant.state_namespaces_readonly || [];
                              const updated = e.target.checked
                                ? [...current, namespace]
                                : current.filter((ns: string) => ns !== namespace);
                              setFormData({ ...formData, state_namespaces_readonly: updated });
                            }}
                            className="mt-1 w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                          />
                          <div className="ml-3 flex-1">
                            <span className="text-sm font-medium text-gray-900 font-mono">{namespace}</span>
                            <p className="text-xs text-gray-500 mt-0.5">
                              {states.filter((c: any) => c.namespace === namespace).length} state(s)
                            </p>
                          </div>
                        </label>
                      ))}
                    </div>
                  ) : (
                    <div className="bg-gray-50 rounded-lg p-3 border border-gray-200">
                      <p className="text-sm text-gray-500">No states available. Create states first.</p>
                    </div>
                  )}
                </div>

                {/* Read-write Namespaces */}
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 mb-2">Read-write State Namespaces</h3>
                  <p className="text-xs text-gray-500 mb-3">
                    This agent can save, update, and delete states in these namespaces (full access)
                  </p>
                  {states && states.length > 0 ? (
                    <div className="space-y-2 border border-gray-200 rounded-lg p-3 max-h-64 overflow-y-auto">
                      {Array.from(new Set(states.map((c: any) => c.namespace))).map((namespace: string) => (
                        <label
                          key={namespace}
                          className="flex items-start p-2 hover:bg-gray-50 rounded cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={(formData.state_namespaces_readwrite || assistant.state_namespaces_readwrite || []).includes(namespace)}
                            onChange={(e) => {
                              const current = formData.state_namespaces_readwrite || assistant.state_namespaces_readwrite || [];
                              const updated = e.target.checked
                                ? [...current, namespace]
                                : current.filter((ns: string) => ns !== namespace);
                              setFormData({ ...formData, state_namespaces_readwrite: updated });
                            }}
                            className="mt-1 w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                          />
                          <div className="ml-3 flex-1">
                            <span className="text-sm font-medium text-gray-900 font-mono">{namespace}</span>
                            <p className="text-xs text-gray-500 mt-0.5">
                              {states.filter((c: any) => c.namespace === namespace).length} state(s)
                            </p>
                          </div>
                        </label>
                      ))}
                    </div>
                  ) : (
                    <div className="bg-gray-50 rounded-lg p-3 border border-gray-200">
                      <p className="text-sm text-gray-500">No states available. Create states first.</p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="card bg-gray-50">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Metadata</h2>
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="font-medium text-gray-700">Created</dt>
              <dd className="text-gray-600">{new Date(assistant.created_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-700">Last Updated</dt>
              <dd className="text-gray-600">{new Date(assistant.updated_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-700">Agent ID</dt>
              <dd className="text-gray-600 font-mono text-xs">{assistant.id}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-700">User ID</dt>
              <dd className="text-gray-600 font-mono text-xs">{assistant.user_id || 'N/A'}</dd>
            </div>
          </dl>
        </div>

        {/* Actions */}
        <div className="flex justify-end space-x-3">
          <Link to="/agents" className="btn btn-secondary">
            Cancel
          </Link>
          <button
            type="submit"
            disabled={updateMutation.isPending}
            className="btn btn-primary flex items-center"
          >
            {updateMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="w-4 h-4 mr-2" />
                Save Changes
              </>
            )}
          </button>
        </div>

        {updateMutation.isSuccess && (
          <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
            Agent updated successfully!
          </div>
        )}

        {updateMutation.isError && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            Failed to update agent. Please try again.
          </div>
        )}
      </form>
    </div>
  );
}
