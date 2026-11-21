import axios, { type AxiosError, type AxiosInstance, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios';

/** Where the backend is. VITE_API_URL wins. On localhost an app on one of
 * the frontends' own ports (or Vite dev) talks to the conventional backend
 * on :8000; any other localhost port means the backend served the app, so
 * same origin. A real domain: same host, default port. */
const FRONTEND_PORTS = ['51245', '51246', ''];
export const API_BASE_URL: string = import.meta.env.VITE_API_URL
  ? import.meta.env.VITE_API_URL
  : window.location.hostname === 'localhost'
    ? (import.meta.env.DEV || FRONTEND_PORTS.includes(window.location.port)
        ? 'http://localhost:8000'
        : window.location.origin)
    : `${window.location.protocol}//${window.location.hostname}`;

/** The same keys as the console, so a browser signed in to one app on an
 * origin is signed in to all of them. */
export const TOKEN_KEY = 'auth_token';
export const REFRESH_KEY = 'refresh_token';
export const USER_KEY = 'user';

export function clearStoredSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

declare module 'axios' {
  interface AxiosRequestConfig {
    /** Statuses that don't raise the global error toast, because the page
     * explains them in place. */
    quietStatuses?: number[];
  }
}

type RetryConfig = AxiosRequestConfig & { _retry?: boolean };

/** The readable message of a failed request: the API's `detail`, else the
 * transport error. */
export function errorMessage(error: unknown): string {
  const err = error as AxiosError<{ detail?: unknown; message?: unknown }>;
  const detail = err?.response?.data?.detail ?? err?.response?.data?.message;
  if (typeof detail === 'string') return detail;
  if (detail && typeof (detail as { message?: unknown }).message === 'string') {
    return (detail as { message: string }).message;
  }
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg}, ...]
    return detail.map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d)).join('; ');
  }
  return err?.message || 'Request failed';
}

let onError: ((message: string) => void) | null = null;
let onSignedOut: (() => void) | null = null;

/** The toast that shows failed requests. */
export function setErrorHandler(handler: ((message: string) => void) | null) {
  onError = handler;
}

/** Called when the session can't be refreshed any more. */
export function setSignedOutHandler(handler: (() => void) | null) {
  onSignedOut = handler;
}

let refreshing: Promise<string> | null = null;

/** One refresh at a time; every 401 in the meantime waits for it. */
export function refreshAccessToken(): Promise<string> {
  if (!refreshing) {
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    refreshing = (refreshToken
      ? axios.post<{ access_token: string }>(`${API_BASE_URL}/auth/refresh`, { refresh_token: refreshToken })
          .then((r) => {
            localStorage.setItem(TOKEN_KEY, r.data.access_token);
            return r.data.access_token;
          })
      : Promise.reject(new Error('No refresh token'))
    ).finally(() => { refreshing = null; });
  }
  return refreshing;
}

function withAuth(client: AxiosInstance): AxiosInstance {
  client.interceptors.request.use((config) => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  });
  client.interceptors.response.use(
    (response) => response,
    async (error: AxiosError) => {
      const config = error.config as (RetryConfig & InternalAxiosRequestConfig) | undefined;
      const status = error.response?.status;
      if (status === 401 && config && !config._retry && !config.url?.startsWith('/auth/')) {
        config._retry = true;
        try {
          const token = await refreshAccessToken();
          config.headers.set('Authorization', `Bearer ${token}`);
          return client(config);
        } catch {
          clearStoredSession();
          onSignedOut?.();
          return Promise.reject(error);
        }
      }
      if (status !== 401 && !(status && config?.quietStatuses?.includes(status))) {
        onError?.(errorMessage(error));
      }
      return Promise.reject(error);
    },
  );
  return client;
}

/** `/api/v1` (management) and the runtime API at the root. */
export const configApi = withAuth(axios.create({ baseURL: `${API_BASE_URL}/api/v1` }));
export const runtimeApi = withAuth(axios.create({ baseURL: API_BASE_URL }));
