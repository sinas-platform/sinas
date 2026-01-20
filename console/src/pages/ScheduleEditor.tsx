import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { ArrowLeft, Save, Trash2 } from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';

export function ScheduleEditor() {
  const { scheduleId } = useParams<{ scheduleId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = scheduleId === 'new';

  const [formData, setFormData] = useState({
    name: '',
    function_name: '',
    description: '',
    cron_expression: '0 0 * * *',
    timezone: 'UTC',
    input_data: '{}',
    is_active: true,
  });

  const { data: schedule, isLoading } = useQuery({
    queryKey: ['schedule', scheduleId],
    queryFn: () => apiClient.getSchedule(scheduleId!),
    enabled: !isNew && !!scheduleId,
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });

  // Load schedule data when available
  useState(() => {
    if (schedule && !isNew) {
      setFormData({
        name: schedule.name || '',
        function_name: schedule.function_name || '',
        description: schedule.description || '',
        cron_expression: schedule.cron_expression || '0 0 * * *',
        timezone: schedule.timezone || 'UTC',
        input_data: JSON.stringify(schedule.input_data || {}, null, 2),
        is_active: schedule.is_active ?? true,
      });
    }
  });

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      const payload = {
        ...data,
        input_data: JSON.parse(data.input_data),
      };
      return isNew
        ? apiClient.createSchedule(payload)
        : apiClient.updateSchedule(scheduleId!, payload);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['schedule', scheduleId] });
      if (isNew) {
        navigate(`/schedules/${data.id}`);
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.deleteSchedule(scheduleId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      navigate('/schedules');
    },
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    try {
      JSON.parse(formData.input_data);
      saveMutation.mutate(formData);
    } catch (err) {
      alert('Invalid JSON in input data');
    }
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
          <Link to="/schedules" className="mr-4 text-gray-600 hover:text-gray-900">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-900">
              {isNew ? 'New Schedule' : formData.name || 'Edit Schedule'}
            </h1>
            <p className="text-gray-600 mt-1">
              {isNew ? 'Schedule a function to run automatically' : 'Edit scheduled job configuration'}
            </p>
          </div>
        </div>
        <div className="flex space-x-3">
          {!isNew && (
            <button
              onClick={() => {
                if (confirm('Are you sure you want to delete this schedule?')) {
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

      <form onSubmit={handleSave} className="space-y-6">
        {/* Basic Info */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Schedule Configuration</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Schedule Name *
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="Daily Report"
                required
                className="input"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Function to Execute *
              </label>
              <select
                value={formData.function_name}
                onChange={(e) => setFormData({ ...formData, function_name: e.target.value })}
                required
                className="input"
              >
                <option value="">Select a function...</option>
                {functions?.map((func: any) => (
                  <option key={func.id} value={func.name}>
                    {func.name} {func.description ? `- ${func.description}` : ''}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Description
              </label>
              <textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="What does this schedule do?"
                rows={2}
                className="input"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Cron Expression *
                </label>
                <input
                  type="text"
                  value={formData.cron_expression}
                  onChange={(e) => setFormData({ ...formData, cron_expression: e.target.value })}
                  placeholder="0 0 * * *"
                  required
                  className="input font-mono"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Examples: <code className="font-mono bg-gray-100 px-1 rounded">0 0 * * *</code> (daily at midnight),{' '}
                  <code className="font-mono bg-gray-100 px-1 rounded">*/15 * * * *</code> (every 15 min)
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Timezone *
                </label>
                <input
                  type="text"
                  value={formData.timezone}
                  onChange={(e) => setFormData({ ...formData, timezone: e.target.value })}
                  placeholder="UTC"
                  required
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  e.g., UTC, America/New_York, Europe/Amsterdam
                </p>
              </div>
            </div>

            <div className="flex items-center">
              <input
                type="checkbox"
                id="is_active"
                checked={formData.is_active}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
              />
              <label htmlFor="is_active" className="ml-2 text-sm text-gray-700">
                Active (schedule will run)
              </label>
            </div>
          </div>
        </div>

        {/* Input Data */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Function Input Data</h2>
          <p className="text-sm text-gray-600 mb-4">
            JSON object that will be passed to the function on each execution
          </p>
          <div className="border border-gray-300 rounded-lg overflow-hidden">
            <CodeEditor
              value={formData.input_data}
              language="json"
              placeholder='{}'
              onChange={(e) => setFormData({ ...formData, input_data: e.target.value })}
              padding={15}
              style={{
                fontSize: 14,
                backgroundColor: '#fafafa',
                color: '#1f2937',
                fontFamily: 'ui-monospace, SFMono-Regular, SF Mono, Consolas, Liberation Mono, Menlo, monospace',
                minHeight: '200px',
              }}
            />
          </div>
        </div>

        {saveMutation.isError && (
          <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            Failed to save schedule. Please check your configuration.
          </div>
        )}

        {saveMutation.isSuccess && (
          <div className="p-4 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
            Schedule saved successfully!
          </div>
        )}
      </form>
    </div>
  );
}
