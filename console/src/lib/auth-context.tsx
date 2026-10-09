import React, { createContext, useContext, useState, useEffect, useRef, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { API_BASE_URL } from './api';
import { SinasClient } from '@sinas/sdk';
import type { AuthUser, InfoResponse, SinasConfig } from '@sinas/sdk';
import type { AuthMode, InstanceInfo } from '../types';

declare global {
  interface Window {
    __SINAS_AUTH_TOKEN__?: string | null;
    __SINAS_CONFIG__?: SinasConfig;
  }
}

interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  authMode: AuthMode;
  instanceInfo: InstanceInfo | null;
  /** Returns session_id when an OTP step follows; undefined for password-only mode. */
  login: (email: string, password?: string) => Promise<string | undefined>;
  verifyOTP: (sessionId: string, otpCode: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

function toInstanceInfo(info: InfoResponse): InstanceInfo {
  return {
    auth_mode: info.auth_mode as AuthMode,
    version: info.version,
    features: info.features,
  };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [instanceInfo, setInstanceInfo] = useState<InstanceInfo | null>(null);
  const refreshTimerRef = useRef<number | null>(null);
  const tokenRef = useRef<string | null>(null);

  // Single SinasClient configured for standalone (browser) use. Token getters
  // read from localStorage so every request — including those issued from the
  // SDK's own auto-refresh path — always sees the latest tokens.
  const client = useMemo<SinasClient>(
    () =>
      new SinasClient({
        baseUrl: API_BASE_URL,
        getAccessToken: () => localStorage.getItem('auth_token'),
        getRefreshToken: () => localStorage.getItem('refresh_token'),
        onTokenRefresh: (access) => {
          localStorage.setItem('auth_token', access);
          setToken(access);
          window.__SINAS_AUTH_TOKEN__ = access;
        },
        onUnauthenticated: () => {
          localStorage.removeItem('auth_token');
          localStorage.removeItem('refresh_token');
          localStorage.removeItem('user');
          window.__SINAS_AUTH_TOKEN__ = null;
          setToken(null);
          setUser(null);
        },
      }),
    [],
  );

  // Fetch instance info once at startup so login UI knows which auth mode to render
  useEffect(() => {
    client.auth.getInfo()
      .then((info) => setInstanceInfo(toInstanceInfo(info)))
      .catch((err) => {
        // Fall back to OTP if /info is unreachable (older backend)
        console.error('Failed to fetch instance info:', err);
        setInstanceInfo({ auth_mode: 'otp', version: 'unknown', features: {} });
      });
  }, [client]);

  // Clear refresh timer
  const clearRefreshTimer = () => {
    if (refreshTimerRef.current) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  };

  // Proactive token refresh every 14 minutes (access tokens expire after 15).
  const setupRefreshTimer = () => {
    clearRefreshTimer();
    refreshTimerRef.current = window.setInterval(async () => {
      const refreshToken = localStorage.getItem('refresh_token');
      if (!refreshToken) return;
      try {
        const response = await client.auth.refresh(refreshToken);
        localStorage.setItem('auth_token', response.access_token);
        setToken(response.access_token);
        window.__SINAS_AUTH_TOKEN__ = response.access_token;
      } catch (error) {
        console.error('Failed to refresh token:', error);
        // Refresh failed — user will be logged out on next API call
        clearRefreshTimer();
      }
    }, 14 * 60 * 1000);
  };

  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  useEffect(() => {
    // Restore session from storage
    const storedToken = localStorage.getItem('auth_token');
    const storedRefreshToken = localStorage.getItem('refresh_token');
    const storedUser = localStorage.getItem('user');

    if (storedToken && storedRefreshToken && storedUser) {
      setToken(storedToken);
      try {
        setUser(JSON.parse(storedUser) as AuthUser);
      } catch {
        // ignore — refetch below
      }

      // Setup proactive refresh timer
      setupRefreshTimer();

      // Verify token is still valid
      client.auth.getMe()
        .then((u) => {
          setUser(u);
          localStorage.setItem('user', JSON.stringify(u));
        })
        .catch((error) => {
          // Token invalid, clear
          console.error('Token verification failed on refresh:', error);
          localStorage.removeItem('auth_token');
          localStorage.removeItem('refresh_token');
          localStorage.removeItem('user');
          setToken(null);
          setUser(null);
          clearRefreshTimer();
        })
        .finally(() => {
          setLoading(false);
        });
    } else {
      setLoading(false);
    }

    return () => clearRefreshTimer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync auth token to window global for embedded components / older SDK callers
  useEffect(() => {
    window.__SINAS_AUTH_TOKEN__ = token;
  }, [token]);

  // Set SINAS config for @sinas/sdk embedded callers (once)
  useEffect(() => {
    if (!window.__SINAS_CONFIG__) {
      window.__SINAS_CONFIG__ = {
        apiBase: API_BASE_URL,
        component: { namespace: '', name: '', version: '' },
        resources: {
          enabledAgents: [],
          enabledFunctions: [],
          enabledQueries: [],
          enabledComponents: [],
          enabledStores: [] as Array<{ store: string; access: 'readonly' | 'readwrite' }>,
        },
        input: {},
      };
    }
  }, []);

  const persistSession = (access: string, refresh: string, u: AuthUser) => {
    setToken(access);
    setUser(u);
    localStorage.setItem('auth_token', access);
    localStorage.setItem('refresh_token', refresh);
    localStorage.setItem('user', JSON.stringify(u));
    window.__SINAS_AUTH_TOKEN__ = access;
    setupRefreshTimer();
  };

  const login = async (email: string, password?: string): Promise<string | undefined> => {
    const response = await client.auth.login({ email, password });
    // Password-only mode: tokens returned immediately
    if (response.access_token && response.refresh_token && response.user) {
      persistSession(response.access_token, response.refresh_token, response.user);
      return undefined;
    }
    // OTP or password+otp: caller must invoke verifyOTP next
    return response.session_id;
  };

  const verifyOTP = async (sessionId: string, otpCode: string): Promise<void> => {
    const response = await client.auth.verifyOTP({
      session_id: sessionId,
      otp_code: otpCode,
    });
    persistSession(response.access_token, response.refresh_token, response.user);
  };

  const logout = async () => {
    const refreshToken = localStorage.getItem('refresh_token');

    if (refreshToken) {
      try {
        await client.auth.logout(refreshToken);
      } catch (error) {
        console.error('Logout error:', error);
        // Continue with local cleanup even if API call fails
      }
    }

    setToken(null);
    setUser(null);
    window.__SINAS_AUTH_TOKEN__ = null;
    // Nothing cached for this user may outlive the session (share links
    // carry credentials; any page's data is theirs).
    queryClient.clear();
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');

    clearRefreshTimer();
  };

  const authMode: AuthMode = instanceInfo?.auth_mode ?? 'otp';

  return (
    <AuthContext.Provider
      value={{ user, token, loading, authMode, instanceInfo, login, verifyOTP, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
