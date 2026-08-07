import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Bot, Clock, Loader2, Plus, Webhook as WebhookIcon, X, Zap } from 'lucide-react';
import { api } from '../lib/api';
import { avatarStyle, cronToEnglish, initials, kebab, prettyName, projectMembers } from '../lib/model';
import type { Manifest, ManifestResourceRef } from '../lib/types';

function AddAssistantModal({ project, onClose }: { project: Manifest; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'existing' | 'new'>('existing');
  const [newName, setNewName] = useState('');
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents });

  const memberIds = new Set(
    projectMembers(project).assistants.map((r) => `${r.namespace}/${r.name}`),
  );
  const candidates = (agents ?? []).filter((a) => a.is_active && !memberIds.has(`${a.namespace}/${a.name}`));

  const addMember = useMutation({
    mutationFn: (ref: ManifestResourceRef) =>
      api.updateProject(project.namespace, project.name, {
        required_resources: [...(project.required_resources ?? []), ref],
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['project', project.namespace, project.name] }),
  });

  const createNew = useMutation({
    mutationFn: async () => {
      // New assistants land in the project's namespace by convention.
      const agent = await api.createAgent({ namespace: project.namespace, name: kebab(newName) });
      await api.updateProject(project.namespace, project.name, {
        required_resources: [
          ...(project.required_resources ?? []),
          { type: 'agent', namespace: agent.namespace, name: agent.name },
        ],
      });
      return agent;
    },
    onSuccess: (agent) => {
      queryClient.invalidateQueries();
      navigate(`/projects/${project.namespace}/${project.name}/assistants/${agent.namespace}/${agent.name}`);
    },
  });

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
          <h2 style={{ fontSize: 16 }}>Add an assistant</h2>
          <button className="kebab" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
          <button
            className={mode === 'existing' ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
            onClick={() => setMode('existing')}
          >
            Existing
          </button>
          <button
            className={mode === 'new' ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
            onClick={() => setMode('new')}
          >
            New assistant
          </button>
        </div>

        {mode === 'existing' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 300, overflowY: 'auto' }}>
            {candidates.length === 0 && (
              <p style={{ fontSize: 13, color: 'var(--muted)' }}>Every assistant in the workspace is already on this project.</p>
            )}
            {candidates.map((a) => (
              <button
                key={a.id}
                className="pop-item"
                disabled={addMember.isPending}
                onClick={() =>
                  addMember.mutate({ type: 'agent', namespace: a.namespace, name: a.name }, { onSuccess: onClose })
                }
              >
                <span className="row-logo" style={{ width: 28, height: 28, fontSize: 11, ...avatarStyle(a.name) }}>
                  {initials(a.name)}
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontWeight: 600, color: 'var(--ink)', fontSize: 13 }}>{prettyName(a.name)}</span>
                  <span style={{ display: 'block', fontSize: 11.5, color: 'var(--faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {a.description || `${a.namespace}/${a.name}`}
                  </span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (newName.trim()) createNew.mutate();
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
          >
            <div className="field">
              <label>Assistant name</label>
              <input className="input" placeholder="e.g. Triage assistant" value={newName} onChange={(e) => setNewName(e.target.value)} autoFocus />
            </div>
            {createNew.isError && <div className="error-box">{(createNew.error as Error).message}</div>}
            <button className="btn btn-primary" disabled={!newName.trim() || createNew.isPending} style={{ justifyContent: 'center' }}>
              {createNew.isPending ? <Loader2 size={14} className="spin" /> : 'Create & open editor'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export function ProjectHome() {
  const { ns, name } = useParams<{ ns: string; name: string }>();
  const [showAdd, setShowAdd] = useState(false);

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', ns, name],
    queryFn: () => api.getProject(ns!, name!),
    enabled: !!ns && !!name,
  });
  const { data: schedules } = useQuery({ queryKey: ['schedules'], queryFn: api.listSchedules });
  const { data: webhooks } = useQuery({ queryKey: ['webhooks'], queryFn: api.listWebhooks });
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: api.listAgents });

  if (isLoading || !project) {
    return (
      <div className="empty">
        <Loader2 size={20} className="spin" style={{ margin: '20px auto', display: 'block' }} />
      </div>
    );
  }

  const members = projectMembers(project);

  return (
    <div className="page" style={{ width: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
        <Link to="/projects" style={{ color: 'var(--muted)', display: 'flex' }}>
          <ArrowLeft size={18} />
        </Link>
        <h1 style={{ fontSize: 20 }}>{prettyName(project.name)}</h1>
      </div>
      {project.description && (
        <p style={{ color: 'var(--muted)', margin: '0 0 24px 30px', fontSize: 13.5 }}>{project.description}</p>
      )}

      {/* Assistants */}
      <section className="card" style={{ marginTop: 16 }}>
        <div className="card-head" style={{ justifyContent: 'space-between', paddingBottom: 4 }}>
          <span className="card-title">Assistants</span>
        </div>
        <div className="rows">
          {members.assistants.length === 0 && (
            <p style={{ padding: '10px 20px', margin: 0, fontSize: 13, color: 'var(--muted)' }}>
              No assistants on this project yet.
            </p>
          )}
          {members.assistants.map((ref) => {
            const agent = agents?.find((a) => a.namespace === ref.namespace && a.name === ref.name);
            return (
              <Link
                key={`${ref.namespace}/${ref.name}`}
                to={`/projects/${project.namespace}/${project.name}/assistants/${ref.namespace}/${ref.name}`}
                className="row"
                style={{ color: 'inherit', textDecoration: 'none' }}
              >
                <span className="row-logo" style={agent ? avatarStyle(agent.name) : undefined}>
                  {agent ? initials(agent.name) : <Bot size={15} />}
                </span>
                <span className="row-main">
                  <span className="row-name">{prettyName(ref.name)}</span>
                  <span className="row-desc" style={{ display: 'block' }}>
                    {agent ? agent.description || 'No description yet' : 'Missing from the workspace'}
                  </span>
                </span>
                <span className="row-side">
                  {!agent && <span className="pill pill-bad">Missing</span>}
                  {agent && <span className="pill">Open editor →</span>}
                </span>
              </Link>
            );
          })}
          <button className="add-row" onClick={() => setShowAdd(true)}>
            <Plus size={15} /> Add an assistant
          </button>
        </div>
      </section>

      {/* Workflows */}
      <section className="card" style={{ marginTop: 18 }}>
        <div className="card-head" style={{ paddingBottom: 4 }}>
          <span className="card-title">Workflows</span>
          <span className="card-hint">what runs automatically</span>
        </div>
        <div className="rows">
          {members.workflows.length === 0 && (
            <p style={{ padding: '10px 20px 16px', margin: 0, fontSize: 13, color: 'var(--muted)' }}>
              No workflows on this project yet.
            </p>
          )}
          {members.workflows.map((ref) => {
            const kind = ref.type.replace(/s$/, '');
            const schedule = kind === 'schedule' ? schedules?.find((s) => s.name === ref.name) : undefined;
            const webhook = kind === 'webhook' ? webhooks?.find((w) => w.path === ref.name) : undefined;
            const exists = !!(schedule || webhook);
            const href = schedule
              ? `/projects/${project.namespace}/${project.name}/workflows/schedule/${encodeURIComponent(schedule.name)}`
              : webhook
                ? `/projects/${project.namespace}/${project.name}/workflows/webhook/${webhook.path}`
                : undefined;
            return (
              <Link
                key={`${ref.type}:${ref.name}`}
                to={href ?? '#'}
                className="row"
                style={{ color: 'inherit', textDecoration: 'none', pointerEvents: href ? undefined : 'none' }}
              >
                <span className="row-logo" style={{ background: 'var(--warn-soft)', color: 'var(--warn)' }}>
                  {kind === 'schedule' ? <Clock size={15} /> : <WebhookIcon size={15} />}
                </span>
                <span className="row-main">
                  <span className="row-name">
                    {schedule
                      ? `Runs ${cronToEnglish(schedule.cron_expression)}`
                      : webhook
                        ? webhook.description || `When ${webhook.http_method} /webhooks/${webhook.path} is called`
                        : prettyName(ref.name)}
                  </span>
                  <span className="row-desc" style={{ display: 'block' }}>
                    {schedule
                      ? schedule.description || schedule.name
                      : webhook
                        ? `/webhooks/${webhook.path}${webhook.requires_auth ? '' : ' · public'}`
                        : 'Missing from the workspace'}
                  </span>
                </span>
                <span className="row-side">
                  {!exists && <span className="pill pill-bad">Missing</span>}
                  {schedule && <span className={schedule.is_active ? 'pill pill-good' : 'pill'}>{schedule.is_active ? 'On' : 'Off'}</span>}
                  {webhook && <span className={webhook.is_active ? 'pill pill-good' : 'pill'}>{webhook.is_active ? 'On' : 'Off'}</span>}
                </span>
              </Link>
            );
          })}
          <Link className="add-row" to={`/projects/${project.namespace}/${project.name}/workflows/new`} style={{ textDecoration: 'none' }}>
            <Plus size={15} /> Add a workflow
          </Link>
        </div>
      </section>

      <p style={{ fontSize: 12, color: 'var(--faint)', marginTop: 16, display: 'flex', alignItems: 'center', gap: 6 }}>
        <Zap size={13} /> Removing something here only takes it off the project — it never deletes the resource.
      </p>

      {showAdd && <AddAssistantModal project={project} onClose={() => setShowAdd(false)} />}
    </div>
  );
}
