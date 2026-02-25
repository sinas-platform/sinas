import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { SearchCode, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Query, QueryCreate, DatabaseConnection } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';

export function Queries() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createFormData, setCreateFormData] = useState<QueryCreate>({
    namespace: 'default',
    name: '',
    database_connection_id: '',
    operation: 'read',
    sql: '',
  });
  const [inputSchema, setInputSchema] = useState<any>({});
  const [outputSchema, setOutputSchema] = useState<any>({});

  const { data: queries, isLoading } = useQuery({
    queryKey: ['queries'],
    queryFn: () => apiClient.listQueries(),
    retry: false,
  });

  const { data: connections } = useQuery({
    queryKey: ['databaseConnections'],
    queryFn: () => apiClient.listDatabaseConnections(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: QueryCreate) => apiClient.createQuery(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queries'] });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteQuery(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queries'] });
    },
  });

  const resetCreateForm = () => {
    setCreateFormData({
      namespace: 'default',
      name: '',
      database_connection_id: '',
      operation: 'read',
      sql: '',
    });
    setInputSchema({});
    setOutputSchema({});
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (createFormData.name.trim() && createFormData.sql.trim() && createFormData.database_connection_id) {
      createMutation.mutate({
        ...createFormData,
        input_schema: inputSchema,
        output_schema: outputSchema,
      });
    }
  };

  // Group queries by namespace
  const groupedQueries: Record<string, Query[]> = {};
  if (queries) {
    for (const q of queries) {
      if (!groupedQueries[q.namespace]) {
        groupedQueries[q.namespace] = [];
      }
      groupedQueries[q.namespace].push(q);
    }
  }

  const activeConnections = (connections || []).filter((c: DatabaseConnection) => c.is_active);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Queries</h1>
          <p className="text-gray-400 mt-1">SQL query templates for agent data access</p>
        </div>
        <button
          onClick={() => { resetCreateForm(); setShowCreateModal(true); }}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Add Query
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : queries && queries.length > 0 ? (
        <div className="space-y-8">
          {Object.entries(groupedQueries).map(([namespace, nsQueries]) => (
            <div key={namespace}>
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">
                {namespace}
              </h2>
              <div className="grid gap-4">
                {nsQueries.map((query) => (
                  <Link
                    key={query.id}
                    to={`/queries/${query.namespace}/${query.name}`}
                    className={`card transition-colors block ${!query.is_active ? 'opacity-60 bg-[#0d0d0d]' : ''}`}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex items-center flex-1">
                        <SearchCode className={`w-6 h-6 mr-3 flex-shrink-0 ${query.is_active ? 'text-primary-600' : 'text-gray-500'}`} />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <h3 className="font-semibold text-gray-100 font-mono">
                              {query.namespace}/{query.name}
                            </h3>
                            <span className={`px-2 py-0.5 text-xs font-medium rounded ${
                              query.operation === 'read'
                                ? 'bg-blue-900/30 text-blue-400'
                                : 'bg-orange-900/30 text-orange-400'
                            }`}>
                              {query.operation}
                            </span>
                            {!query.is_active && (
                              <span className="px-2 py-0.5 bg-[#1e1e1e] text-gray-400 text-xs font-medium rounded">Inactive</span>
                            )}
                          </div>
                          {query.description && (
                            <p className="text-sm text-gray-400 mt-1">{query.description}</p>
                          )}
                          <p className="text-xs text-gray-500 mt-1">
                            Timeout: {query.timeout_ms}ms | Max rows: {query.max_rows}
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          if (confirm(`Delete query '${query.namespace}/${query.name}'?`)) {
                            deleteMutation.mutate({ namespace: query.namespace, name: query.name });
                          }
                        }}
                        className="text-red-600 hover:text-red-400 ml-4 flex-shrink-0"
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="w-5 h-5" />
                      </button>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <SearchCode className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No queries configured</h3>
          <p className="text-gray-400 mb-4">Create SQL query templates for agents to access external data</p>
          <button onClick={() => setShowCreateModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Add Query
          </button>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Add Query</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Namespace</label>
                  <input
                    type="text"
                    value={createFormData.namespace || 'default'}
                    onChange={(e) => setCreateFormData({ ...createFormData, namespace: e.target.value })}
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Name *</label>
                  <input
                    type="text"
                    value={createFormData.name}
                    onChange={(e) => setCreateFormData({ ...createFormData, name: e.target.value })}
                    placeholder="get-customers"
                    required
                    className="input"
                    autoFocus
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Description</label>
                <input
                  type="text"
                  value={createFormData.description || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, description: e.target.value })}
                  placeholder="Shown to LLM as tool description"
                  className="input"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Connection *</label>
                  <select
                    value={createFormData.database_connection_id}
                    onChange={(e) => setCreateFormData({ ...createFormData, database_connection_id: e.target.value })}
                    className="input"
                    required
                  >
                    <option value="">Select...</option>
                    {activeConnections.map((conn: DatabaseConnection) => (
                      <option key={conn.id} value={conn.id}>{conn.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Operation *</label>
                  <select
                    value={createFormData.operation}
                    onChange={(e) => setCreateFormData({ ...createFormData, operation: e.target.value })}
                    className="input"
                    required
                  >
                    <option value="read">Read</option>
                    <option value="write">Write</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">SQL *</label>
                <textarea
                  value={createFormData.sql}
                  onChange={(e) => setCreateFormData({ ...createFormData, sql: e.target.value })}
                  placeholder="SELECT * FROM customers WHERE id = :customer_id"
                  rows={5}
                  required
                  className="input resize-none font-mono text-sm"
                />
                <p className="text-xs text-gray-500 mt-1">Use :param_name for parameters</p>
              </div>
              <JSONSchemaEditor
                label="Input Schema"
                description="Defines the input parameters for this query"
                value={inputSchema}
                onChange={setInputSchema}
              />
              <JSONSchemaEditor
                label="Output Schema"
                description="Defines the output columns/structure"
                value={outputSchema}
                onChange={setOutputSchema}
              />
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Timeout (ms)</label>
                  <input
                    type="number"
                    value={createFormData.timeout_ms || 5000}
                    onChange={(e) => setCreateFormData({ ...createFormData, timeout_ms: parseInt(e.target.value) || 5000 })}
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Max Rows</label>
                  <input
                    type="number"
                    value={createFormData.max_rows || 1000}
                    onChange={(e) => setCreateFormData({ ...createFormData, max_rows: parseInt(e.target.value) || 1000 })}
                    className="input"
                  />
                </div>
              </div>

              {createMutation.isError && (
                <ErrorDisplay error={createMutation.error} title="Failed to create query" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => { setShowCreateModal(false); resetCreateForm(); }}
                  className="btn btn-secondary"
                  disabled={createMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createMutation.isPending || !createFormData.name.trim() || !createFormData.sql.trim()}
                >
                  {createMutation.isPending ? 'Creating...' : 'Create Query'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
