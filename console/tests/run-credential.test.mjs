// Tests for console/src/lib/runCredential.ts: an LLM provider's per-run
// credential setting (config.run_credential) in the provider form.
//
// Run: npm test (node:test; the module is transpiled with esbuild).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const entry = fileURLToPath(new URL('../src/lib/runCredential.ts', import.meta.url));
const { outputFiles } = await build({ entryPoints: [entry], bundle: true, format: 'esm', write: false, logLevel: 'silent' });
const m = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`);

const OFF = { enabled: false, header: '', prefix: '', required: false };

test('a config without the setting reads as off, and writes nothing', () => {
  assert.deepEqual(m.runCredentialFromConfig({ api_version: 'x' }), OFF);
  assert.deepEqual(m.runCredentialFromConfig(undefined), OFF);
  assert.deepEqual(m.configWithRunCredential({ api_version: 'x', run_credential: { enabled: false } }, OFF), {
    api_version: 'x',
  });
});

test('the setting round-trips, header empty meaning null', () => {
  const config = { headers: { 'x-a': '{{user.id}}' } };
  const on = m.configWithRunCredential(config, { enabled: true, header: '', prefix: '', required: true });
  assert.deepEqual(on.run_credential, { enabled: true, header: null, prefix: '', required: true });
  assert.deepEqual(on.headers, config.headers);
  assert.deepEqual(m.runCredentialFromConfig(on), { enabled: true, header: '', prefix: '', required: true });
  const named = m.configWithRunCredential({}, { enabled: true, header: ' X-Key ', prefix: 'Bearer ', required: false });
  assert.deepEqual(named.run_credential, { enabled: true, header: 'X-Key', prefix: 'Bearer ', required: false });
});

test('problems the backend would refuse', () => {
  const on = { enabled: true, header: '', prefix: '', required: false };
  assert.equal(m.runCredentialProblem(on, 'openai'), null);
  assert.equal(m.runCredentialProblem(on, 'anthropic'), null);
  assert.match(m.runCredentialProblem(on, 'ollama'), /name a header/);
  assert.equal(m.runCredentialProblem({ ...on, header: 'X-Key' }, 'ollama'), null);
  assert.equal(m.runCredentialProblem({ ...on, header: 'Authorization' }, 'anthropic'), null);
  assert.match(m.runCredentialProblem({ ...on, header: 'Host' }, 'openai'), /can't carry/);
  assert.match(m.runCredentialProblem({ ...on, header: 'x-sinas-x' }, 'openai'), /can't carry/);
  assert.match(m.runCredentialProblem({ ...on, header: 'bad name' }, 'openai'), /letters/);
  assert.match(m.runCredentialProblem({ ...on, prefix: 'Bearer ' }, 'openai'), /named header only/);
  assert.match(m.runCredentialProblem({ ...OFF, required: true }, 'openai'), /enabled/);
});
