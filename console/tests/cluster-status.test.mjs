// Tests for console/src/lib/clusterStatus.ts: the System page's
// Kubernetes-dependent panels and their polling backoff.
//
// Run: npm test (node:test; the module is transpiled with esbuild).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const entry = fileURLToPath(new URL('../src/lib/clusterStatus.ts', import.meta.url));
const { outputFiles } = await build({ entryPoints: [entry], bundle: true, format: 'esm', write: false, logLevel: 'silent' });
const m = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`);

test('health response: available unless it carries a cluster reason', () => {
  assert.deepEqual(m.healthClusterStatus(undefined), { available: true, reason: null, retryAfterSeconds: null });
  assert.equal(m.healthClusterStatus({ services: [], cluster_unavailable_reason: null }).available, true);
  // a Docker deployment's own services_unavailable_reason is not the cluster
  assert.equal(m.healthClusterStatus({ services_unavailable_reason: 'Docker API not reachable' }).available, true);
  assert.deepEqual(
    m.healthClusterStatus({ cluster_unavailable_reason: 'did not answer in time', cluster_retry_after_seconds: 60 }),
    { available: false, reason: 'did not answer in time', retryAfterSeconds: 60 },
  );
  assert.equal(m.healthClusterStatus({ cluster_unavailable_reason: 'turned off' }).retryAfterSeconds, null);
});

test('executor cards: only cluster_unavailable counts', () => {
  assert.equal(m.cardClusterStatus({ unavailable_reason: 'Code execution is disabled' }).available, true);
  assert.deepEqual(
    m.cardClusterStatus({ cluster_unavailable: true, unavailable_reason: 'forbidden', retry_after_seconds: 42 }),
    { available: false, reason: 'forbidden', retryAfterSeconds: 42 },
  );
  assert.equal(m.cardClusterStatus({ cluster_unavailable: true, retry_after_seconds: 'soon' }).retryAfterSeconds, null);
});

test('normal polling while the cluster status is available or unknown', () => {
  const ok = m.healthClusterStatus({});
  assert.equal(m.clusterPollInterval(5000, ok), 5000);
  assert.equal(m.clusterPollInterval(5000, null), 5000);
  assert.equal(m.clusterPollInterval(30000, undefined, 0), 30000);
});

test('backs off to the backend retry window, within bounds', () => {
  const down = (s) => ({ available: false, reason: 'x', retryAfterSeconds: s });
  assert.equal(m.clusterPollInterval(5000, down(3)), m.CLUSTER_POLL_MIN_MS); // never faster than a minute
  assert.equal(m.clusterPollInterval(5000, down(120)), 120_000); // the breaker window
  assert.equal(m.clusterPollInterval(5000, down(3600)), m.CLUSTER_POLL_MAX_MS); // still re-checks
  assert.equal(m.clusterPollInterval(5000, down(null)), m.CLUSTER_POLL_MAX_MS); // not retrying (turned off)
  // a slow base interval is never sped up
  assert.equal(m.clusterPollInterval(600_000, down(60)), 600_000);
});

test('failed requests back off exponentially, capped', () => {
  assert.equal(m.clusterPollInterval(5000, null, 1), 10_000);
  assert.equal(m.clusterPollInterval(5000, null, 3), 40_000);
  assert.equal(m.clusterPollInterval(5000, null, 10), m.ERROR_POLL_MAX_MS);
  assert.equal(m.clusterPollInterval(10_000, m.healthClusterStatus({}), 2), 40_000);
});

test('retry label', () => {
  assert.equal(m.retryLabel(null), null);
  assert.equal(m.retryLabel(12), 'The backend tries again within a minute.');
  assert.equal(m.retryLabel(60), 'The backend tries again in about 1 minute.');
  assert.equal(m.retryLabel(240), 'The backend tries again in about 4 minutes.');
});
