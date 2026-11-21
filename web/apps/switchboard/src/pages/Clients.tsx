import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Loader2, Plus } from 'lucide-react';
import { errorMessage } from '@sinas/ui';
import { createApiKey, listApiKeys, listIdentityProviders, listRoles, revokeApiKey } from '../lib/api';
import { consoleUrl } from '../lib/links';
import { relativeTime } from '../lib/tools';
import type { APIKey } from '../lib/types';
import { CopyButton } from '../components/CopyButton';
import { Dialog } from '../components/Dialog';
import { Empty, PageHeader, Pill } from '../components/Status';

export function Clients() {
  const idps = useQuery({ queryKey: ['identity-providers'], queryFn: listIdentityProviders, retry: false });
  const keys = useQuery({ queryKey: ['api-keys'], queryFn: listApiKeys });
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<APIKey | null>(null);
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: (id: string) => revokeApiKey(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      setRevoking(null);
    },
  });

  const activeKeys = (keys.data ?? []).filter((k) => k.is_active);

  return (
    <div>
      <PageHeader
        title="Clients"
        description="How agents prove who they are. People sign in through your identity provider and get the tools their groups allow. Servers and scripts use an API key with its own role."
      />

      <section aria-labelledby="idp-heading" className="mb-10">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h2 id="idp-heading" className="text-[17px] font-semibold">Sign-in for people</h2>
            <span className="text-[13px] text-gray-400">Agents that support OAuth, such as Claude, ChatGPT and Cursor, send people here to sign in.</span>
          </div>
          <a href={consoleUrl('/identity-providers')} className="btn min-h-10 border border-orange-500/40 bg-orange-500/10 text-orange-300 hover:bg-orange-500/20">
            Manage in console
          </a>
        </div>
        {idps.isError ? (
          <Empty title="Only administrators can see identity providers" />
        ) : idps.isLoading ? (
          <div role="status" className="flex items-center gap-2 py-6 text-gray-400"><Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> Loading</div>
        ) : (idps.data ?? []).length === 0 ? (
          <Empty title="No identity provider yet">Without one, agents can only connect with an API key. Add Okta, Microsoft Entra ID or another OpenID Connect provider in the console.</Empty>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4">
            {idps.data!.map((p) => (
              <div key={p.id} className="flex flex-col gap-3 rounded-xl border border-line bg-surface-1 p-5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-base font-semibold">{p.display_name}</span>
                  {p.is_active === false ? <Pill tone="gray">Off</Pill> : <Pill tone="green">Active</Pill>}
                </div>
                <span className="mono break-all text-xs text-gray-400">{p.issuer}</span>
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs text-gray-400">Group or domain to role</span>
                  {p.role_mappings.length === 0 && <span className="text-[13px] text-gray-400">No mappings: people get the default role.</span>}
                  {p.role_mappings.map((r, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-2 text-[13px]">
                      <span className="text-gray-300">{r.group ?? (r.email_domain ? `@${r.email_domain}` : 'anyone')}</span>
                      <ArrowRight className="h-3.5 w-3.5 text-gray-400" aria-hidden="true" />
                      {r.roles.map((role) => <span key={role} className="rounded-md bg-blue-500/15 px-2 py-0.5 text-xs text-blue-300">{role}</span>)}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="keys-heading">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h2 id="keys-heading" className="text-[17px] font-semibold">API keys</h2>
            <span className="text-[13px] text-gray-400">For agents without a person behind them. A key only reaches the tools its roles may run.</span>
          </div>
          <button type="button" onClick={() => setCreating(true)} className="btn btn-primary min-h-11">
            <Plus className="h-4 w-4" aria-hidden="true" /> Create API key
          </button>
        </div>
        {keys.isLoading ? (
          <div role="status" className="flex items-center gap-2 py-6 text-gray-400"><Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> Loading</div>
        ) : activeKeys.length === 0 ? (
          <Empty title="No API keys" />
        ) : (
          <div className="overflow-x-auto rounded-xl border border-line bg-surface-1">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-gray-400">
                  {['Name', 'Key', 'Roles', 'Last used', 'Expires'].map((h) => <th key={h} scope="col" className="whitespace-nowrap px-4 py-2.5 font-medium">{h}</th>)}
                  <th scope="col" className="px-4 py-2.5"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {activeKeys.map((k) => {
                  const soon = k.expires_at && new Date(k.expires_at).getTime() - Date.now() < 14 * 86400_000;
                  return (
                    <tr key={k.id} className="border-b border-line-soft align-top last:border-0">
                      <td className="whitespace-nowrap px-4 py-3 font-medium">
                        {k.name}
                        {k.user_email && <div className="mt-1 text-xs font-normal text-gray-400">{k.user_email}</div>}
                      </td>
                      <td className="mono whitespace-nowrap px-4 py-3 text-[13px] text-gray-400">{k.key_prefix}…</td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1.5">
                          {k.roles.length === 0 ? <span className="text-[13px] text-gray-400">Own permissions</span>
                            : k.roles.map((r) => <span key={r.id} className="rounded-md bg-blue-500/15 px-2 py-0.5 text-xs text-blue-300">{r.name}</span>)}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-[13px] text-gray-400">{relativeTime(k.last_used_at)}</td>
                      <td className={`whitespace-nowrap px-4 py-3 text-[13px] ${soon ? 'text-amber-400' : 'text-gray-400'}`}>{k.expires_at ? relativeTime(k.expires_at) : 'never'}</td>
                      <td className="px-4 py-3">
                        <button type="button" onClick={() => setRevoking(k)} className="btn btn-sm min-h-9 border border-red-500/40 text-red-300 hover:bg-red-500/10">Revoke</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <CreateKey open={creating} onClose={() => setCreating(false)} />

      <Dialog open={revoking !== null} onClose={() => setRevoking(null)} title="Revoke this key?">
        <p className="text-sm text-gray-300">Agents using <span className="font-semibold">{revoking?.name}</span> stop working at once. This cannot be undone.</p>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={() => setRevoking(null)} className="btn btn-secondary min-h-11">Cancel</button>
          <button type="button" onClick={() => revoking && revoke.mutate(revoking.id)} disabled={revoke.isPending} className="btn btn-danger min-h-11 disabled:opacity-60">
            {revoke.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />} Revoke
          </button>
        </div>
      </Dialog>
    </div>
  );
}

const EXPIRY: { days: number | null; label: string }[] = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
  { days: null, label: 'Never' },
];

function CreateKey({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const roles = useQuery({ queryKey: ['roles'], queryFn: listRoles, enabled: open, retry: false });
  const [name, setName] = useState('');
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [days, setDays] = useState<number | null>(90);
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState('');

  const create = useMutation({
    mutationFn: () => createApiKey({
      name: name.trim(),
      role_ids: roleIds,
      expires_at: days ? new Date(Date.now() + days * 86400_000).toISOString() : undefined,
    }),
    onSuccess: (k) => {
      setCreated(k.key);
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const close = () => {
    setName(''); setRoleIds([]); setDays(90); setCreated(null); setError('');
    onClose();
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError('');
    create.mutate();
  };

  return (
    <Dialog open={open} onClose={close} title={created ? 'Copy your key now' : 'Create API key'}>
      {created ? (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-gray-300">This is the only time the key is shown. Store it in your agent’s secret store.</p>
          <div className="flex items-center gap-2">
            <code className="mono min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-lg border border-line-soft bg-surface-0 px-3 py-2.5 text-[13px]">{created}</code>
            <CopyButton text={created} label="Copy key" />
          </div>
          <div className="flex justify-end"><button type="button" onClick={close} className="btn btn-primary min-h-11">Done</button></div>
        </div>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div>
            <label htmlFor="key-name" className="label">Name</label>
            <input id="key-name" value={name} onChange={(e) => setName(e.target.value)} required placeholder="quote-bot" className="input" />
          </div>
          <fieldset>
            <legend className="label">Roles</legend>
            <p className="mb-2 text-[13px] text-gray-400">The key can run what these roles allow, never more than you can. No roles: it acts with your own permissions.</p>
            {roles.isError ? (
              <p className="text-[13px] text-gray-400">You can’t list roles, so this key will act with your own permissions.</p>
            ) : (
              <div className="flex max-h-48 flex-col gap-1 overflow-y-auto rounded-lg border border-line p-2">
                {(roles.data ?? []).map((r) => (
                  <label key={r.id} className="flex min-h-10 items-center gap-2.5 rounded-md px-2 text-sm hover:bg-hover">
                    <input
                      type="checkbox"
                      checked={roleIds.includes(r.id)}
                      onChange={(e) => setRoleIds(e.target.checked ? [...roleIds, r.id] : roleIds.filter((x) => x !== r.id))}
                      className="h-4 w-4 accent-orange-600"
                    />
                    <span>{r.name}</span>
                    {r.description && <span className="truncate text-xs text-gray-400">{r.description}</span>}
                  </label>
                ))}
              </div>
            )}
          </fieldset>
          <div>
            <label htmlFor="key-expiry" className="label">Expires after</label>
            <select id="key-expiry" value={days ?? ''} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)} className="input">
              {EXPIRY.map((x) => <option key={x.label} value={x.days ?? ''}>{x.label}</option>)}
            </select>
          </div>
          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={close} className="btn btn-secondary min-h-11">Cancel</button>
            <button type="submit" disabled={create.isPending} className="btn btn-primary min-h-11 disabled:opacity-60">
              {create.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />} Create key
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
