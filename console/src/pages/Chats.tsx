import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Link } from 'react-router-dom';
import { Plus, Trash2, Search } from 'lucide-react';
import { useState } from 'react';
import type { ChatCreate } from '../types';

export function Chats() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newChatTitle, setNewChatTitle] = useState('');
  const [selectedAssistantId, setSelectedAssistantId] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState('');

  const { data: chats, isLoading } = useQuery({
    queryKey: ['chats'],
    queryFn: () => apiClient.listChats(),
    retry: false,
  });

  const { data: assistants } = useQuery({
    queryKey: ['assistants'],
    queryFn: () => apiClient.listAssistants(),
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
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (chatId: string) => apiClient.deleteChat(chatId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chats'] });
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedAssistantId && newChatTitle.trim()) {
      const assistant = assistants?.find(a => a.id === selectedAssistantId);
      if (assistant) {
        createMutation.mutate({
          namespace: assistant.namespace,
          name: assistant.name,
          data: { title: newChatTitle.trim() }
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
          <h1 className="text-3xl font-bold text-gray-900">Chats</h1>
          <p className="text-gray-600 mt-1">Manage your AI conversations</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn btn-primary"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search chats..."
          className="input pl-11 w-full"
        />
      </div>

      {/* Chats Table */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Your Chats</h2>
          <span className="text-sm text-gray-500">{filteredChats?.length || 0} chats</span>
        </div>

        {isLoading ? (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
            <p className="text-gray-600 mt-2">Loading chats...</p>
          </div>
        ) : !filteredChats || filteredChats.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            {searchQuery ? 'No chats match your search' : 'No chats yet. Create one to get started.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Title
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Agent
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Created
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Updated
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {filteredChats.map((chat: any) => {
                  return (
                    <tr key={chat.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <Link
                          to={`/chats/${chat.id}`}
                          className="text-sm font-medium text-primary-600 hover:text-primary-700"
                        >
                          {chat.title}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-900">
                        {chat.agent_namespace && chat.agent_name ? (
                          `${chat.agent_namespace}/${chat.agent_name}`
                        ) : (
                          <span className="text-gray-400">No agent</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {new Date(chat.created_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {new Date(chat.updated_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => {
                            if (confirm('Are you sure you want to delete this chat?')) {
                              deleteMutation.mutate(chat.id);
                            }
                          }}
                          className="p-1 text-red-600 hover:text-red-900 hover:bg-red-50 rounded"
                          disabled={deleteMutation.isPending}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
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
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Create New Chat</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label htmlFor="title" className="block text-sm font-medium text-gray-700 mb-2">
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
                <label htmlFor="assistant" className="block text-sm font-medium text-gray-700 mb-2">
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

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    setNewChatTitle('');
                    setSelectedAssistantId('');
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
