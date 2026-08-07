import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { fetchInstanceInfo, login, verifyOtp } from '../lib/api';
import { normalizeBaseUrl, saveConnection } from '../lib/connection';
import type { InstanceInfo } from '../lib/types';

type Step = 'probing' | 'workspace' | 'credentials' | 'otp';

export function Connect() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>('probing');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [urlInput, setUrlInput] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [info, setInfo] = useState<InstanceInfo | null>(null);
  // True when Studio is served from the workspace itself (bundled at
  // <workspace>/studio/) — the workspace step is skipped entirely then.
  const [workspaceContext, setWorkspaceContext] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [otp, setOtp] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetchInstanceInfo(window.location.origin)
      .then((instanceInfo) => {
        if (cancelled) return;
        setBaseUrl(window.location.origin);
        setInfo(instanceInfo);
        setWorkspaceContext(true);
        setStep('credentials');
      })
      .catch(() => {
        if (!cancelled) setStep('workspace'); // standalone deployment
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const needsPassword = info?.auth_mode === 'password' || info?.auth_mode === 'password+otp';

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  const checkWorkspace = () =>
    run(async () => {
      const url = normalizeBaseUrl(urlInput);
      const instanceInfo = await fetchInstanceInfo(url);
      setBaseUrl(url);
      setInfo(instanceInfo);
      setStep('credentials');
    });

  const finish = (access: string, refresh: string, user: any) => {
    saveConnection({ baseUrl, accessToken: access, refreshToken: refresh, user });
    navigate('/projects');
  };

  const submitCredentials = () =>
    run(async () => {
      const res = await login(baseUrl, email, needsPassword ? password : undefined);
      if (res.access_token && res.refresh_token && res.user) {
        finish(res.access_token, res.refresh_token, res.user);
      } else if (res.session_id) {
        setSessionId(res.session_id);
        setStep('otp');
      } else {
        setError('Unexpected response from the workspace');
      }
    });

  const submitOtp = () =>
    run(async () => {
      const res = await verifyOtp(baseUrl, sessionId, otp);
      finish(res.access_token!, res.refresh_token!, res.user!);
    });

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 20 }}>
      <div className="card" style={{ width: '100%', maxWidth: 420, padding: 28 }}>
        <div className="wordmark" style={{ marginBottom: 6 }}>
          <span className="wm-name" style={{ fontSize: 22 }}>sinas</span>
          <span className="wm-app">Studio</span>
        </div>
        <p style={{ color: 'var(--muted)', fontSize: 13.5, margin: '0 0 22px' }}>
          Build assistants and automations on your Sinas workspace.
        </p>

        {step === 'probing' && (
          <div style={{ display: 'grid', placeItems: 'center', padding: 24 }}>
            <Loader2 size={18} className="spin" />
          </div>
        )}

        {step === 'workspace' && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              checkWorkspace();
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
          >
            <div className="field">
              <label>Workspace address</label>
              <input
                className="input"
                placeholder="sinas.yourcompany.com"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                autoFocus
              />
            </div>
            {error && <div className="error-box">{error}</div>}
            <button className="btn btn-primary" disabled={busy || !urlInput.trim()} style={{ justifyContent: 'center' }}>
              {busy ? <Loader2 size={15} className="spin" /> : 'Continue'}
            </button>
          </form>
        )}

        {step === 'credentials' && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitCredentials();
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
          >
            {!workspaceContext && (
              <p style={{ fontSize: 12.5, color: 'var(--faint)', margin: 0 }}>
                Connecting to <b style={{ color: 'var(--ink)' }}>{new URL(baseUrl).host}</b>{' '}
                <button type="button" className="link-btn" onClick={() => setStep('workspace')}>
                  change
                </button>
              </p>
            )}
            <div className="field">
              <label>Email</label>
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
            </div>
            {needsPassword && (
              <div className="field">
                <label>Password</label>
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
            )}
            {error && <div className="error-box">{error}</div>}
            <button
              className="btn btn-primary"
              disabled={busy || !email.trim() || (needsPassword && !password)}
              style={{ justifyContent: 'center' }}
            >
              {busy ? <Loader2 size={15} className="spin" /> : needsPassword ? 'Sign in' : 'Email me a code'}
            </button>
          </form>
        )}

        {step === 'otp' && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitOtp();
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
          >
            <p style={{ fontSize: 13, color: 'var(--muted)', margin: 0 }}>
              We sent a sign-in code to <b style={{ color: 'var(--ink)' }}>{email}</b>.
            </p>
            <div className="field">
              <label>Code</label>
              <input
                className="input"
                inputMode="numeric"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                autoFocus
              />
            </div>
            {error && <div className="error-box">{error}</div>}
            <button className="btn btn-primary" disabled={busy || !otp.trim()} style={{ justifyContent: 'center' }}>
              {busy ? <Loader2 size={15} className="spin" /> : 'Connect'}
            </button>
          </form>
        )}

        {/* Deliberately unobtrusive: in workspace context this is an escape
            hatch for the rare cross-workspace case, not a primary action. */}
        {workspaceContext && step === 'credentials' && (
          <p style={{ margin: '18px 0 0', textAlign: 'center' }}>
            <button
              type="button"
              className="link-btn"
              style={{ fontSize: 11, color: 'var(--faint)', fontWeight: 500 }}
              onClick={() => {
                setWorkspaceContext(false);
                setBaseUrl('');
                setInfo(null);
                setStep('workspace');
              }}
            >
              connect to a different workspace
            </button>
          </p>
        )}
      </div>
    </div>
  );
}
