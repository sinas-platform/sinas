import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Cable, Plus, Trash2, Edit2, Zap } from 'lucide-react';
import { useState } from 'react';
import type { DatabaseConnection, DatabaseConnectionCreate, DatabaseConnectionUpdate, DatabaseConnectionTestResponse } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';

export function DatabaseConnections() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedConnection, setSelectedConnection] = useState<DatabaseConnection | null>(null);
  const [testResults, setTestResults] = useState<Record<string, DatabaseConnectionTestResponse>>({});
  const [modalTestResult, setModalTestResult] = useState<DatabaseConnectionTestResponse | null>(null);
  const [modalTestLoading, setModalTestLoading] = useState(false);
  const [createFormData, setCreateFormData] = useState<DatabaseConnectionCreate>({
    name: '',
    connection_type: 'postgresql',
    host: '',
    port: 5432,
    database: '',
    username: '',
  });
  const [editFormData, setEditFormData] = useState<DatabaseConnectionUpdate>({});
  const [createConfigText, setCreateConfigText] = useState('{}');
  const [editConfigText, setEditConfigText] = useState('{}');

  const { data: connections, isLoading } = useQuery({
    queryKey: ['databaseConnections'],
    queryFn: () => apiClient.listDatabaseConnections(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: DatabaseConnectionCreate) => apiClient.createDatabaseConnection(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['databaseConnections'] });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: DatabaseConnectionUpdate }) =>
      apiClient.updateDatabaseConnection(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['databaseConnections'] });
      setShowEditModal(false);
      setSelectedConnection(null);
      setEditFormData({});
      setEditConfigText('{}');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiClient.deleteDatabaseConnection(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['databaseConnections'] });
    },
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => apiClient.testDatabaseConnection(id),
    onSuccess: (result, id) => {
      setTestResults((prev) => ({ ...prev, [id]: result }));
    },
  });

  const handleTestRaw = async (formData: DatabaseConnectionCreate | DatabaseConnectionUpdate) => {
    setModalTestResult(null);
    setModalTestLoading(true);
    try {
      const result = await apiClient.testDatabaseConnectionRaw({
        connection_type: formData.connection_type || 'postgresql',
        host: formData.host || '',
        port: formData.port || 5432,
        database: formData.database || '',
        username: formData.username || '',
        password: formData.password || undefined,
        ssl_mode: formData.ssl_mode || undefined,
      });
      setModalTestResult(result);
    } catch (err: any) {
      setModalTestResult({
        success: false,
        message: err?.message || 'Test failed',
      });
    } finally {
      setModalTestLoading(false);
    }
  };

  const resetCreateForm = () => {
    setCreateFormData({
      name: '',
      connection_type: 'postgresql',
      host: '',
      port: 5432,
      database: '',
      username: '',
    });
    setCreateConfigText('{}');
    setModalTestResult(null);
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (createFormData.name.trim() && createFormData.host.trim()) {
      createMutation.mutate(createFormData);
    }
  };

  const handleEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedConnection && editFormData.name?.trim()) {
      updateMutation.mutate({
        id: selectedConnection.id,
        data: editFormData,
      });
    }
  };

  const openEditModal = (conn: DatabaseConnection) => {
    setSelectedConnection(conn);
    const configJson = JSON.stringify(conn.config || {}, null, 2);
    setEditConfigText(configJson);
    setEditFormData({
      name: conn.name,
      connection_type: conn.connection_type,
      host: conn.host,
      port: conn.port,
      database: conn.database,
      username: conn.username,
      ssl_mode: conn.ssl_mode || '',
      config: conn.config || {},
      is_active: conn.is_active,
    });
    setShowEditModal(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Database Connections</h1>
          <p className="text-gray-600 mt-1">Manage external database connections for queries</p>
        </div>
        <button
          onClick={() => {
            resetCreateForm();
            setShowCreateModal(true);
          }}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Add Connection
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : connections && connections.length > 0 ? (
        <div className="grid gap-6">
          {connections.map((conn) => (
            <div key={conn.id} className={`card ${!conn.is_active ? 'opacity-60 bg-gray-50' : ''}`}>
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <Cable className={`w-8 h-8 mr-3 flex-shrink-0 ${conn.is_active ? 'text-primary-600' : 'text-gray-400'}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-900">{conn.name}</h3>
                      <span className="text-xs font-medium bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{conn.connection_type}</span>
                      {conn.is_active ? (
                        <span className="px-2 py-0.5 bg-green-100 text-green-800 text-xs font-medium rounded">Active</span>
                      ) : (
                        <span className="px-2 py-0.5 bg-gray-200 text-gray-600 text-xs font-medium rounded">Inactive</span>
                      )}
                    </div>
                    <p className="text-sm text-gray-600 font-mono">
                      {conn.host}:{conn.port}/{conn.database}
                    </p>
                    <p className="text-sm text-gray-600">
                      User: <span className="font-medium">{conn.username}</span>
                      {conn.ssl_mode && <> | SSL: {conn.ssl_mode}</>}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      Created: {new Date(conn.created_at).toLocaleString()}
                    </p>
                    {testResults[conn.id] && (
                      <div className={`mt-2 text-sm ${testResults[conn.id].success ? 'text-green-700' : 'text-red-700'}`}>
                        {testResults[conn.id].success ? '✓' : '✗'} {testResults[conn.id].message}
                        {testResults[conn.id].latency_ms != null && ` (${testResults[conn.id].latency_ms}ms)`}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center space-x-2 ml-4 flex-shrink-0">
                  <button
                    onClick={() => testMutation.mutate(conn.id)}
                    className="text-green-600 hover:text-green-700"
                    title="Test Connection"
                    disabled={testMutation.isPending}
                  >
                    <Zap className="w-5 h-5" />
                  </button>
                  <button
                    onClick={() => openEditModal(conn)}
                    className="text-blue-600 hover:text-blue-700"
                    disabled={updateMutation.isPending}
                  >
                    <Edit2 className="w-5 h-5" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this database connection?')) {
                        deleteMutation.mutate(conn.id);
                      }
                    }}
                    className="text-red-600 hover:text-red-700"
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
          <Cable className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No database connections configured</h3>
          <p className="text-gray-600 mb-4">Add external database connections so agents can query data</p>
          <button onClick={() => setShowCreateModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Add Connection
          </button>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Add Database Connection</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Name *</label>
                <input
                  type="text"
                  value={createFormData.name}
                  onChange={(e) => setCreateFormData({ ...createFormData, name: e.target.value })}
                  placeholder="my-postgres"
                  required
                  className="input"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Type *</label>
                <select
                  value={createFormData.connection_type}
                  onChange={(e) => setCreateFormData({ ...createFormData, connection_type: e.target.value })}
                  className="input"
                  required
                >
                  <option value="postgresql">PostgreSQL</option>
                  <option value="clickhouse">ClickHouse</option>
                  <option value="snowflake">Snowflake</option>
                </select>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-2">Host *</label>
                  <input
                    type="text"
                    value={createFormData.host}
                    onChange={(e) => setCreateFormData({ ...createFormData, host: e.target.value })}
                    placeholder="localhost"
                    required
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Port *</label>
                  <input
                    type="number"
                    value={createFormData.port}
                    onChange={(e) => setCreateFormData({ ...createFormData, port: parseInt(e.target.value) || 5432 })}
                    required
                    className="input"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Database *</label>
                <input
                  type="text"
                  value={createFormData.database}
                  onChange={(e) => setCreateFormData({ ...createFormData, database: e.target.value })}
                  placeholder="mydb"
                  required
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Username *</label>
                <input
                  type="text"
                  value={createFormData.username}
                  onChange={(e) => setCreateFormData({ ...createFormData, username: e.target.value })}
                  placeholder="postgres"
                  required
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Password</label>
                <input
                  type="password"
                  value={createFormData.password || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, password: e.target.value })}
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">SSL Mode</label>
                <select
                  value={createFormData.ssl_mode || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, ssl_mode: e.target.value || undefined })}
                  className="input"
                >
                  <option value="">Default (prefer)</option>
                  <option value="disable">Disable</option>
                  <option value="prefer">Prefer</option>
                  <option value="require">Require</option>
                  <option value="verify-ca">Verify CA</option>
                  <option value="verify-full">Verify Full</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Config (JSON)</label>
                <textarea
                  value={createConfigText}
                  onChange={(e) => {
                    setCreateConfigText(e.target.value);
                    try {
                      const parsed = JSON.parse(e.target.value);
                      setCreateFormData({ ...createFormData, config: parsed });
                    } catch { /* invalid JSON */ }
                  }}
                  rows={3}
                  className="input resize-none font-mono text-xs"
                  placeholder='{"min_pool_size": 2, "max_pool_size": 10}'
                />
              </div>

              {/* Test Connection */}
              <div>
                <button
                  type="button"
                  onClick={() => handleTestRaw(createFormData)}
                  className="btn btn-secondary flex items-center text-sm"
                  disabled={modalTestLoading || !createFormData.host?.trim()}
                >
                  <Zap className="w-4 h-4 mr-2" />
                  {modalTestLoading ? 'Testing...' : 'Test Connection'}
                </button>
                {modalTestResult && (
                  <div className={`mt-2 text-sm ${modalTestResult.success ? 'text-green-700' : 'text-red-700'}`}>
                    {modalTestResult.success ? '✓' : '✗'} {modalTestResult.message}
                    {modalTestResult.latency_ms != null && ` (${modalTestResult.latency_ms}ms)`}
                  </div>
                )}
              </div>

              {createMutation.isError && (
                <ErrorDisplay error={createMutation.error} title="Failed to create connection" />
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
                  disabled={createMutation.isPending || !createFormData.name.trim() || !createFormData.host.trim()}
                >
                  {createMutation.isPending ? 'Creating...' : 'Create Connection'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {showEditModal && selectedConnection && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Edit Database Connection</h2>
            <form onSubmit={handleEdit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Name *</label>
                <input
                  type="text"
                  value={editFormData.name || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, name: e.target.value })}
                  required
                  className="input"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Type *</label>
                <select
                  value={editFormData.connection_type || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, connection_type: e.target.value })}
                  className="input"
                  required
                >
                  <option value="postgresql">PostgreSQL</option>
                  <option value="clickhouse">ClickHouse</option>
                  <option value="snowflake">Snowflake</option>
                </select>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-2">Host *</label>
                  <input
                    type="text"
                    value={editFormData.host || ''}
                    onChange={(e) => setEditFormData({ ...editFormData, host: e.target.value })}
                    required
                    className="input"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Port *</label>
                  <input
                    type="number"
                    value={editFormData.port || 5432}
                    onChange={(e) => setEditFormData({ ...editFormData, port: parseInt(e.target.value) || 5432 })}
                    required
                    className="input"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Database *</label>
                <input
                  type="text"
                  value={editFormData.database || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, database: e.target.value })}
                  required
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Username *</label>
                <input
                  type="text"
                  value={editFormData.username || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, username: e.target.value })}
                  required
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Password</label>
                <input
                  type="password"
                  value={editFormData.password || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, password: e.target.value })}
                  placeholder="Leave empty to keep current"
                  className="input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">SSL Mode</label>
                <select
                  value={editFormData.ssl_mode || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, ssl_mode: e.target.value || undefined })}
                  className="input"
                >
                  <option value="">Default (prefer)</option>
                  <option value="disable">Disable</option>
                  <option value="prefer">Prefer</option>
                  <option value="require">Require</option>
                  <option value="verify-ca">Verify CA</option>
                  <option value="verify-full">Verify Full</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Config (JSON)</label>
                <textarea
                  value={editConfigText}
                  onChange={(e) => {
                    setEditConfigText(e.target.value);
                    try {
                      const parsed = JSON.parse(e.target.value);
                      setEditFormData({ ...editFormData, config: parsed });
                    } catch { /* invalid JSON */ }
                  }}
                  rows={3}
                  className="input resize-none font-mono text-xs"
                />
              </div>
              <div className="flex items-center">
                <input
                  type="checkbox"
                  checked={editFormData.is_active ?? true}
                  onChange={(e) => setEditFormData({ ...editFormData, is_active: e.target.checked })}
                  className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
                />
                <label className="ml-2 block text-sm text-gray-700">Active</label>
              </div>

              {/* Test Connection */}
              <div>
                <button
                  type="button"
                  onClick={() => handleTestRaw(editFormData)}
                  className="btn btn-secondary flex items-center text-sm"
                  disabled={modalTestLoading || !editFormData.host?.trim()}
                >
                  <Zap className="w-4 h-4 mr-2" />
                  {modalTestLoading ? 'Testing...' : 'Test Connection'}
                </button>
                {modalTestResult && (
                  <div className={`mt-2 text-sm ${modalTestResult.success ? 'text-green-700' : 'text-red-700'}`}>
                    {modalTestResult.success ? '✓' : '✗'} {modalTestResult.message}
                    {modalTestResult.latency_ms != null && ` (${modalTestResult.latency_ms}ms)`}
                  </div>
                )}
              </div>

              {updateMutation.isError && (
                <ErrorDisplay error={updateMutation.error} title="Failed to update connection" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => { setShowEditModal(false); setSelectedConnection(null); setModalTestResult(null); }}
                  className="btn btn-secondary"
                  disabled={updateMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={updateMutation.isPending || !editFormData.name?.trim()}
                >
                  {updateMutation.isPending ? 'Updating...' : 'Update Connection'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
