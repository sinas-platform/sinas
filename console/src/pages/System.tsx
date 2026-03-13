import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useState } from 'react';
import { Server, Plus, Minus, RefreshCw, AlertTriangle, RotateCcw, Box, X, AlertCircle, Info, RotateCw, Trash2, Loader2 } from 'lucide-react';

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

function formatUptimeSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

function ResourceBar({ value, label, unit }: { value: number | null; label: string; unit: string }) {
  if (value === null || value === undefined) return null;
  const color = value > 90 ? 'bg-red-500' : value > 75 ? 'bg-yellow-500' : 'bg-green-500';
  const textColor = value > 90 ? 'text-red-400' : value > 75 ? 'text-yellow-400' : 'text-green-400';
  return (
    <div>
      <div className="flex justify-between items-center mb-1">
        <span className="text-sm text-gray-400">{label}</span>
        <span className={`text-sm font-semibold ${textColor}`}>{value}{unit}</span>
      </div>
      <div className="h-2 bg-[#161616] rounded-full overflow-hidden">
        <div className={`h-full ${color} transition-all`} style={{ width: `${Math.min(value, 100)}%` }} />
      </div>
    </div>
  );
}

export function System() {
  const queryClient = useQueryClient();
  const [jobStatusFilter, setJobStatusFilter] = useState<string>('');
  const [restartingContainer, setRestartingContainer] = useState<string | null>(null);

  const { data: systemHealth } = useQuery({
    queryKey: ['system-health'],
    queryFn: () => apiClient.getSystemHealth(),
    refetchInterval: 10000,
  });

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

  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => apiClient.cancelJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue-jobs'] });
      queryClient.invalidateQueries({ queryKey: ['queue-stats'] });
    },
  });

  const restartContainerMutation = useMutation({
    mutationFn: (containerName: string) => apiClient.restartContainer(containerName),
    onSuccess: () => {
      setRestartingContainer(null);
      queryClient.invalidateQueries({ queryKey: ['system-health'] });
    },
    onError: () => {
      setRestartingContainer(null);
    },
  });

  const flushDLQMutation = useMutation({
    mutationFn: () => apiClient.flushDLQ(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue-dlq'] });
      queryClient.invalidateQueries({ queryKey: ['queue-stats'] });
      queryClient.invalidateQueries({ queryKey: ['system-health'] });
    },
  });

  const flushStuckJobsMutation = useMutation({
    mutationFn: () => apiClient.flushStuckJobs(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue-jobs'] });
      queryClient.invalidateQueries({ queryKey: ['queue-stats'] });
      queryClient.invalidateQueries({ queryKey: ['system-health'] });
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
      case 'running': return 'bg-blue-900/30 text-blue-300';
      case 'completed': return 'bg-green-900/30 text-green-300';
      case 'queued': return 'bg-[#161616] text-gray-200';
      case 'failed': return 'bg-red-900/30 text-red-300';
      case 'cancelled': return 'bg-yellow-900/30 text-yellow-300';
      default: return 'bg-[#161616] text-gray-200';
    }
  };

  const getContainerStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'bg-green-900/30 text-green-300';
      case 'missing': case 'exited': return 'bg-red-900/30 text-red-300';
      default: return 'bg-[#161616] text-gray-200';
    }
  };

  const getServiceStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'text-green-400';
      case 'exited': case 'dead': return 'text-red-400';
      case 'restarting': return 'text-yellow-400';
      default: return 'text-gray-400';
    }
  };

  const getHealthColor = (health: string) => {
    switch (health) {
      case 'healthy': return 'text-green-400';
      case 'unhealthy': return 'text-red-400';
      case 'starting': return 'text-yellow-400';
      default: return 'text-gray-500';
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

  const warnings = systemHealth?.warnings || [];
  const criticalWarnings = warnings.filter((w: any) => w.level === 'critical');
  const warningWarnings = warnings.filter((w: any) => w.level === 'warning');
  const infoWarnings = warnings.filter((w: any) => w.level === 'info');

  const host = systemHealth?.host || {};
  const services = systemHealth?.services || [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-100">System</h1>
        <p className="text-gray-400 mt-1">Workers, queues, and execution infrastructure</p>
      </div>

      {/* Health Warnings */}
      {criticalWarnings.length > 0 && (
        <div className="p-4 bg-red-900/20 border border-red-800/40 rounded-lg space-y-1">
          {criticalWarnings.map((w: any, i: number) => (
            <div key={i} className="flex items-center gap-2 text-red-300">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span className="text-sm font-medium">{w.message}</span>
            </div>
          ))}
        </div>
      )}

      {warningWarnings.length > 0 && (
        <div className="p-4 bg-yellow-900/15 border border-yellow-800/30 rounded-lg space-y-1">
          {warningWarnings.map((w: any, i: number) => (
            <div key={i} className="flex items-center gap-2 text-yellow-300">
              <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              <span className="text-sm">{w.message}</span>
            </div>
          ))}
        </div>
      )}

      {infoWarnings.length > 0 && (
        <div className="p-3 bg-blue-900/10 border border-blue-800/20 rounded-lg space-y-1">
          {infoWarnings.map((w: any, i: number) => (
            <div key={i} className="flex items-center gap-2 text-blue-300">
              <Info className="w-4 h-4 flex-shrink-0" />
              <span className="text-sm">{w.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* Stats Overview + Host Resources */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Workers */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Workers</h3>
          <div className="mt-3 flex items-baseline gap-3">
            <div>
              <span className="text-2xl font-bold text-gray-100">{functionWorkers.length}</span>
              <span className="text-sm text-gray-500 ml-1">fn</span>
            </div>
            <div>
              <span className="text-2xl font-bold text-gray-100">{agentWorkers.length}</span>
              <span className="text-sm text-gray-500 ml-1">agent</span>
            </div>
            <span className="text-xs text-gray-500">{totalWorkerSlots} slots</span>
          </div>
        </div>

        {/* Queue Depth */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Queue Depth</h3>
          <div className="mt-3 flex items-baseline gap-3">
            <div>
              <span className="text-2xl font-bold text-gray-100">{stats?.queues?.functions?.pending ?? 0}</span>
              <span className="text-sm text-gray-500 ml-1">functions</span>
            </div>
            <div>
              <span className="text-2xl font-bold text-gray-100">{stats?.queues?.agents?.pending ?? 0}</span>
              <span className="text-sm text-gray-500 ml-1">agents</span>
            </div>
          </div>
        </div>

        {/* Jobs */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Jobs (24h)</h3>
          <div className="mt-3 grid grid-cols-5 gap-1 text-center">
            {[
              { label: 'Q', value: stats?.jobs?.queued ?? 0, color: 'text-gray-300' },
              { label: 'Run', value: stats?.jobs?.running ?? 0, color: 'text-blue-600' },
              { label: 'Done', value: stats?.jobs?.completed ?? 0, color: 'text-green-600' },
              { label: 'Fail', value: stats?.jobs?.failed ?? 0, color: stats?.jobs?.failed ? 'text-red-600' : 'text-gray-500' },
              { label: 'Can', value: stats?.jobs?.cancelled ?? 0, color: stats?.jobs?.cancelled ? 'text-yellow-500' : 'text-gray-500' },
            ].map(s => (
              <div key={s.label}>
                <div className={`text-lg font-bold ${s.color}`}>{s.value}</div>
                <div className="text-xs text-gray-500">{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Health */}
        <div className={`card ${dlqSize > 0 ? 'border-red-300 bg-red-900/20' : ''}`}>
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Health</h3>
          <div className="mt-3 space-y-2">
            <div className="flex justify-between items-center">
              <span className="text-sm text-gray-400">Dead letter queue</span>
              <span className={`text-sm font-bold ${dlqSize > 0 ? 'text-red-600' : 'text-green-600'}`}>
                {dlqSize > 0 ? dlqSize : 'clean'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-gray-400">Shared containers</span>
              <span className="text-sm font-semibold">{sharedCount} running</span>
            </div>
          </div>
        </div>

        {/* Host Resources */}
        <div className="card">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Host</h3>
          <div className="mt-3 space-y-2">
            <ResourceBar value={host.cpu_percent} label="CPU" unit="%" />
            <ResourceBar value={host.memory_percent} label="Mem" unit="%" />
            <ResourceBar value={host.disk_percent} label="Disk" unit="%" />
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
                  ? 'bg-purple-900/20 border-purple-800/30 text-purple-400'
                  : 'bg-indigo-900/20 border-indigo-800/30 text-indigo-300'
              }`}
            >
              <span className="font-mono">{w.worker_id.slice(0, 8)}</span>
              <span className="text-gray-500">|</span>
              <span>{w.queue === 'functions' ? 'fn' : 'agent'} &times;{w.max_jobs}</span>
              <span className="text-gray-500">|</span>
              <span className="text-gray-500">{w.started_at ? formatUptime(w.started_at) : '-'}</span>
            </span>
          ))}
        </div>
      )}

      {/* Services */}
      {services.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-100">Services</h2>
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey: ['system-health'] })}
              className="btn btn-secondary flex items-center"
              title="Refresh"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Service</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Status</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Health</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Uptime</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">CPU</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Memory</th>
                  <th className="text-right py-2 font-medium text-gray-500">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {services.map((svc: any) => (
                  <tr key={svc.name} className="hover:bg-white/5">
                    <td className="py-2 pr-4">
                      <div className="font-mono text-xs text-gray-300">{svc.name}</div>
                      {svc.service !== svc.name && (
                        <div className="text-xs text-gray-500">{svc.service}</div>
                      )}
                    </td>
                    <td className="py-2 pr-4">
                      <span className={`font-medium ${getServiceStatusColor(svc.status)}`}>
                        {svc.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      <span className={`${getHealthColor(svc.health)}`}>
                        {svc.health === 'none' ? '-' : svc.health}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-gray-400">
                      {svc.uptime_seconds > 0 ? formatUptimeSeconds(svc.uptime_seconds) : '-'}
                    </td>
                    <td className="py-2 pr-4 text-gray-400">
                      {svc.status === 'running' ? `${svc.cpu_percent}%` : '-'}
                    </td>
                    <td className="py-2 pr-4 text-gray-400">
                      {svc.status === 'running' && svc.memory ? (
                        <span>
                          {svc.memory.used_mb}MB
                          <span className="text-gray-500 text-xs ml-1">
                            ({svc.memory.percent}%)
                          </span>
                        </span>
                      ) : '-'}
                    </td>
                    <td className="py-2 text-right">
                      <button
                        onClick={() => {
                          if (restartingContainer === svc.name) {
                            restartContainerMutation.mutate(svc.name);
                          } else {
                            setRestartingContainer(svc.name);
                            setTimeout(() => setRestartingContainer(null), 3000);
                          }
                        }}
                        disabled={restartContainerMutation.isPending}
                        className={`btn text-xs py-1 px-2 inline-flex items-center gap-1 ${
                          restartingContainer === svc.name
                            ? 'btn-secondary border-yellow-800/50 text-yellow-400'
                            : 'btn-secondary'
                        }`}
                        title={restartingContainer === svc.name ? 'Click again to confirm' : 'Restart container'}
                      >
                        <RotateCw className="w-3 h-3" />
                        {restartingContainer === svc.name ? 'Confirm' : 'Restart'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Sandbox Containers & Shared Containers — side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Sandbox Containers */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-100">Sandbox Containers</h2>
              <p className="text-sm text-gray-400 mt-1">Isolated containers for function and code execution</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handlePoolScale(-1)}
                disabled={poolTotal === 0 || poolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale down"
              >
                {poolScaleMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Minus className="w-4 h-4" />}
              </button>
              <span className="text-sm font-semibold text-gray-300 w-8 text-center">{poolTotal}</span>
              <button
                onClick={() => handlePoolScale(1)}
                disabled={(poolMax != null && poolTotal >= poolMax) || poolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale up"
              >
                {poolScaleMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
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

          <div className="mb-3 h-2 bg-[#161616] rounded-full overflow-hidden">
            <div className="h-full flex">
              <div className="bg-blue-500 transition-all" style={{ width: `${poolBusyPct}%` }} />
              <div className="bg-green-400 transition-all" style={{ width: `${poolUtilPct - poolBusyPct}%` }} />
            </div>
          </div>
          <div className="flex gap-4 text-xs text-gray-500 mb-4">
            <span><span className="inline-block w-2 h-2 rounded-full bg-blue-500 mr-1" />busy ({poolInUse})</span>
            <span><span className="inline-block w-2 h-2 rounded-full bg-green-400 mr-1" />idle ({poolIdle})</span>
            {poolMax != null && <span className="text-gray-500">max {poolMax}</span>}
          </div>

          {poolScaleMutation.isError && (
            <div className="mb-4 p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
              Failed to scale sandbox containers.
            </div>
          )}

          {allSandboxContainers.length === 0 ? (
            <div className="text-center py-4">
              <Box className="w-8 h-8 text-gray-500 mx-auto mb-2" />
              <p className="text-gray-500 text-sm">No containers</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-white/[0.06]">
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[45%]">Container</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[15%]">Status</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[20%]">Executions</th>
                    <th className="text-left py-2 font-medium text-gray-500 w-[20%]">Age</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04]">
                  {allSandboxContainers.map((c: any) => (
                    <tr key={c.name} className="hover:bg-white/5">
                      <td className="py-2 pr-4 font-mono text-xs text-gray-300">{c.name}</td>
                      <td className="py-2 pr-4">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                          c.state === 'in_use' ? 'bg-blue-900/30 text-blue-300' : 'bg-green-900/30 text-green-300'
                        }`}>
                          {c.state === 'in_use' ? 'busy' : 'idle'}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-gray-400">
                        {c.executions}
                        <span className="text-gray-500"> / {poolStats?.max_executions ?? '?'}</span>
                      </td>
                      <td className="py-2 text-gray-400">{formatAge(c.age_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Shared Containers */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-100">Shared Containers</h2>
              <p className="text-sm text-gray-400 mt-1">Persistent containers for <code className="px-1 py-0.5 bg-[#161616] rounded text-xs">shared</code> execution mode functions</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleSharedPoolScale(-1)}
                disabled={sharedCount === 0 || sharedPoolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale down"
              >
                {sharedPoolScaleMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Minus className="w-4 h-4" />}
              </button>
              <span className="text-sm font-semibold text-gray-300 w-8 text-center">{sharedCount}</span>
              <button
                onClick={() => handleSharedPoolScale(1)}
                disabled={sharedCount >= 10 || sharedPoolScaleMutation.isPending}
                className="btn btn-secondary flex items-center"
                title="Scale up"
              >
                {sharedPoolScaleMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
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
            <div className="mb-4 p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
              Failed to scale shared containers.
            </div>
          )}

          {!sharedPoolContainers || sharedPoolContainers.length === 0 ? (
            <div className="text-center py-4">
              <Server className="w-8 h-8 text-gray-500 mx-auto mb-2" />
              <p className="text-gray-500 text-sm">No containers</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-white/[0.06]">
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[45%]">Container</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[15%]">Status</th>
                    <th className="text-left py-2 pr-4 font-medium text-gray-500 w-[20%]">Executions</th>
                    <th className="text-left py-2 font-medium text-gray-500 w-[20%]">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04]">
                  {sharedPoolContainers.map((w: any) => (
                    <tr key={w.id} className="hover:bg-white/5">
                      <td className="py-2 pr-4 font-mono text-xs text-gray-300">{w.container_name}</td>
                      <td className="py-2 pr-4">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${getContainerStatusColor(w.status)}`}>
                          {w.status}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-gray-400">{w.executions}</td>
                      <td className="py-2 text-gray-400 text-xs">{new Date(w.created_at).toLocaleString()}</td>
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
          <h2 className="text-lg font-semibold text-gray-100">Jobs</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => flushStuckJobsMutation.mutate()}
              disabled={flushStuckJobsMutation.isPending}
              className="btn btn-secondary text-xs py-1.5 px-3 inline-flex items-center gap-1"
              title="Cancel jobs stuck running for over 2 hours"
            >
              <Trash2 className="w-3 h-3" />
              Flush stuck
            </button>
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
              <option value="cancelled">Cancelled</option>
            </select>
          </div>
        </div>

        {flushStuckJobsMutation.isSuccess && (
          <div className="mb-4 p-2 bg-green-900/20 border border-green-800/30 rounded text-sm text-green-400">
            Flushed {(flushStuckJobsMutation.data as any)?.cancelled ?? 0} stuck job(s).
          </div>
        )}

        {!jobs || jobs.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-gray-500 text-sm">No jobs found</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Status</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Description</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Time</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Error</th>
                  <th className="text-right py-2 font-medium text-gray-500">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {jobs.map((job: any) => (
                  <tr key={job.job_id} className="hover:bg-white/5">
                    <td className="py-2 pr-4">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${getJobStatusColor(job.status)}`}>
                        {job.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-sm text-gray-300">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium mr-2 ${
                        job.queue === 'functions' ? 'bg-purple-900/30 text-purple-400' : 'bg-indigo-900/30 text-indigo-300'
                      }`}>
                        {job.queue === 'functions' ? 'fn' : 'agent'}
                      </span>
                      {job.queue === 'functions'
                        ? (job.function || '-')
                        : (job.agent || 'unknown')}
                      {job.queue === 'agents' && job.type === 'resume' && (
                        <span className="text-xs text-gray-500 ml-1">(resume)</span>
                      )}
                      <span className="text-xs text-gray-500 ml-1">via {(job.trigger_type || (job.queue === 'agents' ? 'agent' : 'api')).toLowerCase()}</span>
                      {job.chat_id && (
                        <span className="text-xs text-gray-500 ml-1">chat:{job.chat_id.slice(0, 8)}</span>
                      )}
                    </td>
                    <td className="py-2 pr-4 text-xs text-gray-500 whitespace-nowrap">
                      {job.enqueued_at
                        ? new Date(job.enqueued_at * 1000).toLocaleTimeString()
                        : '-'}
                    </td>
                    <td className="py-2 pr-4 text-xs text-red-500 max-w-xs truncate">
                      {job.error || ''}
                    </td>
                    <td className="py-2 text-right">
                      {(job.status === 'running' || job.status === 'queued') && (
                        <button
                          onClick={() => cancelMutation.mutate(job.job_id)}
                          disabled={cancelMutation.isPending}
                          className="btn btn-secondary text-xs py-1 px-2 inline-flex items-center gap-1"
                          title="Cancel job"
                        >
                          <X className="w-3 h-3" />
                          Cancel
                        </button>
                      )}
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
        <div className="card border-red-800/30">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-red-500" />
              <h2 className="text-lg font-semibold text-gray-100">Dead Letter Queue</h2>
              <span className="text-sm text-red-600 font-medium">({dlqEntries.length})</span>
            </div>
            <button
              onClick={() => flushDLQMutation.mutate()}
              disabled={flushDLQMutation.isPending}
              className="btn btn-secondary text-xs py-1.5 px-3 inline-flex items-center gap-1 border-red-800/30 text-red-400 hover:bg-red-900/20"
              title="Remove all DLQ entries"
            >
              <Trash2 className="w-3 h-3" />
              Flush all
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Function</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Error</th>
                  <th className="text-left py-2 pr-4 font-medium text-gray-500">Attempts</th>
                  <th className="text-right py-2 font-medium text-gray-500">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {dlqEntries.map((entry: any, idx: number) => (
                  <tr key={entry.job_id || idx} className="hover:bg-white/5">
                    <td className="py-2 pr-4 text-gray-300">{entry.function || '-'}</td>
                    <td className="py-2 pr-4 text-red-600 text-xs max-w-md truncate">
                      {entry.error || '-'}
                    </td>
                    <td className="py-2 pr-4 text-gray-400">{entry.attempts ?? '-'}</td>
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
            <div className="mt-3 p-2 bg-green-900/20 border border-green-800/30 rounded text-sm text-green-400">
              Job re-enqueued.
            </div>
          )}
          {retryMutation.isError && (
            <div className="mt-3 p-2 bg-red-900/20 border border-red-800/30 rounded text-sm text-red-400">
              Retry failed.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
