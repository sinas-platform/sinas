// The stored link between this Studio app and a Sinas workspace.
import type { AuthUser } from './types';

const KEY = 'studio-connection';

export interface Connection {
  baseUrl: string; // e.g. https://sinas.company.com
  accessToken: string;
  refreshToken: string;
  user: AuthUser;
}

export function getConnection(): Connection | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Connection) : null;
  } catch {
    return null;
  }
}

export function saveConnection(conn: Connection): void {
  localStorage.setItem(KEY, JSON.stringify(conn));
}

export function updateTokens(accessToken: string, refreshToken: string): void {
  const conn = getConnection();
  if (conn) saveConnection({ ...conn, accessToken, refreshToken });
}

export function clearConnection(): void {
  localStorage.removeItem(KEY);
}

/** Normalize whatever the user typed into an origin we can call. */
export function normalizeBaseUrl(input: string): string {
  let url = input.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//.test(url)) url = `https://${url}`;
  return url;
}
