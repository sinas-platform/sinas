import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Download, Loader2 } from 'lucide-react';
import { listApiKeys, listCalls, listManifests, listUsers } from '../lib/api';
import { toCsv } from '../lib/tools';
import type { CallFilters, SwitchboardCall } from '../lib/types';
import { Empty, PageHeader, Pill } from '../components/Status';

const PAGE = 50;
const RANGES: { id: string; label: string; hours: number | null }[] = [
  { id: '24h', label: 'Last 24 hours', hours: 24 },
  { id: '7d', label: 'Last 7 days', hours: 24 * 7 },
  { id: '30d', label: 'Last 30 days', hours: 24 * 30 },
  { id: 'all', label: 'Everything kept', hours: null },
];

function sinceFor(rangeId: string): string | undefined {
  const hours = RANGES.find((r) => r.id === rangeId)?.hours;
  if (!hours) return undefined;
  return new Date(Math.floor((Date.now() - hours * 3600_000) / 60000) * 60000).toISOString();
}

const time = (iso: string) => new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

/** Today's calls show the time only; older ones the date too. */
const shortTime = (iso: string) => {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};
const took = (ms: number | null) => (ms === null ? '–' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

export function Audit() {
  const [params, setParams] = useSearchParams();
  const manifest = params.get('manifest') ?? '';
  const tool = params.get('tool') ?? '';
  const result = params.get('result') ?? '';
  const range = params.get('range') ?? '24h';
  const page = Number(params.get('page') ?? '0') || 0;
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== 'page') next.delete('page');
    setParams(next, { replace: true });
  };

  const since = useMemo(() => sinceFor(range), [range]);
  const filters: CallFilters = {
    manifest: manifest || undefined,
    tool: tool.trim() || undefined,
    is_error: result === 'errors' ? true : result === 'ok' ? false : undefined,
    since,
    limit: PAGE,
    offset: page * PAGE,
  };
  const calls = useQuery({ queryKey: ['calls', 'audit', filters], queryFn: () => listCalls(filters), placeholderData: keepPreviousData });
  const manifests = useQuery({ queryKey: ['manifests'], queryFn: listManifests });
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers, staleTime: 300_000 });
  const keys = useQuery({ queryKey: ['api-keys'], queryFn: listApiKeys, staleTime: 60_000 });

  const emails = useMemo(() => new Map((users.data ?? []).map((u) => [u.id, u.email])), [users.data]);
  const keyNames = useMemo(() => new Map((keys.data ?? []).map((k) => [k.id, k.name])), [keys.data]);
  const caller = (c: SwitchboardCall) => {
    const person = emails.get(c.user_id) ?? `user ${c.user_id.slice(0, 8)}`;
    if (!c.api_key_id) return person;
    return `key: ${keyNames.get(c.api_key_id) ?? c.api_key_id.slice(0, 8)}`;
  };

  const items = calls.data?.items ?? [];
  const total = calls.data?.total ?? 0;
  const selected = items.find((c) => c.id === selectedId) ?? items[0] ?? null;
  const boards = (manifests.data ?? []).filter((m) => m.exposed_tools.length > 0);

  const exportCsv = () => {
    const csv = toCsv(
      ['time', 'caller', 'user_id', 'api_key_id', 'switchboard', 'tool', 'resource', 'duration_ms', 'error', 'arguments', 'result'],
      items.map((c) => [c.created_at, caller(c), c.user_id, c.api_key_id, `${c.manifest_namespace}/${c.manifest_name}`, c.tool_name,
        `${c.resource_type} ${c.resource_ref}`, c.duration_ms, c.is_error, c.arguments, c.result]),
    );
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `switchboard-calls-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Every tool call through every switchboard: who called it, with what arguments, and what came back. Secrets in arguments are redacted before they are stored."
        actions={
          <button type="button" onClick={exportCsv} disabled={items.length === 0} className="btn btn-secondary min-h-11 disabled:opacity-50">
            <Download className="h-4 w-4" aria-hidden="true" /> Export this page
          </button>
        }
      />

      <div role="search" className="mb-5 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="f-manifest" className="label text-xs">Switchboard</label>
          <select id="f-manifest" value={manifest} onChange={(e) => set('manifest', e.target.value)} className="input h-10 w-52">
            <option value="">All</option>
            {boards.map((m) => <option key={m.id} value={`${m.namespace}/${m.name}`}>{m.namespace}/{m.name}</option>)}
            {manifest && !boards.some((m) => `${m.namespace}/${m.name}` === manifest) && <option value={manifest}>{manifest}</option>}
          </select>
        </div>
        <div>
          <label htmlFor="f-tool" className="label text-xs">Tool</label>
          <input id="f-tool" type="search" value={tool} onChange={(e) => set('tool', e.target.value)} placeholder="Exact tool name" className="input h-10 w-48" />
        </div>
        <div>
          <label htmlFor="f-result" className="label text-xs">Result</label>
          <select id="f-result" value={result} onChange={(e) => set('result', e.target.value)} className="input h-10 w-36">
            <option value="">Any</option>
            <option value="errors">Errors only</option>
            <option value="ok">Succeeded</option>
          </select>
        </div>
        <div>
          <label htmlFor="f-range" className="label text-xs">When</label>
          <select id="f-range" value={range} onChange={(e) => set('range', e.target.value)} className="input h-10 w-44">
            {RANGES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        </div>
      </div>

      {calls.isLoading ? (
        <div role="status" className="flex items-center gap-2 py-10 text-gray-400"><Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> Loading</div>
      ) : items.length === 0 ? (
        <Empty title="No calls match">Calls appear here as soon as an agent uses a switchboard.</Empty>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <div className="min-w-0 flex-[999_1_560px] overflow-x-auto rounded-xl border border-line bg-surface-1">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-gray-400">
                  {['Time', 'Caller', 'Switchboard', 'Tool', 'Result'].map((h) => <th key={h} scope="col" className="whitespace-nowrap px-4 py-2.5 font-medium">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {items.map((c) => {
                  const active = selected?.id === c.id;
                  return (
                    <tr key={c.id} className={`border-b border-line-soft last:border-0 ${active ? 'bg-orange-500/10' : 'hover:bg-hover'}`}>
                      <td className="p-0">
                        <button type="button" onClick={() => setSelectedId(c.id)} aria-pressed={active} className="w-full whitespace-nowrap px-4 py-3 text-left text-gray-400">
                          <time dateTime={c.created_at} title={time(c.created_at)}>{shortTime(c.created_at)}</time>
                        </button>
                      </td>
                      <td className="max-w-[220px] break-words px-4 py-3">{caller(c)}</td>
                      <td className="mono px-4 py-3 text-[13px] text-gray-400">{c.manifest_namespace}/{c.manifest_name}</td>
                      <td className="mono whitespace-nowrap px-4 py-3 text-[13px]">{c.tool_name}</td>
                      <td className="px-4 py-3" title={took(c.duration_ms)}>{c.is_error ? <Pill tone="red">error</Pill> : <Pill tone="green">ok</Pill>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-[13px] text-gray-400">
              <span>Showing {page * PAGE + 1}–{page * PAGE + items.length} of {total.toLocaleString()}</span>
              <span className="flex gap-2">
                <button type="button" disabled={page === 0} onClick={() => set('page', String(page - 1))} className="btn btn-secondary btn-sm min-h-9 disabled:opacity-40">Previous</button>
                <button type="button" disabled={(page + 1) * PAGE >= total} onClick={() => set('page', String(page + 1))} className="btn btn-secondary btn-sm min-h-9 disabled:opacity-40">Next</button>
              </span>
            </div>
          </div>

          {selected && (
            <aside aria-label="Call details" className="flex min-w-0 flex-[1_1_340px] flex-col gap-3.5 rounded-xl border border-line bg-surface-1 p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="mono truncate text-[17px] font-semibold">{selected.tool_name}</h2>
                {selected.is_error ? <Pill tone="red">error</Pill> : <Pill tone="green">ok</Pill>}
              </div>
              <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[13px]">
                <dt className="text-gray-400">Caller</dt><dd>{caller(selected)}</dd>
                {selected.api_key_id && <><dt className="text-gray-400">Key owner</dt><dd>{emails.get(selected.user_id) ?? selected.user_id}</dd></>}
                <dt className="text-gray-400">Runs</dt><dd className="mono">{selected.resource_type} {selected.resource_ref}</dd>
                <dt className="text-gray-400">When</dt><dd>{time(selected.created_at)} · {took(selected.duration_ms)}</dd>
              </dl>
              <Block label="Arguments" value={selected.arguments} />
              <Block label="Result" value={selected.result} />
            </aside>
          )}
        </div>
      )}
    </div>
  );
}

function Block({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs text-gray-400">{label}</span>
      <pre className="mono max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line-soft bg-surface-0 p-3 text-[12.5px] leading-relaxed text-gray-300">
        {value === null || value === undefined ? '–' : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
