import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus, Search } from 'lucide-react';
import { errorMessage } from '@sinas/ui';
import { createManifest, getManifestStatus, listCalls, listManifests } from '../lib/api';
import { describeLimit } from '../lib/tools';
import type { Manifest } from '../lib/types';
import { Dialog } from '../components/Dialog';
import { Empty, PageHeader, Pill, Tag } from '../components/Status';

const DAY_MS = 24 * 3600 * 1000;

function since24h(): string {
  // Rounded to the minute so the query key stays put between renders.
  return new Date(Math.floor((Date.now() - DAY_MS) / 60000) * 60000).toISOString();
}

const PLURAL = { query: 'queries', function: 'functions', pipeline: 'pipelines' } as const;

function kinds(m: Manifest): string[] {
  const count = { query: 0, function: 0, pipeline: 0 };
  m.exposed_tools.forEach((t) => { count[t.type] += 1; });
  return (Object.keys(count) as (keyof typeof count)[])
    .filter((k) => count[k] > 0)
    .map((k) => `${count[k]} ${count[k] === 1 ? k : PLURAL[k]}`);
}

export function Switchboards() {
  const [search, setSearch] = useState('');
  const [showOthers, setShowOthers] = useState(false);
  const [creating, setCreating] = useState(false);
  const since = useMemo(since24h, []);

  const manifests = useQuery({ queryKey: ['manifests'], queryFn: listManifests });
  const total = useQuery({ queryKey: ['calls', 'count', since], queryFn: () => listCalls({ since, limit: 1 }) });
  const failed = useQuery({ queryKey: ['calls', 'count', since, 'errors'], queryFn: () => listCalls({ since, is_error: true, limit: 1 }) });

  const all = manifests.data ?? [];
  const term = search.trim().toLowerCase();
  const matches = (m: Manifest) => !term || `${m.namespace}/${m.name} ${m.description ?? ''}`.toLowerCase().includes(term);
  const boards = all.filter((m) => m.exposed_tools.length > 0 && matches(m));
  const others = all.filter((m) => m.exposed_tools.length === 0 && matches(m));

  const statuses = useQueries({
    queries: boards.map((m) => ({
      queryKey: ['status', m.namespace, m.name],
      queryFn: () => getManifestStatus(m.namespace, m.name),
    })),
  });
  const callCounts = useQueries({
    queries: boards.map((m) => ({
      queryKey: ['calls', 'count', since, `${m.namespace}/${m.name}`],
      queryFn: () => listCalls({ manifest: `${m.namespace}/${m.name}`, since, limit: 1 }),
    })),
  });

  const totalCalls = total.data?.total;
  const failedCalls = failed.data?.total;

  return (
    <div>
      <PageHeader
        title="Switchboards"
        description="Publish functions, queries and pipelines to outside agents such as Claude, ChatGPT or Copilot. Each caller sees only the tools their role allows, and every call is logged."
        actions={
          <button type="button" onClick={() => setCreating(true)} className="btn btn-primary min-h-11">
            <Plus className="h-4 w-4" aria-hidden="true" /> New switchboard
          </button>
        }
      />

      <section aria-label="Last 24 hours" className="mb-8 grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
        <Stat label="Tool calls, last 24 h" value={totalCalls?.toLocaleString()} />
        <Stat
          label="Failed calls"
          value={failedCalls?.toLocaleString()}
          extra={totalCalls ? `${((100 * (failedCalls ?? 0)) / totalCalls).toFixed(1)}%` : undefined}
        />
        <Stat label="Switchboards" value={manifests.data ? String(all.filter((m) => m.exposed_tools.length > 0).length) : undefined} />
        <Stat label="Tools published" value={manifests.data ? String(all.reduce((n, m) => n + m.exposed_tools.length, 0)) : undefined} />
      </section>

      <section aria-label="Your switchboards">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-[17px] font-semibold">Your switchboards</h2>
          <label className="relative">
            <span className="sr-only">Search switchboards</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" aria-hidden="true" />
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name or namespace" className="input h-10 w-60 pl-9" />
          </label>
        </div>

        {manifests.isLoading ? (
          <div role="status" className="flex items-center gap-2 py-10 text-gray-400"><Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> Loading</div>
        ) : boards.length === 0 ? (
          <Empty title={term ? 'No switchboard matches' : 'No switchboards yet'}>
            {term ? 'Try another name.' : 'Create one, then add the queries, functions and pipelines agents may call.'}
          </Empty>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4">
            {boards.map((m, i) => {
              const status = statuses[i]?.data;
              const missing = status?.resources.missing.length ?? 0;
              return (
                <Link
                  key={m.id}
                  to={`/switchboards/${encodeURIComponent(m.namespace)}/${encodeURIComponent(m.name)}`}
                  className="flex flex-col gap-3.5 rounded-xl border border-line bg-surface-1 p-5 text-gray-100 hover:border-orange-500/60 hover:bg-surface-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 flex-col gap-1">
                      <span className="mono text-xs text-gray-400">{m.namespace}/</span>
                      <span className="truncate text-[17px] font-semibold">{m.name}</span>
                    </div>
                    {!m.is_active ? <Pill tone="gray">Off</Pill>
                      : !status ? null
                      : missing > 0 ? <Pill tone="amber">{missing} {missing === 1 ? 'tool' : 'tools'} missing</Pill>
                      : <Pill tone="green">Ready</Pill>}
                  </div>
                  {m.description && <p className="line-clamp-2 text-sm leading-relaxed text-gray-400">{m.description}</p>}
                  <div className="flex flex-wrap gap-1.5">{kinds(m).map((k) => <Tag key={k}>{k}</Tag>)}</div>
                  <div className="mt-auto flex flex-wrap gap-5 border-t border-line-soft pt-3 text-[13px] text-gray-400">
                    <span><span className="font-semibold text-gray-100">{m.exposed_tools.length}</span> tools</span>
                    <span><span className="font-semibold text-gray-100">{callCounts[i]?.data?.total.toLocaleString() ?? '–'}</span> calls today</span>
                    <span>{describeLimit(m.switchboard_limits)}</span>
                  </div>
                </Link>
              );
            })}
          </div>
        )}

        {others.length > 0 && (
          <div className="mt-8">
            <button type="button" onClick={() => setShowOthers(!showOthers)} aria-expanded={showOthers} className="text-sm text-gray-400 hover:text-gray-200">
              {showOthers ? 'Hide' : 'Show'} {others.length} {others.length === 1 ? 'manifest' : 'manifests'} without tools
            </button>
            {showOthers && (
              <ul className="mt-3 divide-y divide-line-soft rounded-xl border border-line bg-surface-1">
                {others.map((m) => (
                  <li key={m.id}>
                    <Link to={`/switchboards/${encodeURIComponent(m.namespace)}/${encodeURIComponent(m.name)}`} className="flex min-h-11 items-center justify-between gap-3 px-4 py-2.5 text-sm hover:bg-hover">
                      <span className="mono">{m.namespace}/{m.name}</span>
                      <span className="text-gray-400">Add tools</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <NewSwitchboard open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

function Stat({ label, value, extra }: { label: string; value?: string; extra?: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-line bg-surface-1 px-5 py-4">
      <span className="text-[13px] text-gray-400">{label}</span>
      <span className="text-[28px] font-bold leading-tight">
        {value ?? '–'} {extra && <span className="text-sm font-medium text-gray-400">{extra}</span>}
      </span>
    </div>
  );
}

const NAME = /^[a-zA-Z][a-zA-Z0-9_-]*$/;

function NewSwitchboard({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [namespace, setNamespace] = useState('default');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const create = useMutation({
    mutationFn: () => createManifest({ namespace: namespace.trim(), name: name.trim(), description: description.trim() || undefined }),
    onSuccess: (m) => {
      queryClient.invalidateQueries({ queryKey: ['manifests'] });
      onClose();
      navigate(`/switchboards/${encodeURIComponent(m.namespace)}/${encodeURIComponent(m.name)}`);
    },
    onError: (err) => setError(errorMessage(err)),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError('');
    if (!NAME.test(namespace.trim()) || !NAME.test(name.trim())) {
      setError('Namespace and name start with a letter and hold only letters, digits, - and _.');
      return;
    }
    create.mutate();
  };
  return (
    <Dialog open={open} onClose={onClose} title="New switchboard">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <p className="text-sm text-gray-400">Agents reach it at <span className="mono text-gray-200">/mcp/{namespace || 'namespace'}/{name || 'name'}</span>. You add its tools next.</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="sb-ns" className="label">Namespace</label>
            <input id="sb-ns" value={namespace} onChange={(e) => setNamespace(e.target.value)} required className="input" />
          </div>
          <div>
            <label htmlFor="sb-name" className="label">Name</label>
            <input id="sb-name" value={name} onChange={(e) => setName(e.target.value)} required placeholder="crm-tools" className="input" />
          </div>
        </div>
        <div>
          <label htmlFor="sb-desc" className="label">Description</label>
          <textarea id="sb-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="input" placeholder="What this switchboard is for" />
        </div>
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn btn-secondary min-h-11">Cancel</button>
          <button type="submit" disabled={create.isPending} className="btn btn-primary min-h-11 disabled:opacity-60">
            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />} Create
          </button>
        </div>
      </form>
    </Dialog>
  );
}
