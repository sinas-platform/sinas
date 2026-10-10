import { useEffect, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, History, RotateCcw } from 'lucide-react';
import { apiClient, getApiErrorMessage } from '../lib/api';
import { useToast } from '../lib/toast-context';
import type { ConfigRevision } from '../types';

const PAGE_SIZE = 50;

// Every kind that records history, in the order the console lists them.
const KINDS: { value: string; label: string }[] = [
  { value: 'agents', label: 'Agents' },
  { value: 'functions', label: 'Functions' },
  { value: 'pipelines', label: 'Pipelines' },
  { value: 'skills', label: 'Skills' },
  { value: 'queries', label: 'Queries' },
  { value: 'templates', label: 'Templates' },
  { value: 'components', label: 'Components' },
  { value: 'connectors', label: 'Connectors' },
  { value: 'webhooks', label: 'Webhooks' },
  { value: 'schedules', label: 'Schedules' },
  { value: 'databaseTriggers', label: 'Database triggers' },
  { value: 'collections', label: 'Collections' },
  { value: 'stores', label: 'Stores' },
  { value: 'manifests', label: 'Manifests' },
  { value: 'roles', label: 'Roles' },
  { value: 'secrets', label: 'Secrets' },
  { value: 'llmProviders', label: 'LLM providers' },
  { value: 'databaseConnections', label: 'Database connections' },
  { value: 'dependencies', label: 'Dependencies' },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label]));

const ACTION_STYLE: Record<string, string> = {
  create: 'bg-green-900/20 text-green-400',
  update: 'bg-blue-900/20 text-blue-400',
  delete: 'bg-red-900/20 text-red-400',
};

function who(revision: ConfigRevision): string {
  if (revision.origin === 'package') return `package ${revision.managed_by?.replace(/^pkg:/, '') ?? ''}`;
  if (revision.origin === 'config') {
    const source = revision.config_name ? `config "${revision.config_name}"` : 'config';
    return revision.actor_email ? `${source} (${revision.actor_email})` : source;
  }
  if (revision.origin === 'startup') return 'startup';
  return revision.actor_email ?? 'unknown';
}

// History never holds secret values: they're recorded as "<redacted:…>"
// markers (whole values, or inside headers, token params and URLs). They're
// shown as "hidden", or "hidden (new value)" where the marker changed.
const MARKER = /<redacted:[0-9a-f]+>/g;
const isHidden = (value: unknown) => typeof value === 'string' && /^<redacted:[0-9a-f]+>$/.test(value);

function markersIn(value: unknown): Set<string> {
  return new Set(JSON.stringify(value ?? null).match(MARKER) ?? []);
}

function masked(value: unknown, before?: Set<string>): unknown {
  const label = (marker: string) =>
    before && !before.has(marker) ? '‹hidden (new value)›' : '‹hidden›';
  if (typeof value === 'string') return value.replace(MARKER, label);
  if (Array.isArray(value)) return value.map((v) => masked(v, before));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, masked(v, before)]));
  }
  return value;
}

function Value({ value, before }: { value: unknown; before?: unknown }) {
  if (value === null || value === undefined) return <span className="text-gray-500 italic">none</span>;
  if (isHidden(value)) {
    const changed = before !== undefined && !markersIn(before).has(value as string);
    return <span className="text-gray-500 italic">{changed ? 'hidden (new value)' : 'hidden'}</span>;
  }
  value = masked(value, before !== undefined ? markersIn(before) : undefined);
  if (typeof value === 'string') {
    if (value.includes('\n') || value.length > 120) {
      return <pre className="whitespace-pre-wrap break-words text-xs max-h-48 overflow-auto">{value}</pre>;
    }
    return <span className="break-words">{value}</span>;
  }
  if (typeof value === 'object') {
    return (
      <pre className="whitespace-pre-wrap break-words text-xs max-h-48 overflow-auto">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return <span>{String(value)}</span>;
}

function RevisionDetail({ revision }: { revision: ConfigRevision }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { data, isLoading, error } = useQuery({
    queryKey: ['config-revision', revision.id],
    queryFn: () => apiClient.getConfigRevision(revision.id),
  });
  const restore = useMutation({
    mutationFn: () => apiClient.restoreConfigRevision(revision.id),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['config-history'] });
      const what = `${KIND_LABEL[result.resource_kind] ?? result.resource_kind} ${result.resource_key}`;
      showToast(
        result.action === 'unchanged'
          ? `${what} is already in this state`
          : result.action === 'create'
            ? `${what} restored`
            : `${what} reverted`,
        'success',
      );
    },
    onError: (err: unknown) => showToast(getApiErrorMessage(err, 'Could not restore'), 'error'),
  });

  if (isLoading) return <p className="text-sm text-gray-500 px-4 py-3">Loading…</p>;
  if (error || !data) return <p className="text-sm text-red-400 px-4 py-3">Could not load this change.</p>;

  const changes = Object.entries(data.changes ?? {});
  const restoreLabel = data.action === 'delete' ? 'Restore it' : 'Restore this version';
  const restoreHelp =
    data.action === 'delete'
      ? 'Brings it back as it was when it was deleted.'
      : 'Puts it back in the state right after this change.';

  return (
    <div className="px-4 pb-4 space-y-3">
      {changes.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500">
                <th className="py-1 pr-4 font-medium w-48">Field</th>
                {data.action === 'update' ? (
                  <>
                    <th className="py-1 pr-4 font-medium">Before</th>
                    <th className="py-1 font-medium">After</th>
                  </>
                ) : (
                  <th className="py-1 font-medium">{data.action === 'delete' ? 'Last state' : 'Value'}</th>
                )}
              </tr>
            </thead>
            <tbody>
              {changes.map(([field, change]) => (
                <tr key={field} className="border-t border-line-soft align-top">
                  <td className="py-2 pr-4 font-mono text-xs text-gray-300">{field}</td>
                  {data.action === 'update' ? (
                    <>
                      <td className="py-2 pr-4 text-gray-400">
                        <Value value={change.from} />
                      </td>
                      <td className="py-2 text-gray-200">
                        <Value value={change.to} before={change.from} />
                      </td>
                    </>
                  ) : (
                    <td className="py-2 text-gray-200">
                      <Value value={data.action === 'delete' ? change.from : change.to} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-gray-500">No field-level changes recorded.</p>
      )}
      {data.spec && (
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              if (confirm(`${restoreLabel}? ${restoreHelp}`)) restore.mutate();
            }}
            disabled={restore.isPending}
            className="btn-secondary text-sm inline-flex items-center gap-2"
          >
            <RotateCcw className="w-4 h-4" />
            {restore.isPending ? 'Restoring…' : restoreLabel}
          </button>
          <span className="text-xs text-gray-500">{restoreHelp}</span>
        </div>
      )}
    </div>
  );
}

export function ChangeHistory() {
  const [kind, setKind] = useState('');
  const [key, setKey] = useState('');
  const [name, setName] = useState('');  // `key`, once typing pauses
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setName(key.trim()), 300);
    return () => clearTimeout(timer);
  }, [key]);

  const history = useInfiniteQuery({
    queryKey: ['config-history', kind, name],
    queryFn: ({ pageParam }) =>
      apiClient.listConfigHistory({
        kind: kind || undefined,
        key: name || undefined,
        before: pageParam,
        limit: PAGE_SIZE,
      }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === PAGE_SIZE ? last[last.length - 1].id : undefined),
  });
  const revisions = history.data?.pages.flat() ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-100 flex items-center gap-3">
          <History className="w-7 h-7" />
          Change History
        </h1>
        <p className="text-gray-400 mt-1">
          Every change to agents, functions and other configuration, from the console, the API, config
          files or packages. Open a change to see what it did, or restore an earlier state.
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <div className="w-56">
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="input">
          <option value="">All kinds</option>
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
        </div>
        <div className="w-72">
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Exact name, e.g. support/triage"
            className="input"
          />
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        {history.isLoading && <p className="text-sm text-gray-500 p-4">Loading…</p>}
        {history.error && (
          <p className="text-sm text-red-400 p-4">
            {getApiErrorMessage(history.error, 'Could not load the change history')}
          </p>
        )}
        {!history.isLoading && !history.error && revisions.length === 0 && (
          <p className="text-sm text-gray-500 p-4">No changes recorded yet.</p>
        )}
        <ul>
          {revisions.map((revision) => {
            const expanded = open === revision.id;
            return (
              <li key={revision.id} className="border-b border-line-soft last:border-b-0">
                <button
                  onClick={() => setOpen(expanded ? null : revision.id)}
                  className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-hover"
                >
                  {expanded ? (
                    <ChevronDown className="w-4 h-4 mt-1 text-gray-500 shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 mt-1 text-gray-500 shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          ACTION_STYLE[revision.action] ?? 'bg-gray-800 text-gray-300'
                        }`}
                      >
                        {revision.restored_from_id ? `restore (${revision.action})` : revision.action}
                      </span>
                      <span className="text-gray-400">
                        {KIND_LABEL[revision.resource_kind] ?? revision.resource_kind}
                      </span>
                      <span className="text-gray-100 font-medium break-all">{revision.resource_key}</span>
                    </div>
                    <div className="text-xs text-gray-500 mt-1">
                      {new Date(revision.created_at).toLocaleString()} · {who(revision)}
                      {revision.changed_fields.length > 0 && revision.action === 'update' && (
                        <> · {revision.changed_fields.join(', ')}</>
                      )}
                    </div>
                  </div>
                </button>
                {expanded && <RevisionDetail revision={revision} />}
              </li>
            );
          })}
        </ul>
      </div>

      {history.hasNextPage && (
        <div className="flex justify-center">
          <button
            onClick={() => history.fetchNextPage()}
            disabled={history.isFetchingNextPage}
            className="btn-secondary text-sm"
          >
            {history.isFetchingNextPage ? 'Loading…' : 'Load older changes'}
          </button>
        </div>
      )}
    </div>
  );
}
