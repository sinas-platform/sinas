// The System page's Kubernetes-dependent panels (Services, Sandbox and
// Shared containers): whether the cluster part of a polled response is
// available, and how often to poll while it isn't.
//
// The backend keeps a circuit breaker in front of the Kubernetes API: when
// it is unreachable or not allowed (no RBAC, a NetworkPolicy, turned off),
// the responses say so (`cluster_unavailable_reason` on /system/health,
// `cluster_unavailable` on the executor cards) along with when it tries
// again. Polling faster than that only re-reads the same reason, so these
// queries slow down until it is back. Pure: unit-tested in
// tests/cluster-status.test.mjs.

export interface ClusterStatus {
  available: boolean;
  reason: string | null;
  /** When the backend calls the API again; null = it isn't backing off
   *  (turned off, or a setting is missing). */
  retryAfterSeconds: number | null;
}

/** Poll no faster than this while the cluster part is unavailable... */
export const CLUSTER_POLL_MIN_MS = 60_000;
/** ...and at least this often, so recovery shows up on its own. */
export const CLUSTER_POLL_MAX_MS = 300_000;
/** Cap of the backoff after failed requests (5xx, network). */
export const ERROR_POLL_MAX_MS = 120_000;

const AVAILABLE: ClusterStatus = { available: true, reason: null, retryAfterSeconds: null };

function seconds(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null;
}

/** Cluster status of a /system/health response (null/undefined: not loaded yet). */
export function healthClusterStatus(data: unknown): ClusterStatus {
  const d = (data ?? {}) as Record<string, unknown>;
  const reason = typeof d.cluster_unavailable_reason === 'string' ? d.cluster_unavailable_reason : null;
  if (!reason) return AVAILABLE;
  return { available: false, reason, retryAfterSeconds: seconds(d.cluster_retry_after_seconds) };
}

/** Cluster status of an executor card (/containers/stats, /workers/count). */
export function cardClusterStatus(data: unknown): ClusterStatus {
  const d = (data ?? {}) as Record<string, unknown>;
  if (d.cluster_unavailable !== true) return AVAILABLE;
  const reason = typeof d.unavailable_reason === 'string' && d.unavailable_reason ? d.unavailable_reason : null;
  return { available: false, reason, retryAfterSeconds: seconds(d.retry_after_seconds) };
}

/** Consecutive failed polls of one query. react-query's fetchFailureCount
 *  only counts the retries within one fetch (it is reset when the next
 *  poll starts), so the streak is derived from the cumulative
 *  errorUpdateCount instead, and reset by a successful poll. */
export interface FailureStreak {
  errorUpdateCount: number;
  streak: number;
}

export function nextFailureStreak(
  prev: FailureStreak | undefined,
  state: { status: string; errorUpdateCount: number },
): FailureStreak {
  if (state.status !== 'error') return { errorUpdateCount: state.errorUpdateCount, streak: 0 };
  if (!prev) return { errorUpdateCount: state.errorUpdateCount, streak: 1 };
  const failedPolls = Math.max(0, state.errorUpdateCount - prev.errorUpdateCount);
  return { errorUpdateCount: state.errorUpdateCount, streak: prev.streak + failedPolls };
}

/**
 * react-query refetchInterval for a query that depends on the Kubernetes API.
 *
 * - failed polls (`failureCount`: consecutive failed polls, see
 *   nextFailureStreak): double the interval per failed poll,
 *   up to ERROR_POLL_MAX_MS, so a down backend isn't hammered either;
 * - cluster unavailable: the backend's retry-after, but at least
 *   CLUSTER_POLL_MIN_MS and at most CLUSTER_POLL_MAX_MS (CLUSTER_POLL_MAX_MS
 *   when it isn't retrying at all);
 * - otherwise the normal interval.
 */
export function clusterPollInterval(
  baseMs: number,
  status: ClusterStatus | null | undefined,
  failureCount = 0,
): number {
  if (failureCount > 0) {
    return Math.min(baseMs * 2 ** failureCount, Math.max(baseMs, ERROR_POLL_MAX_MS));
  }
  if (!status || status.available) return baseMs;
  if (status.retryAfterSeconds == null) return Math.max(baseMs, CLUSTER_POLL_MAX_MS);
  const retryMs = status.retryAfterSeconds * 1000;
  return Math.min(Math.max(baseMs, CLUSTER_POLL_MIN_MS, retryMs), Math.max(baseMs, CLUSTER_POLL_MAX_MS));
}

/** "The backend tries again in about 2 minutes." (null when not retrying). */
export function retryLabel(retryAfterSeconds: number | null): string | null {
  if (retryAfterSeconds == null) return null;
  if (retryAfterSeconds < 60) return 'The backend tries again within a minute.';
  const minutes = Math.round(retryAfterSeconds / 60);
  return `The backend tries again in about ${minutes} minute${minutes === 1 ? '' : 's'}.`;
}
