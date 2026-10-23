'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.argv[2] || path.resolve(__dirname, '..');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
async function flush() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function timers() {
  const pending = new Map(); let next = 0;
  return {pending, setTimeout(fn, delay) { const id = ++next; pending.set(id, {fn, delay}); return id; }, clearTimeout(id) { pending.delete(id); },
    fire() { assert.equal(pending.size, 1); const [id, value] = pending.entries().next().value; pending.delete(id); value.fn(); }};
}
function ide() {
  const source = fs.readFileSync(path.join(root, 'web/ide.js'), 'utf8');
  const start = source.indexOf(' function stopJobWatch()');
  const end = source.indexOf(" $('#ideChatForm').onsubmit", start);
  assert(start >= 0 && end > start);
  const clock = timers(), subscriptions = [], displays = [], publicEvents = [], controls = {};
  const state = {job: {id: 'one', projectId: 'p', status: 'running'}, jobWatch: null, chatEpoch: 0};
  const fixture = {snapshot: {...state.job}, read: null};
  const sandbox = {state, console, ...clock, window: {ChaosRealtime: {subscribe(options) {
    const subscription = {options, closed: false, close() { this.closed = true; }};
    subscriptions.push(subscription); return subscription;
  }}}, chatCurrent: ticket => ticket.projectId === 'p' && ticket.epoch === state.chatEpoch,
  chatTicket: () => ({projectId: 'p', epoch: state.chatEpoch}),
  assistantPath: id => '/api/projects/p/assistant/' + id,
  request: url => fixture.read ? fixture.read(url) : Promise.resolve({...fixture.snapshot}),
  renderJob: job => displays.push({kind: 'snapshot', status: job.status}),
  agentState: (message, busy = false) => displays.push({kind: 'status', message, busy}),
  record: (watch, event) => { watch.latest = event.payload.summary; publicEvents.push(event.type); },
  $: selector => controls[selector] ||= {}, guarded: fn => fn};
  vm.createContext(sandbox);
  vm.runInContext(source.slice(start, end), sandbox);
  vm.runInContext('renderJobActivity = record;', sandbox);
  const cancel = source.split('\n').find(line => line.includes("$('#ideChatCancel').onclick="));
  assert(cancel); vm.runInContext(cancel, sandbox);
  return {state, fixture, subscriptions, displays, publicEvents, controls, clock, sandbox,
    start(id = 'one') { sandbox.watchJob(id, sandbox.chatTicket()); return subscriptions.at(-1); },
    event(subscription, type, id = 'one') { return subscription.options.onEvent({id: publicEvents.length + 1, taskId: id, type, payload: {summary: 'public ' + type}}); }};
}
function target() {
  const listeners = new Map();
  return {listeners, addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); }, emit(type, value = {}) { for (const fn of [...(listeners.get(type) || [])]) fn(value); }};
}
function transport() {
  const clock = timers(), sources = [], sandbox = {...target(), ...clock, console, URL, location: new URL('http://localhost:8123/')};
  sandbox.document = {...target(), hidden: false}; sandbox.window = sandbox;
  sandbox.EventSource = class { constructor(url) { this.url = url; this.closed = false; sources.push(this); } close() { this.closed = true; } };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(root, 'web/json.js'), 'utf8'), sandbox);
  vm.runInContext(fs.readFileSync(path.join(root, 'web/realtime.js'), 'utf8'), sandbox);
  return {clock, sources, sandbox, emit(source, id) { source.onmessage({data: JSON.stringify({id, type: 'bot.progress'})}); }};
}
const cases = {
  async terminal_snapshot_keeps_subscription_until_terminal_event() {
    for (const status of ['completed', 'failed', 'cancelled', 'interrupted']) {
      const h = ide(); h.fixture.snapshot.status = status;
      const stream = h.start(); await flush();
      assert.equal(stream.closed, false, status + ' snapshot must not discard replay');
      assert.equal(h.state.job.id, 'one');
      assert.equal(h.displays.at(-1).busy, true);
      await h.event(stream, 'task.queued');
      assert.equal(stream.closed, false, 'queued event followed by terminal snapshot still needs tail');
      await h.event(stream, 'agent.tool_completed');
      await h.event(stream, 'task.' + status); await flush();
      assert.deepEqual(h.publicEvents, ['task.queued', 'agent.tool_completed', 'task.' + status]);
      assert.equal(stream.closed, true, status + ' final event should finish observation');
      assert.equal(h.state.job, null); assert.equal(h.displays.at(-1).busy, false);
    }
  },
  async late_snapshot_from_old_watch_cannot_replace_new_job() {
    const h = ide(), oldRead = deferred(); h.fixture.read = () => oldRead.promise;
    const oldStream = h.start(); await flush();
    h.state.job = {id: 'two', projectId: 'p', status: 'running'};
    h.fixture.read = null; h.fixture.snapshot = {...h.state.job};
    const newStream = h.start('two'); await flush();
    const before = h.displays.length;
    oldRead.resolve({id: 'one', projectId: 'p', status: 'completed'}); await flush();
    await h.event(oldStream, 'task.completed');
    assert.equal(h.state.job.id, 'two'); assert.equal(h.state.jobWatch.id, 'two');
    assert.equal(oldStream.closed, true); assert.equal(newStream.closed, false);
    assert.equal(h.displays.length, before);
  },
  async read_failure_preserves_job_and_cancel_identity() {
    const h = ide(); h.fixture.read = () => Promise.reject(Error('fixture read unavailable'));
    const stream = h.start(); await flush();
    assert.equal(stream.closed, false); assert.equal(h.state.job.id, 'one');
    assert.equal(h.displays.at(-1).busy, true);
    const paths = []; h.fixture.read = url => { paths.push(url); return Promise.resolve({}); };
    await h.controls['#ideChatCancel'].onclick();
    assert.deepEqual(paths, ['/api/projects/p/assistant/one/cancel']);
    assert.equal(h.state.job.id, 'one');
  },
  async late_cancel_response_does_not_reopen_finished_controls() {
    const h = ide(), cancel = deferred(); h.fixture.read = () => cancel.promise;
    const work = h.controls['#ideChatCancel'].onclick();
    h.state.job = null; h.sandbox.agentState('completed', false);
    cancel.resolve({status: 'completed'}); await work;
    assert.equal(h.displays.at(-1).message, 'completed'); assert.equal(h.displays.at(-1).busy, false);
  },
  async cached_page_preserves_accepted_cursor_and_pending_work() {
    const h = transport(), gate = deferred(), seen = [];
    const subscription = h.sandbox.ChaosRealtime.subscribe({url: '/api/events', onEvent: async event => {
      seen.push(String(event.id)); if (String(event.id) === '1') await gate.promise;
    }});
    const old = h.sources[0]; old.onopen(); h.emit(old, 1); h.emit(old, 2);
    h.sandbox.emit('pagehide', {persisted: true});
    assert.equal(old.closed, true); assert.equal(h.clock.pending.size, 0);
    gate.resolve(); await flush();
    assert.deepEqual(seen, ['1', '2']); assert.equal(subscription.cursor(), '2');
    subscription.reconnect(); assert.equal(h.clock.pending.size, 0, 'must not connect while cached');
    h.sandbox.emit('pageshow', {persisted: true}); h.clock.fire();
    assert.equal(h.sources.length, 2);
    const current = h.sources[1]; assert.equal(new URL(current.url, h.sandbox.location).searchParams.get('after'), '2');
    current.onopen(); h.emit(old, 99); h.emit(current, 2); h.emit(current, 3); await flush();
    assert.deepEqual(seen, ['1', '2', '3']); subscription.close();
    for (const name of ['online', 'pagehide', 'pageshow']) assert.equal(h.sandbox.listeners.get(name).size, 0);
  },
  async permanent_navigation_closes_subscription_without_restore() {
    const h = transport();
    const subscription = h.sandbox.ChaosRealtime.subscribe({url: '/api/events', onEvent() {}});
    h.sandbox.emit('pagehide', {persisted: false});
    h.sandbox.emit('pageshow', {persisted: true}); subscription.reconnect();
    assert.equal(h.sources[0].closed, true); assert.equal(h.sources.length, 1); assert.equal(h.clock.pending.size, 0);
  },
};
(async () => {
  const names = process.argv[3] ? [process.argv[3]] : Object.keys(cases);
  for (const name of names) { assert(cases[name], name); await cases[name](); }
  console.log(JSON.stringify({status: 'passed', cases: names.length, names}));
})().catch(error => { console.error(error); process.exitCode = 1; });
