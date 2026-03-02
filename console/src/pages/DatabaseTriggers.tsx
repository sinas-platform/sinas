import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Activity, Plus, Trash2, PlayCircle, PauseCircle, AlertTriangle, Database } from 'lucide-react';
import { useState } from 'react';
import type { DatabaseTrigger, DatabaseConnection } from '../types';

function CreateTriggerModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState('');
  const [connectionId, setConnectionId] = useState('');
  const [schemaName, setSchemaName] = useState('public');
  const [tableName, setTableName] = useState('');
  const [pollColumn, setPollColumn] = useState('');
  const [operations, setOperations] = useState<string[]>(['INSERT', 'UPDATE']);
  const [functionNamespace, setFunctionNamespace] = useState('default');
  const [functionName, setFunctionName] = useState('');
  const [pollInterval, setPollInterval] = useState(10);
  const [batchSize, setBatchSize] = useState(100);
  const [error, setError] = useState('');

  const { data: connections } = useQuery({
    queryKey: ['database-connections'],
    queryFn: () => apiClient.listDatabaseConnections(),
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
  });

  const createMutation = useMutation({
    mutationFn: (data: any) => apiClient.createDatabaseTrigger(data),
    onSuccess: () => {
      onCreated();
      onClose();
    },
    onError: (err: any) => {
      setError(err.response?.data?.detail || 'Failed to create trigger');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    createMutation.mutate({
      name,
      database_connection_id: connectionId,
      schema_name: schemaName,
      table_name: tableName,
      poll_column: pollColumn,
      operations,
      function_namespace: functionNamespace,
      function_name: functionName,
      poll_interval_seconds: pollInterval,
      batch_size: batchSize,
    });
  };

  const toggleOperation = (op: string) => {
    setOperations((prev) =>
      prev.includes(op) ? prev.filter((o) => o !== op) : [...prev, op]
    );
  };

  // Extract unique namespaces from functions
  const namespaces = [...new Set((functions || []).map((f: any) => f.namespace))];
  const filteredFunctions = (functions || []).filter(
    (f: any) => f.namespace === functionNamespace
  );

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-[#161616] rounded-lg border border-white/[0.06] w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <h2 className="text-xl font-semibold text-gray-100 mb-4">New Database Trigger</h2>

          {error && (
            <div className="mb-4 p-3 bg-red-900/20 border border-red-800/30 rounded text-red-300 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="input w-full"
                placeholder="order-changes"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Database Connection</label>
              <select
                value={connectionId}
                onChange={(e) => setConnectionId(e.target.value)}
                className="input w-full"
                required
              >
                <option value="">Select connection...</option>
                {(connections || []).map((c: DatabaseConnection) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.connection_type})
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Schema</label>
                <input
                  type="text"
                  value={schemaName}
                  onChange={(e) => setSchemaName(e.target.value)}
                  className="input w-full"
                  placeholder="public"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Table</label>
                <input
                  type="text"
                  value={tableName}
                  onChange={(e) => setTableName(e.target.value)}
                  className="input w-full"
                  placeholder="orders"
                  required
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Poll Column</label>
              <input
                type="text"
                value={pollColumn}
                onChange={(e) => setPollColumn(e.target.value)}
                className="input w-full"
                placeholder="updated_at"
                required
              />
              <p className="text-xs text-gray-500 mt-1">Monotonic column (timestamp or auto-increment ID)</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Operations</label>
              <div className="flex gap-3">
                {['INSERT', 'UPDATE'].map((op) => (
                  <label key={op} className="flex items-center gap-2 text-sm text-gray-300">
                    <input
                      type="checkbox"
                      checked={operations.includes(op)}
                      onChange={() => toggleOperation(op)}
                      className="rounded border-gray-600"
                    />
                    {op}
                  </label>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Function Namespace</label>
                <select
                  value={functionNamespace}
                  onChange={(e) => {
                    setFunctionNamespace(e.target.value);
                    setFunctionName('');
                  }}
                  className="input w-full"
                >
                  {namespaces.map((ns: string) => (
                    <option key={ns} value={ns}>{ns}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Function</label>
                <select
                  value={functionName}
                  onChange={(e) => setFunctionName(e.target.value)}
                  className="input w-full"
                  required
                >
                  <option value="">Select function...</option>
                  {filteredFunctions.map((f: any) => (
                    <option key={f.name} value={f.name}>{f.name}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Poll Interval (sec)</label>
                <input
                  type="number"
                  value={pollInterval}
                  onChange={(e) => setPollInterval(Number(e.target.value))}
                  className="input w-full"
                  min={1}
                  max={3600}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1">Batch Size</label>
                <input
                  type="number"
                  value={batchSize}
                  onChange={(e) => setBatchSize(Number(e.target.value))}
                  className="input w-full"
                  min={1}
                  max={10000}
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <button type="button" onClick={onClose} className="btn btn-secondary">
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? 'Creating...' : 'Create Trigger'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

export function DatabaseTriggers() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);

  const { data: triggers, isLoading } = useQuery({
    queryKey: ['database-triggers'],
    queryFn: () => apiClient.listDatabaseTriggers(),
    retry: false,
  });

  const { data: connections } = useQuery({
    queryKey: ['database-connections'],
    queryFn: () => apiClient.listDatabaseConnections(),
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => apiClient.deleteDatabaseTrigger(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['database-triggers'] });
    },
  });

  const toggleActiveMutation = useMutation({
    mutationFn: ({ name, is_active }: { name: string; is_active: boolean }) =>
      apiClient.updateDatabaseTrigger(name, { is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['database-triggers'] });
    },
  });

  const connectionMap = new Map(
    (connections || []).map((c: DatabaseConnection) => [c.id, c.name])
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Database Triggers</h1>
          <p className="text-gray-400 mt-1">
            Poll external databases for changes and trigger function executions (CDC)
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn btn-primary flex items-center">
          <Plus className="w-5 h-5 mr-2" />
          New Trigger
        </button>
      </div>

      {showCreate && (
        <CreateTriggerModal
          onClose={() => setShowCreate(false)}
          onCreated={() => queryClient.invalidateQueries({ queryKey: ['database-triggers'] })}
        />
      )}

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : triggers && triggers.length > 0 ? (
        <div className="grid gap-6">
          {triggers.map((trigger: DatabaseTrigger) => (
            <div key={trigger.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <Database className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-100">{trigger.name}</h3>
                      {trigger.operations.map((op) => (
                        <span
                          key={op}
                          className="px-2 py-0.5 text-xs font-medium rounded bg-blue-900/30 text-blue-300"
                        >
                          {op}
                        </span>
                      ))}
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded ${
                          trigger.error_message
                            ? 'bg-red-900/30 text-red-300'
                            : trigger.is_active
                              ? 'bg-green-900/30 text-green-300'
                              : 'bg-[#161616] text-gray-200'
                        }`}
                      >
                        {trigger.error_message ? 'Error' : trigger.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 mt-2">
                      <p className="text-xs text-gray-500">
                        Connection:{' '}
                        <span className="font-mono">
                          {connectionMap.get(trigger.database_connection_id) || trigger.database_connection_id}
                        </span>
                      </p>
                      <p className="text-xs text-gray-500">
                        Table:{' '}
                        <span className="font-mono">
                          {trigger.schema_name}.{trigger.table_name}
                        </span>
                      </p>
                      <p className="text-xs text-gray-500">
                        Poll: <span className="font-mono">{trigger.poll_column}</span> every{' '}
                        {trigger.poll_interval_seconds}s
                      </p>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      Function:{' '}
                      <span className="font-mono">
                        {trigger.function_namespace}/{trigger.function_name}
                      </span>
                    </p>
                    {trigger.last_poll_value && (
                      <p className="text-xs text-gray-500 mt-1">
                        Last bookmark: <span className="font-mono">{trigger.last_poll_value}</span>
                      </p>
                    )}
                    {trigger.error_message && (
                      <div className="mt-2 p-2 bg-red-900/10 border border-red-800/20 rounded text-xs text-red-300 flex items-start gap-2">
                        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                        <span>{trigger.error_message}</span>
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() =>
                      toggleActiveMutation.mutate({
                        name: trigger.name,
                        is_active: !trigger.is_active,
                      })
                    }
                    className={`${
                      trigger.is_active
                        ? 'text-amber-600 hover:text-amber-700'
                        : 'text-green-600 hover:text-green-400'
                    }`}
                    disabled={toggleActiveMutation.isPending}
                    title={trigger.is_active ? 'Pause' : 'Resume'}
                  >
                    {trigger.is_active ? (
                      <PauseCircle className="w-5 h-5" />
                    ) : (
                      <PlayCircle className="w-5 h-5" />
                    )}
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this trigger?')) {
                        deleteMutation.mutate(trigger.name);
                      }
                    }}
                    className="text-red-600 hover:text-red-400"
                    disabled={deleteMutation.isPending}
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
          <Activity className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No database triggers configured</h3>
          <p className="text-gray-400 mb-4">
            Create triggers to automatically run functions when data changes in external databases
          </p>
          <button onClick={() => setShowCreate(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Create Trigger
          </button>
        </div>
      )}
    </div>
  );
}
