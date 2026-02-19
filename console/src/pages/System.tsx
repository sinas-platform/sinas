import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useState } from 'react';
import { Server, Plus, Minus, RefreshCw, AlertTriangle, RotateCcw, Box } from 'lucide-react';

function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function formatUptime(startedAt: number): string {
  const seconds = Math.floor(Date.now() / 1000 - startedAt);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

export function System() {
  const queryClient = useQueryClient();
  const [jobStatusFilter, setJobStatusFilter] = useState<string>('');

  const { data: stats } = useQuery({
    queryKey: ['queue-stats'],
    queryFn: () => apiClient.getQueueStats(),
    refetchInterval: 5000,
  });

  const { data: poolStats } = useQuery({
    queryKey: ['container-stats'],
    queryFn: () => apiClient.getContainerStats(),
    refetchInterval: 5000,
  });

  const { data: sharedPoolContainers } = useQuery({
    queryKey: ['shared-pool'],
    queryFn: () => apiClient.listWorkers(),
    refetchInterval: 5000,
  });

  const { data: sharedPoolCount } = useQuery({
    queryKey: ['shared-pool-count'],
    queryFn: () => apiClient.getWorkerCount(),
    refetchInterval: 5000,
  });

  const { data: queueWorkers } = useQuery({
    queryKey: ['queue-workers'],
    queryFn: () => apiClient.getQueueWorkers(),
    refetchInterval: 5000,
  });

  const { data: jobs } = useQuery({
    queryKey: ['queue-jobs', jobStatusFilter],
    queryFn: () => apiClient.getQueueJobs(jobStatusFilter || undefined),
    refetchInterval: 5000,
  });

  const { data: dlqEntries } = useQuery({
    queryKey: ['queue-dlq'],
    queryFn: () => apiClient.getQueueDLQ(),
    refetchInterval: 5000,
  });

  const poolScaleMutation = useMutation({
    mutationFn: (target: number) => apiClient.scaleContainerPool(target),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['container-stats'] });
      queryClient.invalidateQueries({ queryKey: ['queue-stats'] });
    },
  });

  const sharedPoolScaleMutation = useMutation({
    mutationFn: (targetCount: number) => apiClient.scaleWorkers(targetCount),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['shared-pool'] });
      queryClient.invalidateQueries({ queryKey: ['shared-pool-count'] });
      queryClient.invalidateQueries({ queryKey: ['queue-stats'] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: (jobId: string) => apiClient.retryDLQJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue-dlq'] });
      queryClient.invalidateQueries({ queryKey: ['queue-stats'] });
      queryClient.invalidateQueries({ queryKey: ['queue-jobs'] });
    },
  });

  const handlePoolScale = (delta: number) => {
    const current = poolStats?.total ?? 0;
    const target = Math.max(0, current + delta);
    poolScaleMutation.mutate(target);
  };

  const handleSharedPoolScale = (delta: number) => {
    const current = sharedPoolCount?.count || 0;
    const target = Math.max(0, Math.min(10, current + delta));
    sharedPoolScaleMutation.mutate(target);
  };

  const getJobStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'bg-blue-100 text-blue-800';
      case 'completed': return 'bg-green-100 text-green-800';
      case 'queued': return 'bg-gray-100 text-gray-800';
      case 'failed': return 'bg-red-100 text-red-800';
      default: return 'bg-gray-100 text-gray-800';
    }
  };

  const getContainerStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'bg-green-100 text-green-800';
      case 'missing': case 'exited': return 'bg-red-100 text-red-800';
      default: return 'bg-gray-100 text-gray-800';
    }
  };

  const dlqSize = stats?.dlq?.size || 0;
  const sharedCount = sharedPoolCount?.count || 0;
  const poolIdle = poolStats?.idle ?? stats?.pool?.idle ?? 0;
  const poolInUse = poolStats?.in_use ?? stats?.pool?.in_use ?? 0;
  const poolTotal = poolStats?.total ?? (poolIdle + poolInUse);
  const poolMax = poolStats?.max_size;

  const allSandboxContainers = [
    ...(poolStats?.in_use_containers || []).map((c: any) => ({ ...c, state: 'in_use' as const })),
    ...(poolStats?.idle_containers || []).map((c: any) => ({ ...c, state: 'idle' as const })),
  ];

  const poolUtilPct = poolMax ? Math.round((poolTotal / poolMax) * 100) : 0;
  const poolBusyPct = poolTotal > 0 ? Math.round((poolInUse / poolTotal) * 100) : 0;

  const functionWorkers = (queueWorkers || []).filter((w: any) => w.queue === 'functions');
  const agentWorkers = (queueWorkers || []).filter((w: any) => w.queue === 'agents');
  const totalWorkerSlots = (queueWorkers || []).reduce((sum: number, w: any) => sum + (w.max_jobs || 0), 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">System</h1>
        <p className="text-gray-600 mt-1">Workers, queues, and execution infrastructure</p>
      </div>

      {/* Stats Overview */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Workers */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Workers</h3>
          <div className="mt-3 flex items-baseline gap-3">
            <div>
              <span className="text-2xl font-bold text-gray-900">{functionWorkers.length}</span>
              <span className="text-sm text-gray-500 ml-1">fn</span>
            </div>
            <div>
              <span className="text-2xl font-bold text-gray-900">{agentWorkers.length}</span>
              <span className="text-sm text-gray-500 ml-1">agent</span>
            </div>
            <span className="text-xs text-gray-400">{totalWorkerSlots} slots</span>
          </div>
        </div>

        {/* Queue Depth */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Queue Depth</h3>
          <div className="mt-3 flex items-baseline gap-3">
            <div>
              <span className="text-2xl font-bold text-gray-900">{stats?.queues?.functions?.pending ?? 0}</span>
              <span className="text-sm text-gray-500 ml-1">functions</span>
            </div>
            <div>
              <span className="text-2xl font-bold text-gray-900">{stats?.queues?.agents?.pending ?? 0}</span>
              <span className="text-sm text-gray-500 ml-1">agents</span>
            </div>
          </div>
        </div>

        {/* Jobs */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Jobs (24h)</h3>
          <div className="mt-3 grid grid-cols-4 gap-1 text-center">
            {[
              { label: 'Q', value: stats?.jobs?.queued ?? 0, color: 'text-gray-700' },
              { label: 'Run', value: stats?.jobs?.running ?? 0, color: 'text-blue-600' },
              { label: 'Done', value: stats?.jobs?.completed ?? 0, color: 'text-green-600' },
              { label: 'Fail', value: stats?.jobs?.failed ?? 0, color: stats?.jobs?.failed ? 'text-red-600' : 'text-gray-400' },
            ].map(s => (
              <div key={s.label}>
                <div className={`text-lg font-bold ${s.color}`}>{s.value}</div>
                <div className="text-xs text-gray-400">{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Health */}
        <div className={`card ${dlqSize > 0 ? 'border-red-300 bg-red-50' : ''}`}>
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Health</h3>
          <div className="mt-3 space-y-2">
            <div className="flex justify-between items-center">
              <span className="text-sm text-gray-600">Dead letter queue</span>
              <span className={`text-sm font-bold ${dlqSize > 0 ? 'text-red-600' : 'text-green-600'}`}>
                {dlqSize > 0 ? dlqSize : 'clean'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-gray-600">Shared pool</span>
              <span className="text-sm font-semibold">{sharedCount} running</span>
            </div>
          </div>
        </div>
      </div>

      {/* Workers — compact row */}
      {queueWorkers && queueWorkers.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-gray-500 font-medium">Workers</span>
          {queueWorkers.map((w: any) => (
            <span
              key={w.worker_id}
              className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md border ${
                w.queue === 'functions'
                  ? 'bg-purple-50 border-purple-200 text-purple-700'
                  : 'bg-indigo-50 border-indigo-200 text-indigo-700'
              }`}
            >
              <span className="font-mono">{w.worker_id.slice(0, 8)}</span>
              <span className="text-gray-400">|</span>
              <span>{w.queue === 'functions' ? 'fn' : 'agent'} &times;{w.max_jobs}</span>
              <span className="text-gray-400">|</span>
              <span className="text-gray-500">{w.started_at ? formatUptime(w.started_at) : '-'}</span>
            </span>
          ))}
        </div>
      )}

      {/* Sandbox Pool & Shared Pool — side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Sandbox Pool */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Sandbox Pool</h2>
              <p className="text-sm text-gray-600 mt-1">Isolated containers for user functions</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handlePoolScale(-1)}
                disabled={poolTotal === 0 || poolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale down"
              >
                <Minus className="w-4 h-4" />
              </button>
              <span className="text-sm font-semibold text-gray-700 w-8 text-center">{poolTotal}</span>
              <button
                onClick={() => handlePoolScale(1)}
                disabled={(poolMax != null && poolTotal >= poolMax) || poolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale up"
              >
                <Plus className="w-4 h-4" />
              </button>
              <button
                onClick={() => queryClient.invalidateQueries({ queryKey: ['container-stats'] })}
                className="btn btn-secondary flex items-center"
                title="Refresh"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="mb-3 h-2 bg-gray-100 rounded-full overflow-hidden">
            <div className="h-full flex">
              <div className="bg-blue-500 transition-all" style={{ width: `${poolBusyPct}%` }} />
              <div className="bg-green-400 transition-all" style={{ width: `${poolUtilPct - poolBusyPct}%` }} />
            </div>
          </div>
          <div className="flex gap-4 text-xs text-gray-500 mb-4">
            <span><span className="inline-block w-2 h-2 rounded-full bg-blue-500 mr-1" />busy ({poolInUse})</span>
            <span><span className="inline-block w-2 h-2 rounded-full bg-green-400 mr-1" />idle ({poolIdle})</span>
            {poolMax != null && <span className="text-gray-400">max {poolMax}</span>}
          </div>

          {poolScaleMutation.isError && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              Failed to scale sandbox pool.
            </div>
          )}

          {allSandboxContainers.length === 0 ? (
            <div className="text-center py-4">
              <Box className="w-8 h-8 text-gray-400 mx-auto mb-2" />
              <p className="text-gray-500 text-sm">No containers</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[45%]">Container</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[15%]">Status</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[20%]">Executions</th>
                    <th className="text-left py-2 font-medium text-gray-500 w-[20%]">Age</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {allSandboxContainers.map((c: any) => (
                    <tr key={c.name} className="hover:bg-gray-50">
                      <td className="py-2 pr-4 font-mono text-xs text-gray-700">{c.name}</td>
                      <td className="py-2 pr-4">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                          c.state === 'in_use' ? 'bg-blue-100 text-blue-800' : 'bg-green-100 text-green-800'
                        }`}>
                          {c.state === 'in_use' ? 'busy' : 'idle'}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-gray-600">
                        {c.executions}
                        <span className="text-gray-400"> / {poolStats?.max_executions ?? '?'}</span>
                      </td>
                      <td className="py-2 text-gray-600">{formatAge(c.age_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Shared Pool */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Shared Pool</h2>
              <p className="text-sm text-gray-600 mt-1">Persistent containers for <code className="px-1 py-0.5 bg-gray-100 rounded text-xs">shared_pool</code> functions</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleSharedPoolScale(-1)}
                disabled={sharedCount === 0 || sharedPoolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale down"
              >
                <Minus className="w-4 h-4" />
              </button>
              <span className="text-sm font-semibold text-gray-700 w-8 text-center">{sharedCount}</span>
              <button
                onClick={() => handleSharedPoolScale(1)}
                disabled={sharedCount >= 10 || sharedPoolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale up"
              >
                <Plus className="w-4 h-4" />
              </button>
              <button
                onClick={() => queryClient.invalidateQueries({ queryKey: ['shared-pool'] })}
                className="btn btn-secondary flex items-center"
                title="Refresh"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
          </div>

          {sharedPoolScaleMutation.isError && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              Failed to scale shared pool.
            </div>
          )}

          {!sharedPoolContainers || sharedPoolContainers.length === 0 ? (
            <div className="text-center py-4">
              <Server className="w-8 h-8 text-gray-400 mx-auto mb-2" />
              <p className="text-gray-500 text-sm">No containers</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[45%]">Container</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[15%]">Status</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[20%]">Executions</th>
                    <th className="text-left py-2 font-medium text-gray-500 w-[20%]">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {sharedPoolContainers.map((w: any) => (
                    <tr key={w.id} className="hover:bg-gray-50">
                      <td className="py-2 pr-4 font-mono text-xs text-gray-700">{w.container_name}</td>
                      <td className="py-2 pr-4">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${getContainerStatusColor(w.status)}`}>
                          {w.status}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-gray-600">{w.executions}</td>
                      <td className="py-2 text-gray-600 text-xs">{new Date(w.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Jobs */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Jobs</h2>
          <select
            value={jobStatusFilter}
            onChange={(e) => setJobStatusFilter(e.target.value)}
            className="input !w-40"
          >
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </div>

        {!jobs || jobs.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-gray-500 text-sm">No jobs found</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Status</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Description</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Time</th>
                  <th className="text-left py-2 font-medium text-gray-500">Error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {jobs.map((job: any) => (
                  <tr key={job.job_id} className="hover:bg-gray-50">
                    <td className="py-2 pr-4">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${getJobStatusColor(job.status)}`}>
                        {job.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-sm text-gray-700">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium mr-2 ${
                        job.queue === 'functions' ? 'bg-purple-100 text-purple-700' : 'bg-indigo-100 text-indigo-700'
                      }`}>
                        {job.queue === 'functions' ? 'fn' : 'agent'}
                      </span>
                      {job.queue === 'functions'
                        ? (job.function || '-')
                        : (job.agent || 'unknown')}
                      {job.queue === 'agents' && job.type === 'resume' && (
                        <span className="text-xs text-gray-400 ml-1">(resume)</span>
                      )}
                      <span className="text-xs text-gray-400 ml-1">via {(job.trigger_type || (job.queue === 'agents' ? 'agent' : 'api')).toLowerCase()}</span>
                      {job.chat_id && (
                        <span className="text-xs text-gray-400 ml-1">chat:{job.chat_id.slice(0, 8)}</span>
                      )}
                    </td>
                    <td className="py-2 pr-4 text-xs text-gray-400 whitespace-nowrap">
                      {job.enqueued_at
                        ? new Date(job.enqueued_at * 1000).toLocaleTimeString()
                        : '-'}
                    </td>
                    <td className="py-2 text-xs text-red-500 max-w-xs truncate">
                      {job.error || ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Dead Letter Queue */}
      {dlqEntries && dlqEntries.length > 0 && (
        <div className="card border-red-200">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle className="w-5 h-5 text-red-500" />
            <h2 className="text-lg font-semibold text-gray-900">Dead Letter Queue</h2>
            <span className="text-sm text-red-600 font-medium">({dlqEntries.length})</span>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Function</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Error</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Attempts</th>
                  <th className="text-right py-2 font-medium text-gray-500">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {dlqEntries.map((entry: any, idx: number) => (
                  <tr key={entry.job_id || idx} className="hover:bg-gray-50">
                    <td className="py-2 pr-4 text-gray-700">{entry.function || '-'}</td>
                    <td className="py-2 pr-4 text-red-600 text-xs max-w-md truncate">
                      {entry.error || '-'}
                    </td>
                    <td className="py-2 pr-4 text-gray-600">{entry.attempts ?? '-'}</td>
                    <td className="py-2 text-right">
                      <button
                        onClick={() => retryMutation.mutate(entry.job_id)}
                        disabled={retryMutation.isPending}
                        className="btn btn-secondary text-xs py-1 px-2 inline-flex items-center gap-1"
                      >
                        <RotateCcw className="w-3 h-3" />
                        Retry
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {retryMutation.isSuccess && (
            <div className="mt-3 p-2 bg-green-50 border border-green-200 rounded text-sm text-green-700">
              Job re-enqueued.
            </div>
          )}
          {retryMutation.isError && (
            <div className="mt-3 p-2 bg-red-50 border border-red-200 rounded text-sm text-red-700">
              Retry failed.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
