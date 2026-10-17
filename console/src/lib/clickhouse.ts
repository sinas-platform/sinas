// Request logs live in ClickHouse, which a deployment may leave out
// (CLICKHOUSE_HOST empty: the lite profile, or `clickhouse.enabled: false` in
// the Helm chart). `/info` reports it as `features.clickhouse`, and the
// ClickHouse-backed endpoints answer 503 when it's disabled or unreachable.
// Pure, so `npm test` covers it.

export const CLICKHOUSE_ENV_VAR = 'CLICKHOUSE_HOST';
export const CLICKHOUSE_DOCS_URL =
  'https://docs.sinas.co/getting-started/environment-variables#clickhouse-optional';

/** How often an unavailable (configured but unreachable) ClickHouse is
 * re-checked. A disabled one isn't: that only changes with a redeploy. */
export const UNAVAILABLE_REFETCH_MS = 30_000;

type InfoLike = { features?: Record<string, boolean> | null } | null | undefined;

export type ClickHouseFeature =
  /** The startup /info request hasn't settled yet: don't decide anything. */
  | 'pending'
  | 'enabled'
  | 'disabled';

/** ClickHouse availability from the instance info. The auth context keeps
 * `instanceInfo` null until /info settles (it sets a fallback on failure), so
 * null means pending. Once settled, only an explicit `false` disables: a
 * missing flag (older backend, or /info unreachable) counts as enabled, and a
 * 503 is then explained in place. */
export function clickHouseFeature(info: InfoLike): ClickHouseFeature {
  if (!info) return 'pending';
  return info.features?.clickhouse === false ? 'disabled' : 'enabled';
}

/** True for the 503 the request-log endpoints answer when ClickHouse is
 * disabled or unreachable. */
export function isUnavailableError(error: unknown): boolean {
  return (error as any)?.response?.status === 503;
}

export type RequestLogsState =
  | 'pending'
  /** Deliberately off on this deployment: explain how to turn it on. */
  | 'disabled'
  /** Configured, but the backend couldn't reach it (or reported it off). */
  | 'unavailable'
  /** Some other failure; the global error handling already reported it. */
  | 'error'
  | 'ok';

/** What the request-log view should show, from the instance info and the
 * error (if any) of its list request. */
export function requestLogsState(info: InfoLike, error: unknown): RequestLogsState {
  const feature = clickHouseFeature(info);
  if (feature !== 'enabled') return feature;
  if (!error) return 'ok';
  return isUnavailableError(error) ? 'unavailable' : 'error';
}

/** Refetch interval for a request-log query: slow polling while ClickHouse
 * is unavailable, so the view recovers on its own; none otherwise. */
export function unavailableRefetchInterval(error: unknown): number | false {
  return isUnavailableError(error) ? UNAVAILABLE_REFETCH_MS : false;
}

/** The backend's explanation of a 503, or a generic one. */
export function unavailableDetail(error: unknown): string {
  const detail = (error as any)?.response?.data?.detail;
  return typeof detail === 'string' && detail
    ? detail
    : 'Request logs are temporarily unavailable: ClickHouse could not be reached.';
}
