// Tests for console/src/lib/databaseTriggers.ts — how a trigger reads when CDC
// is turned off (`features.cdc: false`): a distinct state, never "Inactive",
// and only switching on is greyed out.
//
// Run: npm test (node:test; the module is transpiled with esbuild).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const entry = fileURLToPath(new URL('../src/lib/databaseTriggers.ts', import.meta.url));
const { outputFiles } = await build({ entryPoints: [entry], bundle: true, format: 'esm', write: false, logLevel: 'silent' });
const m = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`);

const info = (features) => ({ auth_mode: 'otp', version: 'x', features });
const on = { is_active: true, error_message: null };
const paused = { is_active: false, error_message: null };
const failing = { is_active: true, error_message: 'connection refused' };

test('only an explicit false turns CDC off (older backends / unsettled /info count as on)', () => {
  assert.equal(m.cdcEnabled(info({ cdc: false })), false);
  assert.equal(m.cdcEnabled(info({ cdc: true })), true);
  assert.equal(m.cdcEnabled(info({})), true);
  assert.equal(m.cdcEnabled(null), true);
});

test('with CDC on, statuses are unchanged', () => {
  assert.equal(m.triggerStatus(on, true), 'active');
  assert.equal(m.triggerStatus(paused, true), 'inactive');
  assert.equal(m.triggerStatus(failing, true), 'error');
});

test('with CDC off, a trigger that is on reads as CDC-off, not inactive', () => {
  assert.equal(m.triggerStatus(on, false), 'cdc_off');
  assert.equal(m.triggerStatus(failing, false), 'cdc_off'); // a stale poll error isn't the story now
  assert.notEqual(m.triggerStatus(on, false), m.triggerStatus(paused, false));
  assert.equal(m.triggerStatus(paused, false), 'inactive'); // someone did pause this one
});

test('with CDC off, pausing still works but switching on is refused', () => {
  assert.equal(m.canToggle(on, false), true);
  assert.equal(m.canToggle(paused, false), false);
  assert.equal(m.canToggle(paused, true), true);
});

test('the reason names the env var', () => {
  assert.match(m.CDC_OFF_REASON, /CDC_ENABLED=false/);
});
