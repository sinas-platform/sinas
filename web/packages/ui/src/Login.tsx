import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { KeyRound, Loader2, Lock, Mail } from 'lucide-react';
import { useAuth } from './auth';
import { errorMessage } from './http';
import { SinasMark } from './Logo';
import { ThemeToggle } from './ThemeToggle';

/** Only paths inside this app are followed after signing in. */
export function returnPath(state: unknown): string {
  const from = (state as { from?: string } | null)?.from;
  return from && from.startsWith('/') && !from.startsWith('//') ? from : '/';
}

interface LoginProps {
  product: string;
  tagline: string;
  /** Where people with single sign-on go instead (the console signs them in). */
  ssoHref?: string;
}

export function Login({ product, tagline, ssoHref }: LoginProps) {
  const { user, authMode, login, verifyOtp } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const needsPassword = authMode === 'password' || authMode === 'password+otp';

  if (user) return <Navigate to={returnPath(location.state)} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (sessionId) {
        await verifyOtp(sessionId, code.trim());
        navigate(returnPath(location.state), { replace: true });
      } else {
        const next = await login(email.trim(), needsPassword ? password : undefined);
        if (next) setSessionId(next);
        else navigate(returnPath(location.state), { replace: true });
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-surface-page px-4 text-gray-100">
      <div className="absolute right-4 top-4"><ThemeToggle /></div>
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <SinasMark size={44} />
          <h1 className="text-2xl font-bold">{product}</h1>
          <p className="text-sm text-gray-400">{tagline}</p>
        </div>
        <form onSubmit={submit} className="card flex flex-col gap-4">
          {!sessionId ? (
            <>
              <div>
                <label htmlFor="email" className="label">Email</label>
                <div className="relative">
                  <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" aria-hidden="true" />
                  <input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="input pl-9" />
                </div>
              </div>
              {needsPassword && (
                <div>
                  <label htmlFor="password" className="label">Password</label>
                  <div className="relative">
                    <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" aria-hidden="true" />
                    <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} className="input pl-9" />
                  </div>
                </div>
              )}
            </>
          ) : (
            <div>
              <label htmlFor="code" className="label">Code from your email</label>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" aria-hidden="true" />
                <input id="code" inputMode="numeric" autoComplete="one-time-code" required autoFocus value={code} onChange={(e) => setCode(e.target.value)} className="input pl-9 tracking-widest" />
              </div>
            </div>
          )}
          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
          <button type="submit" disabled={busy} className="btn btn-primary min-h-11 justify-center disabled:opacity-60">
            {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {sessionId ? 'Verify' : needsPassword ? 'Sign in' : 'Email me a code'}
          </button>
          {sessionId && (
            <button type="button" onClick={() => { setSessionId(null); setCode(''); }} className="text-sm text-gray-400 hover:text-gray-200">
              Use another email
            </button>
          )}
        </form>
        {ssoHref && (
          <p className="mt-4 text-center text-sm text-gray-400">
            Your organisation uses single sign-on? <a href={ssoHref} className="text-orange-300 hover:text-orange-200">Sign in through the console</a>
          </p>
        )}
      </div>
    </div>
  );
}
