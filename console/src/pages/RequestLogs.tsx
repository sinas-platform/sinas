import { useState } from 'react';
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
} from 'lucide-react';
import { Messages } from './Messages';

type Tab = 'requests' | 'messages';

export function RequestLogs() {
  const [activeTab, setActiveTab] = useState<Tab>('requests');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Logs</h1>
        <p className="text-gray-600 mt-1">Monitor API requests and chat messages</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex space-x-8">
          <button
            onClick={() => setActiveTab('requests')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'requests'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
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
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <MessageSquare className="w-5 h-5 inline mr-2" />
            Message Logs
          </button>
        </nav>
      </div>

      {/* Tab Content */}
      <div>
        {activeTab === 'requests' && <RequestLogsTab />}
        {activeTab === 'messages' && <Messages />}
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
    if (statusCode >= 200 && statusCode < 300) return 'text-green-600 bg-green-50';
    if (statusCode >= 300 && statusCode < 400) return 'text-blue-600 bg-blue-50';
    if (statusCode >= 400 && statusCode < 500) return 'text-orange-600 bg-orange-50';
    return 'text-red-600 bg-red-50';
  };

  const getMethodColor = (method: string) => {
    const colors: Record<string, string> = {
      GET: 'text-blue-600 bg-blue-50',
      POST: 'text-green-600 bg-green-50',
      PUT: 'text-orange-600 bg-orange-50',
      PATCH: 'text-purple-600 bg-purple-50',
      DELETE: 'text-red-600 bg-red-50',
    };
    return colors[method] || 'text-gray-600 bg-gray-50';
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
                <p className="text-sm font-medium text-gray-600">Total Requests</p>
                <p className="text-2xl font-bold text-gray-900 mt-1">{stats.total_requests}</p>
              </div>
              <div className="p-3 bg-primary-50 rounded-lg">
                <FileText className="w-5 h-5 text-primary-600" />
              </div>
            </div>
          </div>
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600">Unique Users</p>
                <p className="text-2xl font-bold text-gray-900 mt-1">{stats.unique_users}</p>
              </div>
              <div className="p-3 bg-blue-50 rounded-lg">
                <Users className="w-5 h-5 text-blue-600" />
              </div>
            </div>
          </div>
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600">Avg Response Time</p>
                <p className="text-2xl font-bold text-gray-900 mt-1">{Math.round(stats.avg_response_time_ms)}ms</p>
              </div>
              <div className="p-3 bg-green-50 rounded-lg">
                <Clock className="w-5 h-5 text-green-600" />
              </div>
            </div>
          </div>
          <div className="card">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600">Error Rate</p>
                <p className="text-2xl font-bold text-gray-900 mt-1">{(stats.error_rate * 100).toFixed(1)}%</p>
              </div>
              <div className="p-3 bg-red-50 rounded-lg">
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
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Top Paths</h3>
            <div className="space-y-2">
              {stats.top_paths.map((item: any, idx: number) => (
                <div key={idx} className="flex items-center justify-between p-2 bg-gray-50 rounded">
                  <span className="text-sm font-mono text-gray-900">{item.path}</span>
                  <span className="text-sm font-semibold text-gray-600">{item.count}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Top Permissions</h3>
            <div className="space-y-2">
              {stats.top_permissions.map((item: any, idx: number) => (
                <div key={idx} className="flex items-center justify-between p-2 bg-gray-50 rounded">
                  <span className="text-sm font-mono text-gray-900">{item.permission}</span>
                  <span className="text-sm font-semibold text-gray-600">{item.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      {showFilters && (
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Filters</h3>
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
          <h2 className="text-lg font-semibold text-gray-900">Request Logs</h2>
          <span className="text-sm text-gray-500">{logs?.length || 0} results</span>
        </div>

        {isLoading ? (
          <div className="text-center py-8 text-gray-500">Loading logs...</div>
        ) : !logs || logs.length === 0 ? (
          <div className="text-center py-8 text-gray-500">No logs found</div>
        ) : (
          <div className="space-y-2">
            {logs.map((log: any) => (
              <div key={log.request_id} className="border border-gray-200 rounded-lg overflow-hidden">
                <button
                  onClick={() => setExpandedLog(expandedLog === log.request_id ? null : log.request_id)}
                  className="w-full px-4 py-3 flex items-center gap-3 hover:bg-gray-50 transition-colors text-left"
                >
                  {expandedLog === log.request_id ? (
                    <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
                  )}

                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <span className={`px-2 py-0.5 text-xs font-medium rounded ${getMethodColor(log.method)}`}>
                      {log.method}
                    </span>
                    <span className={`px-2 py-0.5 text-xs font-medium rounded ${getStatusColor(log.status_code)}`}>
                      {log.status_code}
                    </span>
                    <span className="text-sm font-mono text-gray-900 truncate">{log.path}</span>
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
                  <div className="px-4 py-3 bg-gray-50 border-t border-gray-200 space-y-3">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">User</p>
                        <p className="text-sm text-gray-900">{log.user_email}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Permission</p>
                        <p className="text-sm text-gray-900 font-mono">{log.permission_used}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">IP Address</p>
                        <p className="text-sm text-gray-900">{log.ip_address}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Response Size</p>
                        <p className="text-sm text-gray-900">{formatBytes(log.response_size_bytes)}</p>
                      </div>
                      {log.resource_type && (
                        <>
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">Resource Type</p>
                            <p className="text-sm text-gray-900">{log.resource_type}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">Resource ID</p>
                            <p className="text-sm text-gray-900 font-mono">{log.resource_id}</p>
                          </div>
                        </>
                      )}
                    </div>

                    {log.query_params && (
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Query Params</p>
                        <pre className="text-xs text-gray-900 bg-white p-2 rounded border border-gray-200 overflow-x-auto">
                          {log.query_params}
                        </pre>
                      </div>
                    )}

                    {log.request_body && log.request_body !== '{}' && (
                      <div>
                        <p className="text-xs font-medium text-gray-500 mb-1">Request Body</p>
                        <pre className="text-xs text-gray-900 bg-white p-2 rounded border border-gray-200 overflow-x-auto">
                          {JSON.stringify(JSON.parse(log.request_body), null, 2)}
                        </pre>
                      </div>
                    )}

                    {log.error_message && (
                      <div>
                        <p className="text-xs font-medium text-red-600 mb-1">Error</p>
                        <pre className="text-xs text-red-900 bg-red-50 p-2 rounded border border-red-200 overflow-x-auto">
                          {log.error_type}: {log.error_message}
                        </pre>
                      </div>
                    )}

                    <div>
                      <p className="text-xs font-medium text-gray-500 mb-1">User Agent</p>
                      <p className="text-xs text-gray-700 break-all">{log.user_agent}</p>
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
