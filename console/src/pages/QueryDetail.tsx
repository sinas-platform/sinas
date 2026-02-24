import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useState, useEffect } from 'react';
import { ArrowLeft, Save, Play, Loader } from 'lucide-react';
import type { QueryUpdate, QueryExecuteResponse, DatabaseConnection } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';
import { SchemaFormField } from '../components/SchemaFormField';

export function QueryDetail() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState<QueryUpdate>({});
  const [inputSchema, setInputSchema] = useState<any>({});
  const [outputSchema, setOutputSchema] = useState<any>({});
  const [executeInput, setExecuteInput] = useState('{}');
  const [inputParams, setInputParams] = useState<Record<string, any>>({});
  const [useAdvancedMode, setUseAdvancedMode] = useState(false);
  const [executeResult, setExecuteResult] = useState<QueryExecuteResponse | null>(null);
  const [executeError, setExecuteError] = useState<string | null>(null);
  const [executing, setExecuting] = useState(false);

  const { data: query, isLoading } = useQuery({
    queryKey: ['query', namespace, name],
    queryFn: () => apiClient.getQuery(namespace!, name!),
    enabled: !!namespace && !!name,
  });

  const { data: connections } = useQuery({
    queryKey: ['databaseConnections'],
    queryFn: () => apiClient.listDatabaseConnections(),
    retry: false,
  });

  useEffect(() => {
    if (query) {
      setFormData({
        namespace: query.namespace,
        name: query.name,
        description: query.description || undefined,
        database_connection_id: query.database_connection_id,
        operation: query.operation,
        sql: query.sql,
        timeout_ms: query.timeout_ms,
        max_rows: query.max_rows,
      });
      setInputSchema(query.input_schema || {});
      setOutputSchema(query.output_schema || {});
    }
  }, [query]);

  const updateMutation = useMutation({
    mutationFn: (data: QueryUpdate) => apiClient.updateQuery(namespace!, name!, data),
    onSuccess: (updatedQuery) => {
      queryClient.invalidateQueries({ queryKey: ['query', namespace, name] });
      queryClient.invalidateQueries({ queryKey: ['queries'] });
      if (updatedQuery.namespace !== namespace || updatedQuery.name !== name) {
        queryClient.invalidateQueries({ queryKey: ['query', updatedQuery.namespace, updatedQuery.name] });
      }
    },
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    const data = {
      ...formData,
      input_schema: inputSchema,
      output_schema: outputSchema,
    };
    updateMutation.mutate(data);
  };

  const handleExecute = async () => {
    setExecuteResult(null);
    setExecuteError(null);
    setExecuting(true);
    try {
      let input: Record<string, any>;
      if (!useAdvancedMode && hasInputSchema) {
        input = inputParams;
      } else {
        input = JSON.parse(executeInput);
      }
      const result = await apiClient.executeQuery(namespace!, name!, input);
      setExecuteResult(result);
    } catch (err: any) {
      setExecuteError(err?.response?.data?.detail || err.message || 'Execution failed');
    } finally {
      setExecuting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="text-center py-12">
        <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (!query) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-600">Query not found</p>
        <Link to="/queries" className="text-primary-600 hover:text-primary-700 mt-2 inline-block">
          Back to Queries
        </Link>
      </div>
    );
  }

  const activeConnections = (connections || []).filter((c: DatabaseConnection) => c.is_active);
  const hasInputSchema = inputSchema?.properties && Object.keys(inputSchema.properties).length > 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Link to="/queries" className="text-gray-500 hover:text-gray-700">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-gray-900 font-mono">
            {query.namespace}/{query.name}
          </h1>
          <div className="flex items-center gap-2 mt-1">
            <span className={`px-2 py-0.5 text-xs font-medium rounded ${
              query.operation === 'read' ? 'bg-blue-100 text-blue-700' : 'bg-orange-100 text-orange-700'
            }`}>
              {query.operation}
            </span>
            {!query.is_active && (
              <span className="px-2 py-0.5 bg-gray-200 text-gray-600 text-xs font-medium rounded">Inactive</span>
            )}
          </div>
        </div>
      </div>

      <form onSubmit={handleSave} className="space-y-6">
        {/* Basic Info */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Configuration</h2>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Namespace</label>
                <input
                  type="text"
                  value={formData.namespace || ''}
                  onChange={(e) => setFormData({ ...formData, namespace: e.target.value })}
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Name</label>
                <input
                  type="text"
                  value={formData.name || ''}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="input"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Description</label>
              <input
                type="text"
                value={formData.description || ''}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Shown to LLM as tool description"
                className="input"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Connection</label>
                <select
                  value={formData.database_connection_id || ''}
                  onChange={(e) => setFormData({ ...formData, database_connection_id: e.target.value })}
                  className="input"
                >
                  <option value="">Select...</option>
                  {activeConnections.map((conn: DatabaseConnection) => (
                    <option key={conn.id} value={conn.id}>{conn.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Operation</label>
                <select
                  value={formData.operation || 'read'}
                  onChange={(e) => setFormData({ ...formData, operation: e.target.value })}
                  className="input"
                >
                  <option value="read">Read</option>
                  <option value="write">Write</option>
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Timeout (ms)</label>
                <input
                  type="number"
                  value={formData.timeout_ms || 5000}
                  onChange={(e) => setFormData({ ...formData, timeout_ms: parseInt(e.target.value) || 5000 })}
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Max Rows</label>
                <input
                  type="number"
                  value={formData.max_rows || 1000}
                  onChange={(e) => setFormData({ ...formData, max_rows: parseInt(e.target.value) || 1000 })}
                  className="input"
                />
              </div>
            </div>
          </div>
        </div>

        {/* SQL Editor */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">SQL Template</h2>
          <textarea
            value={formData.sql || ''}
            onChange={(e) => setFormData({ ...formData, sql: e.target.value })}
            rows={10}
            className="input resize-y font-mono text-sm"
            placeholder="SELECT * FROM table WHERE column = :param_name"
          />
          <p className="text-xs text-gray-500 mt-2">
            Use <code className="bg-gray-100 px-1 rounded">:param_name</code> for input parameters.
            Context vars <code className="bg-gray-100 px-1 rounded">:user_id</code> and <code className="bg-gray-100 px-1 rounded">:user_email</code> are auto-injected.
          </p>
        </div>

        {/* Schemas */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Schemas</h2>
          <div className="grid grid-cols-2 gap-6">
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
          </div>
        </div>

        {/* Save Button */}
        <div className="flex justify-end">
          {updateMutation.isError && (
            <div className="mr-4">
              <ErrorDisplay error={updateMutation.error} title="Failed to save" />
            </div>
          )}
          <button
            type="submit"
            className="btn btn-primary flex items-center"
            disabled={updateMutation.isPending}
          >
            <Save className="w-4 h-4 mr-2" />
            {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </form>

      {/* Execute Section */}
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Play className="w-5 h-5 text-green-600" />
          Test Execute
        </h2>
        <div className="space-y-4">
          {/* Input Mode Toggle */}
          {hasInputSchema && (
            <div className="flex items-center justify-between p-3 bg-blue-50 border border-blue-200 rounded">
              <span className="text-sm text-blue-900">
                {useAdvancedMode ? 'Advanced Mode (JSON)' : 'Form Mode (Schema-based)'}
              </span>
              <button
                onClick={() => setUseAdvancedMode(!useAdvancedMode)}
                className="text-xs text-blue-700 hover:text-blue-900 underline"
              >
                {useAdvancedMode ? 'Switch to Form' : 'Switch to JSON'}
              </button>
            </div>
          )}

          {/* Schema-based Form */}
          {hasInputSchema && !useAdvancedMode ? (
            <div>
              <h4 className="text-sm font-medium text-gray-900 mb-3">Input Parameters</h4>
              {Object.entries(inputSchema.properties || {}).map(([key, prop]: [string, any]) => (
                <SchemaFormField
                  key={key}
                  name={key}
                  schema={prop}
                  value={inputParams[key]}
                  onChange={(value) => setInputParams({ ...inputParams, [key]: value })}
                  required={inputSchema.required?.includes(key)}
                />
              ))}
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Input Parameters (JSON)</label>
              <textarea
                value={executeInput}
                onChange={(e) => setExecuteInput(e.target.value)}
                rows={4}
                className="input resize-y font-mono text-sm"
                placeholder='{"customer_id": "abc123"}'
              />
            </div>
          )}

          <button
            type="button"
            onClick={handleExecute}
            className="btn btn-primary flex items-center"
            disabled={executing}
          >
            {executing ? (
              <>
                <Loader className="w-4 h-4 mr-2 animate-spin" />
                Executing...
              </>
            ) : (
              <>
                <Play className="w-4 h-4 mr-2" />
                Execute Query
              </>
            )}
          </button>

          {executeError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-700">{executeError}</p>
            </div>
          )}

          {executeResult && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
              <div className="flex items-center gap-4 mb-3 text-sm">
                <span className={`font-medium ${executeResult.success ? 'text-green-700' : 'text-red-700'}`}>
                  {executeResult.success ? 'Success' : 'Failed'}
                </span>
                <span className="text-gray-500">{executeResult.duration_ms}ms</span>
                {executeResult.row_count != null && (
                  <span className="text-gray-500">{executeResult.row_count} rows</span>
                )}
                {executeResult.affected_rows != null && (
                  <span className="text-gray-500">{executeResult.affected_rows} affected</span>
                )}
              </div>
              {executeResult.data && executeResult.data.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-300">
                        {Object.keys(executeResult.data[0]).map((col) => (
                          <th key={col} className="px-3 py-2 text-left font-medium text-gray-700">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {executeResult.data.slice(0, 50).map((row, i) => (
                        <tr key={i} className="border-b border-gray-200">
                          {Object.values(row).map((val, j) => (
                            <td key={j} className="px-3 py-2 text-gray-600 font-mono text-xs">
                              {val === null ? <span className="text-gray-400 italic">null</span> : String(val)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {executeResult.data.length > 50 && (
                    <p className="text-xs text-gray-500 mt-2">
                      Showing first 50 of {executeResult.data.length} rows
                    </p>
                  )}
                </div>
              )}
              {executeResult.data && executeResult.data.length === 0 && (
                <p className="text-sm text-gray-500 italic">No rows returned</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
