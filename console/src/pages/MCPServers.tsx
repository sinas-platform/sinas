import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Server, Plus, Trash2, CheckCircle, XCircle, Edit, X } from 'lucide-react';
import { useState } from 'react';
import type { MCPServerCreate, MCPServerUpdate, MCPServer } from '../types';

export function MCPServers() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingServer, setEditingServer] = useState<MCPServer | null>(null);
  const [formData, setFormData] = useState<MCPServerCreate>({
    name: '',
    url: '',
    protocol: 'stdio',
  });

  const { data: servers, isLoading } = useQuery({
    queryKey: ['mcpServers'],
    queryFn: () => apiClient.listMCPServers(),
    retry: false,
  });

  const { data: groups } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listGroups(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: MCPServerCreate) => apiClient.createMCPServer(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcpServers'] });
      setShowCreateModal(false);
      setFormData({ name: '', url: '', protocol: 'stdio' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ serverId, data }: { serverId: string; data: MCPServerUpdate }) =>
      apiClient.updateMCPServer(serverId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcpServers'] });
      setEditingServer(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (serverId: string) => apiClient.deleteMCPServer(serverId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcpServers'] });
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (formData.name.trim() && formData.url.trim()) {
      createMutation.mutate(formData);
    }
  };

  const handleEdit = (server: MCPServer) => {
    setEditingServer(server);
    setFormData({
      name: server.name,
      url: server.url,
      protocol: server.protocol,
      api_key: undefined, // API key is not returned from backend for security
      group_id: server.group_id || undefined,
    });
  };

  const handleUpdate = (e: React.FormEvent) => {
    e.preventDefault();
    if (editingServer && formData.name.trim() && formData.url.trim()) {
      updateMutation.mutate({
        serverId: editingServer.id,
        data: formData as MCPServerUpdate,
      });
    }
  };

  const resetForm = () => {
    setShowCreateModal(false);
    setEditingServer(null);
    setFormData({ name: '', url: '', protocol: 'stdio' });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">MCP Servers</h1>
          <p className="text-gray-600 mt-1">Model Context Protocol server management</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Add Server
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : servers && servers.length > 0 ? (
        <div className="grid gap-6">
          {servers.map((server) => (
            <div key={server.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <Server className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center">
                      <h3 className="font-semibold text-gray-900">{server.name}</h3>
                      {server.is_active ? (
                        <CheckCircle className="w-5 h-5 text-green-500 ml-2" />
                      ) : (
                        <XCircle className="w-5 h-5 text-gray-400 ml-2" />
                      )}
                    </div>
                    <p className="text-sm text-gray-600 truncate">{server.url}</p>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <p className="text-xs text-gray-500">
                        Protocol: {server.protocol}
                      </p>
                      <span className="text-gray-300">•</span>
                      <span className={`text-xs ${server.is_active ? 'text-green-600' : 'text-red-600'}`}>
                        {server.connection_status}
                      </span>
                      {server.group_id && groups && (
                        <>
                          <span className="text-gray-300">•</span>
                          <span className="text-xs text-gray-500">
                            Group: {groups.find((g) => g.id === server.group_id)?.name || server.group_id}
                          </span>
                        </>
                      )}
                    </div>
                    {server.last_connected && (
                      <p className="text-xs text-gray-500 mt-1">
                        Last connected: {new Date(server.last_connected).toLocaleString()}
                      </p>
                    )}
                    {server.error_message && (
                      <p className="text-xs text-red-600 mt-1">
                        Error: {server.error_message}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                  <button
                    onClick={() => handleEdit(server)}
                    className="text-gray-600 hover:text-gray-900"
                    title="Edit server"
                  >
                    <Edit className="w-5 h-5" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this MCP server?')) {
                        deleteMutation.mutate(server.id);
                      }
                    }}
                    className="text-red-600 hover:text-red-700"
                    disabled={deleteMutation.isPending}
                    title="Delete server"
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
          <Server className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No MCP servers configured</h3>
          <p className="text-gray-600 mb-4">Connect to Model Context Protocol servers to extend functionality</p>
          <button onClick={() => setShowCreateModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Add Server
          </button>
        </div>
      )}

      {/* Create/Edit Modal */}
      {(showCreateModal || editingServer) && (
        <>
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50" onClick={resetForm} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 pointer-events-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-gray-900">
                {editingServer ? 'Edit MCP Server' : 'Add MCP Server'}
              </h2>
              <button
                onClick={resetForm}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={editingServer ? handleUpdate : handleCreate} className="space-y-4">
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-2">
                  Server Name *
                </label>
                <input
                  id="name"
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="My MCP Server"
                  required
                  className="input"
                  autoFocus
                />
              </div>

              <div>
                <label htmlFor="url" className="block text-sm font-medium text-gray-700 mb-2">
                  Server URL *
                </label>
                <input
                  id="url"
                  type="text"
                  value={formData.url}
                  onChange={(e) => setFormData({ ...formData, url: e.target.value })}
                  placeholder="http://localhost:3000"
                  required
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="protocol" className="block text-sm font-medium text-gray-700 mb-2">
                  Protocol *
                </label>
                <select
                  id="protocol"
                  value={formData.protocol}
                  onChange={(e) => setFormData({ ...formData, protocol: e.target.value })}
                  className="input"
                >
                  <option value="stdio">STDIO</option>
                  <option value="http">HTTP</option>
                  <option value="sse">SSE</option>
                </select>
              </div>

              <div>
                <label htmlFor="group_id" className="block text-sm font-medium text-gray-700 mb-2">
                  Group (optional)
                </label>
                <select
                  id="group_id"
                  value={formData.group_id || ''}
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
                <label htmlFor="api_key" className="block text-sm font-medium text-gray-700 mb-2">
                  API Key (optional)
                </label>
                <input
                  id="api_key"
                  type="password"
                  value={formData.api_key || ''}
                  onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                  placeholder="Optional API key"
                  className="input"
                />
              </div>

              {(createMutation.isError || updateMutation.isError) && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                  Failed to {editingServer ? 'update' : 'add'} server. Please check the configuration and try again.
                </div>
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={resetForm}
                  className="btn btn-secondary"
                  disabled={createMutation.isPending || updateMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={
                    createMutation.isPending ||
                    updateMutation.isPending ||
                    !formData.name.trim() ||
                    !formData.url.trim()
                  }
                >
                  {createMutation.isPending || updateMutation.isPending
                    ? editingServer ? 'Updating...' : 'Adding...'
                    : editingServer ? 'Update Server' : 'Add Server'}
                </button>
              </div>
            </form>
          </div>
        </div>
        </>
      )}
    </div>
  );
}
