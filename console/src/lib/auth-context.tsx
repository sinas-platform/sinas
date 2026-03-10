import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { apiClient, API_BASE_URL } from './api';
import type { User } from '../types';
import type { SinasConfig } from '@sinas/sdk';

declare global {
  interface Window {
    __SINAS_AUTH_TOKEN__?: string | null;
    __SINAS_CONFIG__?: SinasConfig;
  }
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string) => Promise<string>; // Returns session_id
  verifyOTP: (sessionId: string, otpCode: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshTimerRef = useRef<number | null>(null);

  // Clear refresh timer
  const clearRefreshTimer = () => {
    if (refreshTimerRef.current) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  };

  // Setup proactive token refresh (every 14 minutes)
  const setupRefreshTimer = () => {
    clearRefreshTimer();

    refreshTimerRef.current = window.setInterval(async () => {
      const refreshToken = localStorage.getItem('refresh_token');
      if (refreshToken) {
        try {
          const response = await apiClient.refreshToken(refreshToken);
          localStorage.setItem('auth_token', response.access_token);
          setToken(response.access_token);
        } catch (error) {
          console.error('Failed to refresh token:', error);
          // Refresh failed, user will be logged out on next API call
          clearRefreshTimer();
        }
      }
    }, 14 * 60 * 1000); // 14 minutes
  };

  useEffect(() => {
    // Check for existing token
    const storedToken = localStorage.getItem('auth_token');
    const storedRefreshToken = localStorage.getItem('refresh_token');
    const storedUser = localStorage.getItem('user');

    if (storedToken && storedRefreshToken && storedUser) {
      setToken(storedToken);
      setUser(JSON.parse(storedUser));

      // Setup proactive refresh timer
      setupRefreshTimer();

      // Verify token is still valid
      apiClient.getCurrentUser()
        .then((user) => {
          setUser(user);
          localStorage.setItem('user', JSON.stringify(user));
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

    // Cleanup timer on unmount
    return () => clearRefreshTimer();
  }, []);

  // Sync auth token to window global for @sinas/sdk
  useEffect(() => {
    window.__SINAS_AUTH_TOKEN__ = token;
  }, [token]);

  // Set SINAS config for @sinas/sdk (once)
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

  const login = async (email: string): Promise<string> => {
    const response = await apiClient.login({ email });
    return response.session_id;
  };

  const verifyOTP = async (sessionId: string, otpCode: string): Promise<void> => {
    const response = await apiClient.verifyOTP({ session_id: sessionId, otp_code: otpCode });

    setToken(response.access_token);
    setUser(response.user);

    // Store both access and refresh tokens
    localStorage.setItem('auth_token', response.access_token);
    localStorage.setItem('refresh_token', response.refresh_token);
    localStorage.setItem('user', JSON.stringify(response.user));

    // Setup proactive refresh timer
    setupRefreshTimer();
  };

  const logout = async () => {
    const refreshToken = localStorage.getItem('refresh_token');

    // Call logout endpoint to revoke refresh token
    if (refreshToken) {
      try {
        await apiClient.logout(refreshToken);
      } catch (error) {
        console.error('Logout error:', error);
        // Continue with local cleanup even if API call fails
      }
    }

    // Clear local state
    setToken(null);
    setUser(null);
    window.__SINAS_AUTH_TOKEN__ = null;
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');

    // Clear refresh timer
    clearRefreshTimer();
  };

  return (
    <AuthContext.Provider value={{ user, token, loading, login, verifyOTP, logout }}>
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
