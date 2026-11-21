import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Info, Loader2, Plus, Trash2 } from 'lucide-react';
import { errorMessage, useToast } from '@sinas/ui';
import { deleteManifest, getManifest, getManifestStatus, listCalls, listIdentityProviders, updateManifest } from '../lib/api';
import { endpointUrl } from '../lib/links';
import {
  CLIENTS, TOOL_NAME_PATTERN, WINDOWS, connectSnippet, defaultToolName, duplicateNames, relativeTime, type ClientId,
} from '../lib/tools';
import type { ExposedTool, Manifest, SwitchboardLimits } from '../lib/types';
import { CopyButton } from '../components/CopyButton';
import { Dialog } from '../components/Dialog';
import { Pill, Tag } from '../components/Status';
import { ToolPicker } from '../components/ToolPicker';

type LimitMode = 'default' | 'custom' | 'none';

interface Draft {
  description: string;
  tools: ExposedTool[];
  limitMode: LimitMode;
  maxCalls: string;
  windowSeconds: number;
}

function draftOf(m: Manifest): Draft {
  const l = m.switchboard_limits;
  return {
    description: m.description ?? '',
    tools: m.exposed_tools.map((t) => ({ ...t, tool_name: t.tool_name ?? '', description: t.description ?? '' })),
    limitMode: !l ? 'default' : l.max_calls === 0 ? 'none' : 'custom',
    maxCalls: l && l.max_calls > 0 ? String(l.max_calls) : '120',
    windowSeconds: l?.window_seconds ?? 60,
  };
}

function limitsOf(d: Draft): SwitchboardLimits | null {
  if (d.limitMode === 'default') return null;
  if (d.limitMode === 'none') return { max_calls: 0, window_seconds: d.windowSeconds };
  return { max_calls: Number(d.maxCalls), window_seconds: d.windowSeconds };
}

function toolsOf(d: Draft): ExposedTool[] {
  return d.tools.map((t) => ({
    type: t.type,
    namespace: t.namespace,
    name: t.name,
    tool_name: t.tool_name?.trim() || null,
    description: t.description?.trim() || null,
  }));
}

const NOTES: Record<ClientId, string> = {
  claude: 'People sign in with your identity provider the first time. Each gets only the tools their role allows, and calls are logged under their name.',
  chatgpt: 'ChatGPT signs people in with your identity provider. Workspace admins can add the connector once for the whole team.',
  cursor: 'Cursor opens your identity provider’s sign-in on first use. There is no key to hand out or rotate.',
  apikey: 'For servers and scripts without a person behind them. The key carries its own role, so give it only what the job needs.',
};

export function SwitchboardDetail() {
  const { namespace = '', name = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showSuccess } = useToast();

  const manifest = useQuery({ queryKey: ['manifest', namespace, name], queryFn: () => getManifest(namespace, name) });
  const status = useQuery({ queryKey: ['status', namespace, name], queryFn: () => getManifestStatus(namespace, name) });
  const recent = useQuery({ queryKey: ['calls', 'recent', namespace, name], queryFn: () => listCalls({ manifest: `${namespace}/${name}`, limit: 6 }) });
  const idps = useQuery({ queryKey: ['identity-providers'], queryFn: listIdentityProviders, retry: false });

  const [draft, setDraft] = useState<Draft | null>(null);
  const [picking, setPicking] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [client, setClient] = useState<ClientId>('claude');
  const [saveError, setSaveError] = useState('');

  // A fresh copy from the server replaces the draft only when the draft held
  // no unsaved edits (switching the switchboard on or off refetches it too).
  const lastSaved = useRef<string | null>(null);
  useEffect(() => {
    if (!manifest.data) return;
    const next = draftOf(manifest.data);
    setDraft((current) => (!current || JSON.stringify(current) === lastSaved.current ? next : current));
    lastSaved.current = JSON.stringify(next);
  }, [manifest.data]);

  const saved = manifest.data ? draftOf(manifest.data) : null;
  const dirty = !!draft && !!saved && JSON.stringify(draft) !== JSON.stringify(saved);

  const problems = useMemo(() => {
    if (!draft) return [];
    const out: string[] = [];
    draft.tools.forEach((t) => {
      if (t.tool_name && !TOOL_NAME_PATTERN.test(t.tool_name.trim())) {
        out.push(`“${t.tool_name}” is not a valid tool name: use up to 64 letters, digits, - or _.`);
      }
    });
    duplicateNames(toolsOf(draft)).forEach((n) => out.push(`Two tools would both be called “${n}”. Rename one.`));
    if (draft.limitMode === 'custom' && !(Number(draft.maxCalls) >= 1 && Number.isInteger(Number(draft.maxCalls)))) {
      out.push('The rate limit needs a whole number of calls, 1 or more.');
    }
    return out;
  }, [draft]);

  const missing = useMemo(
    () => new Set((status.data?.resources.missing ?? []).map((r) => `${r.type}:${r.namespace}/${r.name}`)),
    [status.data],
  );

  const save = useMutation({
    mutationFn: () => updateManifest(namespace, name, {
      description: draft!.description.trim() || null,
      exposed_tools: toolsOf(draft!),
      switchboard_limits: limitsOf(draft!),
    }),
    onSuccess: (m) => {
      setSaveError('');
      queryClient.setQueryData(['manifest', namespace, name], m);
      queryClient.invalidateQueries({ queryKey: ['manifests'] });
      queryClient.invalidateQueries({ queryKey: ['status', namespace, name] });
      showSuccess('Switchboard saved');
    },
    onError: (err) => setSaveError(errorMessage(err)),
  });

  const toggleActive = useMutation({
    mutationFn: (active: boolean) => updateManifest(namespace, name, { is_active: active }),
    onSuccess: (m) => {
      queryClient.setQueryData(['manifest', namespace, name], m);
      queryClient.invalidateQueries({ queryKey: ['manifests'] });
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteManifest(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['manifests'] });
      navigate('/switchboards');
    },
  });

  if (manifest.isLoading || !draft) {
    return manifest.isError
      ? <p className="text-gray-400">This switchboard could not be loaded. <Link to="/switchboards" className="text-orange-300">Back to switchboards</Link></p>
      : <div role="status" className="flex items-center gap-2 py-10 text-gray-400"><Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> Loading</div>;
  }
  const m = manifest.data!;
  const url = endpointUrl(m.namespace, m.name);
  const update = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const updateTool = (i: number, patch: Partial<ExposedTool>) =>
    update({ tools: draft.tools.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
  const missingCount = status.data?.resources.missing.length ?? 0;
  const hasIdp = (idps.data?.length ?? 0) > 0;

  return (
    <div>
      <nav aria-label="Breadcrumb" className="mb-4 flex gap-2 text-[13px] text-gray-400">
        <Link to="/switchboards" className="text-orange-300 hover:text-orange-200">Switchboards</Link>
        <span aria-hidden="true">/</span>
        <span>{m.namespace}/{m.name}</span>
      </nav>

      <header className="mb-7 flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 max-w-2xl flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[28px] font-bold leading-tight"><span className="font-medium text-gray-400">{m.namespace}/</span>{m.name}</h1>
            {!m.is_active ? <Pill tone="gray">Off</Pill>
              : missingCount > 0 ? <Pill tone="amber">{missingCount} missing</Pill>
              : status.data ? <Pill tone="green">Ready</Pill> : null}
          </div>
          <label htmlFor="sb-description" className="sr-only">Description</label>
          <textarea
            id="sb-description"
            value={draft.description}
            onChange={(e) => update({ description: e.target.value })}
            rows={1}
            placeholder="Add a description"
            className="w-full resize-none rounded-md border border-transparent bg-transparent px-0 py-1 text-[15px] text-gray-400 hover:border-line focus:border-orange-500 focus:px-2 focus:outline-none"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <label className="flex min-h-11 items-center gap-2 rounded-lg border border-line px-3 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={toggleActive.isPending ? toggleActive.variables : m.is_active}
              disabled={toggleActive.isPending}
              onChange={(e) => toggleActive.mutate(e.target.checked)}
              className="h-[18px] w-[18px] accent-orange-600"
            />
            Live
          </label>
          <button type="button" onClick={() => setConfirmDelete(true)} className="btn btn-secondary min-h-11" aria-label="Delete switchboard">
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
          <button type="button" onClick={() => setDraft(saved)} disabled={!dirty} className="btn btn-secondary min-h-11 disabled:opacity-50">Discard</button>
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={!dirty || problems.length > 0 || save.isPending}
            className="btn btn-primary min-h-11 disabled:opacity-50"
          >
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />} Save changes
          </button>
        </div>
      </header>

      {(problems.length > 0 || saveError) && (
        <div role="alert" className="mb-6 rounded-lg border border-red-800/40 bg-red-900/20 px-4 py-3 text-sm text-red-300">
          {[...problems, ...(saveError ? [saveError] : [])].map((p) => <p key={p}>{p}</p>)}
        </div>
      )}

      <div className="flex flex-wrap items-start gap-6">
        <section aria-labelledby="tools-heading" className="flex min-w-0 flex-[999_1_560px] flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-col gap-1">
              <h2 id="tools-heading" className="text-[17px] font-semibold">Tools</h2>
              <span className="text-[13px] text-gray-400">What an agent sees when it connects. Callers only get the tools their role may run.</span>
            </div>
            <button type="button" onClick={() => setPicking(true)} className="btn min-h-10 border border-orange-500/40 bg-orange-500/10 text-orange-300 hover:bg-orange-500/20">
              <Plus className="h-4 w-4" aria-hidden="true" /> Add tools
            </button>
          </div>

          {draft.tools.length === 0 ? (
            <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center text-sm text-gray-400">
              No tools yet. Add the queries, functions and pipelines agents may call.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-line bg-surface-1">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-gray-400">
                    <th scope="col" className="px-4 py-2.5 font-medium">Tool name</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Runs</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Description the agent reads</th>
                    <th scope="col" className="px-4 py-2.5 font-medium"><span className="sr-only">Remove</span></th>
                  </tr>
                </thead>
                <tbody>
                  {draft.tools.map((t, i) => {
                    const ref = `${t.namespace}/${t.name}`;
                    const isMissing = missing.has(`${t.type}:${ref}`);
                    return (
                      <tr key={`${t.type}:${ref}`} className="border-b border-line-soft align-top last:border-0">
                        <td className="px-4 py-3">
                          <label className="sr-only" htmlFor={`tn-${i}`}>Tool name for {ref}</label>
                          <input
                            id={`tn-${i}`}
                            value={t.tool_name ?? ''}
                            onChange={(e) => updateTool(i, { tool_name: e.target.value })}
                            placeholder={defaultToolName(t.namespace, t.name)}
                            className="input mono h-10 min-w-[180px] text-[13px]"
                          />
                        </td>
                        <td className="whitespace-nowrap px-4 py-3">
                          <Tag>{t.type}</Tag>
                          <div className="mono mt-1.5 text-xs text-gray-400">{ref}</div>
                          {isMissing && <div className="mt-1.5 text-xs text-amber-400">Missing or not servable</div>}
                        </td>
                        <td className="min-w-[240px] px-4 py-3">
                          <label className="sr-only" htmlFor={`td-${i}`}>Description for {ref}</label>
                          <textarea
                            id={`td-${i}`}
                            value={t.description ?? ''}
                            onChange={(e) => updateTool(i, { description: e.target.value })}
                            rows={2}
                            placeholder="The resource’s own description"
                            className="input text-[13px]"
                          />
                        </td>
                        <td className="px-2 py-3">
                          <button
                            type="button"
                            onClick={() => update({ tools: draft.tools.filter((_, j) => j !== i) })}
                            aria-label={`Remove ${ref}`}
                            className="flex h-10 w-10 items-center justify-center rounded-md text-gray-400 hover:bg-hover hover:text-red-400"
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <fieldset className="flex flex-col gap-3 rounded-xl border border-line bg-surface-1 px-5 py-4">
            <legend className="sr-only">Rate limit</legend>
            <h3 className="text-[15px] font-semibold">Rate limit</h3>
            <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
              {([['default', 'Deployment default'], ['custom', 'Custom'], ['none', 'No limit']] as [LimitMode, string][]).map(([mode, label]) => (
                <label key={mode} className="flex min-h-11 items-center gap-2 text-gray-300">
                  <input type="radio" name="limit-mode" checked={draft.limitMode === mode} onChange={() => update({ limitMode: mode })} className="h-4 w-4 accent-orange-600" />
                  {label}
                </label>
              ))}
            </div>
            {draft.limitMode === 'custom' && (
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <label htmlFor="max-calls" className="label">Calls per caller</label>
                  <input id="max-calls" type="number" min={1} value={draft.maxCalls} onChange={(e) => update({ maxCalls: e.target.value })} className="input h-10 w-28" />
                </div>
                <div>
                  <label htmlFor="window" className="label">Per</label>
                  <select id="window" value={draft.windowSeconds} onChange={(e) => update({ windowSeconds: Number(e.target.value) })} className="input h-10 w-36">
                    {WINDOWS.map((w) => <option key={w.seconds} value={w.seconds}>{w.label}</option>)}
                    {!WINDOWS.some((w) => w.seconds === draft.windowSeconds) && <option value={draft.windowSeconds}>{draft.windowSeconds} s</option>}
                  </select>
                </div>
              </div>
            )}
            <p className="text-[13px] text-gray-400">A caller is one API key, or one signed-in person. Over the limit, agents get an error telling them when to retry.</p>
          </fieldset>
        </section>

        <aside aria-label="Connect" className="flex min-w-0 flex-[1_1_340px] flex-col gap-4">
          <section className="flex flex-col gap-3.5 rounded-xl border border-line bg-surface-1 p-5">
            <h2 className="text-[17px] font-semibold">Connect an agent</h2>
            <div className="flex flex-col gap-1.5">
              <span className="text-xs text-gray-400">Endpoint</span>
              <div className="flex items-center gap-2">
                <code className="mono min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-lg border border-line-soft bg-surface-0 px-3 py-2.5 text-[13px]">{url}</code>
                <CopyButton text={url} label="Copy endpoint" />
              </div>
            </div>
            <div role="group" aria-label="Client" className="grid grid-cols-4 gap-1 rounded-lg bg-surface-0 p-1">
              {CLIENTS.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  aria-pressed={client === c.id}
                  onClick={() => setClient(c.id)}
                  className={`h-9 rounded-md px-1 text-[13px] whitespace-nowrap ${client === c.id ? 'bg-surface-2 text-gray-100 ring-1 ring-line-strong' : 'text-gray-400 hover:text-gray-200'}`}
                >
                  {c.label}
                </button>
              ))}
            </div>
            <div className="relative">
              <pre className="mono whitespace-pre-wrap break-words rounded-lg border border-line-soft bg-surface-0 p-3.5 pr-14 text-[12.5px] leading-relaxed text-gray-300">{connectSnippet(client, url, m.name)}</pre>
              <div className="absolute right-1.5 top-1.5"><CopyButton text={connectSnippet(client, url, m.name)} label="Copy setup" /></div>
            </div>
            <div className="flex items-start gap-2.5 rounded-lg border border-blue-500/25 bg-blue-500/10 p-3 text-[13px] leading-relaxed text-gray-300">
              <Info className="mt-0.5 h-4 w-4 flex-none text-blue-300" aria-hidden="true" />
              <span>
                {client !== 'apikey' && idps.isSuccess && !hasIdp
                  ? <>Sign-in for people needs an identity provider. <Link to="/clients" className="text-orange-300">Set one up under Clients</Link>, or use an API key.</>
                  : client === 'apikey'
                    ? <>{NOTES.apikey} <Link to="/clients" className="text-orange-300">Create a key</Link>.</>
                    : NOTES[client]}
              </span>
            </div>
          </section>

          <section className="flex flex-col gap-1 rounded-xl border border-line bg-surface-1 p-5">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-[15px] font-semibold">Recent calls</h2>
              <Link to={`/audit?manifest=${encodeURIComponent(`${m.namespace}/${m.name}`)}`} className="text-[13px] text-orange-300 hover:text-orange-200">Open audit log</Link>
            </div>
            {(recent.data?.items ?? []).length === 0 ? (
              <p className="text-[13px] text-gray-400">{recent.isLoading ? 'Loading' : 'No calls yet.'}</p>
            ) : recent.data!.items.map((c) => (
              <div key={c.id} className="flex justify-between gap-3 border-t border-line-soft py-2 text-[13px]">
                <span className="mono truncate">{c.tool_name}</span>
                <span className="whitespace-nowrap text-gray-400">
                  {relativeTime(c.created_at)} · <span className={c.is_error ? 'text-red-400' : 'text-green-400'}>{c.is_error ? 'error' : 'ok'}</span>
                </span>
              </div>
            ))}
          </section>
        </aside>
      </div>

      <ToolPicker
        open={picking}
        onClose={() => setPicking(false)}
        existing={draft.tools}
        onAdd={(tools) => update({ tools: [...draft.tools, ...tools.map((t) => ({ ...t, tool_name: '', description: '' }))] })}
      />

      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete this switchboard?">
        <p className="text-sm text-gray-300">Agents connected to <span className="mono">{m.namespace}/{m.name}</span> lose its tools at once. The queries, functions and pipelines themselves stay, and so does the audit log.</p>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={() => setConfirmDelete(false)} className="btn btn-secondary min-h-11">Cancel</button>
          <button type="button" onClick={() => remove.mutate()} disabled={remove.isPending} className="btn btn-danger min-h-11 disabled:opacity-60">
            {remove.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />} Delete
          </button>
        </div>
      </Dialog>
    </div>
  );
}
