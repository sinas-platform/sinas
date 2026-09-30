import { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient, getApiErrorMessage } from '../lib/api';
import { useToast } from '../lib/toast-context';
import {
  Save, ArrowLeft, Play, RotateCcw, ChevronDown, ChevronRight, AlertCircle,
  CheckCircle2, XCircle, Clock, Loader2,
} from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';
import { PipelineStepsEditor } from '../components/pipeline-steps/PipelineStepsEditor';
import type { Step } from '../components/pipeline-steps/model';
import type { PipelineRun, PipelineRunOutcome } from '../types';

const codeEditorStyle = {
  fontSize: 13,
  backgroundColor: 'var(--surface-input)',
  color: 'var(--content)',
  fontFamily: 'ui-monospace, SFMono-Regular, monospace',
  borderRadius: 6,
  border: '1px solid var(--line)',
};

function statusIcon(status: string) {
  switch (status) {
    case 'succeeded':
      return <CheckCircle2 className="w-4 h-4 text-green-400" />;
    case 'failed':
      return <XCircle className="w-4 h-4 text-red-400" />;
    case 'timed_out':
      return <Clock className="w-4 h-4 text-yellow-400" />;
    case 'running':
      return <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />;
    default:
      return <Clock className="w-4 h-4 text-gray-500" />;
  }
}

function RunRow({ run, onReplay }: { run: PipelineRun; onReplay: (runId: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="border border-line-soft rounded-lg">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 text-left"
      >
        <div className="flex items-center gap-3 min-w-0">
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-gray-500 flex-shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-500 flex-shrink-0" />
          )}
          {statusIcon(run.status)}
          <span className="font-mono text-xs text-gray-400 truncate">{run.run_id}</span>
          <span className="px-1.5 py-0.5 text-xs rounded bg-gray-800 text-gray-400">{run.trigger_type}</span>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {run.duration_ms != null && <span className="text-xs text-gray-500">{run.duration_ms} ms</span>}
          <span className="text-xs text-gray-500">{new Date(run.started_at).toLocaleString()}</span>
        </div>
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-line-soft pt-2">
          {run.error && <p className="text-xs text-red-400 whitespace-pre-wrap">{run.error}</p>}
          {run.steps?.length > 0 && (
            <div className="space-y-1">
              {run.steps.map((s) => (
                <div key={s.name} className="flex items-center gap-2 text-xs">
                  {statusIcon(s.status)}
                  <span className="font-mono text-gray-300">{s.name}</span>
                  <span className="text-gray-600">({s.type})</span>
                  {s.durationMs != null && <span className="text-gray-500">{s.durationMs} ms</span>}
                  {s.executionId && <span className="text-gray-600 font-mono truncate">exec {s.executionId.slice(0, 8)}</span>}
                  {s.chatId && (
                    <Link to={`/chats/${s.chatId}`} className="text-primary-400 hover:underline">
                      chat
                    </Link>
                  )}
                  {s.error && <span className="text-red-400 truncate">{s.error}</span>}
                </div>
              ))}
            </div>
          )}
          {(run.cursor_before != null || run.cursor_after != null) && (
            <p className="text-xs text-gray-500">
              cursor: <span className="font-mono">{run.cursor_before ?? '∅'}</span> →{' '}
              <span className="font-mono">{run.cursor_after ?? '(held)'}</span>
            </p>
          )}
          {(run.status === 'failed' || run.status === 'timed_out') && (
            <button
              type="button"
              onClick={() => onReplay(run.run_id)}
              className="btn btn-secondary btn-sm inline-flex items-center text-xs"
            >
              <RotateCcw className="w-3 h-3 mr-1" />
              Replay with stored input
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function PipelineEditor() {
  const { namespace = '', name = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = namespace === 'new' && name === 'new';
  const { showSuccess, showError } = useToast();

  const [formData, setFormData] = useState({
    namespace: 'default',
    name: '',
    description: '',
    sync_timeout_seconds: 120,
    concurrency: '' as '' | 'single' | 'parallel',
    disable_after_failures: '' as string,
    as_tool: false,
    tool_description: '',
    per_user_enabled: false,
    per_user_connector: '',
    per_user_disable_after: '' as string,
    is_active: true,
  });
  const [steps, setSteps] = useState<Step[]>([]);
  const [stepsJsonMode, setStepsJsonMode] = useState(false);
  const [stepsText, setStepsText] = useState('[]');
  const [outputMappingText, setOutputMappingText] = useState('');
  const [inputSchema, setInputSchema] = useState<any>({});
  const [saveError, setSaveError] = useState<string | null>(null);

  // Manual run box
  const [runInputText, setRunInputText] = useState('{}');
  const [runMode, setRunMode] = useState<'sync' | 'async'>('sync');
  const [runResult, setRunResult] = useState<PipelineRunOutcome | null>(null);

  const { data: pipeline, isLoading } = useQuery({
    queryKey: ['pipeline', namespace, name],
    queryFn: () => apiClient.getPipeline(namespace, name),
    enabled: !isNew,
    retry: false,
  });

  const { data: runs } = useQuery({
    queryKey: ['pipelineRuns', namespace, name],
    queryFn: () => apiClient.listPipelineRuns(namespace, name, 25),
    enabled: !isNew,
    retry: false,
    refetchInterval: (q) =>
      q.state.data?.some((r: PipelineRun) => r.status === 'running') ? 3000 : false,
  });

  // Resource suggestions for the steps editor (best-effort; free text allowed)
  const { data: connectorsData } = useQuery({
    queryKey: ['connectors'],
    queryFn: () => apiClient.listConnectors(),
    retry: false,
  });
  const { data: functionsData } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });
  const { data: agentsData } = useQuery({
    queryKey: ['agents'],
    queryFn: () => apiClient.listAgents(),
    retry: false,
  });
  const { data: queriesData } = useQuery({
    queryKey: ['queries'],
    queryFn: () => apiClient.listQueries(),
    retry: false,
  });
  const { data: connectionsData } = useQuery({
    queryKey: ['databaseConnections'],
    queryFn: () => apiClient.listDatabaseConnections(),
    retry: false,
  });

  const stepResources = {
    connectors: (connectorsData || []).map((c: any) => ({
      ref: `${c.namespace}/${c.name}`,
      operations: (c.operations || []).map((op: any) => op.name).filter(Boolean),
    })),
    functions: (functionsData || []).map((f: any) => `${f.namespace}/${f.name}`),
    agents: (agentsData || []).map((a: any) => `${a.namespace}/${a.name}`),
    queries: (queriesData || []).map((q: any) => `${q.namespace}/${q.name}`),
    connections: (connectionsData || []).map((c: any) => c.name),
  };

  useEffect(() => {
    if (pipeline) {
      setFormData({
        namespace: pipeline.namespace,
        name: pipeline.name,
        description: pipeline.description || '',
        sync_timeout_seconds: pipeline.sync_timeout_seconds,
        concurrency: (pipeline.concurrency as '' | 'single' | 'parallel') || '',
        disable_after_failures: pipeline.disable_after_failures?.toString() || '',
        as_tool: pipeline.as_tool,
        tool_description: pipeline.tool_description || '',
        per_user_enabled: !!pipeline.per_user,
        per_user_connector: pipeline.per_user?.connector || '',
        per_user_disable_after: pipeline.per_user?.disableAfterFailures?.toString() || '',
        is_active: pipeline.is_active,
      });
      setSteps((pipeline.steps || []) as Step[]);
      setStepsText(JSON.stringify(pipeline.steps, null, 2));
      setOutputMappingText(pipeline.output_mapping ? JSON.stringify(pipeline.output_mapping, null, 2) : '');
      setInputSchema(pipeline.input_schema || {});
    }
  }, [pipeline]);

  const buildPayload = () => {
    let stepsPayload: any = steps;
    if (stepsJsonMode) {
      try {
        stepsPayload = JSON.parse(stepsText);
      } catch (e: any) {
        throw new Error(`Steps is not valid JSON: ${e.message}`);
      }
    }
    let outputMapping: any = null;
    if (outputMappingText.trim()) {
      try {
        outputMapping = JSON.parse(outputMappingText);
      } catch (e: any) {
        throw new Error(`Output mapping is not valid JSON: ${e.message}`);
      }
    }
    return {
      namespace: formData.namespace,
      name: formData.name,
      description: formData.description || null,
      input_schema: inputSchema,
      steps: stepsPayload,
      per_user: formData.per_user_enabled
        ? {
            connector: formData.per_user_connector,
            ...(formData.per_user_disable_after
              ? { disableAfterFailures: parseInt(formData.per_user_disable_after, 10) }
              : {}),
          }
        : null,
      as_tool: formData.as_tool,
      tool_description: formData.tool_description || null,
      sync_timeout_seconds: formData.sync_timeout_seconds,
      concurrency: formData.concurrency || null,
      disable_after_failures: formData.disable_after_failures
        ? parseInt(formData.disable_after_failures, 10)
        : null,
      output_mapping: outputMapping,
      ...(isNew ? {} : { is_active: formData.is_active }),
    };
  };

  const saveMutation = useMutation({
    mutationFn: (data: any) =>
      isNew ? apiClient.createPipeline(data) : apiClient.updatePipeline(namespace, name, data),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ['pipelines'] });
      queryClient.invalidateQueries({ queryKey: ['pipeline', saved.namespace, saved.name] });
      showSuccess(`Pipeline ${saved.namespace}/${saved.name} saved`);
      if (isNew || saved.namespace !== namespace || saved.name !== name) {
        navigate(`/pipelines/${saved.namespace}/${saved.name}`);
      }
    },
    onError: (err) => setSaveError(getApiErrorMessage(err, 'Failed to save pipeline')),
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      let input: any;
      try {
        input = JSON.parse(runInputText || '{}');
      } catch (e: any) {
        throw new Error(`Run input is not valid JSON: ${e.message}`);
      }
      return apiClient.runPipeline(namespace, name, input, runMode);
    },
    onSuccess: (result) => {
      setRunResult(result);
      queryClient.invalidateQueries({ queryKey: ['pipelineRuns', namespace, name] });
    },
    onError: (err: any) => {
      setRunResult(null);
      showError(err?.message?.startsWith('Run input') ? err.message : getApiErrorMessage(err, 'Run failed'));
    },
  });

  const replayMutation = useMutation({
    mutationFn: (runId: string) => apiClient.replayPipelineRun(runId),
    onSuccess: () => {
      showSuccess('Run re-enqueued');
      queryClient.invalidateQueries({ queryKey: ['pipelineRuns', namespace, name] });
    },
    onError: (err) => showError(getApiErrorMessage(err, 'Replay failed')),
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    setSaveError(null);
    let payload: any;
    try {
      payload = buildPayload();
    } catch (err: any) {
      setSaveError(err.message);
      return;
    }
    saveMutation.mutate(payload);
  };

  if (!isNew && isLoading) return <div className="text-gray-400">Loading...</div>;

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/pipelines" className="p-1.5 text-gray-500 hover:text-gray-300">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <h1 className="text-2xl font-bold text-gray-100">
            {isNew ? 'New Pipeline' : (
              <span className="font-mono">
                <span className="text-gray-500">{namespace}/</span>{name}
              </span>
            )}
          </h1>
          {!isNew && pipeline && !pipeline.is_active && (
            <span className="px-2 py-0.5 text-xs font-medium rounded bg-red-900/30 text-red-400">Inactive</span>
          )}
        </div>
        <button onClick={handleSave} disabled={saveMutation.isPending} className="btn btn-primary flex items-center">
          <Save className="w-4 h-4 mr-2" />
          {saveMutation.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>

      {saveError && (
        <div className="card border-red-900/50 bg-red-950/20 flex items-start gap-2 text-sm text-red-300">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span className="whitespace-pre-wrap">{saveError}</span>
        </div>
      )}

      {!isNew && pipeline?.error_message && (
        <div className="card border-yellow-900/50 bg-yellow-950/20 text-sm text-yellow-300">
          Last failure: {pipeline.error_message}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        <div className="card space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Namespace</label>
              <input
                value={formData.namespace}
                onChange={(e) => setFormData({ ...formData, namespace: e.target.value })}
                className="input w-full font-mono"
                disabled={!isNew}
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Name</label>
              <input
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="input w-full font-mono"
                disabled={!isNew}
                required
              />
            </div>
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Description</label>
            <input
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="input w-full"
            />
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Sync timeout (s)</label>
              <input
                type="number"
                min={1}
                max={600}
                value={formData.sync_timeout_seconds}
                onChange={(e) => setFormData({ ...formData, sync_timeout_seconds: parseInt(e.target.value || '120', 10) })}
                className="input w-full"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Concurrency</label>
              <select
                value={formData.concurrency}
                onChange={(e) => setFormData({ ...formData, concurrency: e.target.value as any })}
                className="input w-full"
              >
                <option value="">Auto (single if cursor, else parallel)</option>
                <option value="single">single</option>
                <option value="parallel">parallel</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Disable after failures</label>
              <input
                type="number"
                min={1}
                placeholder="off"
                value={formData.disable_after_failures}
                onChange={(e) => setFormData({ ...formData, disable_after_failures: e.target.value })}
                className="input w-full"
              />
            </div>
          </div>
          {!isNew && (
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input
                type="checkbox"
                checked={formData.is_active}
                onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              />
              Active {!formData.is_active && pipeline?.is_active === false && '(re-activating clears the failure counter)'}
            </label>
          )}
        </div>

        <div className="card space-y-3">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-sm font-medium text-gray-300">Steps</h3>
              <p className="text-xs text-gray-500 mt-0.5">
                Values marked <span className="font-mono">&fnof;x</span> are JMESPath expressions over{' '}
                {'{'}input, steps.&lt;name&gt;.output, cursor, run{'}'}.
              </p>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm text-xs"
              onClick={() => {
                setSaveError(null);
                if (stepsJsonMode) {
                  try {
                    setSteps(JSON.parse(stepsText));
                    setStepsJsonMode(false);
                  } catch (e: any) {
                    setSaveError(`Steps is not valid JSON: ${e.message}`);
                  }
                } else {
                  setStepsText(JSON.stringify(steps, null, 2));
                  setStepsJsonMode(true);
                }
              }}
            >
              {stepsJsonMode ? 'Visual editor' : 'Edit as JSON'}
            </button>
          </div>
          {stepsJsonMode ? (
            <CodeEditor
              value={stepsText}
              language="json"
              onChange={(e) => { setSaveError(null); setStepsText(e.target.value); }}
              padding={12}

              style={{ ...codeEditorStyle, minHeight: 220 }}
            />
          ) : (
            <PipelineStepsEditor
              steps={steps}
              onChange={(next) => { setSaveError(null); setSteps(next); }}
              resources={stepResources}
            />
          )}
        </div>

        <div className="card space-y-4">
          <h3 className="text-sm font-medium text-gray-300">Per-user runs</h3>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={formData.per_user_enabled}
              onChange={(e) => setFormData({ ...formData, per_user_enabled: e.target.checked })}
            />
            Fan out one run per connected user of a per-user-auth connector
          </label>
          {formData.per_user_enabled && (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-gray-400 mb-1">Source connector (namespace/name)</label>
                <input
                  value={formData.per_user_connector}
                  onChange={(e) => setFormData({ ...formData, per_user_connector: e.target.value })}
                  className="input w-full font-mono"
                  placeholder="google/gmail"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">Skip user after N failures</label>
                <input
                  type="number"
                  min={1}
                  placeholder="off"
                  value={formData.per_user_disable_after}
                  onChange={(e) => setFormData({ ...formData, per_user_disable_after: e.target.value })}
                  className="input w-full"
                />
              </div>
            </div>
          )}
        </div>

        <div className="card space-y-4">
          <h3 className="text-sm font-medium text-gray-300">Agent tool exposure</h3>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={formData.as_tool}
              onChange={(e) => setFormData({ ...formData, as_tool: e.target.checked })}
            />
            Expose as an agent tool (requires an input schema and a description)
          </label>
          {formData.as_tool && (
            <div>
              <label className="block text-sm text-gray-400 mb-1">Tool description (defaults to description)</label>
              <input
                value={formData.tool_description}
                onChange={(e) => setFormData({ ...formData, tool_description: e.target.value })}
                className="input w-full"
              />
            </div>
          )}
          <JSONSchemaEditor
            label="Input Schema"
            description="Validates run input; becomes the tool parameters when exposed as a tool"
            value={inputSchema}
            onChange={setInputSchema}
          />
        </div>

        <div className="card space-y-3">
          <div>
            <h3 className="text-sm font-medium text-gray-300">Output mapping (optional)</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {'{"output.$": "steps.act.output"}'} or a literal template; empty = last step's output.
            </p>
          </div>
          <CodeEditor
            value={outputMappingText}
            language="json"
            placeholder='{ "output.$": "steps.fetch.output.body" }'
            onChange={(e) => { setSaveError(null); setOutputMappingText(e.target.value); }}
            padding={12}

            style={{ ...codeEditorStyle, minHeight: 60 }}
          />
        </div>
      </form>

      {!isNew && (
        <div className="card space-y-4">
          <h3 className="text-sm font-medium text-gray-300">Run now</h3>
          <div className="flex gap-3 items-start">
            <div className="flex-1 min-w-0">
              <CodeEditor
                value={runInputText}
                language="json"
                onChange={(e) => setRunInputText(e.target.value)}
                padding={10}

                style={{ ...codeEditorStyle, minHeight: 40 }}
              />
            </div>
            {/* .input is width:100%; unconstrained inside a flex row its basis
                crushes the flex-1 editor to nothing */}
            <select value={runMode} onChange={(e) => setRunMode(e.target.value as any)} className="input" style={{ width: '7rem', flexShrink: 0 }}>
              <option value="sync">sync</option>
              <option value="async">async</option>
            </select>
            <button
              type="button"
              onClick={() => runMutation.mutate()}
              disabled={runMutation.isPending}
              className="btn btn-primary flex items-center"
            >
              <Play className="w-4 h-4 mr-2" />
              {runMutation.isPending ? 'Running…' : 'Run'}
            </button>
          </div>
          {runResult && (
            <div className="text-sm space-y-1">
              <div className="flex items-center gap-2">
                {statusIcon(runResult.status)}
                <span className="text-gray-300">{runResult.status}</span>
                <span className="font-mono text-xs text-gray-500">{runResult.run_id}</span>
                {runResult.duration_ms != null && (
                  <span className="text-xs text-gray-500">{runResult.duration_ms} ms</span>
                )}
              </div>
              {runResult.error && <p className="text-xs text-red-400 whitespace-pre-wrap">{runResult.error}</p>}
              {runResult.output !== undefined && runResult.output !== null && (
                <pre className="text-xs text-gray-400 bg-black/40 rounded p-2 overflow-auto max-h-64">
                  {JSON.stringify(runResult.output, null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
      )}

      {!isNew && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-gray-300">
            Recent runs {pipeline?.cursor_value && (
              <span className="text-xs text-gray-500 font-normal ml-2">
                cursor: <span className="font-mono">{pipeline.cursor_value}</span>
              </span>
            )}
          </h3>
          {!runs?.length ? (
            <p className="text-sm text-gray-500">No runs yet.</p>
          ) : (
            <div className="space-y-2">
              {runs.map((run) => (
                <RunRow key={run.run_id} run={run} onReplay={(id) => replayMutation.mutate(id)} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
