'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.argv[2] || path.resolve(__dirname, '..');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
async function flush(rounds = 24) { for (let i = 0; i < rounds; i++) await Promise.resolve(); }
function target() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    emit(type) { for (const fn of [...(listeners.get(type) || [])]) fn(); },
  };
}
function harness() {
  const sources = [], timers = new Map();
  let timerId = 0;
  class EventSource {
    constructor(url) { this.url = url; this.closed = false; sources.push(this); }
    close() { this.closed = true; }
  }
  const document = Object.assign(target(), {hidden: false});
  const sandbox = Object.assign(target(), {document, location: new URL('http://127.0.0.1:8123/'),
    URL, EventSource, console,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  // Use the production exact-number decoder, not JSON.parse with rounded fixture IDs.
  vm.runInContext(fs.readFileSync(path.join(root, 'web/json.js'), 'utf8'), sandbox);
  vm.runInContext(fs.readFileSync(path.join(root, 'web/realtime.js'), 'utf8'), sandbox);
  const states = [];
  return {
    sandbox, sources, timers, states,
    subscribe(onEvent, after = 0) {
      return sandbox.ChaosRealtime.subscribe({url: '/api/events?projectId=p', after,
        onEvent, onState: value => states.push(value)});
    },
    event(source, id, type = 'bot.progress') {
      source.onmessage({data: '{"id":' + id + ',"type":' + JSON.stringify(type) + ',"payload":{"summary":"public"}}'});
    },
    retry() {
      assert.equal(timers.size, 1, 'recovery should schedule one reconnect');
      const [id, {fn, delay}] = timers.entries().next().value;
      assert(delay >= 250 && delay <= 5000);
      timers.delete(id); fn();
      return sources.at(-1);
    },
  };
}

const cases = {
  async accepted_cursor_follows_awaited_consumer_and_deduplicates_queue() {
    const h = harness(), gate = deferred(), seen = [];
    const sub = h.subscribe(async event => { seen.push(String(event.id)); if (String(event.id) === '1') await gate.promise; });
    const source = h.sources[0]; source.onopen();
    h.event(source, 1); h.event(source, 1); h.event(source, 2);
    await flush();
    assert.deepEqual(seen, ['1']); assert.equal(sub.cursor(), '0');
    gate.resolve(); await flush();
    assert.deepEqual(seen, ['1', '2']); assert.equal(sub.cursor(), '2');
    h.event(source, 2); h.event(source, 1); await flush();
    assert.deepEqual(seen, ['1', '2']); sub.close();
  },
  async rejected_consumer_replays_failed_event_without_skipping_queued_tail() {
    const h = harness(), gate = deferred(), seen = []; let first = true;
    const sub = h.subscribe(async event => {
      seen.push(String(event.id));
      if (first) { first = false; await gate.promise; throw Error('temporary renderer failure'); }
    });
    const old = h.sources[0]; old.onopen(); h.event(old, 1); h.event(old, 2);
    gate.resolve(); await flush();
    assert.equal(sub.cursor(), '0'); assert.equal(old.closed, true);
    const next = h.retry(); assert.equal(new URL(next.url, h.sandbox.location).searchParams.get('after'), '0');
    next.onopen(); h.event(next, 1); h.event(next, 2); await flush();
    assert.deepEqual(seen, ['1', '1', '2']); assert.equal(sub.cursor(), '2'); sub.close();
  },
  async source_error_drains_received_work_before_reconnect_cursor() {
    const h = harness(), gate = deferred(), seen = [];
    const sub = h.subscribe(async event => { seen.push(String(event.id)); if (String(event.id) === '1') await gate.promise; });
    const old = h.sources[0]; old.onopen(); h.event(old, 1); h.event(old, 2); old.onerror();
    assert.equal(old.closed, true); assert.equal(h.timers.size, 0); assert.equal(sub.cursor(), '0');
    h.event(old, 99); gate.resolve(); await flush();
    assert.deepEqual(seen, ['1', '2']); assert.equal(sub.cursor(), '2');
    const next = h.retry(); assert.equal(new URL(next.url, h.sandbox.location).searchParams.get('after'), '2');
    next.onopen(); h.event(next, 3); await flush();
    assert.deepEqual(seen, ['1', '2', '3']); assert.equal(sub.cursor(), '3'); sub.close();
  },
  async raw_json_large_identifiers_remain_exact_through_replay() {
    const h = harness(), seen = [];
    const sub = h.subscribe(event => seen.push(String(event.id)), '9007199254740991');
    const source = h.sources[0]; source.onopen();
    h.event(source, '9007199254740992'); h.event(source, '9007199254740993'); await flush();
    assert.deepEqual(seen, ['9007199254740992', '9007199254740993']);
    assert.equal(sub.cursor(), '9007199254740993'); source.onerror();
    const next = h.retry();
    assert.equal(new URL(next.url, h.sandbox.location).searchParams.get('after'), '9007199254740993');
    next.onopen(); h.event(next, '9007199254740993'); h.event(next, '9007199254740994'); await flush();
    assert.deepEqual(seen, ['9007199254740992', '9007199254740993', '9007199254740994']); sub.close();
  },
  async close_suppresses_late_callbacks_and_releases_listeners() {
    const h = harness(), gate = deferred(), seen = [];
    const sub = h.subscribe(async event => { seen.push(String(event.id)); await gate.promise; });
    const old = h.sources[0]; old.onopen(); h.event(old, 1); h.event(old, 2);
    sub.close(); const states = h.states.slice();
    old.onopen(); old.onerror(); h.event(old, 3); gate.resolve(); await flush();
    h.sandbox.emit('online'); h.sandbox.document.emit('visibilitychange');
    assert.deepEqual(seen, ['1']); assert.equal(sub.cursor(), '0');
    assert.deepEqual(h.states, states); assert.equal(h.timers.size, 0); assert.equal(h.sources.length, 1);
    for (const event of ['online', 'pagehide']) assert.equal(h.sandbox.listeners.get(event).size, 0);
    assert.equal(h.sandbox.document.listeners.get('visibilitychange').size, 0);
  },
  async overflow_bounds_pending_events_and_replays_unreceived_tail() {
    const h = harness(), gate = deferred(), seen = [];
    const sub = h.subscribe(async event => { seen.push(String(event.id)); if (String(event.id) === '1') await gate.promise; });
    const old = h.sources[0]; old.onopen(); h.event(old, 1);
    for (let id = 2; id <= 2000; id++) h.event(old, id);
    assert.equal(old.closed, true); assert.deepEqual(seen, ['1']); assert.equal(h.timers.size, 0);
    gate.resolve();
    for (let i = 0; i < 10000 && sub.cursor() !== '1001'; i++) await Promise.resolve();
    await flush();
    assert.equal(seen.length, 1001, 'one in-flight event plus at most 1000 pending events');
    assert.equal(sub.cursor(), '1001');
    const next = h.retry(); assert.equal(new URL(next.url, h.sandbox.location).searchParams.get('after'), '1001');
    next.onopen(); h.event(next, 1002); await flush();
    assert.equal(seen.at(-1), '1002'); assert.equal(sub.cursor(), '1002'); sub.close();
  },
  async malformed_frames_preserve_accepted_cursor_and_old_source_is_inert() {
    const h = harness(), seen = [];
    const sub = h.subscribe(event => seen.push(String(event.id)));
    const old = h.sources[0]; old.onopen(); h.event(old, 1); await flush();
    old.onmessage({data: '{"id":2,"payload":{}}'});
    assert.equal(old.closed, true); assert.equal(sub.cursor(), '1');
    const next = h.retry(); next.onopen(); const states = h.states.slice();
    old.onerror(); old.onopen(); h.event(old, 3); await flush();
    assert.deepEqual(h.states, states); assert.deepEqual(seen, ['1']);
    h.event(next, 2); await flush(); assert.equal(sub.cursor(), '2'); sub.close();
  },
  async unsafe_endpoint_and_cursor_are_rejected_before_connecting() {
    const h = harness();
    assert.throws(() => h.sandbox.ChaosRealtime.subscribe({url: 'https://external.invalid/events', onEvent() {}}), /this service/);
    for (const after of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1, '01', '-1', 'invalid']) {
      assert.throws(() => h.subscribe(() => {}, after), /cursor/);
    }
    assert.equal(h.sources.length, 0);
  },
};

(async () => {
  const selected = process.argv[3];
  const names = selected ? [selected] : Object.keys(cases);
  for (const name of names) { assert(cases[name], name); await cases[name](); }
  console.log(JSON.stringify({status: 'passed', cases: names.length, names}));
})().catch(error => { console.error(error); process.exitCode = 1; });
