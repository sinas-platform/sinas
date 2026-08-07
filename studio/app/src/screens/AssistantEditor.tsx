import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ArrowLeft,
  BookOpen,
  Brain,
  Loader2,
  Plug,
  Plus,
  Sparkles,
  X,
} from 'lucide-react';
import { api } from '../lib/api';
import {
  avatarStyle,
  cronToEnglish,
  initials,
  inputRowsToSchema,
  memoryRef,
  ownFilesRef,
  prettyName,
  schemaToInputRows,
  splitRef,
  triggersForAgent,
  type InputRow,
} from '../lib/model';
import type { AgentUpdate } from '../lib/types';
import { TestChat } from '../components/TestChat';

import { SetupStudioLink, useStudioRuntimeInstalled } from '../components/SetupStudio';

function Popover({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <>
      <div style={{ position: 'fixed', inset: 0, zIndex: 30 }} onClick={onClose} />
      <div className="pop" style={{ left: 20, marginTop: 6, padding: 8 }}>
        {children}
      </div>
    </>
  );
}

export function AssistantEditor() {
  const { pns, pname, ans, aname } = useParams<{ pns: string; pname: string; ans: string; aname: string }>();
  const queryClient = useQueryClient();

  const { data: agent, isLoading } = useQuery({
    queryKey: ['agent', ans, aname],
    queryFn: () => api.getAgent(ans!, aname!),
    enabled: !!ans && !!aname,
  });
  const connectors = useQuery({ queryKey: ['connectors'], queryFn: api.listConnectors }).data ?? [];
  const skills = useQuery({ queryKey: ['skills'], queryFn: api.listSkills }).data ?? [];
  const collections = useQuery({ queryKey: ['collections'], queryFn: api.listCollections }).data ?? [];
  const stores = useQuery({ queryKey: ['stores'], queryFn: api.listStores }).data ?? [];
  const secrets = useQuery({ queryKey: ['secrets'], queryFn: api.listSecrets, retry: false }).data;
  const schedules = useQuery({ queryKey: ['schedules'], queryFn: api.listSchedules }).data ?? [];
  const webhooks = useQuery({ queryKey: ['webhooks'], queryFn: api.listWebhooks }).data ?? [];
  const copilotAvailable = useStudioRuntimeInstalled();

  const patch = useMutation({
    mutationFn: (update: AgentUpdate) => api.updateAgent(ans!, aname!, update),
    onSuccess: (updated) => queryClient.setQueryData(['agent', ans, aname], updated),
  });

  // ---- Instructions: local draft with debounced autosave ----
  const [instructions, setInstructions] = useState('');
  const loadedFor = useRef<string | null>(null);
  useEffect(() => {
    const key = agent ? `${agent.namespace}/${agent.name}` : null;
    if (agent && loadedFor.current !== key) {
      setInstructions(agent.system_prompt ?? '');
      loadedFor.current = key;
    }
  }, [agent]);
  const saveTimer = useRef<number | undefined>(undefined);
  const onInstructionsChange = (value: string) => {
    setInstructions(value);
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => patch.mutate({ system_prompt: value }), 800);
  };

  const [openPicker, setOpenPicker] = useState<'guidance' | 'tools' | 'storage' | null>(null);
  const [inputModal, setInputModal] = useState<{ row: InputRow; isNew: boolean } | null>(null);
  const [assistBusy, setAssistBusy] = useState(false);
  const [assistError, setAssistError] = useState<string | null>(null);

  const inputRows = useMemo(() => schemaToInputRows(agent?.input_schema), [agent]);

  if (isLoading || !agent) {
    return (
      <div className="empty">
        <Loader2 size={20} className="spin" style={{ margin: '20px auto', display: 'block' }} />
      </div>
    );
  }

  // ---- Derived data for sections & summary (all from stored fields) ----
  const enabledSkillRefs = new Set(agent.enabled_skills.map((s) => s.skill));
  const enabledConnectorRefs = new Set(agent.enabled_connectors.map((c) => c.connector));
  const enabledCollectionRefs = new Set(agent.enabled_collections.map((c) => c.collection));
  const enabledStoreRefs = new Set(agent.enabled_stores.map((s) => s.store));
  const hasOwnFiles = enabledCollectionRefs.has(ownFilesRef(agent));
  const hasMemory = enabledStoreRefs.has(memoryRef(agent));
  const { scheduleHits, webhookHits } = triggersForAgent(agent, schedules, webhooks);

  const toolNames = agent.enabled_connectors.map((c) => prettyName(splitRef(c.connector).name));
  const fileNames = agent.enabled_collections.map((c) =>
    c.collection === ownFilesRef(agent) ? 'its own files' : prettyName(splitRef(c.collection).name),
  );

  const saveInputRows = (rows: InputRow[]) => patch.mutate({ input_schema: inputRowsToSchema(rows) });

  const improveInstructions = async () => {
    // On-demand AI, per the honesty rule: only offered when studio/copilot exists.
    setAssistBusy(true);
    setAssistError(null);
    try {
      const res = await api.invokeAgent('studio', 'copilot', {
        message:
          `Mode: improve-instructions.\nRewrite the following assistant instructions to be clearer and more ` +
          `complete without changing their intent. Reply with ONLY the improved instructions text.\n\n${instructions}`,
      });
      onInstructionsChange(res.reply.trim());
    } catch (e) {
      setAssistError(e instanceof Error ? e.message : 'AI assist failed');
    } finally {
      setAssistBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* Header */}
      <div className="topbar" style={{ height: 50 }}>
        <Link to={`/projects/${pns}/${pname}`} style={{ color: 'var(--muted)', display: 'flex' }}>
          <ArrowLeft size={17} />
        </Link>
        <span className="row-logo" style={{ width: 28, height: 28, fontSize: 11, ...avatarStyle(agent.name) }}>
          {initials(agent.name)}
        </span>
        <span style={{ fontWeight: 700, color: 'var(--ink)', fontSize: 15 }}>{prettyName(agent.name)}</span>
        <div style={{ marginLeft: 'auto' }}>
          {patch.isPending ? (
            <span className="saving">Saving…</span>
          ) : patch.isError ? (
            <span style={{ color: 'var(--bad)', fontSize: 12.5 }}>Save failed — retrying on next edit</span>
          ) : (
            <span className="saved">
              <span className="dot" /> Saved
            </span>
          )}
        </div>
      </div>

      {/* Capability summary — every clause derives from a stored field */}
      <div className="summary">
        {fileNames.length > 0 && (
          <>
            Reads <span className="cap">{fileNames.join(', ')}</span> ·{' '}
          </>
        )}
        {toolNames.length > 0 && (
          <>
            can use <span className="cap">{toolNames.join(', ')}</span> ·{' '}
          </>
        )}
        {hasMemory && <b>remembers between conversations · </b>}
        {inputRows.length > 0 && (
          <>
            asks for <b>{inputRows.map((r) => '@' + r.name).join(', ')}</b> ·{' '}
          </>
        )}
        {scheduleHits.length + webhookHits.length > 0 ? (
          <>
            runs{' '}
            <b>
              {[...scheduleHits.map((s) => cronToEnglish(s.cron_expression)), ...webhookHits.map((w) => `when ${w.description || `/webhooks/${w.path} is called`}`)].join(' and ')}
            </b>
          </>
        ) : (
          <>started manually in chat</>
        )}
      </div>

      <div className="editor-split">
        <main className="editor-scroll">
          {/* Instructions */}
          <section className="card">
            <div className="card-head">
              <span className="card-title">Instructions</span>
              <span className="card-hint">what it should do, in your own words</span>
            </div>
            <div className="card-body">
              <textarea
                className="input"
                style={{ minHeight: 160, fontSize: 14.5 }}
                value={instructions}
                onChange={(e) => onInstructionsChange(e.target.value)}
                placeholder="e.g. You triage incoming support requests. Search the product docs first and answer from them when you can…"
              />

              {/* Guidance (skills) */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 12, position: 'relative' }}>
                <span style={{ fontSize: 12, color: 'var(--faint)', fontWeight: 600 }}>Also follows:</span>
                {agent.enabled_skills.map((s) => (
                  <span key={s.skill} className="skill-chip">
                    <b>{prettyName(splitRef(s.skill).name)}</b>
                    <button
                      className="link-btn"
                      style={{ padding: 0 }}
                      title={s.preload ? 'Applied always — click for on-demand' : 'Retrieved when needed — click for always'}
                      onClick={() =>
                        patch.mutate({
                          enabled_skills: agent.enabled_skills.map((x) =>
                            x.skill === s.skill ? { ...x, preload: !x.preload } : x,
                          ),
                        })
                      }
                    >
                      <span className="mode">{s.preload ? 'always' : 'when needed'}</span>
                    </button>
                    <button
                      className="chip-x"
                      aria-label={`Remove ${s.skill}`}
                      onClick={() => patch.mutate({ enabled_skills: agent.enabled_skills.filter((x) => x.skill !== s.skill) })}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
                <button className="chip-add" onClick={() => setOpenPicker(openPicker === 'guidance' ? null : 'guidance')}>
                  + Add guidance
                </button>
                {openPicker === 'guidance' && (
                  <Popover onClose={() => setOpenPicker(null)}>
                    {skills.filter((s) => !enabledSkillRefs.has(`${s.namespace}/${s.name}`)).length === 0 && (
                      <p style={{ padding: 10, margin: 0, fontSize: 12.5, color: 'var(--muted)' }}>
                        No other guidance modules in the workspace yet.
                      </p>
                    )}
                    {skills
                      .filter((s) => !enabledSkillRefs.has(`${s.namespace}/${s.name}`))
                      .map((s) => (
                        <button
                          key={s.id}
                          className="pop-item"
                          onClick={() => {
                            patch.mutate({
                              enabled_skills: [...agent.enabled_skills, { skill: `${s.namespace}/${s.name}`, preload: false }],
                            });
                            setOpenPicker(null);
                          }}
                        >
                          <span style={{ flex: 1, minWidth: 0 }}>
                            <span style={{ display: 'block', fontWeight: 600, color: 'var(--ink)', fontSize: 13 }}>{prettyName(s.name)}</span>
                            <span style={{ display: 'block', fontSize: 11.5, color: 'var(--faint)' }}>{s.description}</span>
                          </span>
                        </button>
                      ))}
                  </Popover>
                )}
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={!copilotAvailable || assistBusy || !instructions.trim()}
                  title={copilotAvailable ? 'Rewrite for clarity using the workspace copilot' : 'Needs Studio setup — ask your admin to install the studio-runtime package'}
                  onClick={improveInstructions}
                >
                  {assistBusy ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} style={{ color: 'var(--accent)' }} />}
                  Improve with AI
                </button>
                {!copilotAvailable && <SetupStudioLink />}
                {assistError && <span style={{ fontSize: 12, color: 'var(--bad)' }}>{assistError}</span>}
                <span style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--faint)' }}>
                  Changes apply immediately — try them in the test chat →
                </span>
              </div>
            </div>
          </section>

          {/* Tools */}
          <section className="card" style={{ position: 'relative' }}>
            <div className="card-head">
              <span className="card-title">Tools</span>
              <span className="card-hint">what it's allowed to do in other systems</span>
            </div>
            <div className="rows" style={{ marginTop: 10 }}>
              {agent.enabled_connectors.length === 0 && (
                <p style={{ padding: '4px 20px 12px', margin: 0, fontSize: 13, color: 'var(--muted)' }}>No tools yet.</p>
              )}
              {agent.enabled_connectors.map((entry) => {
                const ref = splitRef(entry.connector);
                const connector = connectors.find((c) => c.namespace === ref.namespace && c.name === ref.name);
                const secretName = connector?.auth?.secret;
                const needsKey = secrets !== undefined && !!secretName && !secrets.some((s) => s.name === secretName);
                return (
                  <div key={entry.connector}>
                    <div className="row">
                      <span className="row-logo">
                        <Plug size={15} />
                      </span>
                      <span className="row-main">
                        <span className="row-name">{prettyName(ref.name)}</span>
                        <span className="row-desc" style={{ display: 'block' }}>
                          {connector?.description || entry.connector}
                        </span>
                      </span>
                      <span className="row-side">
                        {needsKey ? <span className="pill pill-warn">Needs a key</span> : connector && <span className="pill pill-good">Ready</span>}
                        {!connector && <span className="pill pill-bad">Missing</span>}
                        <button
                          className="chip-x"
                          aria-label={`Remove ${entry.connector}`}
                          onClick={() =>
                            patch.mutate({ enabled_connectors: agent.enabled_connectors.filter((c) => c.connector !== entry.connector) })
                          }
                        >
                          <X size={14} />
                        </button>
                      </span>
                    </div>
                    {connector && connector.operations.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '0 20px 14px 68px' }}>
                        {connector.operations.map((op) => {
                          const on = entry.operations.includes(op.name);
                          return (
                            <button
                              key={op.name}
                              className={`toggle${on ? ' on' : ''}`}
                              title={op.description || op.name}
                              onClick={() =>
                                patch.mutate({
                                  enabled_connectors: agent.enabled_connectors.map((c) =>
                                    c.connector === entry.connector
                                      ? { ...c, operations: on ? c.operations.filter((o) => o !== op.name) : [...c.operations, op.name] }
                                      : c,
                                  ),
                                })
                              }
                            >
                              <span className="sw" /> {prettyName(op.name)}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
              <button className="add-row" onClick={() => setOpenPicker(openPicker === 'tools' ? null : 'tools')}>
                <Plus size={15} /> Add a tool
              </button>
              {openPicker === 'tools' && (
                <Popover onClose={() => setOpenPicker(null)}>
                  {connectors.filter((c) => !enabledConnectorRefs.has(`${c.namespace}/${c.name}`)).length === 0 && (
                    <p style={{ padding: 10, margin: 0, fontSize: 12.5, color: 'var(--muted)' }}>
                      No more connections available. Your admin can add integrations to the workspace.
                    </p>
                  )}
                  {connectors
                    .filter((c) => !enabledConnectorRefs.has(`${c.namespace}/${c.name}`))
                    .map((c) => (
                      <button
                        key={c.id}
                        className="pop-item"
                        onClick={() => {
                          patch.mutate({
                            enabled_connectors: [
                              ...agent.enabled_connectors,
                              { connector: `${c.namespace}/${c.name}`, operations: c.operations.map((o) => o.name) },
                            ],
                          });
                          setOpenPicker(null);
                        }}
                      >
                        <span className="row-logo" style={{ width: 28, height: 28 }}>
                          <Plug size={13} />
                        </span>
                        <span style={{ flex: 1, minWidth: 0 }}>
                          <span style={{ display: 'block', fontWeight: 600, color: 'var(--ink)', fontSize: 13 }}>{prettyName(c.name)}</span>
                          <span style={{ display: 'block', fontSize: 11.5, color: 'var(--faint)' }}>
                            {c.description || c.base_url}
                          </span>
                        </span>
                      </button>
                    ))}
                </Popover>
              )}
            </div>
          </section>

          {/* Files & memory */}
          <section className="card" style={{ position: 'relative' }}>
            <div className="card-head">
              <span className="card-title">Files &amp; memory</span>
              <span className="card-hint">what it can read, keep, and remember</span>
            </div>
            <div className="rows" style={{ marginTop: 10 }}>
              {agent.enabled_collections.map((entry) => {
                const own = entry.collection === ownFilesRef(agent);
                return (
                  <div key={entry.collection} className="row">
                    <span className="row-logo" style={{ background: own ? '#EDE9FE' : 'var(--good-soft)', color: own ? '#7C3AED' : 'var(--good)' }}>
                      <BookOpen size={15} />
                    </span>
                    <span className="row-main">
                      <span className="row-name">{own ? 'Its own file space' : prettyName(splitRef(entry.collection).name)}</span>
                      <span className="row-desc" style={{ display: 'block' }}>
                        {own ? 'Files it saves or receives live here' : 'Shared library'}
                      </span>
                    </span>
                    <span className="row-side">
                      <span className={entry.access === 'readwrite' ? 'pill pill-good' : 'pill'}>
                        {entry.access === 'readwrite' ? 'Read & write' : 'Read only'}
                      </span>
                      <button
                        className="chip-x"
                        aria-label={`Remove ${entry.collection}`}
                        onClick={() =>
                          patch.mutate({ enabled_collections: agent.enabled_collections.filter((c) => c.collection !== entry.collection) })
                        }
                      >
                        <X size={14} />
                      </button>
                    </span>
                  </div>
                );
              })}
              {agent.enabled_stores.map((entry) => {
                const own = entry.store === memoryRef(agent);
                return (
                  <div key={entry.store} className="row">
                    <span className="row-logo" style={{ background: '#CCFBF1', color: '#0F766E' }}>
                      <Brain size={15} />
                    </span>
                    <span className="row-main">
                      <span className="row-name">{own ? 'Conversation memory' : prettyName(splitRef(entry.store).name)}</span>
                      <span className="row-desc" style={{ display: 'block' }}>
                        {own ? 'Remembers useful facts between conversations' : 'Shared memory'}
                      </span>
                    </span>
                    <span className="row-side">
                      <span className={entry.access === 'readwrite' ? 'pill pill-good' : 'pill'}>
                        {entry.access === 'readwrite' ? 'Read & write' : 'Read only'}
                      </span>
                      <button
                        className="chip-x"
                        aria-label={`Remove ${entry.store}`}
                        onClick={() => patch.mutate({ enabled_stores: agent.enabled_stores.filter((s) => s.store !== entry.store) })}
                      >
                        <X size={14} />
                      </button>
                    </span>
                  </div>
                );
              })}
              {agent.enabled_collections.length === 0 && agent.enabled_stores.length === 0 && (
                <p style={{ padding: '4px 20px 12px', margin: 0, fontSize: 13, color: 'var(--muted)' }}>
                  No files or memory yet — it only knows what's in the conversation.
                </p>
              )}
              <button className="add-row" onClick={() => setOpenPicker(openPicker === 'storage' ? null : 'storage')}>
                <Plus size={15} /> Add files or memory
              </button>
              {openPicker === 'storage' && (
                <Popover onClose={() => setOpenPicker(null)}>
                  {!hasOwnFiles && (
                    <button
                      className="pop-item"
                      onClick={async () => {
                        setOpenPicker(null);
                        // Create-if-missing, then attach read & write.
                        const { namespace, name } = splitRef(ownFilesRef(agent));
                        await api.createCollection({ namespace, name }).catch(() => undefined);
                        patch.mutate({
                          enabled_collections: [...agent.enabled_collections, { collection: ownFilesRef(agent), access: 'readwrite' }],
                        });
                      }}
                    >
                      <span className="row-logo" style={{ width: 28, height: 28, background: '#EDE9FE', color: '#7C3AED' }}>
                        <BookOpen size={13} />
                      </span>
                      <span style={{ flex: 1 }}>
                        <span style={{ display: 'block', fontWeight: 600, color: 'var(--ink)', fontSize: 13 }}>Its own file space</span>
                        <span style={{ display: 'block', fontSize: 11.5, color: 'var(--faint)' }}>Read & write, created automatically</span>
                      </span>
                    </button>
                  )}
                  {!hasMemory && (
                    <button
                      className="pop-item"
                      onClick={async () => {
                        setOpenPicker(null);
                        const { namespace, name } = splitRef(memoryRef(agent));
                        await api
                          .createStore({ namespace, name, description: `Conversation memory for ${agent.namespace}/${agent.name}` })
                          .catch(() => undefined);
                        patch.mutate({
                          enabled_stores: [...agent.enabled_stores, { store: memoryRef(agent), access: 'readwrite' }],
                        });
                      }}
                    >
                      <span className="row-logo" style={{ width: 28, height: 28, background: '#CCFBF1', color: '#0F766E' }}>
                        <Brain size={13} />
                      </span>
                      <span style={{ flex: 1 }}>
                        <span style={{ display: 'block', fontWeight: 600, color: 'var(--ink)', fontSize: 13 }}>Conversation memory</span>
                        <span style={{ display: 'block', fontSize: 11.5, color: 'var(--faint)' }}>Remembers facts between conversations</span>
                      </span>
                    </button>
                  )}
                  {collections
                    .filter((c) => !enabledCollectionRefs.has(`${c.namespace}/${c.name}`) && `${c.namespace}/${c.name}` !== ownFilesRef(agent))
                    .map((c) => (
                      <button
                        key={c.id}
                        className="pop-item"
                        onClick={() => {
                          patch.mutate({
                            enabled_collections: [
                              ...agent.enabled_collections,
                              { collection: `${c.namespace}/${c.name}`, access: 'readonly' },
                            ],
                          });
                          setOpenPicker(null);
                        }}
                      >
                        <span className="row-logo" style={{ width: 28, height: 28, background: 'var(--good-soft)', color: 'var(--good)' }}>
                          <BookOpen size={13} />
                        </span>
                        <span style={{ flex: 1 }}>
                          <span style={{ display: 'block', fontWeight: 600, color: 'var(--ink)', fontSize: 13 }}>{prettyName(c.name)}</span>
                          <span style={{ display: 'block', fontSize: 11.5, color: 'var(--faint)' }}>Shared library · read only</span>
                        </span>
                      </button>
                    ))}
                  {stores
                    .filter((s) => !enabledStoreRefs.has(`${s.namespace}/${s.name}`) && `${s.namespace}/${s.name}` !== memoryRef(agent))
                    .map((s) => (
                      <button
                        key={s.id}
                        className="pop-item"
                        onClick={() => {
                          patch.mutate({
                            enabled_stores: [...agent.enabled_stores, { store: `${s.namespace}/${s.name}`, access: 'readonly' }],
                          });
                          setOpenPicker(null);
                        }}
                      >
                        <span className="row-logo" style={{ width: 28, height: 28 }}>
                          <Archive size={13} />
                        </span>
                        <span style={{ flex: 1 }}>
                          <span style={{ display: 'block', fontWeight: 600, color: 'var(--ink)', fontSize: 13 }}>{prettyName(s.name)}</span>
                          <span style={{ display: 'block', fontSize: 11.5, color: 'var(--faint)' }}>Shared memory · read only</span>
                        </span>
                      </button>
                    ))}
                </Popover>
              )}
            </div>
          </section>

          {/* Inputs */}
          <section className="card">
            <div className="card-head">
              <span className="card-title">Inputs</span>
              <span className="card-hint">values provided each time it starts</span>
            </div>
            <div className="rows" style={{ marginTop: 8 }}>
              {inputRows.map((row) => (
                <div key={row.name} className="row" style={{ padding: '11px 20px' }}>
                  <span className="token">@{row.name}</span>
                  <span className="row-main" style={{ fontSize: 12.5, color: 'var(--muted)' }}>
                    {row.description || '—'}
                  </span>
                  <span style={{ fontSize: 11.5, color: 'var(--faint)', flexShrink: 0 }}>
                    {row.kind === 'choice' ? `Choice: ${row.choices.join(' / ')}` : 'Text'}
                    {row.required ? ' · required' : ' · optional'}
                  </span>
                  <button className="link-btn" onClick={() => setInputModal({ row, isNew: false })}>
                    Edit
                  </button>
                  <button
                    className="chip-x"
                    aria-label={`Remove @${row.name}`}
                    onClick={() => saveInputRows(inputRows.filter((r) => r.name !== row.name))}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
              <button
                className="add-row"
                onClick={() => setInputModal({ row: { name: '', description: '', kind: 'text', choices: [], required: false }, isNew: true })}
              >
                <Plus size={15} /> Add an input
              </button>
            </div>
            <p style={{ padding: '0 20px 14px', margin: 0, fontSize: 12, color: 'var(--faint)' }}>
              Inputs are filled in when a chat starts, or supplied automatically by a workflow. Reference them in the
              instructions as <span className="token">@name</span>.
            </p>
          </section>

          {/* Trigger footnotes */}
          {(scheduleHits.length > 0 || webhookHits.length > 0) && (
            <aside style={{ border: '1px dashed var(--line)', borderRadius: 12, padding: '14px 20px', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {scheduleHits.map((s) => (
                <div key={s.id} style={{ fontSize: 13, color: 'var(--muted)' }}>
                  ◷ Runs <b style={{ color: 'var(--ink)' }}>{cronToEnglish(s.cron_expression)}</b>
                  {s.description ? ` — ${s.description}` : ''}
                </div>
              ))}
              {webhookHits.map((w) => (
                <div key={w.id} style={{ fontSize: 13, color: 'var(--muted)' }}>
                  ⚡ Runs when <b style={{ color: 'var(--ink)' }}>{w.description || `/webhooks/${w.path} is called`}</b>
                </div>
              ))}
            </aside>
          )}
        </main>

        <TestChat agent={agent} />
      </div>

      {inputModal && (
        <InputModal
          initial={inputModal.row}
          isNew={inputModal.isNew}
          existingNames={inputRows.map((r) => r.name)}
          onSave={(row) => {
            const rows = inputModal.isNew
              ? [...inputRows, row]
              : inputRows.map((r) => (r.name === inputModal.row.name ? row : r));
            saveInputRows(rows);
            setInputModal(null);
          }}
          onClose={() => setInputModal(null)}
        />
      )}
    </div>
  );
}

function InputModal({
  initial,
  isNew,
  existingNames,
  onSave,
  onClose,
}: {
  initial: InputRow;
  isNew: boolean;
  existingNames: string[];
  onSave: (row: InputRow) => void;
  onClose: () => void;
}) {
  const [row, setRow] = useState<InputRow>(initial);
  const [choicesText, setChoicesText] = useState(initial.choices.join(', '));

  const name = row.name.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  const duplicate = isNew && existingNames.includes(name);

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <h2 style={{ fontSize: 16 }}>{isNew ? 'New input' : `Edit @${initial.name}`}</h2>
          <button className="kebab" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!name || duplicate) return;
            onSave({
              ...row,
              name,
              choices: row.kind === 'choice' ? choicesText.split(',').map((c) => c.trim()).filter(Boolean) : [],
            });
          }}
          style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
        >
          <div className="field">
            <label>Name (becomes @{name || '…'})</label>
            <input className="input" value={row.name} onChange={(e) => setRow({ ...row, name: e.target.value })} autoFocus={isNew} />
            {duplicate && <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--bad)' }}>An input with this name already exists.</p>}
          </div>
          <div className="field">
            <label>What is it? (shown when someone fills it in)</label>
            <input className="input" value={row.description} onChange={(e) => setRow({ ...row, description: e.target.value })} />
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <div className="field" style={{ flex: 1 }}>
              <label>Type</label>
              <select className="input" value={row.kind} onChange={(e) => setRow({ ...row, kind: e.target.value as InputRow['kind'] })}>
                <option value="text">Text</option>
                <option value="choice">Choice</option>
              </select>
            </div>
            <div className="field" style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: 6 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, margin: 0 }}>
                <input type="checkbox" checked={row.required} onChange={(e) => setRow({ ...row, required: e.target.checked })} />
                Required
              </label>
            </div>
          </div>
          {row.kind === 'choice' && (
            <div className="field">
              <label>Choices (comma-separated)</label>
              <input className="input" placeholder="Free, Pro, Enterprise" value={choicesText} onChange={(e) => setChoicesText(e.target.value)} />
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="btn btn-primary" disabled={!name || duplicate}>
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
