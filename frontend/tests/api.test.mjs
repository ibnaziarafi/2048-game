import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const typesSource = await readFile(new URL('../src/types.ts', import.meta.url), 'utf8');
const typesJs = ts.transpile(typesSource, { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 });
const typesUrl = `data:text/javascript;base64,${Buffer.from(typesJs).toString('base64')}`;
let apiSource = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
apiSource = apiSource.replace("from './types'", `from '${typesUrl}'`).replace('import.meta.env.VITE_API_BASE_URL', "'http://example.test/api'");
const apiJs = ts.transpile(apiSource, { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 });
const apiUrl = `data:text/javascript;base64,${Buffer.from(apiJs).toString('base64')}`;
const api = await import(apiUrl);

const state = { game_id: 'g', board: Array.from({ length: 4 }, () => [0, 0, 0, 0]), score: 0, game_over: false, revision: 3, legal_moves: ['left'], model_version: 2 };

test('uncertain move transport retries the exact payload and request ID', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    calls.push({ input, body: init.body });
    if (calls.length === 1) throw new TypeError('connection reset');
    return new Response(JSON.stringify({ ...state, revision: 4, moved: true, score_gained: 4 }), { status: 200 });
  };
  try {
    const result = await api.makeMove(state, 'left', 'human', 'stable-request-id');
    assert.equal(result.revision, 4);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], calls[1]);
    assert.deepEqual(JSON.parse(calls[0].body), { game_id: 'g', direction: 'left', expected_revision: 3, request_id: 'stable-request-id', source: 'human' });
  } finally { globalThis.fetch = originalFetch; }
});

test('stale revision response retains the authoritative server state', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: 'stale_revision', message: 'stale', state: { ...state, revision: 4 } } }), { status: 409 });
  try {
    await assert.rejects(api.makeMove(state, 'left', 'human', 'request-2'), (error) => {
      assert.equal(error.code, 'stale_revision');
      assert.equal(error.state.revision, 4);
      return true;
    });
  } finally { globalThis.fetch = originalFetch; }
});

test('create and move use the configured same-origin API path and required schemas', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    calls.push({ input, body: init.body });
    return new Response(JSON.stringify(state), { status: 200 });
  };
  try {
    await api.createGame('create-id');
    assert.equal(calls[0].input, 'http://example.test/api/game');
    assert.deepEqual(JSON.parse(calls[0].body), { request_id: 'create-id' });
  } finally { globalThis.fetch = originalFetch; }
});

test('truncated successful move response is retried with the exact request identity and validated', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    calls.push({ input, body: init.body });
    if (calls.length === 1) return new Response('{', { status: 200 });
    return new Response(JSON.stringify({ ...state, revision: 4, moved: true, score_gained: 4 }), { status: 200 });
  };
  try {
    const result = await api.makeMove(state, 'left', 'human', 'stable-after-truncation');
    assert.equal(result.revision, 4);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], calls[1]);
    assert.equal(JSON.parse(calls[1].body).request_id, 'stable-after-truncation');
  } finally { globalThis.fetch = originalFetch; }
});

test('two malformed successful bodies reject instead of returning an empty game', async () => {
  let calls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { calls += 1; return new Response('{', { status: 200 }); };
  try {
    await assert.rejects(api.createGame('stable-create-id'), /incomplete|invalid response/i);
    assert.equal(calls, 2);
  } finally { globalThis.fetch = originalFetch; }
});

test('request deadline aborts and retries the same move request', async () => {
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const calls = [];
  globalThis.setTimeout = (callback) => { callback(); return 1; };
  globalThis.clearTimeout = () => {};
  globalThis.fetch = (input, init) => { calls.push({ input, body: init.body, signal: init.signal }); return new Promise(() => {}); };
  try {
    await assert.rejects(api.makeMove(state, 'left', 'human', 'timeout-request'), /timed out/i);
    assert.equal(calls.length, 2);
    const payloads = calls.map(({ input, body }) => ({ input, body }));
    assert.deepEqual(payloads, [payloads[0], payloads[0]]);
    assert.equal(calls.every((call) => call.signal.aborted), true);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});
