import { API_BASE_URL } from '@sinas/ui';

/** The MCP endpoint agents connect to. */
export function endpointUrl(namespace: string, name: string): string {
  return `${API_BASE_URL}/mcp/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}`;
}

/** A page in the Sinas console: VITE_CONSOLE_URL, else the console's
 * conventional place next to this app (its own port on localhost and behind
 * Caddy, /ui on the same origin otherwise). */
export function consoleUrl(path: string): string {
  const configured = import.meta.env.VITE_CONSOLE_URL as string | undefined;
  const { protocol, hostname, port } = window.location;
  const base = configured
    ? configured.replace(/\/$/, '')
    : port === '51246' || import.meta.env.DEV
      ? `${protocol}//${hostname}:51245/ui`
      : `${window.location.origin}/ui`;
  return `${base}${path}`;
}
