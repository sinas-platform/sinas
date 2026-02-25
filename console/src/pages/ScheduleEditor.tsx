import { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { ArrowLeft, Save, Trash2, Code } from 'lucide-react';
import { SchemaFormField } from '../components/SchemaFormField';
import CodeEditor from '@uiw/react-textarea-code-editor';

export function ScheduleEditor() {
  const { scheduleId } = useParams<{ scheduleId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = scheduleId === 'new';

  const [formData, setFormData] = useState({
    name: '',
    schedule_type: 'function' as 'function' | 'agent',
    target_namespace: 'default',
    target_name: '',
    description: '',
    cron_expression: '0 0 * * *',
    timezone: 'UTC',
    input_data: '{}',
    content: '',
    is_active: true,
  });

  const [rawMode, setRawMode] = useState(false);

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

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAgents(),
    retry: false,
  });

  // Load schedule data when available
  useEffect(() => {
    if (schedule && !isNew) {
      setFormData({
        name: schedule.name || '',
        schedule_type: schedule.schedule_type || 'function',
        target_namespace: schedule.target_namespace || 'default',
        target_name: schedule.target_name || '',
        description: schedule.description || '',
        cron_expression: schedule.cron_expression || '0 0 * * *',
        timezone: schedule.timezone || 'UTC',
        input_data: JSON.stringify(schedule.input_data || {}, null, 2),
        content: schedule.content || '',
        is_active: schedule.is_active ?? true,
      });
    }
  }, [schedule, isNew]);

  // Find selected target to get its input_schema
  const selectedTarget = useMemo(() => {
    if (!formData.target_name) return null;
    if (formData.schedule_type === 'function') {
      if (!functions) return null;
      return functions.find((f: any) =>
        f.namespace === formData.target_namespace && f.name === formData.target_name
      ) || null;
    } else {
      if (!agents) return null;
      return agents.find((a: any) =>
        a.namespace === formData.target_namespace && a.name === formData.target_name
      ) || null;
    }
  }, [functions, agents, formData.schedule_type, formData.target_namespace, formData.target_name]);

  const inputSchema = selectedTarget?.input_schema;
  const schemaProperties = inputSchema?.properties || {};
  const hasSchemaFields = Object.keys(schemaProperties).length > 0;

  // Parse input_data for schema form
  const inputParams = useMemo(() => {
    try {
      return JSON.parse(formData.input_data);
    } catch {
      return {};
    }
  }, [formData.input_data]);

  const updateInputParam = (key: string, value: any) => {
    const updated = { ...inputParams, [key]: value };
    // Remove keys with empty string values
    if (value === '' || value === undefined) {
      delete updated[key];
    }
    setFormData({ ...formData, input_data: JSON.stringify(updated, null, 2) });
  };

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      const payload = {
        ...data,
        input_data: JSON.parse(data.input_data),
        content: data.content || null,
      };
      return isNew
        ? apiClient.createSchedule(payload)
        : apiClient.updateSchedule(scheduleId!, payload);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['schedule', scheduleId] });
      if (isNew || data.name !== scheduleId) {
        navigate(`/schedules/${data.name}`, { replace: true });
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
          <Link to="/schedules" className="mr-4 text-gray-400 hover:text-gray-100">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-100">
              {isNew ? 'New Schedule' : formData.name || 'Edit Schedule'}
            </h1>
            <p className="text-gray-400 mt-1">
              {isNew ? 'Schedule a function or agent to run automatically' : 'Edit scheduled job configuration'}
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
          <h2 className="text-lg font-semibold text-gray-100 mb-4">Schedule Configuration</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
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

            {/* Schedule Type Toggle */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Type
              </label>
              <div className="flex rounded-lg border border-white/10 overflow-hidden w-fit">
                <button
                  type="button"
                  onClick={() => setFormData({ ...formData, schedule_type: 'function', target_namespace: 'default', target_name: '', content: '' })}
                  className={`px-4 py-2 text-sm font-medium ${
                    formData.schedule_type === 'function'
                      ? 'bg-[#2563eb] text-white'
                      : 'bg-[#161616] text-gray-300 hover:bg-white/5'
                  }`}
                >
                  Function
                </button>
                <button
                  type="button"
                  onClick={() => setFormData({ ...formData, schedule_type: 'agent', target_namespace: 'default', target_name: '', content: '' })}
                  className={`px-4 py-2 text-sm font-medium border-l border-white/10 ${
                    formData.schedule_type === 'agent'
                      ? 'bg-[#2563eb] text-white'
                      : 'bg-[#161616] text-gray-300 hover:bg-white/5'
                  }`}
                >
                  Agent
                </button>
              </div>
            </div>

            {/* Target Selection */}
            {formData.schedule_type === 'function' ? (
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Function to Execute *
                </label>
                <select
                  value={formData.target_name ? `${formData.target_namespace}/${formData.target_name}` : ''}
                  onChange={(e) => {
                    const val = e.target.value;
                    if (val) {
                      const [ns, ...rest] = val.split('/');
                      setFormData({ ...formData, target_namespace: ns, target_name: rest.join('/') });
                    } else {
                      setFormData({ ...formData, target_namespace: 'default', target_name: '' });
                    }
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
              </div>
            ) : (
              <>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    Agent *
                  </label>
                  <select
                    value={formData.target_name ? `${formData.target_namespace}/${formData.target_name}` : ''}
                    onChange={(e) => {
                      const val = e.target.value;
                      if (val) {
                        const [ns, ...rest] = val.split('/');
                        setFormData({ ...formData, target_namespace: ns, target_name: rest.join('/') });
                      } else {
                        setFormData({ ...formData, target_namespace: 'default', target_name: '' });
                      }
                    }}
                    required
                    className="input"
                  >
                    <option value="">Select an agent...</option>
                    {agents?.map((agent: any) => (
                      <option key={agent.id} value={`${agent.namespace}/${agent.name}`}>
                        {agent.namespace}/{agent.name} {agent.description ? `- ${agent.description}` : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    Message Content *
                  </label>
                  <textarea
                    value={formData.content}
                    onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                    placeholder="The message to send to the agent on each run..."
                    rows={4}
                    required
                    className="input"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    A new chat is created for each scheduled run
                  </p>
                </div>
              </>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
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
                <label className="block text-sm font-medium text-gray-300 mb-2">
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
                  Examples: <code className="font-mono bg-[#161616] px-1 rounded">0 0 * * *</code> (daily at midnight),{' '}
                  <code className="font-mono bg-[#161616] px-1 rounded">*/15 * * * *</code> (every 15 min)
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
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
                className="w-4 h-4 text-primary-600 border-white/10 rounded focus:ring-primary-500"
              />
              <label htmlFor="is_active" className="ml-2 text-sm text-gray-300">
                Active (schedule will run)
              </label>
            </div>
          </div>
        </div>

        {/* Input Data */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-100">
                {formData.schedule_type === 'function' ? 'Function Input Data' : 'Agent Input Variables'}
              </h2>
              <p className="text-sm text-gray-400 mt-1">
                {formData.schedule_type === 'function'
                  ? 'Parameters passed to the function on each execution'
                  : 'Input variables for the agent (used in system prompt templates)'}
              </p>
            </div>
            {hasSchemaFields && (
              <button
                type="button"
                onClick={() => setRawMode(!rawMode)}
                className="btn btn-secondary text-xs flex items-center gap-1"
              >
                <Code className="w-3.5 h-3.5" />
                {rawMode ? 'Form' : 'JSON'}
              </button>
            )}
          </div>

          {!hasSchemaFields || rawMode ? (
            <div className="border border-white/10 rounded-lg overflow-hidden">
              <CodeEditor
                value={formData.input_data}
                language="json"
                placeholder='{}'
                onChange={(e) => setFormData({ ...formData, input_data: e.target.value })}
                padding={15}
                data-color-mode="dark"
                style={{
                  fontSize: 14,
                  backgroundColor: '#111111',
                  color: '#ededed',
                  fontFamily: 'ui-monospace, SFMono-Regular, SF Mono, Consolas, Liberation Mono, Menlo, monospace',
                  minHeight: '150px',
                }}
              />
            </div>
          ) : (
            <div>
              {Object.entries(schemaProperties).map(([key, prop]: [string, any]) => (
                <SchemaFormField
                  key={key}
                  name={key}
                  schema={prop}
                  value={inputParams[key]}
                  onChange={(value) => updateInputParam(key, value)}
                  required={inputSchema?.required?.includes(key)}
                />
              ))}
            </div>
          )}
        </div>

        {saveMutation.isError && (
          <div className="p-4 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
            Failed to save schedule. Please check your configuration.
          </div>
        )}

        {saveMutation.isSuccess && (
          <div className="p-4 bg-green-900/20 border border-green-800/30 rounded-lg text-sm text-green-400">
            Schedule saved successfully!
          </div>
        )}
      </form>
    </div>
  );
}
