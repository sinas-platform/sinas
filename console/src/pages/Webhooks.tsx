import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Webhook, Plus, Edit2, Trash2 } from 'lucide-react';
import { Link } from 'react-router-dom';

export function Webhooks() {
  const queryClient = useQueryClient();

  const { data: webhooks, isLoading } = useQuery({
    queryKey: ['webhooks'],
    queryFn: () => apiClient.listWebhooks(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (webhookPath: string) => apiClient.deleteWebhook(webhookPath),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
    },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Webhooks</h1>
          <p className="text-gray-400 mt-1">Configure webhook endpoints for external integrations</p>
        </div>
        <Link to="/webhooks/new" className="btn btn-primary flex items-center">
          <Plus className="w-5 h-5 mr-2" />
          New Webhook
        </Link>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : webhooks && webhooks.length > 0 ? (
        <div className="grid gap-6">
          {webhooks.map((webhook: any) => (
            <div key={webhook.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <Webhook className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 bg-blue-900/30 text-blue-300 text-xs font-medium rounded">
                        {webhook.http_method}
                      </span>
                      <h3 className="font-semibold text-gray-100 font-mono">{webhook.path}</h3>
                    </div>
                    <p className="text-sm text-gray-400 mt-1">{webhook.description || 'No description'}</p>
                    <div className="flex items-center gap-4 mt-1">
                      <p className="text-xs text-gray-500">
                        Function: <span className="font-mono">{webhook.function_namespace}/{webhook.function_name}</span>
                      </p>
                      {webhook.requires_auth && (
                        <span className="text-xs text-green-600 font-medium">🔒 Auth Required</span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      Created {new Date(webhook.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <Link
                    to={`/webhooks/${encodeURIComponent(webhook.path.replace(/^\//, ''))}`}
                    className="text-primary-600 hover:text-primary-700"
                  >
                    <Edit2 className="w-5 h-5" />
                  </Link>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this webhook?')) {
                        deleteMutation.mutate(webhook.path);
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
          <Webhook className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No webhooks configured</h3>
          <p className="text-gray-400 mb-4">Create webhooks to integrate with external services</p>
          <Link to="/webhooks/new" className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Create Webhook
          </Link>
        </div>
      )}
    </div>
  );
}
