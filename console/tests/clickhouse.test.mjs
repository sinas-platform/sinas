// Tests for console/src/lib/clickhouse.ts — which state the request-log view
// shows while /info is still loading, when ClickHouse is disabled
// (`features.clickhouse: false`) or unreachable (503 from /request-logs).
//
// Run: npm test (node:test; the module is transpiled with esbuild).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const entry = fileURLToPath(new URL('../src/lib/clickhouse.ts', import.meta.url));
const { outputFiles } = await build({ entryPoints: [entry], bundle: true, format: 'esm', write: false, logLevel: 'silent' });
const m = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`);

const http = (status, detail) => ({ response: { status, data: detail === undefined ? {} : { detail } } });
const info = (features) => ({ auth_mode: 'otp', version: 'x', features });

test('nothing is decided until /info has settled', () => {
  assert.equal(m.clickHouseFeature(null), 'pending');
  assert.equal(m.clickHouseFeature(undefined), 'pending');
  assert.equal(m.requestLogsState(null, null), 'pending');
  // even a 503 seen meanwhile doesn't decide it
  assert.equal(m.requestLogsState(null, http(503)), 'pending');
});

test('once settled, only an explicit false disables (older backends / failed /info count as enabled)', () => {
  assert.equal(m.clickHouseFeature(info({ clickhouse: false })), 'disabled');
  assert.equal(m.clickHouseFeature(info({ clickhouse: true })), 'enabled');
  assert.equal(m.clickHouseFeature(info({})), 'enabled'); // the auth context's /info-failed fallback
  assert.equal(m.clickHouseFeature(info(undefined)), 'enabled');
});

test('disabled wins over any error: the explanation, not a failure', () => {
  assert.equal(m.requestLogsState(info({ clickhouse: false }), null), 'disabled');
  assert.equal(m.requestLogsState(info({ clickhouse: false }), http(503)), 'disabled');
});

test('a 503 from an enabled instance is unavailable; other errors are errors', () => {
  assert.equal(m.requestLogsState(info({ clickhouse: true }), null), 'ok');
  assert.equal(m.requestLogsState(info({ clickhouse: true }), http(503, 'down')), 'unavailable');
  assert.equal(m.requestLogsState(info({}), http(503)), 'unavailable');
  assert.equal(m.requestLogsState(info({ clickhouse: true }), http(500)), 'error');
  assert.equal(m.requestLogsState(info({ clickhouse: true }), new Error('network')), 'error');
});

test('only an unavailable query polls, and slowly', () => {
  assert.equal(m.isUnavailableError(http(503)), true);
  assert.equal(m.isUnavailableError(http(500)), false);
  assert.equal(m.isUnavailableError(null), false);
  assert.equal(m.unavailableRefetchInterval(http(503)), m.UNAVAILABLE_REFETCH_MS);
  assert.ok(m.UNAVAILABLE_REFETCH_MS >= 10_000);
  assert.equal(m.unavailableRefetchInterval(null), false);
  assert.equal(m.unavailableRefetchInterval(http(500)), false);
});

test('unavailableDetail shows the backend explanation, with a fallback', () => {
  assert.equal(m.unavailableDetail(http(503, 'ClickHouse could not be reached.')), 'ClickHouse could not be reached.');
  assert.match(m.unavailableDetail(http(503)), /temporarily unavailable/);
  assert.match(m.unavailableDetail(undefined), /temporarily unavailable/);
});

test('the docs link points at the ClickHouse env var section', () => {
  assert.equal(m.CLICKHOUSE_ENV_VAR, 'CLICKHOUSE_HOST');
  assert.match(m.CLICKHOUSE_DOCS_URL, /environment-variables#clickhouse/);
});
