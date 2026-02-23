import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Filter, Search, MessageSquare, ChevronDown, ChevronRight } from 'lucide-react';

export function Messages() {
  const [agentFilter, setAgentFilter] = useState<string>('');
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [showFilters, setShowFilters] = useState(false);
  const [expandedMessages, setExpandedMessages] = useState<Set<string>>(new Set());

  const { data: messagesData, isLoading } = useQuery({
    queryKey: ['messages', agentFilter, roleFilter, searchTerm],
    queryFn: () => apiClient.searchMessages({
      agent: agentFilter || undefined,
      role: roleFilter || undefined,
      search: searchTerm || undefined,
      limit: 100,
    }),
  });

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAgents(),
  });

  const messages = messagesData?.messages || [];
  const total = messagesData?.total || 0;

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
  };

  const toggleMessageExpanded = (messageId: string) => {
    const newExpanded = new Set(expandedMessages);
    if (newExpanded.has(messageId)) {
      newExpanded.delete(messageId);
    } else {
      newExpanded.add(messageId);
    }
    setExpandedMessages(newExpanded);
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <button
          onClick={() => setShowFilters(!showFilters)}
          className="btn btn-secondary"
        >
          <Filter className="w-4 h-4" />
          Filters
        </button>
      </div>

      {/* Filters */}
      {showFilters && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Search */}
            <div>
              <label className="label">Search Content</label>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Search message content..."
                  className="input pl-10"
                />
              </div>
            </div>

            {/* Agent Filter */}
            <div>
              <label className="label">Agent</label>
              <select
                value={agentFilter}
                onChange={(e) => setAgentFilter(e.target.value)}
                className="input"
              >
                <option value="">All Agents</option>
                {agents?.map((agent) => (
                  <option key={agent.id} value={`${agent.namespace}/${agent.name}`}>
                    {agent.namespace}/{agent.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Role Filter */}
            <div>
              <label className="label">Role</label>
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="input"
              >
                <option value="">All Roles</option>
                <option value="user">User</option>
                <option value="assistant">Assistant</option>
                <option value="tool">Tool</option>
                <option value="system">System</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Messages Table */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Timestamp
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Agent
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  User
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Role
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Content
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Tools
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-gray-500">
                    Loading messages...
                  </td>
                </tr>
              ) : !messages || messages.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center">
                    <MessageSquare className="w-12 h-12 mx-auto text-gray-400 mb-2" />
                    <p className="text-gray-500">No messages found</p>
                    <p className="text-sm text-gray-400 mt-1">
                      Messages will appear here as conversations happen
                    </p>
                  </td>
                </tr>
              ) : (
                messages.map((message: any) => {
                  const isExpanded = expandedMessages.has(message.id);
                  const hasLongContent = message.content && message.content.length > 100;

                  return (
                    <>
                      <tr
                        key={message.id}
                        className="hover:bg-gray-50 cursor-pointer"
                        onClick={() => hasLongContent && toggleMessageExpanded(message.id)}
                      >
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {formatDate(message.created_at)}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm font-mono text-gray-900">
                          {message.chat?.agent_namespace}/{message.chat?.agent_name}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {message.user?.email || '-'}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                              message.role === 'user'
                                ? 'bg-blue-100 text-blue-800'
                                : message.role === 'assistant'
                                ? 'bg-green-100 text-green-800'
                                : message.role === 'tool'
                                ? 'bg-yellow-100 text-yellow-800'
                                : 'bg-gray-100 text-gray-800'
                            }`}
                          >
                            {message.role}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-900 max-w-md">
                          <div className="flex items-start gap-2">
                            {hasLongContent && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleMessageExpanded(message.id);
                                }}
                                className="flex-shrink-0 text-gray-400 hover:text-gray-600"
                              >
                                {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                              </button>
                            )}
                            <div className={!isExpanded && hasLongContent ? "line-clamp-2" : ""}>
                              {message.content || '-'}
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {message.tool_calls && message.tool_calls.length > 0 ? (
                            <span className="text-xs bg-purple-100 text-purple-800 px-2 py-1 rounded">
                              {message.tool_calls.length} call{message.tool_calls.length > 1 ? 's' : ''}
                            </span>
                          ) : message.tool_call_id ? (
                            <span className="text-xs bg-orange-100 text-orange-800 px-2 py-1 rounded">
                              Response
                            </span>
                          ) : (
                            '-'
                          )}
                        </td>
                      </tr>
                      {/* Expanded row for full tool_calls/tool_call_id details */}
                      {isExpanded && (message.tool_calls || message.tool_call_id) && (
                        <tr key={`${message.id}-details`} className="bg-gray-50">
                          <td colSpan={6} className="px-6 py-4">
                            <div className="space-y-2">
                              {message.tool_calls && (
                                <div>
                                  <p className="text-xs font-semibold text-gray-700 mb-2">Tool Calls:</p>
                                  <pre className="text-xs bg-white p-3 rounded border border-gray-200 overflow-x-auto">
                                    {JSON.stringify(message.tool_calls, null, 2)}
                                  </pre>
                                </div>
                              )}
                              {message.tool_call_id && (
                                <div>
                                  <p className="text-xs font-semibold text-gray-700">Tool Call ID: {message.tool_call_id}</p>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Stats */}
      {messages && messages.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Insights</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-gray-50 rounded-lg p-4">
              <p className="text-sm text-gray-500">Total Messages</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{total}</p>
            </div>
            <div className="bg-gray-50 rounded-lg p-4">
              <p className="text-sm text-gray-500">Tool Calls</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">
                {messages.filter((m: any) => m.tool_calls && m.tool_calls.length > 0).length}
              </p>
            </div>
            <div className="bg-gray-50 rounded-lg p-4">
              <p className="text-sm text-gray-500">Showing</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{messages.length}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
