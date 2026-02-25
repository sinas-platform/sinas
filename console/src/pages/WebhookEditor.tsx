import { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, API_BASE_URL } from '../lib/api';
import { ArrowLeft, Save, Trash2 } from 'lucide-react';
import { ApiUsage } from '../components/ApiUsage';

export function WebhookEditor() {
  const { '*': webhookPath } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = webhookPath === 'new';

  const [formData, setFormData] = useState({
    path: '',
    function_name: '',
    http_method: 'POST',
    description: '',
    default_values: {} as Record<string, any>,
    requires_auth: true,
  });

  const [defaultKey, setDefaultKey] = useState('');
  const [defaultValue, setDefaultValue] = useState('');

  const { data: webhook, isLoading } = useQuery({
    queryKey: ['webhook', webhookPath],
    queryFn: () => apiClient.getWebhook(webhookPath!),
    enabled: !isNew && !!webhookPath,
  });

  // Load webhook data when available
  useEffect(() => {
    if (webhook && !isNew) {
      setFormData({
        path: webhook.path || '',
        function_name: webhook.function_namespace && webhook.function_name
          ? `${webhook.function_namespace}/${webhook.function_name}`
          : '',
        http_method: webhook.http_method || 'POST',
        description: webhook.description || '',
        default_values: webhook.default_values || {},
        requires_auth: webhook.requires_auth ?? true,
      });
    }
  }, [webhook, isNew]);

  // Fetch available functions for dropdown
  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      // Split function_name into namespace and name
      const [namespace, name] = data.function_name.split('/');
      const payload = {
        ...data,
        function_namespace: namespace,
        function_name: name,
      };
      return isNew
        ? apiClient.createWebhook(payload)
        : apiClient.updateWebhook(webhookPath!, payload);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      queryClient.invalidateQueries({ queryKey: ['webhook', webhookPath] });
      if (isNew) {
        navigate(`/webhooks/${encodeURIComponent(data.path.replace(/^\//, ''))}`);
      } else if (data.path !== `/${webhookPath}`) {
        // Path changed, navigate to new URL
        navigate(`/webhooks/${encodeURIComponent(data.path.replace(/^\//, ''))}`);
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.deleteWebhook(webhookPath!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      navigate('/webhooks');
    },
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    saveMutation.mutate(formData);
  };

  const addDefaultValue = () => {
    if (defaultKey.trim()) {
      let parsedValue: any = defaultValue.trim();
      // Try to parse as JSON
      try {
        parsedValue = JSON.parse(defaultValue.trim());
      } catch {
        // Keep as string if not valid JSON
      }
      setFormData({
        ...formData,
        default_values: { ...formData.default_values, [defaultKey.trim()]: parsedValue },
      });
      setDefaultKey('');
      setDefaultValue('');
    }
  };

  const removeDefaultValue = (key: string) => {
    const newDefaults = { ...formData.default_values };
    delete newDefaults[key];
    setFormData({ ...formData, default_values: newDefaults });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <Link to="/webhooks" className="mr-4 text-gray-400 hover:text-gray-100">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-100">
              {isNew ? 'New Webhook' : formData.path || 'Edit Webhook'}
            </h1>
            <p className="text-gray-400 mt-1">
              {isNew ? 'Create a web-accessible endpoint to trigger a function' : 'Edit webhook endpoint configuration'}
            </p>
          </div>
        </div>
        <div className="flex space-x-3">
          {!isNew && (
            <button
              onClick={() => {
                if (confirm('Are you sure you want to delete this webhook?')) {
                  deleteMutation.mutate();
                }
              }}
              className="btn btn-danger flex items-center"
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="w-4 h-4 mr-2" />
              Delete
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saveMutation.isPending}
            className="btn btn-primary flex items-center"
          >
            <Save className="w-4 h-4 mr-2" />
            {saveMutation.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {!isNew && formData.path && (
        <ApiUsage
          curl={[
            {
              label: 'Trigger the webhook',
              language: 'bash',
              code: `curl -X ${formData.http_method} ${API_BASE_URL}/webhooks/${formData.path}${formData.requires_auth ? ` \\
  -H "Authorization: Bearer $TOKEN"` : ''} \\
  -H "Content-Type: application/json" \\
  -d '{"key": "value"}'`,
            },
          ]}
          sdk={[
            {
              label: 'Trigger via SDK',
              language: 'python',
              code: `from sinas import SinasClient

client = SinasClient(base_url="${API_BASE_URL}", api_key="sk-...")

result = client.webhooks.run(
    "${formData.path}",
    method="${formData.http_method}",
    body={"key": "value"}
)
print(result["execution_id"], result["result"])`,
            },
          ]}
        />
      )}

      <form onSubmit={handleSave} className="space-y-6">
        {/* Basic Info */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Webhook Configuration</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Function to Execute *
              </label>
              <select
                value={formData.function_name}
                onChange={(e) => {
                  const selectedFunc = functions?.find((f: any) => `${f.namespace}/${f.name}` === e.target.value);
                  setFormData({
                    ...formData,
                    function_name: e.target.value,
                    path: formData.path || (selectedFunc ? selectedFunc.name : ''),
                    description: formData.description || selectedFunc?.description || '',
                  });
                }}
                required
                className="input"
              >
                <option value="">Select a function...</option>
                {functions?.map((func: any) => (
                  <option key={func.id} value={`${func.namespace}/${func.name}`}>
                    {func.namespace}/{func.name} {func.description ? `- ${func.description}` : ''}
                  </option>
                ))}
              </select>
              <p className="text-xs text-gray-500 mt-1">
                The function that will be called when this webhook is triggered
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                URL Path *
              </label>
              <div className="flex items-stretch">
                <span className="inline-flex items-center px-3 rounded-l-md border border-r-0 border-white/10 bg-[#0d0d0d] text-gray-500 text-sm font-mono">
                  /h/
                </span>
                <input
                  type="text"
                  value={formData.path}
                  onChange={(e) => setFormData({ ...formData, path: e.target.value })}
                  placeholder="my_function"
                  pattern="^[a-zA-Z0-9_/-]+$"
                  title="Only letters, numbers, underscores, hyphens and forward slashes allowed"
                  required
                  className="input rounded-l-none flex-1"
                />
              </div>
              <p className="text-xs text-gray-500 mt-1">
                The URL path where this webhook will be accessible (will be available at /h/{formData.path || 'your-path'})
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                HTTP Method *
              </label>
              <select
                value={formData.http_method}
                onChange={(e) => setFormData({ ...formData, http_method: e.target.value })}
                className="input"
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
                <option value="DELETE">DELETE</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Description
              </label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="What does this webhook do?"
                rows={3}
                className="input"
              />
            </div>

            <div className="flex items-center">
              <input
                type="checkbox"
                id="requires_auth"
                checked={formData.requires_auth}
                onChange={(e) => setFormData({ ...formData, requires_auth: e.target.checked })}
                className="w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
              />
              <label htmlFor="requires_auth" className="ml-2 text-sm text-gray-300">
                Requires Authentication
              </label>
            </div>
          </div>
        </div>

        {/* Default Values */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Default Input Values</h2>
          <p className="text-sm text-gray-400 mb-4">
            Provide default values that will be merged with incoming request data when the function is executed.
          </p>
          <div className="space-y-2 mb-4">
            <div className="flex space-x-2">
              <input
                type="text"
                value={defaultKey}
                onChange={(e) => setDefaultKey(e.target.value)}
                placeholder="Parameter name"
                className="input flex-1"
              />
              <input
                type="text"
                value={defaultValue}
                onChange={(e) => setDefaultValue(e.target.value)}
                placeholder="Value (JSON or string)"
                className="input flex-1"
              />
              <button
                type="button"
                onClick={addDefaultValue}
                className="btn btn-secondary"
              >
                Add
              </button>
            </div>
          </div>
          {Object.keys(formData.default_values).length > 0 && (
            <div className="space-y-2">
              {Object.entries(formData.default_values).map(([key, value]) => (
                <div
                  key={key}
                  className="flex items-center justify-between p-3 bg-[#0d0d0d] rounded"
                >
                  <div className="flex-1">
                    <span className="font-mono text-sm font-medium">{key}</span>
                    <span className="text-gray-500 mx-2">:</span>
                    <span className="font-mono text-sm">{JSON.stringify(value)}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeDefaultValue(key)}
                    className="text-red-600 hover:text-red-400 text-sm"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {saveMutation.isError && (
          <div className="p-4 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
            Failed to save webhook. Please check your configuration.
          </div>
        )}

        {saveMutation.isSuccess && (
          <div className="p-4 bg-green-900/20 border border-green-800/30 rounded-lg text-sm text-green-400">
            Webhook saved successfully!
          </div>
        )}
      </form>

    </div>
  );
}
