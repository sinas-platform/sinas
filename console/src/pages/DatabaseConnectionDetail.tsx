import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import { apiClient } from '../lib/api';
import { useState } from 'react';
import { Cable, Table2, Eye, Plus, Trash2, ChevronDown, ChevronRight, ArrowLeft, Lock, AlertTriangle } from 'lucide-react';
import { ErrorDisplay } from '../components/ErrorDisplay';
import type { DbTableInfo, DbViewInfo, ColumnDefinition, SchemaInfo } from '../types';

const PG_TYPES = [
  'integer', 'bigint', 'smallint', 'serial', 'bigserial',
  'text', 'varchar(255)', 'char(1)',
  'boolean',
  'real', 'double precision', 'numeric',
  'date', 'timestamp', 'timestamptz', 'time',
  'uuid', 'json', 'jsonb',
  'bytea',
];

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

/** Type-to-confirm destructive action modal */
function ConfirmDestroyModal({
  title,
  targetName,
  description,
  onConfirm,
  onCancel,
  isPending,
  error,
}: {
  title: string;
  targetName: string;
  description: string;
  onConfirm: () => void;
  onCancel: () => void;
  isPending: boolean;
  error?: any;
}) {
  const [typed, setTyped] = useState('');
  const matches = typed === targetName;

  return (
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-[#161616] rounded-lg max-w-md w-full p-6">
        <div className="flex items-center gap-2 mb-4">
          <AlertTriangle className="w-5 h-5 text-red-400" />
          <h2 className="text-xl font-semibold text-gray-100">{title}</h2>
        </div>
        <p className="text-gray-300 text-sm mb-4">{description}</p>
        <p className="text-gray-400 text-sm mb-2">
          Type <span className="font-mono font-bold text-gray-100">{targetName}</span> to confirm:
        </p>
        <input
          type="text"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          className="input font-mono"
          placeholder={targetName}
          autoFocus
        />
        {error && <ErrorDisplay error={error} title="Operation failed" />}
        <div className="flex justify-end space-x-3 pt-4">
          <button type="button" onClick={onCancel} className="btn btn-secondary" disabled={isPending}>
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="btn bg-red-600 hover:bg-red-700 text-white"
            disabled={!matches || isPending}
          >
            {isPending ? 'Dropping...' : 'Drop'}
          </button>
        </div>
      </div>
    </div>
  );
}

export function DatabaseConnectionDetail() {
  const { name } = useParams<{ name: string }>();
  const queryClient = useQueryClient();
  const [selectedSchema, setSelectedSchema] = useState('public');
  const [showCreateTableModal, setShowCreateTableModal] = useState(false);
  const [showCreateViewModal, setShowCreateViewModal] = useState(false);
  const [viewsExpanded, setViewsExpanded] = useState(false);
  const [tableName, setTableName] = useState('');
  const [ifNotExists, setIfNotExists] = useState(false);
  const [columns, setColumns] = useState<ColumnDefinition[]>([
    { name: '', type: 'integer', nullable: true, primary_key: true },
  ]);
  const [viewName, setViewName] = useState('');
  const [viewSql, setViewSql] = useState('');
  const [viewOrReplace, setViewOrReplace] = useState(false);

  // Confirm-destroy modals
  const [dropTarget, setDropTarget] = useState<{ type: 'table' | 'view'; name: string } | null>(null);

  const { data: connection } = useQuery({
    queryKey: ['databaseConnection', name],
    queryFn: () => apiClient.getDatabaseConnection(name!),
    enabled: !!name,
  });

  const isReadOnly = connection?.read_only ?? false;

  const { data: schemas } = useQuery({
    queryKey: ['dbSchemas', name],
    queryFn: () => apiClient.listDbSchemas(name!),
    enabled: !!name,
  });

  const { data: tables, isLoading: tablesLoading, error: tablesError } = useQuery({
    queryKey: ['dbTables', name, selectedSchema],
    queryFn: () => apiClient.listDbTables(name!, selectedSchema),
    enabled: !!name,
  });

  const { data: views, isLoading: viewsLoading } = useQuery({
    queryKey: ['dbViews', name, selectedSchema],
    queryFn: () => apiClient.listDbViews(name!, selectedSchema),
    enabled: !!name && viewsExpanded,
  });

  const createTableMutation = useMutation({
    mutationFn: () =>
      apiClient.createDbTable(name!, {
        table_name: tableName,
        schema_name: selectedSchema,
        columns,
        if_not_exists: ifNotExists,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbTables', name, selectedSchema] });
      setShowCreateTableModal(false);
      resetTableForm();
    },
  });

  const createViewMutation = useMutation({
    mutationFn: () =>
      apiClient.createDbView(name!, {
        name: viewName,
        schema_name: selectedSchema,
        sql: viewSql,
        or_replace: viewOrReplace,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbViews', name, selectedSchema] });
      setShowCreateViewModal(false);
      setViewName('');
      setViewSql('');
      setViewOrReplace(false);
    },
  });

  const dropTableMutation = useMutation({
    mutationFn: (table: string) =>
      apiClient.dropDbTable(name!, table, selectedSchema, false),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbTables', name, selectedSchema] });
      setDropTarget(null);
    },
  });

  const dropViewMutation = useMutation({
    mutationFn: (view: string) =>
      apiClient.dropDbView(name!, view, selectedSchema, false),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbViews', name, selectedSchema] });
      setDropTarget(null);
    },
  });

  const resetTableForm = () => {
    setTableName('');
    setIfNotExists(false);
    setColumns([{ name: '', type: 'integer', nullable: true, primary_key: true }]);
  };

  const addColumn = () => {
    setColumns([...columns, { name: '', type: 'text', nullable: true, primary_key: false }]);
  };

  const updateColumn = (idx: number, field: keyof ColumnDefinition, value: any) => {
    const updated = [...columns];
    updated[idx] = { ...updated[idx], [field]: value };
    setColumns(updated);
  };

  const removeColumn = (idx: number) => {
    if (columns.length > 1) {
      setColumns(columns.filter((_, i) => i !== idx));
    }
  };

  if (!name) return null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/database-connections" className="text-gray-400 hover:text-gray-200">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <Cable className="w-6 h-6 text-primary-600" />
              <h1 className="text-3xl font-bold text-gray-100">{name}</h1>
              {connection && (
                <span className="text-xs font-medium bg-[#161616] text-gray-300 px-2 py-0.5 rounded">
                  {connection.connection_type}
                </span>
              )}
              {isReadOnly && (
                <span className="px-2 py-0.5 bg-yellow-900/30 text-yellow-300 text-xs font-medium rounded flex items-center gap-1" title="Schema Browser is read-only. Query templates are unaffected.">
                  <Lock className="w-3 h-3" />
                  Read-only
                </span>
              )}
            </div>
            {connection && (
              <p className="text-gray-400 mt-1 font-mono text-sm">
                {connection.host}:{connection.port}/{connection.database}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {schemas && schemas.length > 0 && (
            <select
              value={selectedSchema}
              onChange={(e) => setSelectedSchema(e.target.value)}
              className="input text-sm w-40"
            >
              {schemas.map((s: SchemaInfo) => (
                <option key={s.schema_name} value={s.schema_name}>
                  {s.schema_name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Tables Section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-100 flex items-center gap-2">
            <Table2 className="w-5 h-5" />
            Tables
          </h2>
          {!isReadOnly && (
            <button
              onClick={() => { resetTableForm(); setShowCreateTableModal(true); }}
              className="btn btn-primary text-sm flex items-center"
            >
              <Plus className="w-4 h-4 mr-1" />
              Create Table
            </button>
          )}
        </div>

        {tablesLoading ? (
          <div className="text-center py-8">
            <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600" />
          </div>
        ) : tablesError ? (
          <ErrorDisplay error={tablesError} title="Failed to load tables" />
        ) : tables && tables.length > 0 ? (
          <div className="overflow-hidden rounded-lg border border-white/5">
            <table className="w-full text-sm">
              <thead className="bg-[#111]">
                <tr>
                  <th className="text-left px-4 py-3 text-gray-400 font-medium">Table</th>
                  <th className="text-right px-4 py-3 text-gray-400 font-medium">Est. Rows</th>
                  <th className="text-right px-4 py-3 text-gray-400 font-medium">Size</th>
                  {!isReadOnly && (
                    <th className="text-right px-4 py-3 text-gray-400 font-medium w-20">Actions</th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {tables.map((t: DbTableInfo) => (
                  <tr key={t.table_name} className="hover:bg-white/[0.02]">
                    <td className="px-4 py-3">
                      <Link
                        to={`/database-connections/${name}/tables/${t.table_name}?schema=${selectedSchema}`}
                        className="text-gray-100 hover:text-primary-400 font-medium transition-colors"
                      >
                        {t.display_name || t.table_name}
                      </Link>
                      {t.display_name && (
                        <span className="text-gray-500 text-xs ml-2">{t.table_name}</span>
                      )}
                      {t.description && (
                        <p className="text-gray-500 text-xs mt-0.5">{t.description}</p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">
                      {t.estimated_rows.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">
                      {formatSize(t.size_bytes)}
                    </td>
                    {!isReadOnly && (
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => setDropTarget({ type: 'table', name: t.table_name })}
                          className="text-red-600 hover:text-red-400"
                          title="Drop table"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-8 card">
            <Table2 className="w-12 h-12 text-gray-500 mx-auto mb-3" />
            <p className="text-gray-400">No tables in schema "{selectedSchema}"</p>
          </div>
        )}
      </div>

      {/* Views Section (collapsible) */}
      <div>
        <button
          onClick={() => setViewsExpanded(!viewsExpanded)}
          className="flex items-center gap-2 text-xl font-semibold text-gray-100 mb-4 hover:text-gray-300 transition-colors"
        >
          {viewsExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
          <Eye className="w-5 h-5" />
          Views
        </button>

        {viewsExpanded && (
          <>
            {!isReadOnly && (
              <div className="flex justify-end mb-3">
                <button
                  onClick={() => { setViewName(''); setViewSql(''); setViewOrReplace(false); setShowCreateViewModal(true); }}
                  className="btn btn-primary text-sm flex items-center"
                >
                  <Plus className="w-4 h-4 mr-1" />
                  Create View
                </button>
              </div>
            )}

            {viewsLoading ? (
              <div className="text-center py-6">
                <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600" />
              </div>
            ) : views && views.length > 0 ? (
              <div className="space-y-3">
                {views.map((v: DbViewInfo) => (
                  <div key={v.view_name} className="card">
                    <div className="flex items-center justify-between">
                      <div className="flex-1 min-w-0">
                        <h4 className="font-medium text-gray-100">{v.view_name}</h4>
                        {v.view_definition && (
                          <pre className="text-xs text-gray-500 mt-1 overflow-x-auto max-h-20 whitespace-pre-wrap">
                            {v.view_definition.substring(0, 300)}
                            {v.view_definition.length > 300 ? '...' : ''}
                          </pre>
                        )}
                      </div>
                      {!isReadOnly && (
                        <button
                          onClick={() => setDropTarget({ type: 'view', name: v.view_name })}
                          className="text-red-600 hover:text-red-400 ml-4"
                          title="Drop view"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-gray-500 text-sm">No views in schema "{selectedSchema}"</p>
            )}
          </>
        )}
      </div>

      {/* Type-to-confirm Drop Modal */}
      {dropTarget && (
        <ConfirmDestroyModal
          title={`Drop ${dropTarget.type}`}
          targetName={dropTarget.name}
          description={`This will permanently drop the ${dropTarget.type} "${dropTarget.name}" from ${selectedSchema}. This action cannot be undone.`}
          onConfirm={() => {
            if (dropTarget.type === 'table') {
              dropTableMutation.mutate(dropTarget.name);
            } else {
              dropViewMutation.mutate(dropTarget.name);
            }
          }}
          onCancel={() => { setDropTarget(null); dropTableMutation.reset(); dropViewMutation.reset(); }}
          isPending={dropTableMutation.isPending || dropViewMutation.isPending}
          error={dropTableMutation.error || dropViewMutation.error}
        />
      )}

      {/* Create Table Modal */}
      {showCreateTableModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Create Table</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                createTableMutation.mutate();
              }}
              className="space-y-4"
            >
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Table Name *</label>
                  <input
                    type="text"
                    value={tableName}
                    onChange={(e) => setTableName(e.target.value)}
                    placeholder="my_table"
                    required
                    className="input"
                    autoFocus
                  />
                </div>
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-sm text-gray-300">
                    <input
                      type="checkbox"
                      checked={ifNotExists}
                      onChange={(e) => setIfNotExists(e.target.checked)}
                      className="h-4 w-4 text-primary-600 border-white/10 rounded"
                    />
                    IF NOT EXISTS
                  </label>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-medium text-gray-300">Columns *</label>
                  <button type="button" onClick={addColumn} className="text-sm text-primary-400 hover:text-primary-300">
                    + Add Column
                  </button>
                </div>
                <div className="space-y-3">
                  {columns.map((col, idx) => (
                    <div key={idx} className="flex flex-wrap items-center gap-2 p-2 bg-[#111] rounded-lg">
                      <div className="flex items-center gap-2 w-full">
                        <input
                          type="text"
                          value={col.name}
                          onChange={(e) => updateColumn(idx, 'name', e.target.value)}
                          placeholder="column_name"
                          className="input flex-1 min-w-0"
                          required
                        />
                        <select
                          value={col.type}
                          onChange={(e) => updateColumn(idx, 'type', e.target.value)}
                          className="input w-40"
                        >
                          {PG_TYPES.map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      </div>
                      <div className="flex items-center gap-3">
                        <label className="flex items-center gap-1 text-xs text-gray-400 whitespace-nowrap">
                          <input
                            type="checkbox"
                            checked={!col.nullable}
                            onChange={(e) => updateColumn(idx, 'nullable', !e.target.checked)}
                            className="h-3 w-3 text-primary-600 border-white/10 rounded"
                          />
                          NOT NULL
                        </label>
                        <label className="flex items-center gap-1 text-xs text-gray-400 whitespace-nowrap">
                          <input
                            type="checkbox"
                            checked={col.primary_key || false}
                            onChange={(e) => updateColumn(idx, 'primary_key', e.target.checked)}
                            className="h-3 w-3 text-primary-600 border-white/10 rounded"
                          />
                          PK
                        </label>
                        <button
                          type="button"
                          onClick={() => removeColumn(idx)}
                          className="text-red-500 hover:text-red-400"
                          disabled={columns.length <= 1}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Default Value (optional, per column)</label>
                <div className="space-y-1">
                  {columns.map((col, idx) => (
                    col.name ? (
                      <div key={idx} className="flex items-center gap-2 text-sm">
                        <span className="text-gray-400 w-32 truncate">{col.name}:</span>
                        <input
                          type="text"
                          value={col.default || ''}
                          onChange={(e) => updateColumn(idx, 'default', e.target.value || undefined)}
                          placeholder="no default"
                          className="input text-xs flex-1"
                        />
                      </div>
                    ) : null
                  ))}
                </div>
              </div>

              {createTableMutation.isError && (
                <ErrorDisplay error={createTableMutation.error} title="Failed to create table" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => setShowCreateTableModal(false)}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createTableMutation.isPending || !tableName.trim() || columns.some((c) => !c.name.trim())}
                >
                  {createTableMutation.isPending ? 'Creating...' : 'Create Table'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Create View Modal */}
      {showCreateViewModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Create View</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                createViewMutation.mutate();
              }}
              className="space-y-4"
            >
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">View Name *</label>
                <input
                  type="text"
                  value={viewName}
                  onChange={(e) => setViewName(e.target.value)}
                  placeholder="my_view"
                  required
                  className="input"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">SQL Query *</label>
                <textarea
                  value={viewSql}
                  onChange={(e) => setViewSql(e.target.value)}
                  placeholder="SELECT * FROM ..."
                  required
                  rows={6}
                  className="input resize-none font-mono text-xs"
                />
              </div>
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={viewOrReplace}
                  onChange={(e) => setViewOrReplace(e.target.checked)}
                  className="h-4 w-4 text-primary-600 border-white/10 rounded"
                />
                OR REPLACE (overwrite if exists)
              </label>

              {createViewMutation.isError && (
                <ErrorDisplay error={createViewMutation.error} title="Failed to create view" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => setShowCreateViewModal(false)}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createViewMutation.isPending || !viewName.trim() || !viewSql.trim()}
                >
                  {createViewMutation.isPending ? 'Creating...' : 'Create View'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
