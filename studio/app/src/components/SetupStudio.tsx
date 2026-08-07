import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Sparkles, X } from 'lucide-react';
import { ApiError, api } from '../lib/api';
import { getConnection } from '../lib/connection';
// Bundled at build time — same repo, same release train, so the installed
// package version always matches this Studio build.
import studioRuntimeYaml from '../../../packages/studio-runtime.yaml?raw';

/** True while the companion package is installed in the workspace. */
export function useStudioRuntimeInstalled(): boolean {
  const { data: packages } = useQuery({ queryKey: ['packages'], queryFn: api.listPackages, retry: false });
  return (packages ?? []).some((p) => p.name === 'studio-runtime');
}

/**
 * Install trigger for the studio-runtime package. Shown to everyone when the
 * package is missing: the platform doesn't expose permissions on /auth/me yet
 * (studio/README.md §7.5), so authorization is attempt-based — a 403 becomes
 * an "ask your admin" message rather than a hidden button.
 */
export function SetupStudioLink() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="btn btn-secondary btn-sm" onClick={() => setOpen(true)}>
        <Sparkles size={13} style={{ color: 'var(--accent)' }} /> Set up Studio
      </button>
      {open && <SetupStudioModal onClose={() => setOpen(false)} />}
    </>
  );
}

function SetupStudioModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [workspaceUrl, setWorkspaceUrl] = useState(getConnection()?.baseUrl ?? '');

  const install = useMutation({
    mutationFn: () =>
      api.installPackage(studioRuntimeYaml, { WORKSPACE_URL: workspaceUrl.replace(/\/+$/, '') }),
  });

  // Refetching flips the parent's "not installed" conditionals, which unmounts
  // this modal — so hold the invalidation until the user dismisses it.
  const close = () => {
    if (install.isSuccess) queryClient.invalidateQueries();
    onClose();
  };

  const denied = install.error instanceof ApiError && install.error.status === 403;

  return (
    <div className="overlay" onClick={close}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
          <h2 style={{ fontSize: 16 }}>Set up Studio</h2>
          <button className="kebab" onClick={close} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        {install.isSuccess ? (
          <>
            <p style={{ fontSize: 13.5, color: 'var(--muted)', margin: '0 0 14px' }}>
              Done — webhook workflows and “Improve with AI” are now available in this workspace.
            </p>
            <button className="btn btn-primary" style={{ justifyContent: 'center', width: '100%' }} onClick={close}>
              Close
            </button>
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (workspaceUrl.trim() && !install.isPending) install.mutate();
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
          >
            <p style={{ fontSize: 13, color: 'var(--muted)', margin: 0 }}>
              Installs the <b>studio-runtime</b> package that ships with this version of Studio. It adds two
              things to the workspace: the <b>studio/copilot</b> assistant (powers “Improve with AI”) and the{' '}
              <b>studio/ask-agent</b> function (lets web requests start assistants).
            </p>
            <div className="field">
              <label>Workspace URL</label>
              <input
                className="input"
                value={workspaceUrl}
                onChange={(e) => setWorkspaceUrl(e.target.value)}
                placeholder="https://sinas.yourcompany.com"
              />
              <p style={{ fontSize: 11.5, color: 'var(--faint)', margin: '6px 0 0' }}>
                Must be reachable from the containers that run functions. The public workspace address usually
                works; for a local Docker deployment use <code>http://host.docker.internal:8000</code>.
              </p>
            </div>
            {install.isError && (
              <div className="error-box">
                {denied
                  ? 'Your account can’t install packages. Ask a workspace admin to open this dialog, or to install studio/packages/studio-runtime.yaml.'
                  : (install.error as Error).message}
              </div>
            )}
            <button
              className="btn btn-primary"
              disabled={!workspaceUrl.trim() || install.isPending}
              style={{ justifyContent: 'center' }}
            >
              {install.isPending ? <Loader2 size={14} className="spin" /> : 'Install into this workspace'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
