import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Plus, Trash2, Workflow, ChevronRight, AlertTriangle } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { Pipeline } from '../types';

export function Pipelines() {
  const queryClient = useQueryClient();

  const { data: pipelines, isLoading } = useQuery({
    queryKey: ['pipelines'],
    queryFn: () => apiClient.listPipelines(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deletePipeline(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pipelines'] });
    },
  });

  const handleDelete = (pipeline: Pipeline) => {
    if (confirm(`Delete pipeline "${pipeline.namespace}/${pipeline.name}"? Its run history and cursors are deleted too.`)) {
      deleteMutation.mutate({ namespace: pipeline.namespace, name: pipeline.name });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Pipelines</h1>
          <p className="text-gray-400 mt-1">
            Linear step sequences (connector → function → agent → query → load) fired by schedules, webhooks, CDC, or agents
          </p>
        </div>
        <Link to="/pipelines/new/new" className="btn btn-primary flex items-center">
          <Plus className="w-5 h-5 mr-2" />
          New Pipeline
        </Link>
      </div>

      {isLoading ? (
        <div className="text-gray-400">Loading...</div>
      ) : !pipelines?.length ? (
        <div className="card text-center py-12">
          <Workflow className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-300">No pipelines yet</h3>
          <p className="text-gray-500 mt-1">
            Create a pipeline to chain connector calls, functions, agents, and database loads
          </p>
          <Link to="/pipelines/new/new" className="btn btn-primary mt-4 inline-flex items-center">
            <Plus className="w-4 h-4 mr-2" />
            Create Pipeline
          </Link>
        </div>
      ) : (
        <div className="grid gap-4">
          {pipelines.map((pipeline) => {
            const stepCount = pipeline.steps?.length || 0;
            const hasCursor = pipeline.steps?.some((s) => (s as any).cursor);
            return (
              <Link
                key={pipeline.id}
                to={`/pipelines/${pipeline.namespace}/${pipeline.name}`}
                className="card flex items-center justify-between hover:border-line transition-colors group"
              >
                <div className="flex items-center gap-4 min-w-0">
                  <Workflow className="w-5 h-5 text-primary-400 flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-sm text-gray-200">
                        <span className="text-gray-500">{pipeline.namespace}/</span>
                        {pipeline.name}
                      </span>
                      {pipeline.as_tool && (
                        <span className="px-2 py-0.5 text-xs font-medium rounded bg-blue-900/30 text-blue-400">
                          Tool
                        </span>
                      )}
                      {pipeline.per_user && (
                        <span className="px-2 py-0.5 text-xs font-medium rounded bg-purple-900/30 text-purple-400">
                          Per-user
                        </span>
                      )}
                      {hasCursor && (
                        <span className="px-2 py-0.5 text-xs font-medium rounded bg-green-900/30 text-green-400">
                          Cursor
                        </span>
                      )}
                      {!pipeline.is_active && (
                        <span className="px-2 py-0.5 text-xs font-medium rounded bg-red-900/30 text-red-400">
                          Inactive
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      <span className="text-xs text-gray-600">
                        {stepCount} step{stepCount !== 1 ? 's' : ''}
                        {' · '}
                        {pipeline.steps?.map((s) => s.type).join(' → ') || 'empty'}
                      </span>
                    </div>
                    {pipeline.description && (
                      <p className="text-xs text-gray-500 mt-0.5 truncate">{pipeline.description}</p>
                    )}
                    {pipeline.error_message && (
                      <p className="text-xs text-red-400 mt-0.5 truncate flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3 flex-shrink-0" />
                        {pipeline.error_message}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      handleDelete(pipeline);
                    }}
                    className="p-1.5 text-gray-500 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                  <ChevronRight className="w-4 h-4 text-gray-600" />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
