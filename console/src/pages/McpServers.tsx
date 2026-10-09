import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Plus, Trash2, Network, Globe, ChevronRight } from 'lucide-react';
import { Link } from 'react-router-dom';

const authBadge: Record<string, { label: string; className: string }> = {
  bearer: { label: 'Bearer', className: 'bg-blue-900/30 text-blue-400' },
  header: { label: 'Header', className: 'bg-yellow-900/30 text-yellow-400' },
  none: { label: 'No Auth', className: 'bg-gray-800 text-gray-500' },
};

function badgeFor(type: string | undefined) {
  if (!type || type === 'none') return authBadge.none;
  return authBadge[type] ?? { label: type, className: 'bg-gray-800 text-gray-400' };
}

export function McpServers() {
  const queryClient = useQueryClient();

  const { data: servers, isLoading } = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: () => apiClient.listMcpServers(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteMcpServer(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] });
    },
  });

  const handleDelete = (server: any) => {
    if (confirm(`Delete MCP server "${server.namespace}/${server.name}"?`)) {
      deleteMutation.mutate({ namespace: server.namespace, name: server.name });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">MCP Servers</h1>
          <p className="text-gray-400 mt-1">Remote Model Context Protocol servers whose tools agents can call</p>
        </div>
        <Link to="/mcp-servers/new/new" className="btn btn-primary flex items-center">
          <Plus className="w-5 h-5 mr-2" />
          New MCP Server
        </Link>
      </div>

      {isLoading ? (
        <div className="text-gray-400">Loading...</div>
      ) : !servers?.length ? (
        <div className="card text-center py-12">
          <Network className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-300">No MCP servers yet</h3>
          <p className="text-gray-500 mt-1">Register a Streamable HTTP (or SSE) MCP endpoint to expose its tools to agents</p>
          <Link to="/mcp-servers/new/new" className="btn btn-primary mt-4 inline-flex items-center">
            <Plus className="w-4 h-4 mr-2" />
            Add MCP Server
          </Link>
        </div>
      ) : (
        <div className="grid gap-4">
          {servers.map((server: any) => {
            const badge = badgeFor(server.auth?.type);
            return (
              <Link
                key={server.id}
                to={`/mcp-servers/${server.namespace}/${server.name}`}
                className="card flex items-center justify-between hover:border-line transition-colors group"
              >
                <div className="flex items-center gap-4 min-w-0">
                  <Network className="w-5 h-5 text-primary-400 flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-sm text-gray-200">
                        <span className="text-gray-500">{server.namespace}/</span>{server.name}
                      </span>
                      <span className={`px-2 py-0.5 text-xs font-medium rounded ${badge.className}`}>
                        {badge.label}
                      </span>
                      <span className="px-2 py-0.5 text-xs font-medium rounded bg-gray-800 text-gray-400">
                        {server.transport === 'sse' ? 'SSE' : 'Streamable HTTP'}
                      </span>
                      {!server.is_active && (
                        <span className="px-2 py-0.5 text-xs font-medium rounded bg-red-900/30 text-red-400">
                          Inactive
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      <span className="text-xs text-gray-500 flex items-center gap-1">
                        <Globe className="w-3 h-3" />
                        {server.url}
                      </span>
                      {(server.tool_allow?.length || server.tool_deny?.length) ? (
                        <span className="text-xs text-gray-600">filtered tools</span>
                      ) : null}
                    </div>
                    {server.description && (
                      <p className="text-xs text-gray-500 mt-0.5 truncate">{server.description}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(server); }}
                    className="p-1.5 text-gray-500 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                  <ChevronRight className="w-4 h-4 text-gray-600" />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
