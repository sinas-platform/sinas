import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Trash2, X } from 'lucide-react';
import { API_BASE_URL, apiClient, getApiErrorMessage } from '../lib/api';
import { useToast } from '../lib/toast-context';
import type { ComponentShare, ComponentShareMode } from '../types';

const MODES: { value: ComponentShareMode; title: string; help: string }[] = [
  {
    value: 'snapshot',
    title: 'Snapshot',
    help: 'Anyone with the link sees the page with the inputs below. No live data access.',
  },
  {
    value: 'viewer',
    title: 'Signed-in users',
    help: 'Opens in the console. Each viewer uses their own permissions.',
  },
  {
    value: 'creator',
    title: 'Anyone, as you',
    help: 'Anyone with the link sees live data through your permissions, limited to what this component declares. Treat the link like a password.',
  },
];

const EXPIRY = [
  { label: 'Never', days: 0 },
  { label: '1 day', days: 1 },
  { label: '7 days', days: 7 },
  { label: '30 days', days: 30 },
];

const MODE_LABEL: Record<ComponentShareMode, string> = {
  snapshot: 'Snapshot',
  viewer: 'Signed-in users',
  creator: 'Anyone, as you',
};

function linkFor(share: ComponentShare): string {
  // Viewer links open in this console (wherever it is served); the others
  // are pages served by the API.
  if (share.mode === 'viewer') return `${window.location.origin}/ui/shared/${share.token}`;
  return `${API_BASE_URL}${share.share_url}`;
}

export function ShareDialog({
  namespace,
  name,
  onClose,
}: {
  namespace: string;
  name: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [mode, setMode] = useState<ComponentShareMode>('snapshot');
  const [allowWrites, setAllowWrites] = useState(false);
  const [expiryDays, setExpiryDays] = useState(7);
  const [maxViews, setMaxViews] = useState('');
  const [label, setLabel] = useState('');
  const [input, setInput] = useState('');
  const [inputError, setInputError] = useState('');

  const sharesKey = ['component-shares', namespace, name];
  const { data: shares } = useQuery({
    queryKey: sharesKey,
    queryFn: () => apiClient.listComponentShares(namespace, name),
  });

  const copy = async (share: ComponentShare) => {
    try {
      await navigator.clipboard.writeText(linkFor(share));
      showToast('Link copied', 'success');
    } catch {
      // The link exists either way; don't make it look like it failed.
      window.prompt('Copy the link:', linkFor(share));
    }
  };

  const create = useMutation({
    mutationFn: () => {
      let inputData: Record<string, unknown> | undefined;
      if (input.trim()) inputData = JSON.parse(input);
      return apiClient.createComponentShare(namespace, name, {
        mode,
        allow_writes: mode === 'creator' ? allowWrites : false,
        input_data: inputData,
        expires_at: expiryDays ? new Date(Date.now() + expiryDays * 86400000).toISOString() : undefined,
        max_views: maxViews ? Number(maxViews) : undefined,
        label: label || undefined,
      });
    },
    onSuccess: async (share) => {
      queryClient.invalidateQueries({ queryKey: sharesKey });
      setLabel('');
      await copy(share);
    },
    onError: (err: unknown) => showToast(getApiErrorMessage(err, 'Could not create the link'), 'error'),
  });

  const revoke = useMutation({
    mutationFn: (token: string) => apiClient.revokeComponentShare(namespace, name, token),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sharesKey }),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim()) {
      try {
        JSON.parse(input);
      } catch {
        setInputError('Inputs must be a JSON object');
        return;
      }
    }
    setInputError('');
    create.mutate();
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-surface-input border border-gray-800 rounded-lg w-full max-w-xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-5 border-b border-gray-800">
          <h2 className="text-lg font-semibold text-gray-100">Share {namespace}/{name}</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={submit} className="p-5 space-y-4">
          <div className="space-y-2">
            {MODES.map((m) => (
              <label key={m.value} className="flex gap-3 items-start cursor-pointer">
                <input
                  type="radio"
                  name="mode"
                  checked={mode === m.value}
                  onChange={() => setMode(m.value)}
                  className="mt-1"
                />
                <span>
                  <span className="block text-sm text-gray-100">{m.title}</span>
                  <span className="block text-xs text-gray-500">{m.help}</span>
                </span>
              </label>
            ))}
          </div>

          {mode === 'creator' && (
            <label className="flex gap-2 items-start text-sm text-gray-300">
              <input
                type="checkbox"
                checked={allowWrites}
                onChange={(e) => setAllowWrites(e.target.checked)}
                className="mt-1"
              />
              <span>
                Allow changes
                <span className="block text-xs text-gray-500">
                  Off: read queries and store reads only. On: also functions, agents, write queries
                  and store writes, all as you.
                </span>
              </span>
            </label>
          )}

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Expires</label>
              <select
                value={expiryDays}
                onChange={(e) => setExpiryDays(Number(e.target.value))}
                className="input text-sm"
              >
                {EXPIRY.map((x) => (
                  <option key={x.days} value={x.days}>{x.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Max views</label>
              <input
                value={maxViews}
                onChange={(e) => setMaxViews(e.target.value.replace(/[^0-9]/g, ''))}
                placeholder="No limit"
                className="input text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Label</label>
              <input value={label} onChange={(e) => setLabel(e.target.value)} className="input text-sm" />
            </div>
          </div>

          <div>
            <label className="block text-xs text-gray-400 mb-1">Inputs (JSON, optional)</label>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={3}
              placeholder='{"customer": "Acme"}'
              className="input text-sm font-mono"
            />
            {inputError && <p className="text-xs text-red-400 mt-1">{inputError}</p>}
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={create.isPending}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors text-sm"
            >
              {create.isPending ? 'Creating…' : 'Create link and copy'}
            </button>
          </div>
        </form>

        <div className="p-5 border-t border-gray-800">
          <h3 className="text-sm text-gray-300 mb-2">Links</h3>
          {!shares?.length && <p className="text-xs text-gray-500">No links yet.</p>}
          <ul className="space-y-2">
            {shares?.map((share) => (
              <li key={share.id} className="flex items-center gap-3 text-xs">
                <div className="flex-1 min-w-0">
                  <div className="text-gray-200 truncate">
                    {share.label || MODE_LABEL[share.mode]}
                    {share.mode === 'creator' && share.allow_writes && (
                      <span className="ml-2 text-amber-400">changes allowed</span>
                    )}
                  </div>
                  <div className="text-gray-500">
                    {MODE_LABEL[share.mode]} · {share.view_count}
                    {share.max_views ? `/${share.max_views}` : ''} views ·{' '}
                    {share.expires_at ? `expires ${new Date(share.expires_at).toLocaleDateString()}` : 'no expiry'}
                  </div>
                </div>
                <button onClick={() => copy(share)} className="p-1 text-gray-500 hover:text-gray-100" title="Copy link">
                  <Copy className="w-4 h-4" />
                </button>
                <button
                  onClick={() => {
                    if (confirm('Revoke this link? Anyone using it loses access at once.')) revoke.mutate(share.token);
                  }}
                  className="p-1 text-gray-500 hover:text-red-400"
                  title="Revoke"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
