import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Plus, Trash2, Plug, Globe, ChevronRight } from 'lucide-react';
import { Link } from 'react-router-dom';

const authTypeBadge: Record<string, { label: string; className: string }> = {
  bearer: { label: 'Bearer', className: 'bg-blue-900/30 text-blue-400' },
  basic: { label: 'Basic', className: 'bg-purple-900/30 text-purple-400' },
  api_key: { label: 'API Key', className: 'bg-yellow-900/30 text-yellow-400' },
  sinas_token: { label: 'Sinas Token', className: 'bg-green-900/30 text-green-400' },
  none: { label: 'No Auth', className: 'bg-gray-800 text-gray-500' },
};

export function Connectors() {
  const queryClient = useQueryClient();

  const { data: connectors, isLoading } = useQuery({
    queryKey: ['connectors'],
    queryFn: () => apiClient.listConnectors(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteConnector(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
  });

  const handleDelete = (connector: any) => {
    if (confirm(`Delete connector "${connector.namespace}/${connector.name}"?`)) {
      deleteMutation.mutate({ namespace: connector.namespace, name: connector.name });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Connectors</h1>
          <p className="text-gray-400 mt-1">Named HTTP clients with typed operations for agent tools</p>
        </div>
        <Link to="/connectors/new/new" className="btn btn-primary flex items-center">
          <Plus className="w-5 h-5 mr-2" />
          New Connector
        </Link>
      </div>

      {isLoading ? (
        <div className="text-gray-400">Loading...</div>
      ) : !connectors?.length ? (
        <div className="card text-center py-12">
          <Plug className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-300">No connectors yet</h3>
          <p className="text-gray-500 mt-1">Create a connector to integrate with external APIs</p>
          <Link to="/connectors/new/new" className="btn btn-primary mt-4 inline-flex items-center">
            <Plus className="w-4 h-4 mr-2" />
            Create Connector
          </Link>
        </div>
      ) : (
        <div className="grid gap-4">
          {connectors.map((conn: any) => {
            const badge = authTypeBadge[conn.auth?.type] || authTypeBadge.none;
            const opCount = conn.operations?.length || 0;
            return (
              <Link
                key={conn.id}
                to={`/connectors/${conn.namespace}/${conn.name}`}
                className="card flex items-center justify-between hover:border-white/[0.12] transition-colors group"
              >
                <div className="flex items-center gap-4 min-w-0">
                  <Plug className="w-5 h-5 text-primary-400 flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-sm text-gray-200">
                        <span className="text-gray-500">{conn.namespace}/</span>{conn.name}
                      </span>
                      <span className={`px-2 py-0.5 text-xs font-medium rounded ${badge.className}`}>
                        {badge.label}
                      </span>
                      {!conn.is_active && (
                        <span className="px-2 py-0.5 text-xs font-medium rounded bg-red-900/30 text-red-400">
                          Inactive
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      <span className="text-xs text-gray-500 flex items-center gap-1">
                        <Globe className="w-3 h-3" />
                        {conn.base_url}
                      </span>
                      <span className="text-xs text-gray-600">
                        {opCount} operation{opCount !== 1 ? 's' : ''}
                      </span>
                    </div>
                    {conn.description && (
                      <p className="text-xs text-gray-500 mt-0.5 truncate">{conn.description}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(conn); }}
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
