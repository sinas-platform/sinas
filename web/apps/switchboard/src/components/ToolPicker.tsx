import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Search } from 'lucide-react';
import { listResources } from '../lib/api';
import type { ExposedTool, Resource, ToolType } from '../lib/types';
import { Dialog } from './Dialog';
import { Tag } from './Status';

const TYPES: { id: ToolType | 'all'; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'query', label: 'Queries' },
  { id: 'function', label: 'Functions' },
  { id: 'pipeline', label: 'Pipelines' },
];

const key = (r: { type: string; namespace: string; name: string }) => `${r.type}:${r.namespace}/${r.name}`;

/** Pick functions, queries and pipelines to add as tools. */
export function ToolPicker({ open, onClose, existing, onAdd }: {
  open: boolean;
  onClose: () => void;
  existing: ExposedTool[];
  onAdd: (tools: ExposedTool[]) => void;
}) {
  const resources = useQuery({ queryKey: ['resources'], queryFn: listResources, enabled: open });
  const [type, setType] = useState<ToolType | 'all'>('all');
  const [search, setSearch] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const taken = useMemo(() => new Set(existing.map(key)), [existing]);

  const term = search.trim().toLowerCase();
  const shown = (resources.data ?? []).filter((r) =>
    (type === 'all' || r.type === type)
    && (!term || `${r.namespace}/${r.name} ${r.description ?? ''}`.toLowerCase().includes(term)));

  const toggle = (r: Resource) => {
    const next = new Set(picked);
    if (next.has(key(r))) next.delete(key(r)); else next.add(key(r));
    setPicked(next);
  };

  const close = () => { setPicked(new Set()); setSearch(''); onClose(); };
  const add = () => {
    onAdd((resources.data ?? []).filter((r) => picked.has(key(r))).map((r) => ({ type: r.type, namespace: r.namespace, name: r.name })));
    close();
  };

  return (
    <Dialog open={open} onClose={close} title="Add tools" wide>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <div role="group" aria-label="Type" className="flex flex-wrap gap-1 rounded-lg bg-surface-0 p-1">
            {TYPES.map((t) => (
              <button
                key={t.id}
                type="button"
                aria-pressed={type === t.id}
                onClick={() => setType(t.id)}
                className={`h-9 rounded-md px-3 text-[13px] ${type === t.id ? 'bg-surface-2 text-gray-100 ring-1 ring-line-strong' : 'text-gray-400 hover:text-gray-200'}`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <label className="relative flex-1">
            <span className="sr-only">Search resources</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" aria-hidden="true" />
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by name or description" className="input h-10 pl-9" />
          </label>
        </div>

        {resources.isLoading ? (
          <div role="status" className="flex items-center gap-2 py-8 text-gray-400"><Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> Loading</div>
        ) : shown.length === 0 ? (
          <p className="py-8 text-center text-sm text-gray-400">Nothing matches. Build queries, functions and pipelines in the Sinas console first.</p>
        ) : (
          <ul className="max-h-[50vh] divide-y divide-line-soft overflow-y-auto rounded-lg border border-line">
            {shown.map((r) => {
              const id = key(r);
              const disabled = taken.has(id) || r.unavailable !== null;
              return (
                <li key={id}>
                  <label className={`flex min-h-11 gap-3 px-4 py-3 ${disabled ? 'opacity-60' : 'cursor-pointer hover:bg-hover'}`}>
                    <input
                      type="checkbox"
                      checked={picked.has(id) || taken.has(id)}
                      disabled={disabled}
                      onChange={() => toggle(r)}
                      className="mt-0.5 h-[18px] w-[18px] flex-none accent-orange-600"
                    />
                    <span className="flex min-w-0 flex-1 flex-col gap-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="mono text-sm text-gray-100">{r.namespace}/{r.name}</span>
                        <Tag>{r.type}</Tag>
                        {taken.has(id) && <span className="text-xs text-gray-400">Already added</span>}
                        {!taken.has(id) && r.unavailable && <span className="text-xs text-amber-400">{r.unavailable}</span>}
                      </span>
                      {r.description && <span className="line-clamp-2 text-[13px] text-gray-400">{r.description}</span>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-gray-400">{picked.size} selected</span>
          <div className="flex gap-2">
            <button type="button" onClick={close} className="btn btn-secondary min-h-11">Cancel</button>
            <button type="button" onClick={add} disabled={picked.size === 0} className="btn btn-primary min-h-11 disabled:opacity-50">Add {picked.size || ''}</button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
