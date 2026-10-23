'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(process.argv[2]);
const sandbox = {console, setTimeout, clearTimeout, queueMicrotask};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'web/vendor/codemirror.js'), 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'web/editor-assist.js'), 'utf8'), sandbox);
const cm = sandbox.ChaosCM, assist = sandbox.ChaosEditorAssist;
assert.equal(typeof cm.insertCompletionText, 'function');
assert.equal(typeof cm.pickedCompletion.of, 'function');

let checks = 0;
function fixture(text, position, items, selection) {
  let scope = 'project-a/main.chaos/dialect-2/dependencies-1', editable = true;
  const view = {
    state: cm.EditorState.create({doc: text, selection: selection || {anchor: position},
      extensions: cm.EditorState.allowMultipleSelections.of(true)}),
    composing: false, compositionStarted: false, transactions: [],
    dispatch(spec) {const tr = this.state.update(spec); this.transactions.push(tr); this.state = tr.state;}
  };
  const calls = [];
  let response = async () => ({completions: items});
  const source = assist.createSource({
    analyze: position => {calls.push(position); return response();},
    allowed: () => editable,
    currentContext: () => scope
  });
  const context = () => ({state: view.state, pos: view.state.selection.main.head,
                          view, explicit: false, aborted: false});
  return {view, calls, source, context,
    scope(value) {scope = value;}, editable(value) {editable = value;},
    response(value) {response = value;}};
}
function accept(f, result, index = 0) {
  const option = result.options[index];
  option.apply(f.view, option, result.from, result.to);
}
function mutate(f, kind) {
  if (kind === 'dependency') f.scope('project-a/main.chaos/dialect-2/dependencies-2');
  if (kind === 'source') f.view.dispatch({changes: {from: 0, insert: 'other '}});
  if (kind === 'caret') f.view.dispatch({selection: {anchor: 0}});
  if (kind === 'composition') f.view.composing = true;
  if (kind === 'composition-start') {f.view.compositionStarted = true; f.view.composing = false;}
  if (kind === 'readonly') f.editable(false);
}

(async () => {
  {
    const prefix = '// 🙂 中文\nlet value = 42;', text = prefix + 'valueName';
    const f = fixture(text, prefix.length + 3,
      [{label: 'value', apply: 'value', from: prefix.length, to: text.length}]);
    const result = await f.source(f.context());
    assert.equal(result.from, prefix.length); assert.equal(result.to, text.length);
    assert.equal(result.filter, false); assert.equal(result.validFor, undefined);
    accept(f, result);
    assert.equal(f.view.state.doc.toString(), prefix + 'value');
    assert.equal(f.view.state.selection.main.head, prefix.length + 5);
    const tr = f.view.transactions.at(-1);
    assert.equal(tr.annotation(cm.pickedCompletion), result.options[0]);
    assert.equal(tr.isUserEvent('input.complete'), true);
    checks++;
  }
  {
    const text = 'root.partTail', f = fixture(text, 7, [
      {label: 'part', from: 5, to: text.length},
      {label: 'completeRoot', from: 0, to: text.length}
    ]);
    const result = await f.source(f.context()); accept(f, result, 1);
    assert.equal(f.view.state.doc.toString(), 'completeRoot'); checks++;
  }
  {
    const Selection = cm.EditorState.create().selection.constructor;
    const selection = Selection.create([Selection.cursor(2), Selection.cursor(11)], 0);
    const f = fixture('vaTail + vaTail', 2, [{label: 'value', from: 0, to: 6}], selection);
    assert.equal(f.view.state.selection.ranges.length, 2);
    accept(f, await f.source(f.context()));
    assert.equal(f.view.state.doc.toString(), 'value + value');
    assert.equal(f.view.state.selection.ranges.length, 2);
    assert.deepEqual(Array.from(f.view.state.selection.ranges, r => r.head), [5, 13]); checks++;
  }
  {
    // No frontend word/keyword table: even an empty automatic request reaches
    // the service, and an arbitrary service-provided symbol remains available.
    const f = fixture('', 0, [{label: '来自后端的名字', from: 0, to: 0}]);
    const result = await f.source(f.context()); assert.deepEqual(f.calls, [0]);
    accept(f, result); assert.equal(f.view.state.doc.toString(), '来自后端的名字'); checks++;
  }
  {
    const f = fixture('🙂name', 3, [null,
      {label: 'broken-start', from: 1, to: 6},
      {label: 'broken-end', from: 0, to: 1},
      {label: 'negative', from: -1, to: 6},
      {label: 'outside', from: 2, to: 99}]);
    assert.equal(await f.source(f.context()), null); assert.equal(f.view.transactions.length, 0); checks++;
  }
  for (const kind of ['dependency', 'source', 'caret', 'composition', 'composition-start', 'readonly']) {
    const f = fixture('valTail', 3, [{label: 'value', from: 0, to: 7}]);
    let resolve;
    f.response(() => new Promise(done => {resolve = done;}));
    const pending = f.source(f.context()); mutate(f, kind);
    resolve({completions: [{label: 'value', from: 0, to: 7}]});
    assert.equal(await pending, null, 'late response accepted after ' + kind); checks++;
  }
  for (const kind of ['dependency', 'source', 'caret', 'composition', 'composition-start', 'readonly']) {
    const f = fixture('valTail', 3, [{label: 'value', from: 0, to: 7}]);
    const result = await f.source(f.context()); assert.ok(result);
    mutate(f, kind);
    const text = f.view.state.doc.toString(), transactions = f.view.transactions.length;
    accept(f, result);
    assert.equal(f.view.state.doc.toString(), text, 'stale menu edited after ' + kind);
    assert.equal(f.view.transactions.length, transactions); checks++;
  }
  for (const kind of ['composition', 'composition-start', 'readonly']) {
    const f = fixture('valTail', 3, [{label: 'value', from: 0, to: 7}]);
    mutate(f, kind); assert.equal(await f.source(f.context()), null);
    assert.deepEqual(f.calls, []); checks++;
  }
  {
    const f = fixture('valTail', 3, [{label: 'value', from: 0, to: 7}]);
    let resolve; f.response(() => new Promise(done => {resolve = done;}));
    const context = f.context(), pending = f.source(context); context.aborted = true;
    resolve({completions: [{label: 'value', from: 0, to: 7}]});
    assert.equal(await pending, null); checks++;
  }
  process.stdout.write(JSON.stringify({passed: true, checks, actualCodeMirror: true,
    dom: false, network: false}) + '\n');
})().catch(error => {console.error(error); process.exitCode = 1;});
