import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Link } from 'react-router-dom';
import { Plus, Trash2, Search, Bot, ArchiveRestore, Clock } from 'lucide-react';
import { useState } from 'react';
import type { Chat, ChatCreate } from '../types';
import { SchemaFormField } from '../components/SchemaFormField';

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const absDiffMs = Math.abs(diffMs);
  const isPast = diffMs < 0;

  if (absDiffMs < 60_000) return isPast ? 'just now' : 'in < 1m';
  if (absDiffMs < 3_600_000) {
    const mins = Math.round(absDiffMs / 60_000);
    return isPast ? `${mins}m ago` : `in ${mins}m`;
  }
  if (absDiffMs < 86_400_000) {
    const hrs = Math.round(absDiffMs / 3_600_000);
    return isPast ? `${hrs}h ago` : `in ${hrs}h`;
  }
  const days = Math.round(absDiffMs / 86_400_000);
  return isPast ? `${days}d ago` : `in ${days}d`;
}

export function Chats() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newChatTitle, setNewChatTitle] = useState('');
  const [selectedAssistantId, setSelectedAssistantId] = useState<string>('');
  const [inputParams, setInputParams] = useState<Record<string, any>>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [showArchived, setShowArchived] = useState(false);

  const { data: chats, isLoading } = useQuery({
    queryKey: ['chats', showArchived],
    queryFn: () => apiClient.listChats(showArchived),
    retry: false,
  });

  const { data: assistants } = useQuery({
    queryKey: ['assistants'],
    queryFn: () => apiClient.listAgents(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: ({ namespace, name, data }: { namespace: string; name: string; data: ChatCreate }) =>
      apiClient.createChatWithAgent(namespace, name, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chats'] });
      setShowCreateModal(false);
      setNewChatTitle('');
      setSelectedAssistantId('');
      setInputParams({});
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (chatId: string) => apiClient.deleteChat(chatId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chats'] });
    },
  });

  const unarchiveMutation = useMutation({
    mutationFn: (chatId: string) => apiClient.updateChat(chatId, { archived: false }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chats'] });
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedAssistantId && newChatTitle.trim()) {
      const assistant = assistants?.find(a => a.id === selectedAssistantId);
      if (assistant) {
        const data: ChatCreate = { title: newChatTitle.trim() };
        // Include input params if any were provided
        if (Object.keys(inputParams).length > 0) {
          data.input = inputParams;
        }
        createMutation.mutate({
          namespace: assistant.namespace,
          name: assistant.name,
          data
        });
      }
    }
  };

  const filteredChats = chats?.filter((chat: any) =>
    chat.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Chats</h1>
          <p className="text-gray-400 mt-1">Manage your AI conversations</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn btn-primary"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </button>
      </div>

      {/* Search + filters */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-500 pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search chats..."
            className="input w-full !pl-11"
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer whitespace-nowrap">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            className="rounded border-gray-600 bg-transparent"
          />
          Show archived
        </label>
      </div>

      {/* Chats Table */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-100">Your Chats</h2>
          <span className="text-sm text-gray-500">{filteredChats?.length || 0} chats</span>
        </div>

        {isLoading ? (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
            <p className="text-gray-400 mt-2">Loading chats...</p>
          </div>
        ) : !filteredChats || filteredChats.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            {searchQuery ? (
              'No chats match your search'
            ) : !assistants || assistants.length === 0 ? (
              <div>
                <Bot className="w-12 h-12 text-gray-500 mx-auto mb-3" />
                <p className="text-gray-100 font-medium mb-1">Create an agent first to start chatting</p>
                <p className="text-sm text-gray-500 mb-4">You need at least one agent before you can start a conversation</p>
                <Link to="/agents" className="btn btn-primary">
                  Go to Agents
                </Link>
              </div>
            ) : (
              'No chats yet. Create one to get started.'
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-[#0d0d0d] border-b border-white/[0.06]">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Title
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Agent
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Creator
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Last Message
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Expires
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {filteredChats.map((chat: Chat) => {
                  return (
                    <tr key={chat.id} className={`hover:bg-white/5 ${chat.archived ? 'opacity-60' : ''}`}>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <Link
                            to={`/chats/${chat.id}`}
                            className="text-sm font-medium text-primary-600 hover:text-primary-700"
                          >
                            {chat.title}
                          </Link>
                          {chat.archived && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/30 text-yellow-500 font-medium">
                              Archived
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-100">
                        {chat.agent_namespace && chat.agent_name ? (
                          `${chat.agent_namespace}/${chat.agent_name}`
                        ) : (
                          <span className="text-gray-500">No agent</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-400">
                        {chat.user_email}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-400">
                        {chat.last_message_at ? new Date(chat.last_message_at).toLocaleString() : (
                          <span className="text-gray-500">No messages</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-400">
                        {chat.expires_at ? (
                          <span className="flex items-center gap-1" title={new Date(chat.expires_at).toLocaleString()}>
                            <Clock className="w-3 h-3" />
                            {formatRelativeTime(chat.expires_at)}
                          </span>
                        ) : (
                          <span className="text-gray-600">-</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right flex items-center justify-end gap-1">
                        {chat.archived ? (
                          <button
                            onClick={() => unarchiveMutation.mutate(chat.id)}
                            className="p-1 text-yellow-500 hover:text-yellow-400 hover:bg-yellow-900/20 rounded"
                            disabled={unarchiveMutation.isPending}
                            title="Restore chat"
                          >
                            <ArchiveRestore className="w-4 h-4" />
                          </button>
                        ) : (
                          <button
                            onClick={() => {
                              if (confirm('Archive this chat? It can be restored later.')) {
                                deleteMutation.mutate(chat.id);
                              }
                            }}
                            className="p-1 text-red-600 hover:text-red-900 hover:bg-red-900/20 rounded"
                            disabled={deleteMutation.isPending}
                            title="Archive chat"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-md w-full p-6">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Create New Chat</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label htmlFor="title" className="block text-sm font-medium text-gray-300 mb-2">
                  Chat Title
                </label>
                <input
                  id="title"
                  type="text"
                  value={newChatTitle}
                  onChange={(e) => setNewChatTitle(e.target.value)}
                  placeholder="My new chat"
                  required
                  className="input"
                  autoFocus
                />
              </div>

              <div>
                <label htmlFor="assistant" className="block text-sm font-medium text-gray-300 mb-2">
                  Agent *
                </label>
                <select
                  id="assistant"
                  value={selectedAssistantId}
                  onChange={(e) => setSelectedAssistantId(e.target.value)}
                  className="input"
                  required
                >
                  <option value="">Select an agent...</option>
                  {assistants?.map((assistant) => (
                    <option key={assistant.id} value={assistant.id}>
                      {assistant.namespace}/{assistant.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Input Parameters (if agent has input_schema) */}
              {selectedAssistantId && (() => {
                const selectedAgent = assistants?.find(a => a.id === selectedAssistantId);
                const inputSchema = selectedAgent?.input_schema;
                const properties = inputSchema?.properties || {};
                const requiredFields = inputSchema?.required || [];

                if (Object.keys(properties).length === 0) {
                  return null;
                }

                return (
                  <div className="border-t pt-4">
                    <h3 className="text-sm font-medium text-gray-100 mb-3">Input Parameters</h3>
                    {Object.entries(properties).map(([key, prop]: [string, any]) => (
                      <SchemaFormField
                        key={key}
                        name={key}
                        schema={prop}
                        value={inputParams[key]}
                        onChange={(value) => setInputParams({ ...inputParams, [key]: value })}
                        required={requiredFields.includes(key)}
                      />
                    ))}
                  </div>
                );
              })()}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    setNewChatTitle('');
                    setSelectedAssistantId('');
                    setInputParams({});
                  }}
                  className="btn btn-secondary"
                  disabled={createMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createMutation.isPending || !newChatTitle.trim() || !selectedAssistantId}
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
