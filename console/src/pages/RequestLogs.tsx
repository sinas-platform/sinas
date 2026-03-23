import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import {
  FileText,
  Filter,
  Download,
  Clock,
  Users,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  CheckCircle,
  XCircle,
  MessageSquare,
  Activity,
  Workflow,
} from 'lucide-react';
import { Messages } from './Messages';

type Tab = 'requests' | 'messages' | 'executions';

export function RequestLogs() {
  const [activeTab, setActiveTab] = useState<Tab>('requests');
  const [treeChatId, setTreeChatId] = useState<string | null>(null);

  const viewTree = (chatId: string) => {
    setTreeChatId(chatId);
    setActiveTab('executions');
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-100">Logs</h1>
        <p className="text-gray-400 mt-1">Monitor API requests and chat messages</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-white/[0.06]">
        <nav className="-mb-px flex space-x-8">
          <button
            onClick={() => setActiveTab('requests')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'requests'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-300 hover:border-white/10'
            }`}
          >
            <Activity className="w-5 h-5 inline mr-2" />
            Request Logs
          </button>
          <button
            onClick={() => setActiveTab('messages')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'messages'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-300 hover:border-white/10'
            }`}
          >
            <MessageSquare className="w-5 h-5 inline mr-2" />
            Message Logs
          </button>
          <button
            onClick={() => setActiveTab('executions')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'executions'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-300 hover:border-white/10'
            }`}
          >
            <Workflow className="w-5 h-5 inline mr-2" />
            Executions
          </button>
        </nav>
      </div>

      {/* Tab Content */}
      <div>
        {activeTab === 'requests' && <RequestLogsTab />}
        {activeTab === 'messages' && <Messages onViewTree={viewTree} />}
        {activeTab === 'executions' && <ExecutionsTab initialTreeChatId={treeChatId} onClearTree={() => setTreeChatId(null)} />}
      </div>
    </div>
  );
}

function RequestLogsTab() {
  const [filters, setFilters] = useState({
    user_id: '',
    start_time: '',
    end_time: '',
    permission: '',
    path_pattern: '',
    status_code: '',
    limit: 100,
    offset: 0,
  });
  const [showFilters, setShowFilters] = useState(false);
  const [expandedLog, setExpandedLog] = useState<string | null>(null);

  const { data: logs, isLoading } = useQuery({
    queryKey: ['requestLogs', filters],
    queryFn: () => {
      const params: any = { ...filters };
      Object.keys(params).forEach(key => {
        if (params[key] === '') delete params[key];
      });
      if (params.status_code) params.status_code = parseInt(params.status_code);
      return apiClient.listRequestLogs(params);
    },
    retry: false,
  });

  const { data: stats } = useQuery({
    queryKey: ['requestLogStats', filters.user_id, filters.start_time, filters.end_time],
    queryFn: () => {
      const params: any = {};
      if (filters.user_id) params.user_id = filters.user_id;
      if (filters.start_time) params.start_time = filters.start_time;
      if (filters.end_time) params.end_time = filters.end_time;
      return apiClient.getRequestLogStats(params);
    },
    retry: false,
  });

  const getStatusColor = (statusCode: number) => {
    if (statusCode >= 200 && statusCode < 300) return 'text-green-600 bg-green-900/20';
    if (statusCode >= 300 && statusCode < 400) return 'text-blue-600 bg-blue-900/20';
    if (statusCode >= 400 && statusCode < 500) return 'text-orange-400 bg-orange-900/20';
    return 'text-red-600 bg-red-900/20';
  };

  const getMethodColor = (method: string) => {
    const colors: Record<string, string> = {
      GET: 'text-blue-600 bg-blue-900/20',
      POST: 'text-green-600 bg-green-900/20',
      PUT: 'text-orange-400 bg-orange-900/20',
      PATCH: 'text-purple-400 bg-purple-900/20',
      DELETE: 'text-red-600 bg-red-900/20',
    };
    return colors[method] || 'text-gray-400 bg-[#0d0d0d]';
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const exportLogs = () => {
    if (!logs) return;
    const csv = [
      ['Timestamp', 'User', 'Method', 'Path', 'Status', 'Response Time', 'Permission', 'Has Permission'].join(','),
      ...logs.map((log: any) => [
        new Date(log.timestamp).toISOString(),
        log.user_email,
        log.method,
        log.path,
        log.status_code,
        log.response_time_ms,
        log.permission_used,
        log.has_permission
      ].join(','))
    ].join('\n');

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `request-logs-${new Date().toISOString()}.csv`;
    a.click();
  };

  return (
    <div className="space-y-6">
      {/* Actions */}
      <div className="flex justify-end gap-2">
        <button
          onClick={() => setShowFilters(!showFilters)}
          className="btn btn-secondary"
        >
          <Filter className="w-4 h-4" />
          Filters
        </button>
        <button
          onClick={exportLogs}
          className="btn btn-secondary"
          disabled={!logs || logs.length === 0}
        >
          <Download className="w-4 h-4" />
          Export CSV
        </button>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-400">Total Requests</p>
                <p className="text-2xl font-bold text-gray-100 mt-1">{stats.total_requests}</p>
              </div>
              <div className="p-3 bg-primary-900/20 rounded-lg">
                <FileText className="w-5 h-5 text-primary-600" />
              </div>
            </div>
          </div>
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-400">Unique Users</p>
                <p className="text-2xl font-bold text-gray-100 mt-1">{stats.unique_users}</p>
              </div>
              <div className="p-3 bg-blue-900/20 rounded-lg">
                <Users className="w-5 h-5 text-blue-600" />
              </div>
            </div>
          </div>
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-400">Avg Response Time</p>
                <p className="text-2xl font-bold text-gray-100 mt-1">{Math.round(stats.avg_response_time_ms)}ms</p>
              </div>
              <div className="p-3 bg-green-900/20 rounded-lg">
                <Clock className="w-5 h-5 text-green-600" />
              </div>
            </div>
          </div>
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-400">Error Rate</p>
                <p className="text-2xl font-bold text-gray-100 mt-1">{(stats.error_rate * 100).toFixed(1)}%</p>
              </div>
              <div className="p-3 bg-red-900/20 rounded-lg">
                <AlertCircle className="w-5 h-5 text-red-600" />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Top Paths and Permissions */}
      {stats && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-100 mb-4">Top Paths</h3>
            <div className="space-y-2">
              {stats.top_paths.map((item: any, idx: number) => (
                <div key={idx} className="flex items-center justify-between p-2 bg-[#0d0d0d] rounded">
                  <span className="text-sm font-mono text-gray-100">{item.path}</span>
                  <span className="text-sm font-semibold text-gray-400">{item.count}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-100 mb-4">Top Permissions</h3>
            <div className="space-y-2">
              {stats.top_permissions.map((item: any, idx: number) => (
                <div key={idx} className="flex items-center justify-between p-2 bg-[#0d0d0d] rounded">
                  <span className="text-sm font-mono text-gray-100">{item.permission}</span>
                  <span className="text-sm font-semibold text-gray-400">{item.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      {showFilters && (
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-100 mb-4">Filters</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="label">User ID</label>
              <input
                type="text"
                value={filters.user_id}
                onChange={(e) => setFilters({ ...filters, user_id: e.target.value })}
                className="input"
                placeholder="Filter by user ID"
              />
            </div>
            <div>
              <label className="label">Start Time</label>
              <input
                type="datetime-local"
                value={filters.start_time}
                onChange={(e) => setFilters({ ...filters, start_time: e.target.value })}
                className="input"
              />
            </div>
            <div>
              <label className="label">End Time</label>
              <input
                type="datetime-local"
                value={filters.end_time}
                onChange={(e) => setFilters({ ...filters, end_time: e.target.value })}
                className="input"
              />
            </div>
            <div>
              <label className="label">Permission</label>
              <input
                type="text"
                value={filters.permission}
                onChange={(e) => setFilters({ ...filters, permission: e.target.value })}
                className="input"
                placeholder="e.g. sinas.chats.read"
              />
            </div>
            <div>
              <label className="label">Path Pattern</label>
              <input
                type="text"
                value={filters.path_pattern}
                onChange={(e) => setFilters({ ...filters, path_pattern: e.target.value })}
                className="input"
                placeholder="e.g. /api/v1/chats"
              />
            </div>
            <div>
              <label className="label">Status Code</label>
              <input
                type="number"
                value={filters.status_code}
                onChange={(e) => setFilters({ ...filters, status_code: e.target.value })}
                className="input"
                placeholder="e.g. 200, 404, 500"
              />
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <button
              onClick={() => setFilters({
                user_id: '',
                start_time: '',
                end_time: '',
                permission: '',
                path_pattern: '',
                status_code: '',
                limit: 100,
                offset: 0,
              })}
              className="btn btn-secondary"
            >
              Clear Filters
            </button>
          </div>
        </div>
      )}

      {/* Logs List */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-100">Request Logs</h2>
          <span className="text-sm text-gray-500">{logs?.length || 0} results</span>
        </div>

        {isLoading ? (
          <div className="text-center py-8 text-gray-500">Loading logs...</div>
        ) : !logs || logs.length === 0 ? (
          <div className="text-center py-8 text-gray-500">No logs found</div>
        ) : (
          <div className="space-y-2">
            {logs.map((log: any) => (
              <div key={log.request_id} className="border border-white/[0.06] rounded-lg overflow-hidden">
                <button
                  onClick={() => setExpandedLog(expandedLog === log.request_id ? null : log.request_id)}
                  className="w-full px-4 py-3 flex items-center gap-3 hover:bg-white/5 transition-colors text-left"
                >
                  {expandedLog === log.request_id ? (
                    <ChevronDown className="w-4 h-4 text-gray-500 flex-shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-gray-500 flex-shrink-0" />
                  )}

                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <span className={`px-2 py-0.5 text-xs font-medium rounded ${getMethodColor(log.method)}`}>
                      {log.method}
                    </span>
                    <span className={`px-2 py-0.5 text-xs font-medium rounded ${getStatusColor(log.status_code)}`}>
                      {log.status_code}
                    </span>
                    <span className="text-sm font-mono text-gray-100 truncate">{log.path}</span>
                  </div>

                  <div className="flex items-center gap-4 text-xs text-gray-500 flex-shrink-0">
                    <span>{log.response_time_ms}ms</span>
                    <span>{new Date(log.timestamp).toLocaleString()}</span>
                    {log.has_permission ? (
                      <CheckCircle className="w-4 h-4 text-green-600" />
                    ) : (
                      <XCircle className="w-4 h-4 text-red-600" />
                    )}
                  </div>
                </button>

                {expandedLog === log.request_id && (
                  <div className="px-4 py-3 bg-[#0d0d0d] border-t border-white/[0.06] space-y-3">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">User</p>
                        <p className="text-sm text-gray-100">{log.user_email}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Permission</p>
                        <p className="text-sm text-gray-100 font-mono">{log.permission_used}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">IP Address</p>
                        <p className="text-sm text-gray-100">{log.ip_address}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Response Size</p>
                        <p className="text-sm text-gray-100">{formatBytes(log.response_size_bytes)}</p>
                      </div>
                      {log.resource_type && (
                        <>
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">Resource Type</p>
                            <p className="text-sm text-gray-100">{log.resource_type}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">Resource ID</p>
                            <p className="text-sm text-gray-100 font-mono">{log.resource_id}</p>
                          </div>
                        </>
                      )}
                    </div>

                    {log.query_params && (
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Query Params</p>
                        <pre className="text-xs text-gray-100 bg-[#161616] p-2 rounded border border-white/[0.06] overflow-x-auto">
                          {log.query_params}
                        </pre>
                      </div>
                    )}

                    {log.request_body && log.request_body !== '{}' && (
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Request Body</p>
                        <pre className="text-xs text-gray-100 bg-[#161616] p-2 rounded border border-white/[0.06] overflow-x-auto">
                          {JSON.stringify(JSON.parse(log.request_body), null, 2)}
                        </pre>
                      </div>
                    )}

                    {log.error_message && (
                      <div>
                        <p className="text-xs font-medium text-red-600 mb-1">Error</p>
                        <pre className="text-xs text-red-900 bg-red-900/20 p-2 rounded border border-red-800/30 overflow-x-auto">
                          {log.error_type}: {log.error_message}
                        </pre>
                      </div>
                    )}

                    <div>
                      <p className="text-xs font-medium text-gray-500 mb-1">User Agent</p>
                      <p className="text-xs text-gray-300 break-all">{log.user_agent}</p>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const statusColors: Record<string, string> = {
  COMPLETED: 'text-green-400 bg-green-900/20',
  RUNNING: 'text-blue-400 bg-blue-900/20',
  PENDING: 'text-yellow-400 bg-yellow-900/20',
  FAILED: 'text-red-400 bg-red-900/20',
  CANCELLED: 'text-gray-400 bg-gray-800',
  AWAITING_INPUT: 'text-purple-400 bg-purple-900/20',
};

const triggerColors: Record<string, string> = {
  AGENT: 'text-blue-400',
  WEBHOOK: 'text-green-400',
  SCHEDULE: 'text-yellow-400',
  API: 'text-gray-400',
  CDC: 'text-purple-400',
  HOOK: 'text-orange-400',
  MANUAL: 'text-gray-500',
};

function ExecutionsTab({ initialTreeChatId, onClearTree }: { initialTreeChatId?: string | null; onClearTree?: () => void }) {
  const [triggerFilter, setTriggerFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [expandedExec, setExpandedExec] = useState<string | null>(null);
  const [treeChatId, setTreeChatId] = useState<string | null>(initialTreeChatId || null);

  useEffect(() => {
    if (initialTreeChatId) setTreeChatId(initialTreeChatId);
  }, [initialTreeChatId]);

  const { data: executions, isLoading } = useQuery({
    queryKey: ['executions', triggerFilter, statusFilter],
    queryFn: () => {
      const params: any = { limit: 100 };
      if (triggerFilter) params.trigger_type = triggerFilter;
      if (statusFilter) params.status = statusFilter;
      return apiClient.listExecutions(params);
    },
    retry: false,
    refetchInterval: 5000,
  });

  if (treeChatId) {
    return <ExecutionTree chatId={treeChatId} onBack={() => { setTreeChatId(null); onClearTree?.(); }} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select
          value={triggerFilter}
          onChange={e => setTriggerFilter(e.target.value)}
          className="input text-xs !py-1 !w-32"
        >
          <option value="">All triggers</option>
          <option value="AGENT">Agent</option>
          <option value="WEBHOOK">Webhook</option>
          <option value="SCHEDULE">Schedule</option>
          <option value="API">API</option>
          <option value="CDC">CDC</option>
          <option value="HOOK">Hook</option>
          <option value="MANUAL">Manual</option>
        </select>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="input text-xs !py-1 !w-32"
        >
          <option value="">All statuses</option>
          <option value="COMPLETED">Completed</option>
          <option value="RUNNING">Running</option>
          <option value="FAILED">Failed</option>
          <option value="PENDING">Pending</option>
        </select>
      </div>

      {isLoading ? (
        <div className="text-gray-500 text-sm">Loading executions...</div>
      ) : !executions?.length ? (
        <div className="text-gray-500 text-sm py-8 text-center">No executions found</div>
      ) : (
        <div className="space-y-1">
          {executions.map((exec: any) => {
            const isExpanded = expandedExec === exec.execution_id;
            const sc = statusColors[exec.status] || 'text-gray-400 bg-gray-800';
            const tc = triggerColors[exec.trigger_type] || 'text-gray-400';

            return (
              <div key={exec.execution_id} className="border border-white/[0.06] rounded-lg">
                <button
                  onClick={() => setExpandedExec(isExpanded ? null : exec.execution_id)}
                  className="w-full flex items-center gap-3 p-3 text-left hover:bg-white/[0.02] transition-colors"
                >
                  {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-gray-500 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-500 shrink-0" />}
                  <span className={`px-1.5 py-0.5 text-[10px] font-bold uppercase rounded ${sc}`}>
                    {exec.status}
                  </span>
                  <span className={`text-[10px] font-medium uppercase ${tc}`}>
                    {exec.trigger_type}
                  </span>
                  <span className="text-sm font-mono text-gray-200 truncate">{exec.function_name}</span>
                  {exec.tool_call_id && (
                    <span className="text-[10px] text-gray-600 font-mono" title={`tool_call: ${exec.tool_call_id}`}>
                      tc:{exec.tool_call_id.slice(0, 8)}
                    </span>
                  )}
                  <span className="ml-auto text-xs text-gray-600 shrink-0">
                    {exec.duration_ms != null ? `${exec.duration_ms}ms` : '—'}
                  </span>
                  <span className="text-xs text-gray-600 shrink-0 w-36 text-right">
                    {new Date(exec.started_at).toLocaleString()}
                  </span>
                </button>

                {isExpanded && (
                  <div className="border-t border-white/[0.06] p-4 space-y-3 text-xs">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <span className="text-gray-500 block mb-1">Execution ID</span>
                        <span className="font-mono text-gray-300">{exec.execution_id}</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block mb-1">Trigger</span>
                        <span className="text-gray-300">{exec.trigger_type} — {exec.trigger_id}</span>
                      </div>
                      {exec.chat_id && (
                        <div>
                          <span className="text-gray-500 block mb-1">Chat</span>
                          <button
                            onClick={() => setTreeChatId(exec.chat_id)}
                            className="font-mono text-primary-400 hover:text-primary-300"
                          >
                            {exec.chat_id} →
                          </button>
                        </div>
                      )}
                      {exec.tool_call_id && (
                        <div>
                          <span className="text-gray-500 block mb-1">Tool Call ID</span>
                          <span className="font-mono text-gray-300">{exec.tool_call_id}</span>
                        </div>
                      )}
                      {exec.completed_at && (
                        <div>
                          <span className="text-gray-500 block mb-1">Completed</span>
                          <span className="text-gray-300">{new Date(exec.completed_at).toLocaleString()}</span>
                        </div>
                      )}
                    </div>

                    {exec.error && (
                      <div>
                        <span className="text-gray-500 block mb-1">Error</span>
                        <div className="bg-red-900/10 border border-red-800/30 rounded p-2">
                          <pre className="text-red-400 whitespace-pre-wrap">{exec.error}</pre>
                        </div>
                      </div>
                    )}

                    {exec.input_data && Object.keys(exec.input_data).length > 0 && (
                      <div>
                        <span className="text-gray-500 block mb-1">Input</span>
                        <div className="bg-[#0d0d0d] rounded p-2 max-h-40 overflow-y-auto">
                          <pre className="text-gray-300 font-mono whitespace-pre-wrap">{JSON.stringify(exec.input_data, null, 2)}</pre>
                        </div>
                      </div>
                    )}

                    {exec.output_data != null && (
                      <div>
                        <span className="text-gray-500 block mb-1">Output</span>
                        <div className="bg-[#0d0d0d] rounded p-2 max-h-40 overflow-y-auto">
                          <pre className="text-gray-300 font-mono whitespace-pre-wrap">{typeof exec.output_data === 'string' ? exec.output_data : JSON.stringify(exec.output_data, null, 2)}</pre>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ExecutionTree({ chatId, onBack }: { chatId: string; onBack: () => void }) {
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());

  const { data: chat } = useQuery({
    queryKey: ['chat-tree', chatId],
    queryFn: () => apiClient.getChat(chatId),
    retry: false,
  });

  const { data: executions } = useQuery({
    queryKey: ['executions-tree', chatId],
    queryFn: () => apiClient.listExecutions({ chat_id: chatId, limit: 500 }),
    retry: false,
  });

  const toggleNode = (id: string) => {
    const next = new Set(expandedNodes);
    next.has(id) ? next.delete(id) : next.add(id);
    setExpandedNodes(next);
  };

  // Build execution lookups
  const execByToolCallId: Record<string, any> = {};
  const execsByFunctionName: Record<string, any[]> = {};
  const hookExecutions: any[] = [];
  (executions || []).forEach((ex: any) => {
    if (ex.tool_call_id) execByToolCallId[ex.tool_call_id] = ex;
    // Also index by function name for fallback matching
    const fname = ex.function_name || '';
    if (!execsByFunctionName[fname]) execsByFunctionName[fname] = [];
    execsByFunctionName[fname].push(ex);
    // Collect hook executions separately
    if (ex.trigger_type === 'HOOK') hookExecutions.push(ex);
  });

  // Fallback: find execution by function name closest in time to the tool call
  const findExecFallback = (funcName: string, toolCallId: string, msgTime: string): any => {
    // First try tool_call_id
    if (execByToolCallId[toolCallId]) return execByToolCallId[toolCallId];
    // Fallback: match by function name, pick the one closest to the message time
    const cleanName = funcName.replace('__', '/');
    const candidates = execsByFunctionName[cleanName] || [];
    if (candidates.length === 0) return null;
    const msgTs = new Date(msgTime).getTime();
    let best = candidates[0];
    let bestDelta = Math.abs(new Date(best.started_at).getTime() - msgTs);
    for (const c of candidates) {
      const delta = Math.abs(new Date(c.started_at).getTime() - msgTs);
      if (delta < bestDelta) { best = c; bestDelta = delta; }
    }
    // Only match if within 60 seconds
    return bestDelta < 60000 ? best : null;
  };

  const messages = chat?.messages || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-xs text-primary-400 hover:text-primary-300">
          ← Back to executions
        </button>
        <h3 className="text-sm font-semibold text-gray-300">
          Execution Tree — {chat?.agent_namespace}/{chat?.agent_name}
        </h3>
        <span className="text-xs text-gray-600 font-mono">{chatId.slice(0, 8)}</span>
      </div>

      {messages.length === 0 ? (
        <div className="text-gray-500 text-sm py-8 text-center">No messages in this chat</div>
      ) : (
        <div className="space-y-0.5">
          {/* Interleave hooks into the message timeline */}
          {(() => {
            // Build unified timeline: messages + hook executions, sorted by time
            const timeline: { type: 'message' | 'hook'; data: any; time: number }[] = [];
            messages.forEach((msg: any) => {
              if (msg.role !== 'tool') {
                timeline.push({ type: 'message', data: msg, time: new Date(msg.created_at).getTime() });
              }
            });
            hookExecutions.forEach((ex: any) => {
              timeline.push({ type: 'hook', data: ex, time: new Date(ex.started_at).getTime() });
            });
            timeline.sort((a, b) => a.time - b.time);
            return timeline;
          })().map((item) => {
            if (item.type === 'hook') {
              const exec = item.data;
              const hookExpanded = expandedNodes.has(`hook-${exec.execution_id}`);
              const sc = statusColors[exec.status] || 'text-gray-400 bg-gray-800';
              return (
                <div key={`hook-${exec.execution_id}`}>
                  <button
                    onClick={() => toggleNode(`hook-${exec.execution_id}`)}
                    className="w-full flex items-center gap-2 p-2 rounded bg-orange-900/5 border-l-2 border-orange-600/30 text-left hover:bg-orange-900/10"
                  >
                    {hookExpanded ? <ChevronDown className="w-3.5 h-3.5 text-gray-500" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-500" />}
                    <span className="text-[10px] font-bold uppercase text-orange-400 w-14">HOOK</span>
                    <span className="text-xs font-mono text-gray-200">{exec.function_name}</span>
                    <span className={`px-1 py-0.5 text-[9px] font-bold uppercase rounded ${sc}`}>{exec.status}</span>
                    {exec.duration_ms != null && <span className="text-[10px] text-gray-600">{exec.duration_ms}ms</span>}
                    <span className="ml-auto text-[10px] text-gray-600">{new Date(exec.started_at).toLocaleTimeString()}</span>
                  </button>
                  {hookExpanded && (
                    <div className="ml-8 p-2 space-y-2 text-xs">
                      {exec.input_data && (
                        <div>
                          <span className="text-gray-500 block mb-1">Input</span>
                          <div className="bg-[#0d0d0d] rounded p-2 max-h-32 overflow-y-auto">
                            <pre className="text-gray-300 font-mono whitespace-pre-wrap text-[11px]">{JSON.stringify(exec.input_data, null, 2)}</pre>
                          </div>
                        </div>
                      )}
                      {exec.output_data != null && (
                        <div>
                          <span className="text-gray-500 block mb-1">Output</span>
                          <div className="bg-[#0d0d0d] rounded p-2 max-h-32 overflow-y-auto">
                            <pre className="text-gray-300 font-mono whitespace-pre-wrap text-[11px]">{typeof exec.output_data === 'string' ? exec.output_data : JSON.stringify(exec.output_data, null, 2)}</pre>
                          </div>
                        </div>
                      )}
                      {exec.error && (
                        <div>
                          <span className="text-gray-500 block mb-1">Error</span>
                          <pre className="text-red-400 text-[11px]">{exec.error}</pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            }

            const msg = item.data;
            const isUser = msg.role === 'user';
            const isAssistant = msg.role === 'assistant';
            const toolCalls = msg.tool_calls || [];
            const hasToolCalls = toolCalls.length > 0;
            const nodeId = msg.id;
            const isExpanded = expandedNodes.has(nodeId);

            return (
              <div key={msg.id}>
                {/* Message row */}
                <div
                  className={`flex items-start gap-2 p-2 rounded ${
                    isUser ? 'bg-blue-900/10 border-l-2 border-blue-600/40' :
                    isAssistant && hasToolCalls ? 'bg-yellow-900/5 border-l-2 border-yellow-600/30' :
                    isAssistant ? 'bg-green-900/5 border-l-2 border-green-600/30' :
                    'border-l-2 border-white/[0.06]'
                  }`}
                >
                  {hasToolCalls ? (
                    <button onClick={() => toggleNode(nodeId)} className="mt-0.5 shrink-0">
                      {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-gray-500" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-500" />}
                    </button>
                  ) : (
                    <span className="w-3.5 shrink-0" />
                  )}

                  <span className={`text-[10px] font-bold uppercase mt-0.5 shrink-0 w-14 ${
                    isUser ? 'text-blue-400' : isAssistant ? 'text-green-400' : 'text-gray-500'
                  }`}>
                    {msg.role}
                  </span>

                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-gray-300 whitespace-pre-wrap">
                      {(typeof msg.content === 'string' ? msg.content : null) || (hasToolCalls ? `[${toolCalls.length} tool call${toolCalls.length > 1 ? 's' : ''}]` : '[empty]')}
                    </p>
                  </div>

                  <span className="text-[10px] text-gray-600 shrink-0">
                    {new Date(msg.created_at).toLocaleTimeString()}
                  </span>
                </div>

                {/* Tool calls tree (nested) */}
                {isExpanded && hasToolCalls && (
                  <div className="ml-8 border-l border-white/[0.06] space-y-0.5 my-0.5">
                    {toolCalls.map((tc: any) => {
                      const tcId = tc.id;
                      const funcName = tc.function?.name || 'unknown';
                      const exec = findExecFallback(funcName, tcId, msg.created_at);
                      const toolResult = messages.find((m: any) => m.role === 'tool' && m.tool_call_id === tcId);
                      const tcExpanded = expandedNodes.has(tcId);
                      const sc = exec ? (statusColors[exec.status] || 'text-gray-400 bg-gray-800') : 'text-gray-500 bg-gray-800/50';

                      return (
                        <div key={tcId} className="ml-3">
                          <button
                            onClick={() => toggleNode(tcId)}
                            className="w-full flex items-center gap-2 p-1.5 text-left hover:bg-white/[0.02] rounded"
                          >
                            {tcExpanded ? <ChevronDown className="w-3 h-3 text-gray-500" /> : <ChevronRight className="w-3 h-3 text-gray-500" />}
                            <span className="text-[10px] text-yellow-500">TOOL</span>
                            <span className="text-xs font-mono text-gray-200">{funcName}</span>
                            {exec && (
                              <>
                                <span className={`px-1 py-0.5 text-[9px] font-bold uppercase rounded ${sc}`}>
                                  {exec.status}
                                </span>
                                {exec.duration_ms != null && (
                                  <span className="text-[10px] text-gray-600">{exec.duration_ms}ms</span>
                                )}
                              </>
                            )}
                          </button>

                          {tcExpanded && (
                            <div className="ml-6 space-y-2 p-2 text-xs">
                              {/* Arguments */}
                              <div>
                                <span className="text-gray-500 block mb-1">Arguments</span>
                                <div className="bg-[#0d0d0d] rounded p-2 max-h-32 overflow-y-auto">
                                  <pre className="text-gray-300 font-mono whitespace-pre-wrap text-[11px]">
                                    {(() => { try { return JSON.stringify(JSON.parse(tc.function?.arguments || '{}'), null, 2); } catch { return tc.function?.arguments || '{}'; } })()}
                                  </pre>
                                </div>
                              </div>

                              {/* Tool result */}
                              {toolResult && (
                                <div>
                                  <span className="text-gray-500 block mb-1">Result</span>
                                  <div className="bg-[#0d0d0d] rounded p-2 max-h-32 overflow-y-auto">
                                    <pre className="text-gray-300 font-mono whitespace-pre-wrap text-[11px]">
                                      {(() => { const c = typeof toolResult.content === 'string' ? toolResult.content : JSON.stringify(toolResult.content); try { return JSON.stringify(JSON.parse(c || ''), null, 2); } catch { return c || ''; } })()}
                                    </pre>
                                  </div>
                                </div>
                              )}

                              {/* Execution details */}
                              {exec && (
                                <div className="grid grid-cols-2 gap-2 text-[11px]">
                                  <div>
                                    <span className="text-gray-500">Execution ID</span>
                                    <p className="font-mono text-gray-400">{exec.execution_id.slice(0, 12)}...</p>
                                  </div>
                                  <div>
                                    <span className="text-gray-500">Trigger</span>
                                    <p className="text-gray-400">{exec.trigger_type}</p>
                                  </div>
                                  {exec.error && (
                                    <div className="col-span-2">
                                      <span className="text-gray-500">Error</span>
                                      <p className="text-red-400">{exec.error}</p>
                                    </div>
                                  )}
                                </div>
                              )}
                              {!exec && (
                                <p className="text-gray-600 italic">No execution record (connector or inline tool)</p>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
