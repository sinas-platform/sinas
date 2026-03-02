import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { apiClient } from '../lib/api';
import { useState, useMemo } from 'react';
import {
  ArrowLeft, Table2, Plus, Trash2, Pencil, Key, Link2,
  ChevronLeft, ChevronRight, ArrowUpDown, Filter, X, AlertTriangle, Lock,
} from 'lucide-react';
import { ErrorDisplay } from '../components/ErrorDisplay';
import type { ColumnInfo, ConstraintInfo, IndexInfo, FilterCondition, ColumnDefinition } from '../types';

const PG_TYPES = [
  'integer', 'bigint', 'smallint', 'serial', 'bigserial',
  'text', 'varchar(255)', 'char(1)',
  'boolean',
  'real', 'double precision', 'numeric',
  'date', 'timestamp', 'timestamptz', 'time',
  'uuid', 'json', 'jsonb',
  'bytea',
];

const FILTER_OPERATORS = ['=', '!=', '>', '<', '>=', '<=', 'LIKE', 'ILIKE', 'IS NULL', 'IS NOT NULL'];

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

export function DbTableDetail() {
  const { name: connectionName, table } = useParams<{ name: string; table: string }>();
  const [searchParams] = useSearchParams();
  const schema = searchParams.get('schema') || 'public';
  const queryClient = useQueryClient();

  // Data browser state
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [sortBy, setSortBy] = useState<string | undefined>(undefined);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [filters, setFilters] = useState<FilterCondition[]>([]);
  const [showFilters, setShowFilters] = useState(false);
  const [pendingFilters, setPendingFilters] = useState<FilterCondition[]>([]);

  // Modals
  const [showAddColumnModal, setShowAddColumnModal] = useState(false);
  const [showInsertModal, setShowInsertModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editRow, setEditRow] = useState<Record<string, any> | null>(null);
  const [editOriginal, setEditOriginal] = useState<Record<string, any> | null>(null);
  const [insertFormData, setInsertFormData] = useState<Record<string, string>>({});
  const [newColumns, setNewColumns] = useState<ColumnDefinition[]>([
    { name: '', type: 'text', nullable: true },
  ]);

  // Confirm-destroy
  const [showDropTableConfirm, setShowDropTableConfirm] = useState(false);

  // Connection info (for read_only check)
  const { data: connection } = useQuery({
    queryKey: ['databaseConnection', connectionName],
    queryFn: () => apiClient.getDatabaseConnection(connectionName!),
    enabled: !!connectionName,
  });

  const isReadOnly = connection?.read_only ?? false;

  // Queries
  const { data: tableDetail, isLoading: detailLoading, error: detailError } = useQuery({
    queryKey: ['dbTableDetail', connectionName, table, schema],
    queryFn: () => apiClient.getDbTableDetail(connectionName!, table!, schema),
    enabled: !!connectionName && !!table,
  });

  const { data: rowsData, isLoading: rowsLoading, error: rowsError } = useQuery({
    queryKey: ['dbRows', connectionName, table, schema, limit, offset, sortBy, sortOrder, filters],
    queryFn: () =>
      apiClient.browseDbRows(connectionName!, table!, {
        schema,
        limit,
        offset,
        sort_by: sortBy,
        sort_order: sortOrder,
        filters: filters.length > 0 ? filters : undefined,
      }),
    enabled: !!connectionName && !!table,
  });

  // Mutations
  const alterTableMutation = useMutation({
    mutationFn: (data: { add_columns?: ColumnDefinition[] }) =>
      apiClient.alterDbTable(connectionName!, table!, { schema_name: schema, ...data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbTableDetail', connectionName, table, schema] });
      queryClient.invalidateQueries({ queryKey: ['dbRows', connectionName, table] });
      setShowAddColumnModal(false);
      setNewColumns([{ name: '', type: 'text', nullable: true }]);
    },
  });

  const insertMutation = useMutation({
    mutationFn: (rows: Record<string, any>[]) =>
      apiClient.insertDbRows(connectionName!, table!, rows, schema),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbRows', connectionName, table] });
      setShowInsertModal(false);
      setInsertFormData({});
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ where, set_values }: { where: Record<string, any>; set_values: Record<string, any> }) =>
      apiClient.updateDbRows(connectionName!, table!, where, set_values, schema),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbRows', connectionName, table] });
      setShowEditModal(false);
      setEditRow(null);
      setEditOriginal(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (where: Record<string, any>) =>
      apiClient.deleteDbRows(connectionName!, table!, where, schema),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dbRows', connectionName, table] });
    },
  });

  const dropTableMutation = useMutation({
    mutationFn: () =>
      apiClient.dropDbTable(connectionName!, table!, schema, false),
    onSuccess: () => {
      window.location.href = `/database-connections/${connectionName}`;
    },
  });

  // Derived data
  const pkColumns = useMemo(() => {
    if (!tableDetail) return [];
    return tableDetail.columns.filter((c: ColumnInfo) => c.is_primary_key).map((c: ColumnInfo) => c.column_name);
  }, [tableDetail]);

  const hasPK = pkColumns.length > 0;
  const canMutateRows = hasPK && !isReadOnly;

  const buildPKWhere = (row: Record<string, any>): Record<string, any> => {
    const where: Record<string, any> = {};
    for (const pk of pkColumns) {
      where[pk] = row[pk];
    }
    return where;
  };

  const totalPages = rowsData ? Math.ceil(rowsData.total_count / limit) : 0;
  const currentPage = Math.floor(offset / limit) + 1;

  const columnNames = useMemo(() => {
    if (rowsData && rowsData.rows.length > 0) {
      return Object.keys(rowsData.rows[0]);
    }
    if (tableDetail) {
      return tableDetail.columns.map((c: ColumnInfo) => c.column_name);
    }
    return [];
  }, [rowsData, tableDetail]);

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(col);
      setSortOrder('asc');
    }
    setOffset(0);
  };

  const addFilterRow = () => {
    setPendingFilters([...pendingFilters, { column: columnNames[0] || '', operator: '=', value: '' }]);
  };

  const applyFilters = () => {
    const valid = pendingFilters.filter((f) => f.column && f.operator);
    setFilters(valid);
    setOffset(0);
  };

  const clearFilters = () => {
    setFilters([]);
    setPendingFilters([]);
    setOffset(0);
  };

  const openInsertModal = () => {
    if (!tableDetail) return;
    const defaults: Record<string, string> = {};
    for (const col of tableDetail.columns) {
      if (!col.is_primary_key || !col.column_default?.startsWith('nextval')) {
        defaults[col.column_name] = '';
      }
    }
    setInsertFormData(defaults);
    setShowInsertModal(true);
  };

  const handleInsert = (e: React.FormEvent) => {
    e.preventDefault();
    const row: Record<string, any> = {};
    for (const [key, val] of Object.entries(insertFormData)) {
      if (val !== '') {
        row[key] = val;
      }
    }
    if (Object.keys(row).length > 0) {
      insertMutation.mutate([row]);
    }
  };

  const openEditModal = (row: Record<string, any>) => {
    setEditOriginal({ ...row });
    setEditRow({ ...row });
    setShowEditModal(true);
  };

  const handleUpdate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editRow || !editOriginal) return;
    const where = buildPKWhere(editOriginal);
    const set_values: Record<string, any> = {};
    for (const [key, val] of Object.entries(editRow)) {
      if (val !== editOriginal[key] && !pkColumns.includes(key)) {
        set_values[key] = val;
      }
    }
    if (Object.keys(set_values).length > 0) {
      updateMutation.mutate({ where, set_values });
    } else {
      setShowEditModal(false);
    }
  };

  const handleDelete = (row: Record<string, any>) => {
    if (!confirm('Delete this row? This cannot be undone.')) return;
    const where = buildPKWhere(row);
    deleteMutation.mutate(where);
  };

  const formatCell = (value: any): string => {
    if (value === null || value === undefined) return 'NULL';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  };

  if (!connectionName || !table) return null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link
            to={`/database-connections/${connectionName}`}
            className="text-gray-400 hover:text-gray-200"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <Table2 className="w-6 h-6 text-primary-600" />
              <h1 className="text-3xl font-bold text-gray-100">
                {tableDetail?.display_name || table}
              </h1>
              {tableDetail?.display_name && (
                <span className="text-gray-500 text-sm">{table}</span>
              )}
              {isReadOnly && (
                <span className="px-2 py-0.5 bg-yellow-900/30 text-yellow-300 text-xs font-medium rounded flex items-center gap-1">
                  <Lock className="w-3 h-3" />
                  Read-only
                </span>
              )}
            </div>
            <p className="text-gray-400 mt-1 text-sm">
              {connectionName} / {schema}
              {tableDetail?.description && (
                <span className="ml-2 text-gray-500">- {tableDetail.description}</span>
              )}
            </p>
          </div>
        </div>
        {!isReadOnly && (
          <button
            onClick={() => setShowDropTableConfirm(true)}
            className="btn btn-secondary text-red-400 hover:text-red-300 text-sm"
          >
            <Trash2 className="w-4 h-4 mr-1" />
            Drop Table
          </button>
        )}
      </div>

      {/* Schema Section */}
      {detailLoading ? (
        <div className="text-center py-8">
          <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600" />
        </div>
      ) : detailError ? (
        <ErrorDisplay error={detailError} title="Failed to load table details" />
      ) : tableDetail ? (
        <div className="space-y-4">
          {/* Columns */}
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-lg font-semibold text-gray-100">Columns</h3>
              {!isReadOnly && (
                <button
                  onClick={() => {
                    setNewColumns([{ name: '', type: 'text', nullable: true }]);
                    setShowAddColumnModal(true);
                  }}
                  className="text-sm text-primary-400 hover:text-primary-300 flex items-center"
                >
                  <Plus className="w-4 h-4 mr-1" />
                  Add Column
                </button>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/5">
                    <th className="text-left px-3 py-2 text-gray-400 font-medium">Name</th>
                    <th className="text-left px-3 py-2 text-gray-400 font-medium">Type</th>
                    <th className="text-center px-3 py-2 text-gray-400 font-medium">Nullable</th>
                    <th className="text-left px-3 py-2 text-gray-400 font-medium">Default</th>
                    <th className="text-center px-3 py-2 text-gray-400 font-medium">Keys</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {tableDetail.columns.map((col: ColumnInfo) => {
                    const fk = tableDetail.constraints.find(
                      (c: ConstraintInfo) =>
                        c.constraint_type === 'FOREIGN KEY' && c.columns.includes(col.column_name)
                    );
                    return (
                      <tr key={col.column_name} className="hover:bg-white/[0.02]">
                        <td className="px-3 py-2">
                          <span className="text-gray-100 font-mono">{col.column_name}</span>
                          {col.display_name && (
                            <span className="text-gray-500 text-xs ml-2">{col.display_name}</span>
                          )}
                          {col.description && (
                            <p className="text-gray-500 text-xs">{col.description}</p>
                          )}
                        </td>
                        <td className="px-3 py-2 text-gray-300 font-mono text-xs">{col.data_type}</td>
                        <td className="px-3 py-2 text-center">
                          <span className={col.is_nullable === 'YES' ? 'text-gray-500' : 'text-yellow-500 text-xs font-medium'}>
                            {col.is_nullable === 'YES' ? 'yes' : 'NOT NULL'}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-gray-400 font-mono text-xs">
                          {col.column_default || '-'}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <div className="flex items-center justify-center gap-1">
                            {col.is_primary_key && (
                              <span title="Primary Key">
                                <Key className="w-4 h-4 text-yellow-500" />
                              </span>
                            )}
                            {fk && (
                              <span title={`FK -> ${fk.ref_table}(${fk.ref_columns?.join(', ')})`}>
                                <Link2 className="w-4 h-4 text-blue-400" />
                              </span>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Constraints & Indexes side by side */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {tableDetail.constraints.length > 0 && (
              <div className="card">
                <h3 className="text-lg font-semibold text-gray-100 mb-3">Constraints</h3>
                <div className="space-y-2 text-sm">
                  {tableDetail.constraints.map((c: ConstraintInfo) => (
                    <div key={c.constraint_name} className="flex items-start gap-2">
                      <span className="px-1.5 py-0.5 bg-[#1e1e1e] text-gray-300 rounded text-xs font-mono whitespace-nowrap">
                        {c.constraint_type}
                      </span>
                      <div>
                        <span className="text-gray-200 font-mono text-xs">{c.constraint_name}</span>
                        <span className="text-gray-500 text-xs ml-2">({c.columns.join(', ')})</span>
                        {c.ref_table && (
                          <span className="text-blue-400 text-xs ml-1">
                            &rarr; {c.ref_table}({c.ref_columns?.join(', ')})
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {tableDetail.indexes.length > 0 && (
              <div className="card">
                <h3 className="text-lg font-semibold text-gray-100 mb-3">Indexes</h3>
                <div className="space-y-2 text-sm">
                  {tableDetail.indexes.map((idx: IndexInfo) => (
                    <div key={idx.index_name}>
                      <span className="text-gray-200 font-mono text-xs">{idx.index_name}</span>
                      <p className="text-gray-500 text-xs font-mono mt-0.5 whitespace-pre-wrap">{idx.definition}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}

      {/* Data Browser Section */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-100">Data Browser</h3>
          <div className="flex items-center gap-2">
            {!hasPK && (
              <span className="flex items-center text-xs text-yellow-500 gap-1">
                <AlertTriangle className="w-3 h-3" />
                No PK - edit/delete disabled
              </span>
            )}
            <button
              onClick={() => {
                if (!showFilters) {
                  setPendingFilters(filters.length > 0 ? [...filters] : []);
                }
                setShowFilters(!showFilters);
              }}
              className={`btn btn-secondary text-sm flex items-center ${filters.length > 0 ? 'text-primary-400' : ''}`}
            >
              <Filter className="w-4 h-4 mr-1" />
              Filters{filters.length > 0 ? ` (${filters.length})` : ''}
            </button>
            <select
              value={limit}
              onChange={(e) => { setLimit(Number(e.target.value)); setOffset(0); }}
              className="input text-sm w-20"
            >
              {[10, 25, 50, 100, 200].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            {!isReadOnly && (
              <button onClick={openInsertModal} className="btn btn-primary text-sm flex items-center">
                <Plus className="w-4 h-4 mr-1" />
                Add Row
              </button>
            )}
          </div>
        </div>

        {/* Filter Builder */}
        {showFilters && (
          <div className="mb-4 p-3 bg-[#111] rounded-lg border border-white/5">
            <div className="space-y-2">
              {pendingFilters.map((f, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <select
                    value={f.column}
                    onChange={(e) => {
                      const updated = [...pendingFilters];
                      updated[idx] = { ...updated[idx], column: e.target.value };
                      setPendingFilters(updated);
                    }}
                    className="input text-sm flex-1"
                  >
                    {columnNames.map((col) => (
                      <option key={col} value={col}>{col}</option>
                    ))}
                  </select>
                  <select
                    value={f.operator}
                    onChange={(e) => {
                      const updated = [...pendingFilters];
                      updated[idx] = { ...updated[idx], operator: e.target.value };
                      setPendingFilters(updated);
                    }}
                    className="input text-sm w-32"
                  >
                    {FILTER_OPERATORS.map((op) => (
                      <option key={op} value={op}>{op}</option>
                    ))}
                  </select>
                  {!['IS NULL', 'IS NOT NULL'].includes(f.operator) && (
                    <input
                      type="text"
                      value={f.value ?? ''}
                      onChange={(e) => {
                        const updated = [...pendingFilters];
                        updated[idx] = { ...updated[idx], value: e.target.value };
                        setPendingFilters(updated);
                      }}
                      placeholder="value"
                      className="input text-sm flex-1"
                    />
                  )}
                  <button
                    onClick={() => setPendingFilters(pendingFilters.filter((_, i) => i !== idx))}
                    className="text-red-500 hover:text-red-400"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
            <div className="flex items-center gap-2 mt-3">
              <button onClick={addFilterRow} className="text-sm text-primary-400 hover:text-primary-300">
                + Add Filter
              </button>
              <div className="flex-1" />
              <button onClick={clearFilters} className="btn btn-secondary text-sm">
                Clear
              </button>
              <button onClick={applyFilters} className="btn btn-primary text-sm">
                Apply
              </button>
            </div>
          </div>
        )}

        {/* Data Table */}
        {rowsLoading ? (
          <div className="text-center py-8">
            <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600" />
          </div>
        ) : rowsError ? (
          <ErrorDisplay error={rowsError} title="Failed to load rows" />
        ) : rowsData ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-[#111]">
                  <tr>
                    {columnNames.map((col) => (
                      <th
                        key={col}
                        className="text-left px-3 py-2 text-gray-400 font-medium whitespace-nowrap cursor-pointer hover:text-gray-200"
                        onClick={() => handleSort(col)}
                      >
                        <span className="flex items-center gap-1">
                          {col}
                          {sortBy === col && (
                            <ArrowUpDown className={`w-3 h-3 ${sortOrder === 'desc' ? 'rotate-180' : ''}`} />
                          )}
                        </span>
                      </th>
                    ))}
                    {canMutateRows && (
                      <th className="text-center px-3 py-2 text-gray-400 font-medium w-20">Actions</th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {rowsData.rows.length === 0 ? (
                    <tr>
                      <td colSpan={columnNames.length + (canMutateRows ? 1 : 0)} className="text-center py-8 text-gray-500">
                        No rows found
                      </td>
                    </tr>
                  ) : (
                    rowsData.rows.map((row, idx) => (
                      <tr key={idx} className="hover:bg-white/[0.02]">
                        {columnNames.map((col) => (
                          <td key={col} className="px-3 py-2 max-w-xs truncate">
                            <span
                              className={row[col] === null ? 'text-gray-600 italic' : 'text-gray-200 font-mono text-xs'}
                              title={formatCell(row[col])}
                            >
                              {formatCell(row[col])}
                            </span>
                          </td>
                        ))}
                        {canMutateRows && (
                          <td className="px-3 py-2 text-center">
                            <div className="flex items-center justify-center gap-1">
                              <button
                                onClick={() => openEditModal(row)}
                                className="text-blue-500 hover:text-blue-400"
                                title="Edit"
                              >
                                <Pencil className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => handleDelete(row)}
                                className="text-red-500 hover:text-red-400"
                                title="Delete"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-white/5">
              <span className="text-sm text-gray-400">
                {rowsData.total_count} rows total
                {rowsData.total_count > 0 && (
                  <> | Showing {offset + 1}-{Math.min(offset + limit, rowsData.total_count)}</>
                )}
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                  disabled={offset === 0}
                  className="btn btn-secondary text-sm p-1.5 disabled:opacity-30"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <span className="text-sm text-gray-300">
                  Page {currentPage} of {totalPages || 1}
                </span>
                <button
                  onClick={() => setOffset(offset + limit)}
                  disabled={offset + limit >= rowsData.total_count}
                  className="btn btn-secondary text-sm p-1.5 disabled:opacity-30"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          </>
        ) : null}
      </div>

      {/* Type-to-confirm Drop Table Modal */}
      {showDropTableConfirm && (
        <ConfirmDestroyModal
          title="Drop Table"
          targetName={table!}
          description={`This will permanently drop the table "${table}" from ${schema}. All data will be lost. This action cannot be undone.`}
          onConfirm={() => dropTableMutation.mutate()}
          onCancel={() => { setShowDropTableConfirm(false); dropTableMutation.reset(); }}
          isPending={dropTableMutation.isPending}
          error={dropTableMutation.error}
        />
      )}

      {/* Add Column Modal */}
      {showAddColumnModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Add Column(s)</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                alterTableMutation.mutate({ add_columns: newColumns });
              }}
              className="space-y-4"
            >
              <div className="space-y-2">
                {newColumns.map((col, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={col.name}
                      onChange={(e) => {
                        const updated = [...newColumns];
                        updated[idx] = { ...updated[idx], name: e.target.value };
                        setNewColumns(updated);
                      }}
                      placeholder="column_name"
                      required
                      className="input flex-1"
                    />
                    <select
                      value={col.type}
                      onChange={(e) => {
                        const updated = [...newColumns];
                        updated[idx] = { ...updated[idx], type: e.target.value };
                        setNewColumns(updated);
                      }}
                      className="input w-36"
                    >
                      {PG_TYPES.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                    <label className="flex items-center gap-1 text-xs text-gray-400 whitespace-nowrap">
                      <input
                        type="checkbox"
                        checked={!col.nullable}
                        onChange={(e) => {
                          const updated = [...newColumns];
                          updated[idx] = { ...updated[idx], nullable: !e.target.checked };
                          setNewColumns(updated);
                        }}
                        className="h-3 w-3 text-primary-600 border-white/10 rounded"
                      />
                      NOT NULL
                    </label>
                    {newColumns.length > 1 && (
                      <button
                        type="button"
                        onClick={() => setNewColumns(newColumns.filter((_, i) => i !== idx))}
                        className="text-red-500 hover:text-red-400"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              <button
                type="button"
                onClick={() => setNewColumns([...newColumns, { name: '', type: 'text', nullable: true }])}
                className="text-sm text-primary-400 hover:text-primary-300"
              >
                + Add Another
              </button>

              {alterTableMutation.isError && (
                <ErrorDisplay error={alterTableMutation.error} title="Failed to alter table" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button type="button" onClick={() => setShowAddColumnModal(false)} className="btn btn-secondary">
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={alterTableMutation.isPending || newColumns.some((c) => !c.name.trim())}
                >
                  {alterTableMutation.isPending ? 'Adding...' : 'Add Column(s)'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Insert Row Modal */}
      {showInsertModal && tableDetail && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Insert Row</h2>
            <form onSubmit={handleInsert} className="space-y-3">
              {tableDetail.columns.map((col: ColumnInfo) => {
                const isSerial = col.column_default?.startsWith('nextval');
                if (isSerial && col.is_primary_key) return null;
                return (
                  <div key={col.column_name}>
                    <label className="block text-sm text-gray-300 mb-1">
                      {col.column_name}
                      <span className="text-gray-500 text-xs ml-2">{col.data_type}</span>
                      {col.is_nullable === 'NO' && !col.column_default && (
                        <span className="text-red-400 ml-1">*</span>
                      )}
                    </label>
                    <input
                      type="text"
                      value={insertFormData[col.column_name] ?? ''}
                      onChange={(e) =>
                        setInsertFormData({ ...insertFormData, [col.column_name]: e.target.value })
                      }
                      placeholder={col.column_default ? `default: ${col.column_default}` : ''}
                      className="input text-sm"
                    />
                  </div>
                );
              })}

              {insertMutation.isError && (
                <ErrorDisplay error={insertMutation.error} title="Failed to insert row" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button type="button" onClick={() => setShowInsertModal(false)} className="btn btn-secondary">
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={insertMutation.isPending}>
                  {insertMutation.isPending ? 'Inserting...' : 'Insert Row'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Row Modal */}
      {showEditModal && editRow && tableDetail && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Edit Row</h2>
            <form onSubmit={handleUpdate} className="space-y-3">
              {columnNames.map((col) => {
                const isPK = pkColumns.includes(col);
                return (
                  <div key={col}>
                    <label className="block text-sm text-gray-300 mb-1">
                      {col}
                      {isPK && <span className="text-yellow-500 text-xs ml-2">(PK - read only)</span>}
                    </label>
                    <input
                      type="text"
                      value={editRow[col] === null ? '' : String(editRow[col] ?? '')}
                      onChange={(e) => setEditRow({ ...editRow, [col]: e.target.value || null })}
                      className="input text-sm"
                      disabled={isPK}
                    />
                  </div>
                );
              })}

              {updateMutation.isError && (
                <ErrorDisplay error={updateMutation.error} title="Failed to update row" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button type="button" onClick={() => setShowEditModal(false)} className="btn btn-secondary">
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={updateMutation.isPending}>
                  {updateMutation.isPending ? 'Updating...' : 'Update Row'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
