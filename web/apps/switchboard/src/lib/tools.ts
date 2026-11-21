// Pure helpers, kept free of imports so node --test runs them directly.

type ToolType = 'function' | 'query' | 'pipeline';

interface ToolRef {
  type: ToolType;
  namespace: string;
  name: string;
  tool_name?: string | null;
}

/** The name an agent sees when none is set: `namespace__name` in the MCP
 * character set, as the backend derives it. */
export function defaultToolName(namespace: string, name: string): string {
  return `${namespace}__${name}`.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 64);
}

export const TOOL_NAME_PATTERN = /^[a-zA-Z0-9_-]{1,64}$/;

/** The name each tool is served under. */
export function servedNames(tools: ToolRef[]): string[] {
  return tools.map((t) => t.tool_name || defaultToolName(t.namespace, t.name));
}

/** Names served by more than one tool: the backend refuses to save those. */
export function duplicateNames(tools: ToolRef[]): string[] {
  const seen = new Set<string>();
  const dupes = new Set<string>();
  for (const name of servedNames(tools)) {
    if (seen.has(name)) dupes.add(name);
    seen.add(name);
  }
  return [...dupes];
}

/** Why the switchboard would leave a resource out of its tool list. */
export function unavailableReason(
  type: ToolType,
  r: { is_active?: boolean; as_tool?: boolean; requires_approval?: boolean },
): string | null {
  if (r.is_active === false) return 'Inactive';
  if (type === 'pipeline' && r.as_tool === false) return 'Not marked as a tool';
  if (type === 'function' && r.requires_approval) return 'Needs approval, which agents cannot give';
  return null;
}

export type ClientId = 'claude' | 'chatgpt' | 'cursor' | 'apikey';

export const CLIENTS: { id: ClientId; label: string }[] = [
  { id: 'claude', label: 'Claude' },
  { id: 'chatgpt', label: 'ChatGPT' },
  { id: 'cursor', label: 'Cursor' },
  { id: 'apikey', label: 'API key' },
];

/** How to connect a client to a switchboard endpoint. */
export function connectSnippet(client: ClientId, url: string, label: string): string {
  switch (client) {
    case 'claude':
      return `Claude: Settings › Connectors › Add custom connector\n\nName:  ${label}\nURL:   ${url}`;
    case 'chatgpt':
      return `ChatGPT: Settings › Apps & Connectors › Create\n\nMCP server URL:  ${url}\nAuthentication:  OAuth`;
    case 'cursor':
      return JSON.stringify({ mcpServers: { [label]: { url } } }, null, 2);
    case 'apikey':
      return [
        `curl ${url} \\`,
        `  -H "Authorization: Bearer $SINAS_API_KEY" \\`,
        `  -H "Content-Type: application/json" \\`,
        `  -H "Accept: application/json, text/event-stream" \\`,
        `  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`,
      ].join('\n');
  }
}

/** One CSV field, quoted when it has to be. */
export function csvField(value: unknown): string {
  const s = value === null || value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(header: string[], rows: unknown[][]): string {
  return [header, ...rows].map((row) => row.map(csvField).join(',')).join('\n');
}

/** "3 min ago", "yesterday", "in 9 days". */
export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return 'never';
  const diff = new Date(iso).getTime() - now;
  const abs = Math.abs(diff);
  const units: [number, string][] = [[86400000, 'day'], [3600000, 'hour'], [60000, 'minute']];
  for (const [ms, unit] of units) {
    if (abs >= ms) {
      const n = Math.round(abs / ms);
      if (unit === 'day' && n === 1) return diff < 0 ? 'yesterday' : 'tomorrow';
      const label = `${n} ${unit}${n === 1 ? '' : 's'}`;
      return diff < 0 ? `${label} ago` : `in ${label}`;
    }
  }
  return diff < 0 ? 'just now' : 'in a moment';
}

export const WINDOWS: { seconds: number; label: string }[] = [
  { seconds: 60, label: 'minute' },
  { seconds: 3600, label: 'hour' },
  { seconds: 86400, label: 'day' },
];

export function describeLimit(limits: { max_calls: number; window_seconds: number } | null): string {
  if (!limits) return 'Deployment default limit';
  if (limits.max_calls === 0) return 'No rate limit';
  const w = WINDOWS.find((x) => x.seconds === limits.window_seconds);
  return `${limits.max_calls} calls / ${w ? w.label : `${limits.window_seconds} s`} per caller`;
}
