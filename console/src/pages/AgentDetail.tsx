import { useParams, Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, API_BASE_URL } from '../lib/api';
import { useState, useEffect } from 'react';
import { ArrowLeft, Save, Trash2, Loader2, Bot } from 'lucide-react';
import type { AgentUpdate } from '../types';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';
import { ApiUsage } from '../components/ApiUsage';

export function AgentDetail() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: agent, isLoading } = useQuery({
    queryKey: ['agent', namespace, name],
    queryFn: () => apiClient.getAgent(namespace!, name!),
    enabled: !!namespace && !!name,
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });

  const { data: skills } = useQuery({
    queryKey: ['skills'],
    queryFn: () => apiClient.listSkills(),
    retry: false,
  });

  const { data: stores } = useQuery({
    queryKey: ['stores'],
    queryFn: () => apiClient.listStores(),
    retry: false,
  });

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAgents(),
    retry: false,
  });

  const { data: llmProviders } = useQuery({
    queryKey: ['llmProviders'],
    queryFn: () => apiClient.listLLMProviders(),
    retry: false,
  });

  const { data: collections } = useQuery({
    queryKey: ['collections'],
    queryFn: () => apiClient.listCollections(),
    retry: false,
  });

  const { data: queries } = useQuery({
    queryKey: ['queries'],
    queryFn: () => apiClient.listQueries(),
    retry: false,
  });

  const { data: connectors } = useQuery({
    queryKey: ['connectors'],
    queryFn: () => apiClient.listConnectors(),
    retry: false,
  });

  const { data: databaseConnections } = useQuery({
    queryKey: ['databaseConnections'],
    queryFn: () => apiClient.listDatabaseConnections(),
    retry: false,
  });

  const [formData, setFormData] = useState<AgentUpdate>({});
  const { data: components } = useQuery({
    queryKey: ['components'],
    queryFn: () => apiClient.listComponents(),
    retry: false,
  });

  const [toolsTab, setToolsTab] = useState<'assistants' | 'skills' | 'functions' | 'queries' | 'states' | 'collections' | 'components' | 'connectors' | 'hooks' | 'status' | 'platform'>('assistants');
  const [expandedFunctionParams, setExpandedFunctionParams] = useState<Set<string>>(new Set());
  const [expandedConnectorParams, setExpandedConnectorParams] = useState<Set<string>>(new Set());
  const [iconMode, setIconMode] = useState<'collection' | 'url'>('collection');
  const [iconCollectionNs, setIconCollectionNs] = useState('');
  const [iconCollectionName, setIconCollectionName] = useState('');
  const [iconCollectionFiles, setIconCollectionFiles] = useState<any[]>([]);

  // Initialize form data when agent loads
  useEffect(() => {
    if (agent) {
      setFormData({
        namespace: agent.namespace,
        name: agent.name,
        description: agent.description || '',
        llm_provider_id: agent.llm_provider_id || undefined,
        model: agent.model || undefined,
        temperature: agent.temperature,
        max_tokens: agent.max_tokens ?? undefined,
        system_prompt: agent.system_prompt || undefined,
        input_schema: agent.input_schema || {},
        output_schema: agent.output_schema || {},
        initial_messages: agent.initial_messages || [],
        is_active: agent.is_active,
        is_default: agent.is_default,
        enabled_functions: agent.enabled_functions || [],
        enabled_agents: agent.enabled_agents || [],
        enabled_skills: agent.enabled_skills || [],
        function_parameters: agent.function_parameters || {},
        enabled_queries: agent.enabled_queries || [],
        query_parameters: agent.query_parameters || {},
        enabled_stores: agent.enabled_stores || [],
        enabled_collections: agent.enabled_collections || [],
        enabled_components: agent.enabled_components || [],
        enabled_connectors: agent.enabled_connectors || [],
        hooks: agent.hooks || { on_user_message: [], on_assistant_message: [] },
        status_templates: agent.status_templates || {},
        icon: agent.icon || undefined,
        default_job_timeout: agent.default_job_timeout ?? undefined,
        default_keep_alive: agent.default_keep_alive,
        system_tools: agent.system_tools || [],
      });
    }
  }, [agent]);

  const updateMutation = useMutation({
    mutationFn: (data: AgentUpdate) => apiClient.updateAgent(namespace!, name!, data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['agent', namespace, name] });
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      if (data.namespace !== namespace || data.name !== name) {
        // Name or namespace changed, navigate to new URL
        navigate(`/agents/${data.namespace}/${data.name}`);
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.deleteAgent(namespace!, name!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
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

  if (!agent) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold text-gray-100">Agent not found</h2>
        <Link to="/agents" className="text-primary-600 hover:text-primary-400 mt-2 inline-block">
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
          <Link to="/agents" className="mr-4 text-gray-400 hover:text-gray-100">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-100">{agent.name}</h1>
            <p className="text-gray-400 mt-1">Configure your AI agent</p>
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

      {agent && (
        <ApiUsage
          curl={[
            {
              label: 'Invoke (simple request/response)',
              language: 'bash',
              code: `curl -X POST ${API_BASE_URL}/agents/${agent.namespace}/${agent.name}/invoke \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"message": "Hello"${agent.input_schema && Object.keys(agent.input_schema.properties || {}).length > 0
    ? `, "input": {${Object.keys(agent.input_schema.properties || {}).map(k => `"${k}": "..."`).join(', ')}}`
    : ''}}'

# With session key (conversation continuity):
curl -X POST ${API_BASE_URL}/agents/${agent.namespace}/${agent.name}/invoke \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"message": "Hello", "session_key": "slack:U09ABC123"}'`,
            },
            {
              label: 'Create a chat',
              language: 'bash',
              code: `curl -X POST ${API_BASE_URL}/agents/${agent.namespace}/${agent.name}/chats \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '${agent.input_schema && Object.keys(agent.input_schema.properties || {}).length > 0
    ? `{"title": "My chat", "input": {${Object.keys(agent.input_schema.properties || {}).map(k => `"${k}": "..."`).join(', ')}}}`
    : '{"title": "My chat"}'}'`,
            },
            {
              label: 'Send a message (streaming)',
              language: 'bash',
              code: `curl -N -X POST ${API_BASE_URL}/chats/{chat_id}/messages/stream \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"content": "Hello"}'`,
            },
          ]}
          sdk={[
            {
              label: 'Create a chat and send messages',
              language: 'python',
              code: `from sinas import SinasClient

client = SinasClient(base_url="${API_BASE_URL}", api_key="sk-...")

# Simple invoke (one request, one response)
result = client.agents.invoke("${agent.namespace}", "${agent.name}",
    message="Hello"${
  agent.input_schema && Object.keys(agent.input_schema.properties || {}).length > 0
    ? `,\n    input={${Object.keys(agent.input_schema.properties || {}).map(k => `"${k}": "..."`).join(', ')}}`
    : ''})
print(result["reply"])

# With session key (maintains conversation across calls)
result = client.agents.invoke("${agent.namespace}", "${agent.name}",
    message="What was my last question?",
    session_key="slack:U09ABC123")

# Streaming (for longer interactions)
chat = client.chats.create("${agent.namespace}", "${agent.name}")
import json
for chunk in client.chats.stream(chat["id"], "Hello"):
    data = json.loads(chunk)
    if "content" in data:
        print(data["content"], end="", flush=True)`,
            },
          ]}
        />
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Basic Information</h2>

          <div className="space-y-4">
            <div>
              <label htmlFor="namespace" className="block text-sm font-medium text-gray-300 mb-2">
                Namespace *
              </label>
              <input
                id="namespace"
                type="text"
                value={formData.namespace || agent.namespace}
                onChange={(e) => setFormData({ ...formData, namespace: e.target.value })}
                className="input"
                required
              />
              <p className="text-xs text-gray-500 mt-1">
                Namespace for organizing agents (e.g., "default", "customer-service")
              </p>
            </div>

            <div>
              <label htmlFor="name" className="block text-sm font-medium text-gray-300 mb-2">
                Name *
              </label>
              <input
                id="name"
                type="text"
                value={formData.name || agent.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="input"
                required
              />
            </div>

            <div>
              <label htmlFor="description" className="block text-sm font-medium text-gray-300 mb-2">
                Description
              </label>
              <input
                id="description"
                type="text"
                value={formData.description ?? agent.description ?? ''}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="A helpful agent that..."
                className="input"
              />
            </div>

            {/* Icon Picker */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Icon</label>
              <div className="flex items-start gap-4">
                {/* Preview */}
                <div className="flex-shrink-0 w-[4.5rem] h-[4.5rem] rounded-lg bg-[#1e1e1e] border border-white/[0.06] flex items-center justify-center overflow-hidden">
                  {(agent.icon_url || (formData.icon?.startsWith('url:') && formData.icon.length > 4)) ? (
                    <img
                      src={formData.icon?.startsWith('url:') ? formData.icon.slice(4) : agent.icon_url!}
                      alt=""
                      className="w-full h-full object-cover"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                    />
                  ) : (
                    <Bot className="w-8 h-8 text-gray-500" />
                  )}
                </div>

                <div className="flex-1 space-y-2">
                  {/* Mode toggle */}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setIconMode('collection')}
                      className={`px-3 py-1 text-xs rounded ${iconMode === 'collection' ? 'bg-primary-600 text-white' : 'bg-[#1e1e1e] text-gray-400'}`}
                    >
                      From Collection
                    </button>
                    <button
                      type="button"
                      onClick={() => setIconMode('url')}
                      className={`px-3 py-1 text-xs rounded ${iconMode === 'url' ? 'bg-primary-600 text-white' : 'bg-[#1e1e1e] text-gray-400'}`}
                    >
                      External URL
                    </button>
                  </div>

                  {iconMode === 'url' ? (
                    <input
                      type="url"
                      value={formData.icon?.startsWith('url:') ? formData.icon.slice(4) : ''}
                      onChange={(e) => setFormData({ ...formData, icon: e.target.value ? `url:${e.target.value}` : undefined })}
                      placeholder="https://example.com/icon.png"
                      className="input text-sm"
                    />
                  ) : (
                    <div className="space-y-2">
                      <div className="flex gap-2">
                        <select
                          value={iconCollectionNs && iconCollectionName ? `${iconCollectionNs}/${iconCollectionName}` : ''}
                          onChange={async (e) => {
                            const val = e.target.value;
                            if (!val) {
                              setIconCollectionNs('');
                              setIconCollectionName('');
                              setIconCollectionFiles([]);
                              return;
                            }
                            const [ns, cn] = val.split('/');
                            setIconCollectionNs(ns);
                            setIconCollectionName(cn);
                            try {
                              const files = await apiClient.listFiles(ns, cn);
                              setIconCollectionFiles(files.filter((f: any) => f.content_type?.startsWith('image/')));
                            } catch {
                              setIconCollectionFiles([]);
                            }
                          }}
                          className="input text-sm flex-1"
                        >
                          <option value="">Select collection...</option>
                          {collections?.map((c: any) => (
                            <option key={c.id} value={`${c.namespace}/${c.name}`}>
                              {c.namespace}/{c.name}
                            </option>
                          ))}
                        </select>
                      </div>
                      {iconCollectionFiles.length > 0 && (
                        <div className="grid grid-cols-6 gap-2 max-h-32 overflow-y-auto">
                          {iconCollectionFiles.map((f: any) => {
                            const ref = `collection:${iconCollectionNs}/${iconCollectionName}/${f.name}`;
                            const isSelected = formData.icon === ref;
                            return (
                              <button
                                key={f.id}
                                type="button"
                                onClick={() => setFormData({ ...formData, icon: ref })}
                                className={`w-12 h-12 rounded border-2 overflow-hidden ${isSelected ? 'border-primary-500' : 'border-transparent hover:border-gray-500'}`}
                                title={f.name}
                              >
                                <img
                                  src={`${window.location.hostname === 'localhost' ? 'http://localhost:8000' : ''}/files/public/${iconCollectionNs}/${iconCollectionName}/${f.name}`}
                                  alt={f.name}
                                  className="w-full h-full object-cover"
                                  onError={(e) => { (e.target as HTMLImageElement).src = ''; }}
                                />
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Current value display + remove */}
                  {formData.icon && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500 truncate">{formData.icon}</span>
                      <button
                        type="button"
                        onClick={() => setFormData({ ...formData, icon: '' })}
                        className="text-xs text-red-400 hover:text-red-300"
                      >
                        Remove
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div>
              <label htmlFor="system_prompt" className="block text-sm font-medium text-gray-300 mb-2">
                System Prompt
              </label>
              <textarea
                id="system_prompt"
                value={formData.system_prompt ?? agent.system_prompt ?? ''}
                onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
                placeholder="You are a helpful agent that..."
                rows={8}
                className="input resize-none font-mono text-sm"
              />
              <p className="text-xs text-gray-500 mt-1">
                This prompt defines the agent's behavior and personality. Supports Jinja2 templates.
              </p>
            </div>

            <div className="flex items-center gap-6">
              <div className="flex items-center">
                <input
                  id="is_active"
                  type="checkbox"
                  checked={formData.is_active ?? agent.is_active}
                  onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                  className="w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                />
                <label htmlFor="is_active" className="ml-2 text-sm text-gray-300">
                  Active
                </label>
              </div>
              <div className="flex items-center">
                <input
                  id="is_default"
                  type="checkbox"
                  checked={formData.is_default ?? agent.is_default}
                  onChange={(e) => setFormData({ ...formData, is_default: e.target.checked })}
                  className="w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                />
                <label htmlFor="is_default" className="ml-2 text-sm text-gray-300">
                  Default agent
                </label>
              </div>
              <div className="flex items-center">
                <input
                  id="default_keep_alive"
                  type="checkbox"
                  checked={formData.default_keep_alive ?? agent.default_keep_alive}
                  onChange={(e) => setFormData({ ...formData, default_keep_alive: e.target.checked })}
                  className="w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                />
                <label htmlFor="default_keep_alive" className="ml-2 text-sm text-gray-300">
                  Keep alive (survives disconnect)
                </label>
              </div>
            </div>
            <div>
              <label htmlFor="default_job_timeout" className="block text-sm font-medium text-gray-300 mb-2">
                Default Job Timeout (seconds)
              </label>
              <input
                id="default_job_timeout"
                type="number"
                min="1"
                placeholder="600 (default)"
                value={formData.default_job_timeout ?? agent.default_job_timeout ?? ''}
                onChange={(e) => setFormData({ ...formData, default_job_timeout: e.target.value ? parseInt(e.target.value) : undefined })}
                className="input w-48"
              />
            </div>
          </div>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-gray-100 mb-4">LLM Configuration</h2>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="llm_provider_id" className="block text-sm font-medium text-gray-300 mb-2">
                  LLM Provider
                </label>
                <select
                  id="llm_provider_id"
                  value={'llm_provider_id' in formData ? (formData.llm_provider_id ?? '') : (agent.llm_provider_id ?? '')}
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
                  {llmProviders?.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name} ({provider.provider_type}){!provider.is_active ? ' - INACTIVE' : ''}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  Select a configured LLM provider
                </p>
              </div>

              <div>
                <label htmlFor="model" className="block text-sm font-medium text-gray-300 mb-2">
                  Model
                </label>
                <input
                  id="model"
                  type="text"
                  value={formData.model ?? agent.model ?? ''}
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
              <label htmlFor="temperature" className="block text-sm font-medium text-gray-300 mb-2">
                Temperature ({formData.temperature ?? agent.temperature})
              </label>
              <input
                id="temperature"
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={formData.temperature ?? agent.temperature}
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
              <label htmlFor="max_tokens" className="block text-sm font-medium text-gray-300 mb-2">
                Max Tokens (optional)
              </label>
              <input
                id="max_tokens"
                type="number"
                min="1"
                max="200000"
                value={formData.max_tokens ?? agent.max_tokens ?? ''}
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
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Input/Output Schemas</h2>
          <p className="text-sm text-gray-400 mb-4">
            Define JSON schemas for input variables and expected output structure
          </p>

          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-6">
              <JSONSchemaEditor
                label="Input Schema"
                description="Input variables for system prompt templates (e.g., {{variable_name}})"
                value={formData.input_schema ?? agent.input_schema ?? {}}
                onChange={(schema) => setFormData({ ...formData, input_schema: schema })}
              />
              <JSONSchemaEditor
                label="Output Schema"
                description="Expected response structure (empty = no enforcement)"
                value={formData.output_schema ?? agent.output_schema ?? {}}
                onChange={(schema) => setFormData({ ...formData, output_schema: schema })}
              />
            </div>

            <div>
              <label htmlFor="initial_messages" className="block text-sm font-medium text-gray-300 mb-2">
                Initial Messages (JSON)
              </label>
              <textarea
                id="initial_messages"
                value={JSON.stringify(formData.initial_messages ?? agent.initial_messages ?? [], null, 2)}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value);
                    setFormData({ ...formData, initial_messages: parsed });
                  } catch {
                    // Invalid JSON, don't update
                  }
                }}
                placeholder='[{"role": "user", "content": "Example"}, {"role": "agent", "content": "Response"}]'
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
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Tools & Access</h2>

          {/* Tabs */}
          <div className="flex border-b border-white/[0.06] mb-4">
            <button
              type="button"
              onClick={() => setToolsTab('assistants')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'assistants'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Other Agents
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('skills')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'skills'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Skills
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('functions')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'functions'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Functions
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('queries')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'queries'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Queries
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('states')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'states'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              States
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('collections')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'collections'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Collections
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('components')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'components'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Components
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('connectors')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'connectors'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Connectors
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('hooks')}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                toolsTab === 'hooks'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
            >
              Hooks
            </button>
            <div className="mx-1 w-px bg-white/[0.06] self-stretch" />
            <button
              type="button"
              onClick={() => setToolsTab('status')}
              className={`px-4 py-2 text-sm font-medium transition-colors flex items-center gap-1.5 ${
                toolsTab === 'status'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
              title="Status templates shown during tool execution"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" />
              </svg>
              Status
            </button>
            <button
              type="button"
              onClick={() => setToolsTab('platform')}
              className={`px-4 py-2 text-sm font-medium transition-colors flex items-center gap-1.5 ${
                toolsTab === 'platform'
                  ? 'text-primary-600 border-b-2 border-primary-600'
                  : 'text-gray-400 hover:text-gray-100'
              }`}
              title="Sinas platform capabilities"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
              </svg>
              Platform
            </button>
          </div>

          {/* Tab Content */}
          <div className="space-y-4">
            {/* Other Agents Tab */}
            {toolsTab === 'assistants' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Select agents or add wildcard patterns (<code className="text-xs bg-[#161616] px-1 rounded">namespace/*</code>, <code className="text-xs bg-[#161616] px-1 rounded">*/*</code>)
                </p>
                {/* Tags for selected agents/patterns */}
                {(() => {
                  const current = formData.enabled_agents || agent.enabled_agents || [];
                  return current.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5 mb-2">
                      {current.map((entry: string) => {
                        const isWildcard = entry.includes('*');
                        return (
                          <span
                            key={entry}
                            className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border ${
                              isWildcard
                                ? 'bg-amber-900/20 text-amber-400 border-amber-800/30'
                                : 'bg-primary-900/20 text-primary-400 border-primary-800/30'
                            }`}
                          >
                            {isWildcard && <span className="font-mono">*</span>}
                            {!isWildcard && <Bot className="w-3 h-3" />}
                            {entry}
                            <button
                              type="button"
                              onClick={() => {
                                const updated = current.filter((p: string) => p !== entry);
                                setFormData({ ...formData, enabled_agents: updated });
                              }}
                              className="ml-0.5 hover:opacity-70"
                            >
                              &times;
                            </button>
                          </span>
                        );
                      })}
                    </div>
                  ) : null;
                })()}
                {/* Combobox input with dropdown suggestions */}
                <div className="relative">
                  <input
                    type="text"
                    placeholder="Type to search agents or enter a pattern..."
                    className="w-full px-3 py-1.5 text-sm border border-white/10 rounded-lg focus:ring-primary-500 focus:border-primary-500"
                    onChange={(e) => {
                      const input = e.target as HTMLInputElement;
                      input.dataset.filter = input.value;
                      // Force re-render of dropdown by toggling a data attribute
                      input.dispatchEvent(new Event('input', { bubbles: true }));
                    }}
                    onFocus={(e) => {
                      (e.target as HTMLInputElement).dataset.open = 'true';
                      // Trigger re-render
                      setFormData({ ...formData });
                    }}
                    onBlur={(e) => {
                      // Delay to allow click on dropdown items
                      setTimeout(() => {
                        (e.target as HTMLInputElement).dataset.open = 'false';
                        setFormData({ ...formData });
                      }, 200);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        const value = (e.target as HTMLInputElement).value.trim();
                        if (value) {
                          const current = formData.enabled_agents || agent.enabled_agents || [];
                          if (!current.includes(value)) {
                            setFormData({ ...formData, enabled_agents: [...current, value] });
                          }
                          (e.target as HTMLInputElement).value = '';
                          (e.target as HTMLInputElement).dataset.filter = '';
                        }
                      }
                    }}
                    id="agent-combobox"
                  />
                  {(() => {
                    const input = document.getElementById('agent-combobox') as HTMLInputElement | null;
                    const isOpen = input?.dataset.open === 'true';
                    const filter = (input?.dataset.filter || input?.value || '').toLowerCase();
                    if (!isOpen) return null;

                    const current = formData.enabled_agents || agent.enabled_agents || [];
                    const otherAgents = (agents || []).filter((a: any) => a.id !== agent.id);

                    // Build suggestion list: wildcard patterns + specific agents
                    const wildcardSuggestions = [
                      { value: '*/*', label: 'All agents', description: 'Access every active agent' },
                      ...Array.from(new Set(otherAgents.map((a: any) => a.namespace))).map((ns) => ({
                        value: `${ns}/*`,
                        label: `${ns}/*`,
                        description: `All agents in ${ns} namespace`,
                      })),
                    ];

                    const agentSuggestions = otherAgents.map((a: any) => ({
                      value: `${a.namespace}/${a.name}`,
                      label: `${a.namespace}/${a.name}`,
                      description: a.description || '',
                    }));

                    const allSuggestions = [...wildcardSuggestions, ...agentSuggestions]
                      .filter((s) => !current.includes(s.value))
                      .filter((s) => !filter || s.value.toLowerCase().includes(filter) || s.label.toLowerCase().includes(filter));

                    if (allSuggestions.length === 0) return null;

                    return (
                      <div className="absolute z-10 w-full mt-1 bg-[#161616] border border-white/[0.06] rounded-lg max-h-48 overflow-y-auto">
                        {allSuggestions.map((suggestion) => (
                          <button
                            key={suggestion.value}
                            type="button"
                            className="w-full text-left px-3 py-2 hover:bg-white/5 flex items-center gap-2 text-sm"
                            onMouseDown={(e) => {
                              e.preventDefault();
                              const updated = [...current, suggestion.value];
                              setFormData({ ...formData, enabled_agents: updated });
                              if (input) {
                                input.value = '';
                                input.dataset.filter = '';
                              }
                            }}
                          >
                            {suggestion.value.includes('*') ? (
                              <span className="w-4 h-4 text-amber-500 font-mono text-xs font-bold flex items-center justify-center">*</span>
                            ) : (
                              <Bot className="w-4 h-4 text-primary-600 flex-shrink-0" />
                            )}
                            <div className="flex-1 min-w-0">
                              <div className="font-medium text-gray-100 truncate">{suggestion.label}</div>
                              {suggestion.description && (
                                <div className="text-xs text-gray-500 truncate">{suggestion.description}</div>
                              )}
                            </div>
                          </button>
                        ))}
                      </div>
                    );
                  })()}
                </div>
              </div>
            )}

            {/* Skills Tab */}
            {toolsTab === 'skills' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Enable skills that this agent can retrieve for instructions. Mark as "Preload" to inject into system prompt instead of exposing as tool.
                </p>
                {skills && skills.length > 0 ? (
                  <div className="space-y-2 border border-white/[0.06] rounded-lg p-3 max-h-96 overflow-y-auto">
                    {skills.map((skill: any) => {
                      const skillIdentifier = `${skill.namespace}/${skill.name}`;
                      const current = formData.enabled_skills || agent.enabled_skills || [];
                      const skillConfig = current.find((s: any) => s.skill === skillIdentifier);
                      const isEnabled = !!skillConfig;
                      const isPreloaded = skillConfig?.preload || false;

                      return (
                        <div
                          key={skill.id}
                          className="flex items-start p-2 hover:bg-white/5 rounded"
                        >
                          <input
                            type="checkbox"
                            checked={isEnabled}
                            onChange={(e) => {
                              const current = formData.enabled_skills || agent.enabled_skills || [];
                              const updated = e.target.checked
                                ? [...current, { skill: skillIdentifier, preload: false }]
                                : current.filter((s: any) => s.skill !== skillIdentifier);
                              setFormData({ ...formData, enabled_skills: updated });
                            }}
                            className="mt-1 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                          />
                          <div className="ml-3 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-gray-100 font-mono">
                                {skillIdentifier}
                              </span>
                              {!skill.is_active && (
                                <span className="px-2 py-0.5 bg-[#161616] text-gray-400 text-xs font-medium rounded">
                                  Inactive
                                </span>
                              )}
                            </div>
                            {skill.description && (
                              <p className="text-xs text-gray-500 mt-0.5">{skill.description}</p>
                            )}
                            {isEnabled && (
                              <label className="flex items-center mt-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={isPreloaded}
                                  onChange={(e) => {
                                    const current = formData.enabled_skills || agent.enabled_skills || [];
                                    const updated = current.map((s: any) =>
                                      s.skill === skillIdentifier
                                        ? { ...s, preload: e.target.checked }
                                        : s
                                    );
                                    setFormData({ ...formData, enabled_skills: updated });
                                  }}
                                  className="w-3 h-3 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                                />
                                <span className="ml-2 text-xs text-gray-400">
                                  Preload (inject into system prompt)
                                </span>
                              </label>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                    <p className="text-sm text-gray-500">No skills available. Create skills to use them with agents.</p>
                  </div>
                )}
              </div>
            )}

            {/* Functions Tab */}
            {toolsTab === 'functions' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Select which functions this agent can call and configure default parameters
                </p>
                {functions && functions.length > 0 ? (
                  <div className="space-y-3 border border-white/[0.06] rounded-lg p-3 max-h-[600px] overflow-y-auto">
                    {functions.map((func: any) => {
                      const funcIdentifier = `${func.namespace}/${func.name}`;
                      const isEnabled = (formData.enabled_functions || agent.enabled_functions || []).includes(funcIdentifier);
                      const isExpanded = expandedFunctionParams.has(funcIdentifier);
                      const inputSchema = func.input_schema || {};
                      const properties = inputSchema.properties || {};
                      const hasParameters = Object.keys(properties).length > 0;

                      return (
                        <div
                          key={func.id}
                          className="border border-white/[0.06] rounded-lg p-3"
                        >
                          <div className="flex items-start gap-3">
                            <input
                              type="checkbox"
                              checked={isEnabled}
                              onChange={(e) => {
                                const currentFunctions = formData.enabled_functions || agent.enabled_functions || [];
                                const newFunctions = e.target.checked
                                  ? [...currentFunctions, funcIdentifier]
                                  : currentFunctions.filter((id: string) => id !== funcIdentifier);
                                setFormData({ ...formData, enabled_functions: newFunctions });
                              }}
                              className="mt-1 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                            />
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-gray-100 font-mono">
                                  {funcIdentifier}
                                </span>
                                {!func.is_active && (
                                  <span className="px-2 py-0.5 bg-[#161616] text-gray-400 text-xs font-medium rounded">
                                    Inactive
                                  </span>
                                )}
                              </div>
                              {func.description && (
                                <p className="text-xs text-gray-400 mt-0.5">{func.description}</p>
                              )}

                              {/* Configure Parameters Button */}
                              {isEnabled && hasParameters && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    const newExpanded = new Set(expandedFunctionParams);
                                    if (isExpanded) {
                                      newExpanded.delete(funcIdentifier);
                                    } else {
                                      newExpanded.add(funcIdentifier);
                                    }
                                    setExpandedFunctionParams(newExpanded);
                                  }}
                                  className="mt-2 text-xs text-primary-600 hover:text-primary-400 font-medium"
                                >
                                  {isExpanded ? '▼ Hide' : '▶'} Configure Default Parameters
                                </button>
                              )}

                              {/* Parameter Configuration */}
                              {isEnabled && isExpanded && hasParameters && (
                                <div className="mt-3 space-y-3 pl-4 border-l-2 border-primary-800/30">
                                  <div className="text-xs text-gray-500 italic space-y-1 mb-2">
                                    <p>Tip: Use Jinja2 templates like {'{{'} variable_name {'}}'}  to reference agent input variables</p>
                                    <p>Locked parameters are hidden from the LLM and cannot be overridden (useful for API keys, sender emails, etc.)</p>
                                    <p>Unlocked parameters are shown to the LLM as defaults and can be overridden</p>
                                  </div>
                                  {Object.entries(properties).map(([paramName, paramDef]: [string, any]) => {
                                    const currentParams = formData.function_parameters || agent.function_parameters || {};
                                    const funcParams = currentParams[funcIdentifier] || {};
                                    const paramConfig = funcParams[paramName];

                                    // Support both legacy format (string) and new format ({value, locked})
                                    const isNewFormat = paramConfig && typeof paramConfig === 'object' && 'value' in paramConfig;
                                    const paramValue = isNewFormat ? paramConfig.value : (paramConfig || '');
                                    const isLocked = isNewFormat ? (paramConfig.locked ?? false) : false;

                                    return (
                                      <div key={paramName} className="space-y-1">
                                        <label className="block text-xs font-medium text-gray-300">
                                          {paramName}
                                          {paramDef.type && (
                                            <span className="ml-1 text-gray-500">({paramDef.type})</span>
                                          )}
                                        </label>
                                        <input
                                          type="text"
                                          value={paramValue}
                                          onChange={(e) => {
                                            const newFunctionParams = { ...formData.function_parameters || agent.function_parameters || {} };
                                            if (!newFunctionParams[funcIdentifier]) {
                                              newFunctionParams[funcIdentifier] = {};
                                            }
                                            if (e.target.value) {
                                              // Preserve locked status if using new format
                                              newFunctionParams[funcIdentifier][paramName] = {
                                                value: e.target.value,
                                                locked: isLocked
                                              };
                                            } else {
                                              delete newFunctionParams[funcIdentifier][paramName];
                                              if (Object.keys(newFunctionParams[funcIdentifier]).length === 0) {
                                                delete newFunctionParams[funcIdentifier];
                                              }
                                            }
                                            setFormData({ ...formData, function_parameters: newFunctionParams });
                                          }}
                                          placeholder={paramDef.description || `Default value for ${paramName}`}
                                          className="input text-xs font-mono"
                                        />
                                        <label className="flex items-center gap-2 cursor-pointer">
                                          <input
                                            type="checkbox"
                                            checked={isLocked}
                                            onChange={(e) => {
                                              const newFunctionParams = { ...formData.function_parameters || agent.function_parameters || {} };
                                              if (!newFunctionParams[funcIdentifier]) {
                                                newFunctionParams[funcIdentifier] = {};
                                              }
                                              if (paramValue || e.target.checked) {
                                                newFunctionParams[funcIdentifier][paramName] = {
                                                  value: paramValue,
                                                  locked: e.target.checked
                                                };
                                              } else {
                                                delete newFunctionParams[funcIdentifier][paramName];
                                                if (Object.keys(newFunctionParams[funcIdentifier]).length === 0) {
                                                  delete newFunctionParams[funcIdentifier];
                                                }
                                              }
                                              setFormData({ ...formData, function_parameters: newFunctionParams });
                                            }}
                                            className="rounded border-white/10 text-primary-600 focus:ring-primary-500"
                                          />
                                          <span className="text-xs text-gray-400">
                                            Locked
                                          </span>
                                        </label>
                                        {paramDef.description && (
                                          <p className="text-xs text-gray-500 mt-0.5">{paramDef.description}</p>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                    <p className="text-sm text-gray-500">No functions available. Create functions first.</p>
                  </div>
                )}
              </div>
            )}

            {/* Stores Tab */}
            {toolsTab === 'states' && (
              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-100 mb-2">Stores</h3>
                  <p className="text-xs text-gray-500 mb-3">
                    Configure which stores this agent can access and the access level for each.
                  </p>
                  {stores && stores.length > 0 ? (
                    <div className="space-y-2 border border-white/[0.06] rounded-lg p-3 max-h-80 overflow-y-auto">
                      {stores.map((store: any) => {
                        const storeRef = `${store.namespace}/${store.name}`;
                        const currentStores = formData.enabled_stores || agent.enabled_stores || [];
                        const existing = currentStores.find((s: any) => s.store === storeRef);
                        const accessMode = existing?.access || 'none';
                        return (
                          <div
                            key={storeRef}
                            className="flex items-center justify-between p-2 hover:bg-white/5 rounded"
                          >
                            <div className="flex-1">
                              <span className="text-sm font-medium text-gray-100 font-mono">{storeRef}</span>
                              {store.description && (
                                <p className="text-xs text-gray-500 mt-0.5">{store.description}</p>
                              )}
                              <div className="flex gap-1.5 mt-1">
                                {store.strict && <span className="text-[10px] px-1.5 py-0.5 bg-yellow-900/30 text-yellow-300 rounded">strict</span>}
                                {store.encrypted && <span className="text-[10px] px-1.5 py-0.5 bg-purple-900/30 text-purple-300 rounded">encrypted</span>}
                              </div>
                            </div>
                            <div className="flex rounded-md overflow-hidden border border-white/10">
                              {(['none', 'readonly', 'readwrite'] as const).map((mode) => (
                                <button
                                  key={mode}
                                  type="button"
                                  onClick={() => {
                                    let updated = currentStores.filter((s: any) => s.store !== storeRef);
                                    if (mode !== 'none') {
                                      updated = [...updated, { store: storeRef, access: mode }];
                                    }
                                    setFormData({ ...formData, enabled_stores: updated });
                                  }}
                                  className={`px-2.5 py-1 text-xs font-medium transition-colors ${
                                    accessMode === mode
                                      ? 'bg-primary-600 text-white'
                                      : 'bg-[#1a1a1a] text-gray-400 hover:text-gray-200'
                                  }`}
                                >
                                  {mode === 'none' ? 'off' : mode === 'readonly' ? 'read' : 'read/write'}
                                </button>
                              ))}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                      <p className="text-sm text-gray-500">No stores available. Create stores first.</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Queries Tab */}
            {toolsTab === 'queries' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Enable SQL queries for this agent to execute against external databases
                </p>
                {queries && queries.length > 0 ? (
                  <div className="space-y-2 border border-white/[0.06] rounded-lg p-3 max-h-64 overflow-y-auto">
                    {queries.map((query: any) => {
                      const queryRef = `${query.namespace}/${query.name}`;
                      return (
                        <label
                          key={queryRef}
                          className="flex items-start p-2 hover:bg-white/5 rounded cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={(formData.enabled_queries || agent.enabled_queries || []).includes(queryRef)}
                            onChange={(e) => {
                              const current = formData.enabled_queries || agent.enabled_queries || [];
                              const updated = e.target.checked
                                ? [...current, queryRef]
                                : current.filter((ref: string) => ref !== queryRef);
                              setFormData({ ...formData, enabled_queries: updated });
                            }}
                            className="mt-1 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                          />
                          <div className="ml-3 flex-1">
                            <span className="text-sm font-medium text-gray-100 font-mono">{queryRef}</span>
                            <span className={`ml-2 px-1.5 py-0.5 text-xs font-medium rounded ${
                              query.operation === 'read'
                                ? 'bg-blue-900/30 text-blue-400'
                                : 'bg-orange-900/30 text-orange-400'
                            }`}>
                              {query.operation}
                            </span>
                            {query.description && (
                              <p className="text-xs text-gray-400 mt-0.5">{query.description}</p>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                    <p className="text-sm text-gray-500">No queries available. Create queries first.</p>
                  </div>
                )}
              </div>
            )}

            {/* Collections Tab */}
            {toolsTab === 'collections' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Enable file collections for this agent to search and retrieve files
                </p>
                {collections && collections.length > 0 ? (
                  <div className="space-y-2 border border-white/[0.06] rounded-lg p-3 max-h-64 overflow-y-auto">
                    {collections.map((coll: any) => {
                      const collRef = `${coll.namespace}/${coll.name}`;
                      const current = formData.enabled_collections || agent.enabled_collections || [];
                      const entry = current.find((e: any) =>
                        (typeof e === 'string' ? e : e.collection) === collRef
                      );
                      const isEnabled = !!entry;
                      const access = entry && typeof entry === 'object' ? entry.access : 'readonly';
                      return (
                        <div
                          key={collRef}
                          className="flex items-center gap-3 p-2 hover:bg-white/5 rounded"
                        >
                          <input
                            type="checkbox"
                            checked={isEnabled}
                            onChange={(e) => {
                              const updated = e.target.checked
                                ? [...current, { collection: collRef, access: 'readonly' as const }]
                                : current.filter((entry: any) =>
                                    (typeof entry === 'string' ? entry : entry.collection) !== collRef
                                  );
                              setFormData({ ...formData, enabled_collections: updated as any });
                            }}
                            className="w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                          />
                          <span className="text-sm font-medium text-gray-100 font-mono flex-1">{collRef}</span>
                          {isEnabled && (
                            <div className="flex rounded-md overflow-hidden border border-white/10">
                              {(['readonly', 'readwrite'] as const).map((mode) => (
                                <button
                                  key={mode}
                                  type="button"
                                  onClick={() => {
                                    const updated = current.map((entry: any) => {
                                      const ref = typeof entry === 'string' ? entry : entry.collection;
                                      if (ref === collRef) {
                                        return { collection: collRef, access: mode };
                                      }
                                      return typeof entry === 'string' ? { collection: entry, access: 'readonly' as const } : entry;
                                    });
                                    setFormData({ ...formData, enabled_collections: updated as any });
                                  }}
                                  className={`px-2.5 py-1 text-xs font-medium transition-colors ${
                                    access === mode
                                      ? 'bg-primary-600 text-white'
                                      : 'bg-[#1a1a1a] text-gray-400 hover:text-gray-200'
                                  }`}
                                >
                                  {mode === 'readonly' ? 'read' : 'read/write'}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                    <p className="text-sm text-gray-500">No collections available. Create collections first.</p>
                  </div>
                )}
              </div>
            )}

            {/* Components Tab */}
            {toolsTab === 'components' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Enable UI components for this agent to render and interact with
                </p>
                {components && components.length > 0 ? (
                  <div className="space-y-2 border border-white/[0.06] rounded-lg p-3 max-h-64 overflow-y-auto">
                    {components.map((comp: any) => {
                      const compRef = `${comp.namespace}/${comp.name}`;
                      return (
                        <label
                          key={compRef}
                          className="flex items-start p-2 hover:bg-white/5 rounded cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={(formData.enabled_components || agent.enabled_components || []).includes(compRef)}
                            onChange={(e) => {
                              const current = formData.enabled_components || agent.enabled_components || [];
                              const updated = e.target.checked
                                ? [...current, compRef]
                                : current.filter((ref: string) => ref !== compRef);
                              setFormData({ ...formData, enabled_components: updated });
                            }}
                            className="mt-1 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                          />
                          <div className="ml-3 flex-1">
                            <span className="text-sm font-medium text-gray-100 font-mono">{compRef}</span>
                            {comp.title && (
                              <p className="text-xs text-gray-400 mt-0.5">{comp.title}</p>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                    <p className="text-sm text-gray-500">No components available. Create components first.</p>
                  </div>
                )}
              </div>
            )}

            {/* Hooks Tab */}
            {toolsTab === 'hooks' && (() => {
              const hooks = formData.hooks || agent.hooks || { on_user_message: [], on_assistant_message: [] };
              const allFunctions = functions || [];

              const renderHookList = (hookType: 'on_user_message' | 'on_assistant_message', label: string) => {
                const hookList = hooks[hookType] || [];

                const addHook = () => {
                  const updated = { ...hooks, [hookType]: [...hookList, { function: '', async: false, on_timeout: 'passthrough' }] };
                  setFormData({ ...formData, hooks: updated });
                };

                const removeHook = (index: number) => {
                  const updated = { ...hooks, [hookType]: hookList.filter((_: any, i: number) => i !== index) };
                  setFormData({ ...formData, hooks: updated });
                };

                const updateHook = (index: number, field: string, value: any) => {
                  const newList = [...hookList];
                  newList[index] = { ...newList[index], [field]: value };
                  const updated = { ...hooks, [hookType]: newList };
                  setFormData({ ...formData, hooks: updated });
                };

                const moveHook = (index: number, direction: -1 | 1) => {
                  const newIndex = index + direction;
                  if (newIndex < 0 || newIndex >= hookList.length) return;
                  const newList = [...hookList];
                  [newList[index], newList[newIndex]] = [newList[newIndex], newList[index]];
                  const updated = { ...hooks, [hookType]: newList };
                  setFormData({ ...formData, hooks: updated });
                };

                return (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-sm font-medium text-gray-300">{label}</h4>
                      <button type="button" onClick={addHook} className="text-xs text-primary-400 hover:text-primary-300">+ Add Hook</button>
                    </div>
                    {hookList.length === 0 ? (
                      <p className="text-xs text-gray-600 p-3 border border-white/[0.06] rounded-lg">No hooks configured</p>
                    ) : (
                      <div className="space-y-2">
                        {hookList.map((hook: any, i: number) => (
                          <div key={i} className="border border-white/[0.06] rounded-lg p-3 space-y-2">
                            <div className="flex items-center gap-2">
                              <div className="flex flex-col gap-0.5">
                                <button type="button" onClick={() => moveHook(i, -1)} disabled={i === 0}
                                  className="text-[10px] text-gray-500 hover:text-gray-300 disabled:opacity-30">▲</button>
                                <button type="button" onClick={() => moveHook(i, 1)} disabled={i === hookList.length - 1}
                                  className="text-[10px] text-gray-500 hover:text-gray-300 disabled:opacity-30">▼</button>
                              </div>
                              <div className="flex-1">
                                <select value={hook.function || ''} onChange={e => updateHook(i, 'function', e.target.value)}
                                  className="input w-full text-sm font-mono">
                                  <option value="">Select function...</option>
                                  {allFunctions.map((f: any) => (
                                    <option key={`${f.namespace}/${f.name}`} value={`${f.namespace}/${f.name}`}>
                                      {f.namespace}/{f.name}
                                    </option>
                                  ))}
                                </select>
                              </div>
                              <button type="button" onClick={() => removeHook(i)}
                                className="text-gray-500 hover:text-red-400 p-1">
                                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
                              </button>
                            </div>
                            <div className="flex items-center gap-4 ml-7">
                              <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
                                <input type="checkbox" checked={hook.async || false}
                                  onChange={e => updateHook(i, 'async', e.target.checked)}
                                  className="w-3.5 h-3.5 text-primary-600 border-white/10 rounded" />
                                Fire-and-forget
                              </label>
                              {!hook.async && (
                                <div className="flex items-center gap-2">
                                  <span className="text-xs text-gray-500">On timeout:</span>
                                  <select value={hook.on_timeout || 'passthrough'}
                                    onChange={e => updateHook(i, 'on_timeout', e.target.value)}
                                    className="input text-xs py-1 w-auto">
                                    <option value="passthrough">Skip and continue</option>
                                    <option value="block">Block message</option>
                                  </select>
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              };

              return (
                <div className="space-y-6">
                  <p className="text-xs text-gray-500">
                    Functions that run at defined points in the message pipeline. Sync hooks can mutate or block messages. Async hooks are fire-and-forget.
                  </p>
                  {renderHookList('on_user_message', 'On User Message')}
                  {renderHookList('on_assistant_message', 'On Assistant Message')}
                </div>
              );
            })()}

            {/* Connectors Tab */}
            {toolsTab === 'connectors' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Enable connector operations as agent tools. Select which operations the agent can call.
                </p>
                {connectors && connectors.length > 0 ? (
                  <div className="space-y-3">
                    {connectors.filter((c: any) => c.is_active).map((conn: any) => {
                      const connRef = `${conn.namespace}/${conn.name}`;
                      const enabledConnectors: any[] = formData.enabled_connectors || agent.enabled_connectors || [];
                      const entry = enabledConnectors.find((ec: any) => ec.connector === connRef);
                      const isEnabled = !!entry;
                      const enabledOps: string[] = entry?.operations || [];

                      return (
                        <div key={connRef} className="border border-white/[0.06] rounded-lg p-3">
                          <label className="flex items-start cursor-pointer">
                            <input
                              type="checkbox"
                              checked={isEnabled}
                              onChange={(e) => {
                                let updated = [...enabledConnectors];
                                if (e.target.checked) {
                                  // Enable with all operations selected by default
                                  const allOps = (conn.operations || []).map((op: any) => op.name);
                                  updated.push({ connector: connRef, operations: allOps, parameters: {} });
                                } else {
                                  updated = updated.filter((ec: any) => ec.connector !== connRef);
                                }
                                setFormData({ ...formData, enabled_connectors: updated });
                              }}
                              className="mt-1 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                            />
                            <div className="ml-3 flex-1">
                              <span className="text-sm font-medium text-gray-100 font-mono">{connRef}</span>
                              <span className="text-xs text-gray-500 ml-2">{conn.base_url}</span>
                              {conn.description && (
                                <p className="text-xs text-gray-500 mt-0.5">{conn.description}</p>
                              )}
                            </div>
                          </label>

                          {isEnabled && conn.operations && conn.operations.length > 0 && (
                            <div className="mt-3 ml-7 space-y-1">
                              <div className="flex items-center justify-between mb-1">
                                <span className="text-xs text-gray-500">Operations</span>
                                <button
                                  type="button"
                                  onClick={() => {
                                    const updated = enabledConnectors.map((ec: any) => {
                                      if (ec.connector !== connRef) return ec;
                                      const allOps = (conn.operations || []).map((op: any) => op.name);
                                      const allSelected = allOps.every((o: string) => ec.operations?.includes(o));
                                      return { ...ec, operations: allSelected ? [] : allOps };
                                    });
                                    setFormData({ ...formData, enabled_connectors: updated });
                                  }}
                                  className="text-xs text-primary-400 hover:text-primary-300"
                                >
                                  {enabledOps.length === conn.operations.length ? 'Deselect All' : 'Select All'}
                                </button>
                              </div>
                              {conn.operations.map((op: any) => {
                                const isOpEnabled = enabledOps.includes(op.name);
                                const opParams = op.parameters?.properties || {};
                                const hasParams = Object.keys(opParams).length > 0;
                                const connectorParams = entry?.parameters || {};
                                const opParamValues = connectorParams[op.name] || {};
                                const isParamsExpanded = expandedConnectorParams.has(`${connRef}/${op.name}`);
                                const methodColors: Record<string, string> = {
                                  GET: 'text-green-400', POST: 'text-blue-400',
                                  PUT: 'text-yellow-400', PATCH: 'text-orange-400', DELETE: 'text-red-400',
                                };
                                return (
                                  <div key={op.name} className="border border-white/[0.04] rounded p-1.5">
                                    <label className="flex items-center gap-2 hover:bg-white/5 rounded cursor-pointer p-0.5">
                                      <input
                                        type="checkbox"
                                        checked={isOpEnabled}
                                        onChange={(e) => {
                                          const updated = enabledConnectors.map((ec: any) => {
                                            if (ec.connector !== connRef) return ec;
                                            const ops = e.target.checked
                                              ? [...(ec.operations || []), op.name]
                                              : (ec.operations || []).filter((o: string) => o !== op.name);
                                            return { ...ec, operations: ops };
                                          });
                                          setFormData({ ...formData, enabled_connectors: updated });
                                        }}
                                        className="w-3.5 h-3.5 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                                      />
                                      <span className={`text-[10px] font-bold uppercase ${methodColors[op.method] || 'text-gray-400'}`}>
                                        {op.method}
                                      </span>
                                      <span className="text-sm font-mono text-gray-300">{op.name}</span>
                                      {op.description && (
                                        <span className="text-xs text-gray-600 truncate">{op.description}</span>
                                      )}
                                    </label>
                                    {isOpEnabled && hasParams && (
                                      <button
                                        type="button"
                                        onClick={() => {
                                          const key = `${connRef}/${op.name}`;
                                          const next = new Set(expandedConnectorParams);
                                          next.has(key) ? next.delete(key) : next.add(key);
                                          setExpandedConnectorParams(next);
                                        }}
                                        className="ml-7 mt-1 text-xs text-primary-600 hover:text-primary-400 font-medium"
                                      >
                                        {isParamsExpanded ? '▼ Hide' : '▶'} Default Parameters
                                      </button>
                                    )}
                                    {isOpEnabled && isParamsExpanded && hasParams && (
                                      <div className="mt-2 ml-7 space-y-2 pl-3 border-l-2 border-primary-800/30">
                                        <p className="text-xs text-gray-600 italic">
                                          Set default values. Use {'{{'}variable{'}}'}  for agent input templates. Values are locked (hidden from LLM).
                                        </p>
                                        {Object.entries(opParams).map(([paramName, paramDef]: [string, any]) => (
                                          <div key={paramName} className="space-y-1">
                                            <label className="block text-xs font-medium text-gray-300">
                                              {paramName}
                                              {paramDef.type && <span className="ml-1 text-gray-500">({paramDef.type})</span>}
                                            </label>
                                            <input
                                              type="text"
                                              value={opParamValues[paramName] || ''}
                                              onChange={(e) => {
                                                const updated = enabledConnectors.map((ec: any) => {
                                                  if (ec.connector !== connRef) return ec;
                                                  const params = { ...(ec.parameters || {}) };
                                                  if (!params[op.name]) params[op.name] = {};
                                                  if (e.target.value) {
                                                    params[op.name] = { ...params[op.name], [paramName]: e.target.value };
                                                  } else {
                                                    const opP = { ...params[op.name] };
                                                    delete opP[paramName];
                                                    if (Object.keys(opP).length === 0) delete params[op.name];
                                                    else params[op.name] = opP;
                                                  }
                                                  return { ...ec, parameters: params };
                                                });
                                                setFormData({ ...formData, enabled_connectors: updated });
                                              }}
                                              placeholder={paramDef.description || `Default value for ${paramName}`}
                                              className="input text-xs font-mono w-full"
                                            />
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                    <p className="text-sm text-gray-500">No connectors available. Create connectors first.</p>
                  </div>
                )}
              </div>
            )}

            {toolsTab === 'status' && (() => {
              const templates = formData.status_templates || {};
              const enabledTools: { key: string; label: string; type: string }[] = [];

              // Functions
              (formData.enabled_functions || agent.enabled_functions || []).forEach((ref: string) => {
                enabledTools.push({ key: `function:${ref}`, label: ref, type: 'Function' });
              });
              // Agents
              (formData.enabled_agents || agent.enabled_agents || []).forEach((ref: string) => {
                enabledTools.push({ key: `agent:${ref}`, label: ref, type: 'Agent' });
              });
              // Skills (non-preloaded only — preloaded don't produce tool calls)
              (formData.enabled_skills || agent.enabled_skills || []).forEach((entry: any) => {
                const ref = typeof entry === 'string' ? entry : entry.skill;
                const preload = typeof entry === 'object' && entry.preload;
                if (!preload) {
                  enabledTools.push({ key: `skill:${ref}`, label: ref, type: 'Skill' });
                }
              });
              // Queries
              (formData.enabled_queries || agent.enabled_queries || []).forEach((ref: string) => {
                enabledTools.push({ key: `query:${ref}`, label: ref, type: 'Query' });
              });
              // Collections
              (formData.enabled_collections || agent.enabled_collections || []).forEach((entry: any) => {
                const ref = typeof entry === 'string' ? entry : entry.collection;
                const access = typeof entry === 'object' ? entry.access : 'readonly';
                enabledTools.push({ key: `collection:${ref}`, label: `${ref} (${access})`, type: 'Collection' });
              });
              // Components
              (formData.enabled_components || agent.enabled_components || []).forEach((ref: string) => {
                enabledTools.push({ key: `component:${ref}`, label: ref, type: 'Component' });
              });
              // Connectors
              (formData.enabled_connectors || agent.enabled_connectors || []).forEach((entry: any) => {
                const connRef = entry.connector || '';
                (entry.operations || []).forEach((opName: string) => {
                  enabledTools.push({ key: `connector:${connRef}/${opName}`, label: `${connRef}/${opName}`, type: 'Connector' });
                });
              });

              return (
                <div>
                  <p className="text-xs text-gray-500 mb-3">
                    Status templates shown in the chat UI while a tool is running. Use Jinja2 syntax to reference arguments, e.g. <code className="text-xs bg-[#161616] px-1 rounded">{'Searching for {{query}}...'}</code>
                  </p>
                  {enabledTools.length > 0 ? (
                    <div className="space-y-2 border border-white/[0.06] rounded-lg p-3 max-h-80 overflow-y-auto">
                      {enabledTools.map(({ key, label, type }) => (
                        <div key={key} className="flex items-center gap-3 p-2 hover:bg-white/5 rounded">
                          <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded bg-white/[0.04] text-gray-500 border border-white/[0.06]">
                            {type}
                          </span>
                          <span className="shrink-0 text-sm font-mono text-gray-300 min-w-[140px]">{label}</span>
                          <input
                            type="text"
                            value={templates[key] || ''}
                            onChange={(e) => {
                              const updated = { ...templates };
                              if (e.target.value) {
                                updated[key] = e.target.value;
                              } else {
                                delete updated[key];
                              }
                              setFormData({ ...formData, status_templates: updated });
                            }}
                            placeholder="e.g. Searching for {{query}}..."
                            className="flex-1 bg-transparent text-sm text-gray-200 border border-white/[0.06] rounded px-2.5 py-1.5 placeholder-gray-600 focus:outline-none focus:border-primary-600/50"
                          />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="bg-[#0d0d0d] rounded-lg p-3 border border-white/[0.06]">
                      <p className="text-sm text-gray-500">No tools enabled. Enable functions, agents, or other tools first.</p>
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Platform Tab (System Tools) */}
            {toolsTab === 'platform' && (
              <div>
                <p className="text-xs text-gray-500 mb-3">
                  Sinas platform capabilities. These are opt-in tools beyond the normal function/query toolkit.
                </p>
                <div className="space-y-2 border border-white/[0.06] rounded-lg p-3">
                  {(() => {
                    const currentTools = formData.system_tools ?? agent.system_tools ?? [];
                    const isToolEnabled = (name: string) =>
                      currentTools.some((t: any) => (typeof t === 'string' ? t : t.name) === name);
                    const getToolConfig = (name: string) =>
                      currentTools.find((t: any) => typeof t === 'object' && t.name === name) as any;
                    const toggleTool = (name: string, enabled: boolean, config?: any) => {
                      let updated = currentTools.filter((t: any) => (typeof t === 'string' ? t : t.name) !== name);
                      if (enabled) updated = [...updated, config || name];
                      setFormData({ ...formData, system_tools: updated });
                    };

                    const simpleTools = [
                      { key: 'codeExecution', label: 'Code Execution', desc: 'Generate and run Python in sandboxed containers.' },
                      { key: 'configIntrospection', label: 'Config Introspection', desc: 'Read-only access to inspect the current configuration: list resource types, browse agents/queries/functions, read full details.' },
                      { key: 'packageManagement', label: 'Package Management', desc: 'Validate, preview, install/uninstall Sinas packages. Requires approval for writes.' },
                    ];

                    return (
                      <>
                        {simpleTools.map((tool) => (
                          <label
                            key={tool.key}
                            className="flex items-start gap-3 p-2.5 hover:bg-white/5 rounded cursor-pointer"
                          >
                            <input
                              type="checkbox"
                              checked={isToolEnabled(tool.key)}
                              onChange={(e) => toggleTool(tool.key, e.target.checked)}
                              className="mt-0.5 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                            />
                            <div>
                              <span className="text-sm font-medium text-gray-100">{tool.label}</span>
                              <p className="text-xs text-gray-500 mt-0.5">{tool.desc}</p>
                            </div>
                          </label>
                        ))}

                        {/* Database Introspection — with connection picker */}
                        <div className="p-2.5 hover:bg-white/5 rounded">
                          <label className="flex items-start gap-3 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={isToolEnabled('databaseIntrospection')}
                              onChange={(e) => {
                                if (e.target.checked) {
                                  toggleTool('databaseIntrospection', true, { name: 'databaseIntrospection', connections: [] });
                                } else {
                                  toggleTool('databaseIntrospection', false);
                                }
                              }}
                              className="mt-0.5 w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
                            />
                            <div className="flex-1">
                              <span className="text-sm font-medium text-gray-100">Database Introspection</span>
                              <p className="text-xs text-gray-500 mt-0.5">
                                Read-only schema inspection: list tables, describe columns/indexes/foreign keys. Includes table annotations. No data access.
                              </p>
                            </div>
                          </label>
                          {isToolEnabled('databaseIntrospection') && (() => {
                            const config = getToolConfig('databaseIntrospection') || { name: 'databaseIntrospection', connections: [] };
                            const connections: string[] = config.connections || [];
                            const availableConnections = (databaseConnections || [])
                              .filter((dc: any) => dc.is_active)
                              .map((dc: any) => dc.name);
                            const unselected = availableConnections.filter((name: string) => !connections.includes(name));
                            return (
                              <div className="ml-7 mt-2 space-y-2">
                                <p className="text-xs text-gray-500">Allowed connections:</p>
                                <div className="flex flex-wrap gap-1.5">
                                  {connections.map((conn: string, idx: number) => (
                                    <span
                                      key={idx}
                                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono bg-primary-600/15 text-primary-300 border border-primary-600/30 rounded-md"
                                    >
                                      {conn}
                                      <button
                                        type="button"
                                        onClick={() => {
                                          const updated = connections.filter((_: string, i: number) => i !== idx);
                                          toggleTool('databaseIntrospection', true, { ...config, connections: updated });
                                        }}
                                        className="text-primary-400 hover:text-red-300 -mr-0.5"
                                      >
                                        ✕
                                      </button>
                                    </span>
                                  ))}
                                </div>
                                {unselected.length > 0 && (
                                  <div className="flex flex-wrap gap-1.5">
                                    {unselected.map((name: string) => (
                                      <button
                                        key={name}
                                        type="button"
                                        onClick={() => {
                                          toggleTool('databaseIntrospection', true, {
                                            ...config,
                                            connections: [...connections, name],
                                          });
                                        }}
                                        className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-mono text-gray-500 border border-white/[0.06] rounded-md hover:border-primary-600/30 hover:text-primary-300 transition-colors"
                                      >
                                        <span className="text-[10px]">+</span> {name}
                                      </button>
                                    ))}
                                  </div>
                                )}
                                {availableConnections.length === 0 && (
                                  <p className="text-xs text-gray-600">No database connections available.</p>
                                )}
                              </div>
                            );
                          })()}
                        </div>
                      </>
                    );
                  })()}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="card bg-[#0d0d0d]">
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Metadata</h2>
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="font-medium text-gray-300">Created</dt>
              <dd className="text-gray-400">{new Date(agent.created_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-300">Last Updated</dt>
              <dd className="text-gray-400">{new Date(agent.updated_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-300">Agent ID</dt>
              <dd className="text-gray-400 font-mono text-xs">{agent.id}</dd>
            </div>
            <div>
              <dt className="font-medium text-gray-300">User ID</dt>
              <dd className="text-gray-400 font-mono text-xs">{agent.user_id || 'N/A'}</dd>
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
          <div className="p-3 bg-green-900/20 border border-green-800/30 rounded-lg text-sm text-green-400">
            Agent updated successfully!
          </div>
        )}

        {updateMutation.isError && (
          <div className="p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
            Failed to update agent. Please try again.
          </div>
        )}
      </form>

    </div>
  );
}
