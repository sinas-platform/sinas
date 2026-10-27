// Tests for console/src/lib/redisMemory.ts: the System page's Redis memory
// line.
//
// Run: npm test (node:test; the module is transpiled with esbuild).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const entry = fileURLToPath(new URL('../src/lib/redisMemory.ts', import.meta.url));
const { outputFiles } = await build({ entryPoints: [entry], bundle: true, format: 'esm', write: false, logLevel: 'silent' });
const m = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`);

test('bytes in binary units, one decimal below 100', () => {
  assert.equal(m.formatBytes(0), '0 B');
  assert.equal(m.formatBytes(1023), '1023 B');
  assert.equal(m.formatBytes(1536), '1.5 KB');
  assert.equal(m.formatBytes(50_541_363), '48.2 MB');
  assert.equal(m.formatBytes(256 * 1024 * 1024), '256 MB');
  assert.equal(m.formatBytes(3 * 1024 ** 3), '3 GB');
});

test('used against maxmemory, or no limit', () => {
  const base = { used_bytes: 50_541_363, percent: 18.8, policy: 'noeviction', evicted_keys: 0, oom_errors: 0 };
  assert.equal(m.redisMemoryLabel({ ...base, maxmemory_bytes: 256 * 1024 * 1024 }), '48.2 MB of 256 MB');
  assert.equal(m.redisMemoryLabel({ ...base, maxmemory_bytes: null }), '48.2 MB (no limit)');
});

test('policy note: noeviction refuses writes, anything else drops state', () => {
  assert.match(m.redisPolicyNote('noeviction'), /refuses writes/);
  assert.match(m.redisPolicyNote('noeviction'), /fail until/);
  assert.match(m.redisPolicyNote('allkeys-lru'), /can be lost/);
  assert.match(m.redisPolicyNote('volatile-lru'), /noeviction/);
  assert.equal(m.redisPolicyNote(null), '');
});
