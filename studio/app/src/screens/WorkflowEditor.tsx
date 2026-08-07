import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, ArrowRight, Bot, Clock, Copy, Loader2, Webhook as WebhookIcon } from 'lucide-react';
import { api } from '../lib/api';
import { getConnection } from '../lib/connection';
import {
  SCHEDULE_PRESETS,
  adapterPayloadFields,
  cronFromPreset,
  isAdapter,
  kebab,
  presetFromCron,
  prettyName,
  type SchedulePreset,
} from '../lib/model';
import type { Agent, Execution, Manifest } from '../lib/types';
import { SetupStudioLink } from '../components/SetupStudio';

// ---------- shared bits ----------

function OnOffSwitch({ on, busy, onChange }: { on: boolean; busy: boolean; onChange: (next: boolean) => void }) {
  return (
    <button
      className={`toggle${on ? ' on' : ''}`}
      style={{ fontWeight: 700, color: on ? 'var(--good)' : 'var(--faint)' }}
      disabled={busy}
      onClick={() => onChange(!on)}
      title="Off = assembled but inert. The switch is the publish moment."
    >
      <span className="sw" /> {on ? 'On' : 'Off'}
    </button>
  );
}

function AssistantSelect({
  agents,
  value,
  onChange,
}: {
  agents: Agent[];
  value: string; // "namespace/name" or ''
  onChange: (ref: string) => void;
}) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose an assistant…</option>
      {agents
        .filter((a) => a.is_active)
        .map((a) => (
          <option key={a.id} value={`${a.namespace}/${a.name}`}>
            {prettyName(a.name)} ({a.namespace})
          </option>
        ))}
    </select>
  );
}

/**
 * Per-fire history. Executions carry trigger_type + trigger_id, so a
 * webhook's runs are filterable precisely. Agent-type schedules start chats
 * rather than function executions, so their rail may legitimately be empty —
 * the copy says so instead of pretending.
 */
function RunsRail({ triggerType, triggerId, emptyNote }: { triggerType: string; triggerId: string; emptyNote: string }) {
  const { data: executions, isLoading } = useQuery({
    queryKey: ['executions', triggerType],
    queryFn: () => api.listExecutions({ trigger_type: triggerType, limit: 50 }),
    refetchInterval: 15000,
    retry: false,
  });
  const runs = (executions ?? []).filter((e: Execution) => e.trigger_id === triggerId);

  return (
    <aside className="chat-pane">
      <div className="pane-head">
        <div>
          <div className="pane-title">Runs</div>
          <div className="pane-sub">Every time this workflow fired</div>
        </div>
      </div>
      <div className="pane-scroll" style={{ gap: 0, paddingTop: 6 }}>
        {isLoading && <Loader2 size={16} className="spin" style={{ margin: '12px auto' }} />}
        {!isLoading && runs.length === 0 && (
          <p style={{ fontSize: 12.5, color: 'var(--muted)', padding: '8px 2px' }}>{emptyNote}</p>
        )}
        {runs.map((run) => (
          <div key={run.execution_id} style={{ display: 'flex', gap: 10, padding: '10px 0', borderBottom: '1px solid var(--line-soft)' }}>
            <span
              style={{
                width: 8, height: 8, borderRadius: '50%', marginTop: 6, flexShrink: 0,
                background: run.status === 'completed' ? 'var(--good)' : run.status === 'failed' ? 'var(--bad)' : 'var(--faint)',
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 600 }}>
                {run.status === 'completed' ? 'Succeeded' : run.status === 'failed' ? 'Failed' : run.status}
                {run.duration_ms != null && <span style={{ color: 'var(--faint)', fontWeight: 400 }}> · {(run.duration_ms / 1000).toFixed(1)}s</span>}
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--faint)' }}>
                {run.started_at ? new Date(run.started_at).toLocaleString() : ''}
              </div>
              {run.error && (
                <div style={{ marginTop: 6, background: 'var(--bad-soft)', borderRadius: 8, padding: '8px 10px', fontSize: 12, color: 'var(--bad)', wordBreak: 'break-word' }}>
                  {run.error}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="hint-strip">Off = assembled but inert. The switch is the publish moment.</div>
    </aside>
  );
}

function StripCard({
  kicker,
  title,
  icon,
  tone,
  children,
}: {
  kicker: string;
  title: string;
  icon: React.ReactNode;
  tone: 'trigger' | 'action';
  children: React.ReactNode;
}) {
  return (
    <div className="card" style={{ width: 330, flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 18px', borderBottom: '1px solid var(--line-soft)' }}>
        <span
          className="row-logo"
          style={tone === 'trigger' ? { background: 'var(--warn-soft)', color: 'var(--warn)' } : { background: 'var(--accent-soft)', color: 'var(--accent-ink)' }}
        >
          {icon}
        </span>
        <div>
          <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--faint)' }}>{kicker}</div>
          <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--ink)' }}>{title}</div>
        </div>
      </div>
      <div style={{ padding: '14px 18px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>{children}</div>
    </div>
  );
}

function StripArrow() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', padding: '0 18px' }}>
      <ArrowRight size={18} style={{ color: 'var(--faint)' }} />
    </div>
  );
}

const PUBLIC_WARNING = 'Public link — anyone who has this URL can trigger this workflow. Fine for website forms; don’t share it further.';

// ---------- schedule workflow ----------

export function ScheduleWorkflow() {
  const { pns, pname, name } = useParams<{ pns: string; pname: string; name: string }>();
  const queryClient = useQueryClient();

  const { data: schedules } = useQuery({ queryKey: ['schedules'], queryFn: api.listSchedules });
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents });
  const schedule = schedules?.find((s) => s.name === name);

  const patch = useMutation({
    mutationFn: (data: Parameters<typeof api.updateSchedule>[1]) => api.updateSchedule(name!, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schedules'] }),
  });

  // Local draft for the agent message, autosaved on blur.
  const [content, setContent] = useState<string | null>(null);

  if (!schedules) return <Loader2 size={20} className="spin" style={{ margin: '40px auto', display: 'block' }} />;
  if (!schedule) {
    return (
      <div className="empty">
        This schedule no longer exists in the workspace.{' '}
        <Link to={`/projects/${pns}/${pname}`}>Back to the project</Link>
      </div>
    );
  }

  const parsed = presetFromCron(schedule.cron_expression);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div className="topbar" style={{ height: 50 }}>
        <Link to={`/projects/${pns}/${pname}`} style={{ color: 'var(--muted)', display: 'flex' }}>
          <ArrowLeft size={17} />
        </Link>
        <span className="row-logo" style={{ width: 28, height: 28, background: 'var(--warn-soft)', color: 'var(--warn)' }}>
          <Clock size={14} />
        </span>
        <span style={{ fontWeight: 700, color: 'var(--ink)', fontSize: 15 }}>{schedule.description || prettyName(schedule.name)}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center' }}>
          {patch.isPending ? <span className="saving">Saving…</span> : <span className="saved"><span className="dot" /> Saved</span>}
          <OnOffSwitch on={schedule.is_active} busy={patch.isPending} onChange={(next) => patch.mutate({ is_active: next })} />
        </div>
      </div>

      <div className="editor-split">
        <div style={{ overflow: 'auto', padding: '40px 28px', display: 'flex', alignItems: 'flex-start', justifyContent: 'center' }}>
          <StripCard kicker="When" title="On a schedule" icon={<Clock size={15} />} tone="trigger">
            <div style={{ display: 'flex', gap: 10 }}>
              <div className="field" style={{ flex: 1 }}>
                <label>How often</label>
                <select
                  className="input"
                  value={parsed?.preset ?? ''}
                  onChange={(e) =>
                    patch.mutate({ cron_expression: cronFromPreset(e.target.value as SchedulePreset, parsed?.time ?? '09:00') })
                  }
                >
                  {!parsed && <option value="">Custom: {schedule.cron_expression}</option>}
                  {SCHEDULE_PRESETS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </div>
              {parsed && parsed.preset !== 'hourly' && (
                <div className="field">
                  <label>At</label>
                  <input
                    type="time"
                    className="input"
                    value={parsed.time}
                    onChange={(e) => patch.mutate({ cron_expression: cronFromPreset(parsed.preset, e.target.value) })}
                  />
                </div>
              )}
            </div>
            <p style={{ margin: 0, fontSize: 11.5, color: 'var(--faint)' }}>
              Timezone: {schedule.timezone}
              {schedule.last_run ? ` · last ran ${new Date(schedule.last_run).toLocaleString()}` : ' · has not run yet'}
            </p>
          </StripCard>

          <StripArrow />

          <StripCard
            kicker="Then"
            title={schedule.schedule_type === 'agent' ? 'Ask an assistant' : 'Run a step'}
            icon={<Bot size={15} />}
            tone="action"
          >
            {schedule.schedule_type === 'agent' ? (
              <>
                <div className="field">
                  <label>Assistant</label>
                  <AssistantSelect
                    agents={agents ?? []}
                    value={`${schedule.target_namespace}/${schedule.target_name}`}
                    onChange={(ref) => {
                      const [target_namespace, target_name] = ref.split('/');
                      if (target_name) patch.mutate({ target_namespace, target_name });
                    }}
                  />
                </div>
                <div className="field">
                  <label>With this request, each run</label>
                  <textarea
                    className="input"
                    style={{ minHeight: 70 }}
                    value={content ?? schedule.content ?? ''}
                    onChange={(e) => setContent(e.target.value)}
                    onBlur={() => {
                      if (content !== null && content !== schedule.content) patch.mutate({ content });
                    }}
                  />
                </div>
                <Link
                  to={`/projects/${pns}/${pname}/assistants/${schedule.target_namespace}/${schedule.target_name}`}
                  className="link-btn"
                  style={{ alignSelf: 'flex-start' }}
                >
                  Open assistant editor →
                </Link>
              </>
            ) : (
              <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>
                Runs the step <b style={{ color: 'var(--ink)' }}>{schedule.target_namespace}/{schedule.target_name}</b> —
                a reusable step set up by your admin.
              </p>
            )}
          </StripCard>
        </div>

        <RunsRail
          triggerType="schedule"
          triggerId={schedule.id}
          emptyNote={
            schedule.schedule_type === 'agent'
              ? 'Assistant schedules start conversations rather than recorded runs — check the assistant’s chats for results.'
              : 'No runs recorded yet.'
          }
        />
      </div>
    </div>
  );
}

// ---------- webhook workflow ----------

export function WebhookWorkflow() {
  const { pns, pname } = useParams<{ pns: string; pname: string }>();
  const path = useParams()['*'] ?? '';
  const queryClient = useQueryClient();

  const { data: webhooks } = useQuery({ queryKey: ['webhooks'], queryFn: api.listWebhooks });
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents });
  const { data: functions } = useQuery({ queryKey: ['functions'], queryFn: api.listFunctions });
  const webhook = webhooks?.find((w) => w.path === path);
  const adapter = functions?.find((f) => webhook && f.namespace === webhook.function_namespace && f.name === webhook.function_name);

  const patch = useMutation({
    mutationFn: (data: Parameters<typeof api.updateWebhook>[1]) => api.updateWebhook(path, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['webhooks'] }),
  });

  const [template, setTemplate] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  if (!webhooks) return <Loader2 size={20} className="spin" style={{ margin: '40px auto', display: 'block' }} />;
  if (!webhook) {
    return (
      <div className="empty">
        This webhook no longer exists in the workspace. <Link to={`/projects/${pns}/${pname}`}>Back to the project</Link>
      </div>
    );
  }

  const url = `${getConnection()?.baseUrl}/webhooks/${webhook.path}`;
  const defaults = webhook.default_values ?? {};
  const isAdapterHook = adapter ? isAdapter(adapter) : 'studio_agent' in defaults;
  const payloadFields = adapter ? adapterPayloadFields(adapter) : [];

  const saveDefaults = (patchValues: Record<string, any>) =>
    patch.mutate({ default_values: { ...defaults, ...patchValues } });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div className="topbar" style={{ height: 50 }}>
        <Link to={`/projects/${pns}/${pname}`} style={{ color: 'var(--muted)', display: 'flex' }}>
          <ArrowLeft size={17} />
        </Link>
        <span className="row-logo" style={{ width: 28, height: 28, background: 'var(--warn-soft)', color: 'var(--warn)' }}>
          <WebhookIcon size={14} />
        </span>
        <span style={{ fontWeight: 700, color: 'var(--ink)', fontSize: 15 }}>{webhook.description || `/webhooks/${webhook.path}`}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center' }}>
          {patch.isPending ? <span className="saving">Saving…</span> : <span className="saved"><span className="dot" /> Saved</span>}
          <OnOffSwitch on={webhook.is_active} busy={patch.isPending} onChange={(next) => patch.mutate({ is_active: next })} />
        </div>
      </div>

      <div className="editor-split">
        <div style={{ overflow: 'auto', padding: '40px 28px', display: 'flex', alignItems: 'flex-start', justifyContent: 'center' }}>
          <StripCard kicker="When" title="A request arrives" icon={<WebhookIcon size={15} />} tone="trigger">
            <div>
              <div className="label">This address receives it</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'var(--ground)', border: '1px solid var(--line-soft)', borderRadius: 8, padding: '8px 10px', fontFamily: 'ui-monospace, monospace', fontSize: 11.5, color: 'var(--ink)' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{url}</span>
                <button
                  className="link-btn"
                  style={{ marginLeft: 'auto', fontFamily: 'system-ui', flexShrink: 0 }}
                  onClick={() => {
                    navigator.clipboard.writeText(url);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  }}
                >
                  {copied ? 'Copied' : <Copy size={12} />}
                </button>
              </div>
            </div>
            {!webhook.requires_auth && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', background: 'var(--warn-soft)', border: '1px solid var(--warn-line)', borderRadius: 8, padding: '8px 10px', fontSize: 12, color: 'var(--warn)' }}>
                <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
                <span>{PUBLIC_WARNING}</span>
              </div>
            )}
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--body)' }}>
              <input
                type="checkbox"
                checked={!webhook.requires_auth}
                onChange={(e) => patch.mutate({ requires_auth: !e.target.checked })}
              />
              Public — callable without signing in
            </label>
            {payloadFields.length > 0 && (
              <p style={{ margin: 0, fontSize: 12, color: 'var(--faint)' }}>
                Accepts fields:{' '}
                {payloadFields.map((f) => (
                  <span key={f} className="token" style={{ marginRight: 4 }}>@{f}</span>
                ))}
                <span style={{ opacity: 0.75 }}> — declared by {adapter ? prettyName(adapter.name) : 'the intake step'}</span>
              </p>
            )}
          </StripCard>

          <StripArrow />

          {isAdapterHook ? (
            <StripCard kicker="Then" title="Ask an assistant" icon={<Bot size={15} />} tone="action">
              <div className="field">
                <label>Assistant</label>
                <AssistantSelect
                  agents={agents ?? []}
                  value={typeof defaults.studio_agent === 'string' ? defaults.studio_agent : ''}
                  onChange={(ref) => ref && saveDefaults({ studio_agent: ref })}
                />
              </div>
              <div className="field">
                <label>With this request — @field inserts what the sender sent</label>
                <textarea
                  className="input"
                  style={{ minHeight: 80 }}
                  placeholder={'e.g. New support request from @name (@email):\n"@message"\n\nHandle it end to end.'}
                  value={template ?? (typeof defaults.studio_message_template === 'string' ? defaults.studio_message_template : '')}
                  onChange={(e) => setTemplate(e.target.value)}
                  onBlur={() => {
                    if (template !== null && template !== defaults.studio_message_template) {
                      saveDefaults({ studio_message_template: template });
                    }
                  }}
                />
              </div>
              <p style={{ margin: 0, fontSize: 11.5, color: 'var(--faint)' }}>
                Assistant and request are saved as settings on the intake step — no code involved.
              </p>
              {typeof defaults.studio_agent === 'string' && defaults.studio_agent.includes('/') && (
                <Link
                  to={`/projects/${pns}/${pname}/assistants/${defaults.studio_agent}`}
                  className="link-btn"
                  style={{ alignSelf: 'flex-start' }}
                >
                  Open assistant editor →
                </Link>
              )}
            </StripCard>
          ) : (
            <StripCard kicker="Then" title="Run a step" icon={<Bot size={15} />} tone="action">
              <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>
                Runs <b style={{ color: 'var(--ink)' }}>{webhook.function_namespace}/{webhook.function_name}</b> — a
                reusable step set up by your admin. Its settings aren't editable here.
              </p>
            </StripCard>
          )}
        </div>

        <RunsRail triggerType="webhook" triggerId={webhook.id} emptyNote="No runs recorded yet — send a request to the address on the left to try it." />
      </div>
    </div>
  );
}

// ---------- create flow ----------

export function NewWorkflow() {
  const { pns, pname } = useParams<{ pns: string; pname: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: project } = useQuery({ queryKey: ['project', pns, pname], queryFn: () => api.getProject(pns!, pname!) });
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents });
  const { data: functions } = useQuery({ queryKey: ['functions'], queryFn: api.listFunctions });

  const adapters = (functions ?? []).filter(isAdapter);

  const [kind, setKind] = useState<'schedule' | 'webhook' | null>(null);
  const [description, setDescription] = useState('');
  const [assistant, setAssistant] = useState('');
  const [message, setMessage] = useState('');
  const [preset, setPreset] = useState<SchedulePreset>('daily');
  const [time, setTime] = useState('09:00');
  const [hookPath, setHookPath] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [adapterRef, setAdapterRef] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (adapters.length > 0 && !adapterRef) setAdapterRef(`${adapters[0].namespace}/${adapters[0].name}`);
  }, [adapters, adapterRef]);

  const addMembership = async (manifest: Manifest, type: string, name: string) =>
    api.updateProject(manifest.namespace, manifest.name, {
      required_resources: [...(manifest.required_resources ?? []), { type, namespace: 'default', name }],
    });

  const create = async () => {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      if (kind === 'schedule') {
        const [target_namespace, target_name] = assistant.split('/');
        const scheduleName = `${project.name}-${kebab(description) || 'schedule'}`.slice(0, 250);
        await api.createSchedule({
          name: scheduleName,
          schedule_type: 'agent',
          target_namespace,
          target_name,
          description: description || undefined,
          cron_expression: cronFromPreset(preset, time),
          content: message || 'Run now.',
        });
        // Workflows assemble inert; the on/off switch is the publish moment.
        await api.updateSchedule(scheduleName, { is_active: false });
        await addMembership(project, 'schedule', scheduleName);
        queryClient.invalidateQueries();
        navigate(`/projects/${pns}/${pname}/workflows/schedule/${encodeURIComponent(scheduleName)}`);
      } else if (kind === 'webhook') {
        const [fns, fname] = adapterRef.split('/');
        const path = `${project.name}/${kebab(hookPath) || 'incoming'}`;
        await api.createWebhook({
          path,
          function_namespace: fns,
          function_name: fname,
          description: description || undefined,
          requires_auth: !isPublic,
          default_values: { studio_agent: assistant, studio_message_template: message },
        });
        await api.updateWebhook(path, { is_active: false });
        await addMembership(project, 'webhook', path);
        queryClient.invalidateQueries();
        navigate(`/projects/${pns}/${pname}/workflows/webhook/${path}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the workflow');
    } finally {
      setBusy(false);
    }
  };

  const canCreate = !!kind && assistant.includes('/') && (kind === 'schedule' || adapterRef.includes('/'));

  return (
    <div className="page" style={{ width: '100%', maxWidth: 640 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
        <Link to={`/projects/${pns}/${pname}`} style={{ color: 'var(--muted)', display: 'flex' }}>
          <ArrowLeft size={18} />
        </Link>
        <h1 style={{ fontSize: 20 }}>New workflow</h1>
      </div>

      <div className="field" style={{ marginBottom: 16 }}>
        <label>What starts it?</label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <button
            className="card"
            style={{ padding: 14, textAlign: 'left', cursor: 'pointer', borderColor: kind === 'schedule' ? 'var(--accent)' : undefined }}
            onClick={() => setKind('schedule')}
          >
            <Clock size={16} style={{ color: 'var(--warn)', marginBottom: 6 }} />
            <div style={{ fontWeight: 650, color: 'var(--ink)', fontSize: 13.5 }}>On a schedule</div>
            <div style={{ fontSize: 12, color: 'var(--faint)' }}>Runs automatically at set times</div>
          </button>
          <button
            className="card"
            style={{ padding: 14, textAlign: 'left', cursor: adapters.length ? 'pointer' : 'not-allowed', opacity: adapters.length ? 1 : 0.55, borderColor: kind === 'webhook' ? 'var(--accent)' : undefined }}
            disabled={adapters.length === 0}
            title={adapters.length === 0 ? 'Needs Studio setup — ask your admin to install the studio-runtime package' : undefined}
            onClick={() => setKind('webhook')}
          >
            <WebhookIcon size={16} style={{ color: 'var(--warn)', marginBottom: 6 }} />
            <div style={{ fontWeight: 650, color: 'var(--ink)', fontSize: 13.5 }}>A web request</div>
            <div style={{ fontSize: 12, color: 'var(--faint)' }}>
              {adapters.length ? 'A form or another tool calls a URL' : 'Needs Studio setup'}
            </div>
          </button>
        </div>
        {adapters.length === 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
            <span style={{ fontSize: 12.5, color: 'var(--muted)' }}>
              Webhook workflows need the studio-runtime package in this workspace.
            </span>
            <SetupStudioLink />
          </div>
        )}
      </div>

      {kind && (
        <div className="card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="field">
            <label>{kind === 'schedule' ? 'What is this workflow for?' : 'What starts this, in your words?'}</label>
            <input
              className="input"
              placeholder={kind === 'schedule' ? 'e.g. Morning digest of open tickets' : 'e.g. A support form is submitted'}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {kind === 'schedule' && (
            <div style={{ display: 'flex', gap: 10 }}>
              <div className="field" style={{ flex: 1 }}>
                <label>How often</label>
                <select className="input" value={preset} onChange={(e) => setPreset(e.target.value as SchedulePreset)}>
                  {SCHEDULE_PRESETS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </div>
              {preset !== 'hourly' && (
                <div className="field">
                  <label>At</label>
                  <input type="time" className="input" value={time} onChange={(e) => setTime(e.target.value)} />
                </div>
              )}
            </div>
          )}

          {kind === 'webhook' && (
            <>
              <div className="field">
                <label>Address name (becomes /webhooks/{project?.name}/…)</label>
                <input className="input" placeholder="e.g. form" value={hookPath} onChange={(e) => setHookPath(e.target.value)} />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--body)' }}>
                <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} />
                Public — callable without signing in
              </label>
              {isPublic && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', background: 'var(--warn-soft)', border: '1px solid var(--warn-line)', borderRadius: 8, padding: '8px 10px', fontSize: 12, color: 'var(--warn)' }}>
                  <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
                  <span>{PUBLIC_WARNING}</span>
                </div>
              )}
              {adapters.length > 1 && (
                <div className="field">
                  <label>Intake step</label>
                  <select className="input" value={adapterRef} onChange={(e) => setAdapterRef(e.target.value)}>
                    {adapters.map((a) => (
                      <option key={a.id} value={`${a.namespace}/${a.name}`}>
                        {prettyName(a.name)} — {a.description || `${a.namespace}/${a.name}`}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </>
          )}

          <div className="field">
            <label>Then ask which assistant?</label>
            <AssistantSelect agents={agents ?? []} value={assistant} onChange={setAssistant} />
          </div>

          <div className="field">
            <label>
              {kind === 'schedule' ? 'With this request, each run' : 'With this request — @field inserts what the sender sent'}
            </label>
            <textarea
              className="input"
              style={{ minHeight: 70 }}
              placeholder={kind === 'schedule' ? 'e.g. Prepare the morning ticket digest.' : 'e.g. New support request from @name: "@message"'}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </div>

          {error && <div className="error-box">{error}</div>}

          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button className="btn btn-primary" disabled={!canCreate || busy} onClick={create}>
              {busy ? <Loader2 size={14} className="spin" /> : 'Create workflow (starts off)'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
