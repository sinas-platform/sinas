import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { REFRESH_KEY, TOKEN_KEY, USER_KEY, clearStoredSession, runtimeApi, setSignedOutHandler } from './http';

export type AuthMode = 'otp' | 'password' | 'password+otp';

export interface AuthUser {
  id: string;
  email: string;
  roles: string[];
  last_login_at: string | null;
  created_at: string;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

interface LoginResponse extends Partial<TokenResponse> {
  message: string;
  session_id?: string | null;
}

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  authMode: AuthMode;
  /** The OTP session to verify next, or undefined once signed in. */
  login: (email: string, password?: string) => Promise<string | undefined>;
  verifyOtp: (sessionId: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function storedUser(): AuthUser | null {
  if (!localStorage.getItem(TOKEN_KEY) || !localStorage.getItem(REFRESH_KEY)) return null;
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null') as AuthUser | null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(storedUser);
  const [loading, setLoading] = useState<boolean>(() => storedUser() !== null);
  const [authMode, setAuthMode] = useState<AuthMode>('otp');
  // A check started for an earlier session may not end a newer one.
  const generation = useRef(0);

  const signedOut = useCallback(() => {
    generation.current += 1;
    clearStoredSession();
    queryClient.clear();
    setUser(null);
  }, [queryClient]);

  useEffect(() => {
    setSignedOutHandler(signedOut);
    return () => setSignedOutHandler(null);
  }, [signedOut]);

  useEffect(() => {
    runtimeApi.get<{ auth_mode: AuthMode }>('/info', { quietStatuses: [404, 500, 502, 503] })
      .then((r) => setAuthMode(r.data.auth_mode))
      .catch(() => setAuthMode('otp'));
  }, []);

  // A stored session is checked once at startup.
  useEffect(() => {
    if (!storedUser()) return;
    const started = generation.current;
    runtimeApi.get<AuthUser>('/auth/me')
      .then((r) => {
        if (generation.current !== started) return;
        setUser(r.data);
        localStorage.setItem(USER_KEY, JSON.stringify(r.data));
      })
      .catch(() => { if (generation.current === started) signedOut(); })
      .finally(() => setLoading(false));
  }, [signedOut]);

  const persist = (r: TokenResponse) => {
    generation.current += 1;
    localStorage.setItem(TOKEN_KEY, r.access_token);
    localStorage.setItem(REFRESH_KEY, r.refresh_token);
    localStorage.setItem(USER_KEY, JSON.stringify(r.user));
    setUser(r.user);
  };

  const login = async (email: string, password?: string) => {
    const { data } = await runtimeApi.post<LoginResponse>('/auth/login', { email, password }, { quietStatuses: [400, 401, 403, 429] });
    if (data.access_token && data.refresh_token && data.user) {
      persist(data as TokenResponse);
      return undefined;
    }
    return data.session_id ?? undefined;
  };

  const verifyOtp = async (sessionId: string, code: string) => {
    const { data } = await runtimeApi.post<TokenResponse>('/auth/verify-otp', { session_id: sessionId, otp_code: code }, { quietStatuses: [400, 401, 403, 429] });
    persist(data);
  };

  const logout = async () => {
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    if (refreshToken) {
      await runtimeApi.post('/auth/logout', { refresh_token: refreshToken }, { quietStatuses: [400, 401, 403, 404, 500] }).catch(() => undefined);
    }
    signedOut();
  };

  return (
    <AuthContext.Provider value={{ user, loading, authMode, login, verifyOtp, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}
