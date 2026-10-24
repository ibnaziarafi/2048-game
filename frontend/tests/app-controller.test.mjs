import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import ts from 'typescript';

const appPath = new URL('../src/App.tsx', import.meta.url);
const appSource = await readFile(appPath, 'utf8');
const compiledApp = ts.transpileModule(appSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true } }).outputText;

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const game = (id = 'A', revision = 0, legal = ['left', 'right', 'up', 'down']) => ({ game_id: id, revision, board: [[2, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]], score: revision * 4, game_over: false, legal_moves: legal, model_version: revision });

function setup() {
  const slots = [], effects = [], timers = new Map(), storage = new Map(), listeners = new Map();
  let cursor = 0, dirty = true, tree, nextTimer = 0, nextId = 0;
  const calls = { create: [], move: [], hint: [] };
  class MockApiError extends Error { constructor(message, status, code, state) { super(message); this.status = status; this.code = code; this.state = state; } }
  const api = {
    getAiStats: () => Promise.resolve({ algorithm: 'linear-td-v1', model_version: 0, training_samples: 0, human_moves: 0, ai_moves: 0, games_started: 1, completed_games: 0, updated_at: null }),
    getGame: () => Promise.resolve(game()),
    createGame: (...args) => { const d = deferred(); calls.create.push({ args, ...d }); return d.promise; },
    makeMove: (...args) => { const d = deferred(); calls.move.push({ args, ...d }); return d.promise; },
    getHint: (...args) => { const d = deferred(); calls.hint.push({ args, ...d }); return d.promise; },
  };
  const depsSame = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState(initial) { const i = cursor++; if (!slots[i]) slots[i] = { value: typeof initial === 'function' ? initial() : initial }; return [slots[i].value, (next) => { const value = typeof next === 'function' ? next(slots[i].value) : next; if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; } }]; },
    useRef(initial) { const i = cursor++; return slots[i] ||= { current: initial }; },
    useCallback(fn, deps) { const i = cursor++; if (!slots[i] || !depsSame(slots[i].deps, deps)) slots[i] = { fn, deps }; return slots[i].fn; },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || !depsSame(slots[i].deps, deps)) { const previous = slots[i]; slots[i] = { deps }; effects.push(() => { previous?.cleanup?.(); slots[i].cleanup = fn(); }); } },
  };
  const context = {
    exports: {},
    require: (name) => name === 'react' ? react : name === './api' ? api : name === './types' ? { ApiRequestError: MockApiError } : {},
    crypto: { randomUUID: () => `request-${++nextId}` },
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, String(value)), removeItem: (key) => storage.delete(key) },
    HTMLElement: class HTMLElement {},
    window: { setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; }, clearTimeout: (id) => timers.delete(id), addEventListener: (type, handler) => listeners.set(type, handler), removeEventListener: (type, handler) => { if (listeners.get(type) === handler) listeners.delete(type); } },
  };
  vm.runInNewContext(compiledApp, context, { filename: 'App-under-test.transpiled.js' });
  const render = () => { let passes = 0; while (dirty) { assert.ok(++passes < 30, 'render must settle'); dirty = false; cursor = 0; tree = context.exports.App(); const pending = effects.splice(0); pending.forEach((effect) => effect()); } };
  const flush = async () => { for (let i = 0; i < 16; i++) { await Promise.resolve(); render(); } };
  const flatten = (node) => !node ? [] : Array.isArray(node) ? node.flatMap(flatten) : typeof node === 'object' ? [node, ...flatten(node.props?.children)] : [];
  const label = (node) => !node ? '' : Array.isArray(node) ? node.map(label).join('') : typeof node === 'object' ? label(node.props?.children) : String(node);
  const button = (name) => { const found = flatten(tree).find((node) => node.type === 'button' && (node.props['aria-label'] === name || label(node) === name)); assert.ok(found, `button ${name} should exist`); return found; };
  const click = async (name) => { const found = button(name); assert.equal(Boolean(found.props.disabled), false, `${name} should be enabled`); found.props.onClick(); await flush(); };
  const fireTimer = async (ms = 250) => { const entry = [...timers].find(([, timer]) => timer.ms === ms); assert.ok(entry, `expected timer ${ms}ms`); timers.delete(entry[0]); entry[1].fn(); await flush(); };
  render();
  const touchHandlers = () => {
    const board = flatten(tree).find((node) => node.props?.onTouchStart && node.props?.onTouchEnd);
    assert.ok(board, 'production touch board should exist');
    return { start: board.props.onTouchStart, end: board.props.onTouchEnd };
  };
  const keyDown = (event) => { assert.ok(listeners.has('keydown')); listeners.get('keydown')(event); };
  return { calls, timers, button, click, flush, fireTimer, touchHandlers, keyDown, text: () => label(tree), async init() { calls.create[0].resolve(game()); await flush(); } };
}

test('new generation can move while an old generation request is unresolved', async () => {
  const ui = setup(); await ui.init();
  await ui.click('Move left');
  await ui.click('New Game');
  ui.calls.create[1].resolve(game('B')); await ui.flush();
  await ui.click('Move up');
  assert.equal(ui.calls.move.length, 2);
  assert.deepEqual(ui.calls.move.map((call) => [call.args[0].game_id, call.args[1]]), [['A', 'left'], ['B', 'up']]);
  ui.calls.move[1].resolve({ ...game('B', 1), moved: true, score_gained: 4 }); await ui.flush();
  ui.calls.move[0].resolve({ ...game('A', 1), moved: true, score_gained: 4 }); await ui.flush();
  await ui.click('Move right');
  assert.equal(ui.calls.move[2].args[0].game_id, 'B');
  assert.equal(ui.calls.move[2].args[0].revision, 1);
});

test('retry after an uncertain move reuses the identical game revision and request ID', async () => {
  const ui = setup(); await ui.init(); await ui.click('Move left');
  const original = ui.calls.move[0].args;
  ui.calls.move[0].reject(new Error('response unavailable')); await ui.flush();
  await ui.click('Retry pending move');
  assert.equal(ui.calls.move.length, 2);
  assert.deepEqual(ui.calls.move[1].args, original);
});

test('retry after an uncertain new-game response reuses its create request ID', async () => {
  const ui = setup(); await ui.init(); await ui.click('New Game');
  const requestId = ui.calls.create[1].args[0];
  ui.calls.create[1].reject(new Error('response unavailable')); await ui.flush();
  await ui.click('Retry new game');
  assert.equal(ui.calls.create.length, 3);
  assert.equal(ui.calls.create[2].args[0], requestId);
  ui.calls.create[2].resolve(game('B')); await ui.flush();
  assert.equal(ui.text().includes('New Game'), true);
});

test('autoplay cannot start during a manual hint and stop cancels a delayed hint completion', async () => {
  const ui = setup(); await ui.init(); await ui.click('Hint');
  assert.equal(ui.button('Start autoplay').props.disabled, true);
  ui.calls.hint[0].resolve({ game_id: 'A', revision: 0, direction: 'left', legal_moves: ['left'], model_version: 0 }); await ui.flush();
  await ui.click('Start autoplay'); await ui.fireTimer();
  assert.equal(ui.calls.hint.length, 2);
  await ui.click('Stop autoplay');
  ui.calls.hint[1].resolve({ game_id: 'A', revision: 0, direction: 'left', legal_moves: ['left'], model_version: 0 }); await ui.flush();
  assert.equal(ui.calls.move.length, 0);
  assert.equal(ui.timers.size, 0);
  assert.equal(ui.text().includes('Start autoplay'), true);
});

test('autoplay stops visibly after a failed or stale hint instead of idling as enabled', async () => {
  const ui = setup(); await ui.init(); await ui.click('Start autoplay'); await ui.fireTimer();
  ui.calls.hint[0].reject(new Error('hint read failed')); await ui.flush();
  assert.equal(ui.text().includes('Start autoplay'), true);
  assert.equal(ui.text().includes('Autoplay stopped'), true);
  assert.equal(ui.timers.size, 0);

  const stale = setup(); await stale.init(); await stale.click('Start autoplay'); await stale.fireTimer();
  stale.calls.hint[0].resolve({ game_id: 'A', revision: 9, direction: 'left', legal_moves: ['left'], model_version: 0 }); await stale.flush();
  assert.equal(stale.text().includes('Start autoplay'), true);
  assert.equal(stale.text().includes('board changed'), true);
  assert.equal(stale.calls.move.length, 0);
});

test('a queued hint is discarded if an earlier human move changes its board', async () => {
  const ui = setup(); await ui.init(); await ui.click('Move right'); await ui.click('Hint');
  ui.calls.hint[0].resolve({ game_id: 'A', revision: 0, direction: 'left', legal_moves: ['left'], model_version: 0 }); await ui.flush();
  await ui.click('Apply hint');
  ui.calls.move[0].resolve({ ...game('A', 1, ['up']), moved: true, score_gained: 4 }); await ui.flush();
  assert.equal(ui.calls.move.length, 1);
  assert.equal(ui.text().includes('hint no longer matches'), true);
});

test('same-render move events enter the FIFO queue and are sent in revision order', async () => {
  const ui = setup(); await ui.init();
  const left = ui.button('Move left').props.onClick, right = ui.button('Move right').props.onClick, up = ui.button('Move up').props.onClick;
  left(); right(); up(); await ui.flush();
  assert.equal(ui.calls.move.length, 1);
  const expected = ['left', 'right', 'up'];
  for (let i = 0; i < expected.length; i++) {
    const call = ui.calls.move[i];
    assert.equal(call.args[1], expected[i]);
    assert.equal(call.args[0].revision, i);
    call.resolve({ ...game('A', i + 1), moved: true, score_gained: 4 }); await ui.flush();
    if (i < expected.length - 1) assert.equal(ui.calls.move.length, i + 2);
  }
  assert.equal(ui.calls.move.length, 3);
});

test('direction input is capped at eight outstanding operations with visible overflow', async () => {
  const ui = setup(); await ui.init();
  const directions = ['Move left', 'Move right', 'Move up', 'Move down'];
  for (let index = 0; index < 10; index++) await ui.click(directions[index % directions.length]);
  assert.equal(ui.calls.move.length, 1);
  assert.equal(ui.text().includes('queue is full'), true);
  for (let index = 0; index < 8; index++) {
    assert.equal(ui.calls.move[index].args[0].revision, index);
    ui.calls.move[index].resolve({ ...game('A', index + 1), moved: true, score_gained: 4 });
    await ui.flush();
  }
  assert.equal(ui.calls.move.length, 8);
});

test('an uncertain move blocks autoplay and hints until exact-request recovery', async () => {
  const ui = setup(); await ui.init();
  // Retain an earlier render's enabled handler: the synchronous controller guard
  // must protect this path as well as the subsequently disabled DOM control.
  const priorStart = ui.button('Start autoplay').props.onClick;
  await ui.click('Move left');
  const pending = ui.calls.move[0].args;
  ui.calls.move[0].reject(new Error('move outcome unknown')); await ui.flush();
  assert.equal(ui.button('Start autoplay').props.disabled, true);
  assert.equal(ui.button('Hint').props.disabled, true);
  priorStart(); await ui.flush();
  assert.equal(ui.timers.size, 0);
  assert.equal(ui.calls.hint.length, 0);
  await ui.click('Move right');
  assert.equal(ui.calls.move.length, 1);

  await ui.click('Retry pending move');
  assert.deepEqual(ui.calls.move[1].args, pending);
  ui.calls.move[1].resolve({ ...game('A', 1), moved: true, score_gained: 4 }); await ui.flush();
  await ui.click('Start autoplay'); await ui.fireTimer();
  assert.equal(ui.calls.hint.length, 1);
  assert.equal(ui.calls.hint[0].args[0], 'A');
});

test('rapid production swipe and keyboard callbacks share one ordered revision queue', async () => {
  const ui = setup(); await ui.init();
  const touch = ui.touchHandlers();
  // Invoke actual JSX callbacks repeatedly before another render, as a burst.
  for (const [clientX, clientY] of [[40, 100], [100, 40], [160, 100], [100, 160], [105, 105]]) {
    touch.start({ touches: [{ clientX: 100, clientY: 100 }] });
    touch.end({ changedTouches: [{ clientX, clientY }] });
  }
  let prevented = 0;
  for (const [key, repeat] of [['d', false], ['ArrowUp', false], ['ArrowLeft', true]]) {
    ui.keyDown({ key, repeat, target: {}, preventDefault: () => { prevented += 1; } });
  }
  await ui.flush();
  assert.equal(prevented, 2);
  assert.equal(ui.calls.move.length, 1);
  const expected = ['left', 'up', 'right', 'down', 'right', 'up'];
  for (let index = 0; index < expected.length; index++) {
    const call = ui.calls.move[index];
    assert.equal(call.args[1], expected[index]);
    assert.equal(call.args[0].revision, index);
    assert.equal(call.args[2], 'human');
    call.resolve({ ...game('A', index + 1), moved: true, score_gained: 4 });
    await ui.flush();
  }
  assert.equal(ui.calls.move.length, expected.length);
});
