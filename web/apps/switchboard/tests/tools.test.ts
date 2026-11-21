import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  connectSnippet, csvField, defaultToolName, describeLimit, duplicateNames, relativeTime, servedNames, toCsv, unavailableReason,
} from '../src/lib/tools.ts';

test('default tool names follow the backend: namespace__name in the MCP character set, 64 at most', () => {
  assert.equal(defaultToolName('crm', 'open_orders'), 'crm__open_orders');
  assert.equal(defaultToolName('crm', 'open.orders v2'), 'crm__open_orders_v2');
  assert.equal(defaultToolName('n', 'x'.repeat(100)).length, 64);
});

test('a custom tool name wins over the default', () => {
  assert.deepEqual(
    servedNames([{ type: 'query', namespace: 'crm', name: 'a', tool_name: 'find' }, { type: 'query', namespace: 'crm', name: 'b' }]),
    ['find', 'crm__b'],
  );
});

test('two tools served under one name are reported, as the backend refuses them', () => {
  assert.deepEqual(duplicateNames([
    { type: 'query', namespace: 'crm', name: 'a', tool_name: 'x' },
    { type: 'function', namespace: 'crm', name: 'b', tool_name: 'x' },
    { type: 'pipeline', namespace: 'crm', name: 'c' },
  ]), ['x']);
  assert.deepEqual(duplicateNames([
    { type: 'query', namespace: 'crm', name: 'a' },
    { type: 'function', namespace: 'crm', name: 'a' },
  ]), ['crm__a']);
});

test('resources the switchboard would not serve say why', () => {
  assert.equal(unavailableReason('query', {}), null);
  assert.equal(unavailableReason('query', { is_active: false }), 'Inactive');
  assert.equal(unavailableReason('pipeline', { as_tool: false }), 'Not marked as a tool');
  assert.match(unavailableReason('function', { requires_approval: true }) ?? '', /approval/);
  assert.equal(unavailableReason('pipeline', { as_tool: true }), null);
});

test('connect snippets carry the endpoint', () => {
  const url = 'https://acme.example/mcp/sales/crm-tools';
  for (const client of ['claude', 'chatgpt', 'cursor', 'apikey'] as const) {
    assert.ok(connectSnippet(client, url, 'crm-tools').includes(url), client);
  }
  assert.deepEqual(JSON.parse(connectSnippet('cursor', url, 'crm-tools')), { mcpServers: { 'crm-tools': { url } } });
  assert.match(connectSnippet('apikey', url, 'x'), /tools\/list/);
});

test('CSV fields with commas, quotes or newlines are quoted; objects become JSON', () => {
  assert.equal(csvField('plain'), 'plain');
  assert.equal(csvField('a,b'), '"a,b"');
  assert.equal(csvField('say "hi"'), '"say ""hi"""');
  assert.equal(csvField(null), '');
  assert.equal(csvField({ a: 1 }), '"{""a"":1}"');
  assert.equal(toCsv(['x', 'y'], [[1, 'a\nb']]), 'x,y\n1,"a\nb"');
});

test('relative times read naturally', () => {
  const now = Date.parse('2026-11-21T12:00:00Z');
  assert.equal(relativeTime(null, now), 'never');
  assert.equal(relativeTime('2026-11-21T11:57:00Z', now), '3 minutes ago');
  assert.equal(relativeTime('2026-11-20T12:00:00Z', now), 'yesterday');
  assert.equal(relativeTime('2026-11-30T12:00:00Z', now), 'in 9 days');
  assert.equal(relativeTime('2026-11-21T11:59:50Z', now), 'just now');
});

test('limits describe themselves', () => {
  assert.equal(describeLimit(null), 'Deployment default limit');
  assert.equal(describeLimit({ max_calls: 0, window_seconds: 60 }), 'No rate limit');
  assert.equal(describeLimit({ max_calls: 120, window_seconds: 60 }), '120 calls / minute per caller');
  assert.equal(describeLimit({ max_calls: 5, window_seconds: 90 }), '5 calls / 90 s per caller');
});
