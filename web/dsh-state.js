/* Adapted from DeepSeek Harness (MIT), commit 5badb15009ae1756c3afe0ae0cef1faafc290ccc.
 * Copyright (c) 2026 DeepSeek. See vendor/deepseek/UPSTREAM.md and LICENSE.
 * Uses explicit event metadata; no task-text or tool-name keyword classifier.
 */
(function (root) {
  'use strict';
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, {granularity: 'grapheme'}) : null;
  function normalizeDetail(text) {
    if (typeof text !== 'string') return '';
    const normalized = text.replace(/\s+/g, ' ').trim();
    const chars = segmenter ? Array.from(segmenter.segment(normalized), item => item.segment) : Array.from(normalized);
    return chars.length > 160 ? chars.slice(0, 159).join('').trimEnd() + '…' : normalized;
  }
  // DSH processActivity: distinct call counts, stable ranking, newest live call.
  // Categories and user-visible summaries arrive as typed host metadata.
  function processActivity(items) {
    const counts = new Map(), seen = new Set();
    let running, runningDetail = '', runningTime = -Infinity, preparing = false;
    function visit(item) {
      if (!item || typeof item.id !== 'string' || seen.has(item.id)) return;
      seen.add(item.id);
      const kind = typeof item.category === 'string' ? item.category : 'activity';
      counts.set(kind, (counts.get(kind) || 0) + 1);
      const time = Number(item.time) || 0;
      if (item.status === 'running' && time >= runningTime) {
        running = kind; runningTime = time; runningDetail = normalizeDetail(item.detail);
        preparing = item.phase === 'preparing';
      }
      for (const child of item.children || []) visit(child);
    }
    for (const item of items || []) visit(item);
    return {counts: Array.from(counts, ([kind, count]) => ({kind, count})).sort((a, b) => b.count - a.count),
      running, runningDetail, ...(preparing ? {preparing: true} : {})};
  }
  // Direct adaptation of DSH cordisVisibleStatus: a host activation and a client
  // activation must identify the same immutable package AND plugin run.
  function visibleStatus(row, packageId, loaded) {
    const run = row && row.activeRun;
    if (!run || run.packageId !== packageId) return 'idle';
    const pkg = (row.packages || []).find(value => value.packageId === packageId);
    if (!pkg || pkg.hasClientHalf !== true) return 'running';
    return (loaded || []).some(live => live.pluginId === row.pluginId && live.packageId === packageId &&
      live.pluginRunId === run.pluginRunId) ? 'running' : 'client-pending';
  }
  // The clock owns a small DOM leaf, independent of transcript rerendering.
  function createClock(element, startTime) {
    let stopped = false;
    const started = Number(startTime);
    function render() {
      if (stopped) return;
      const elapsed = Number.isFinite(started) ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
      element.textContent = elapsed >= 60 ? Math.floor(elapsed / 60) + ':' + String(elapsed % 60).padStart(2, '0') : elapsed + 's';
    }
    render(); const timer = setInterval(render, 1000);
    return function dispose() { if (!stopped) { stopped = true; clearInterval(timer); } };
  }
  function createActivityStore(limit = 400) {
    limit = Math.max(10, Math.min(5000, Math.floor(Number(limit) || 400)));
    const events = [], items = new Map(), seen = new Set();
    const states = Object.freeze({'task.queued': 'pending', 'task.started': 'running', 'task.thinking': 'running',
      'agent.thinking': 'running', 'agent.observing': 'running', 'tool.started': 'running',
      'tool.completed': 'completed', 'tool.failed': 'failed', 'agent.tool_started': 'running', 'agent.tool_completed': 'completed', 'agent.tool_failed': 'failed', 'task.completed': 'completed',
      'task.failed': 'failed', 'task.cancelled': 'cancelled', 'task.interrupted': 'interrupted', 'task.iteration': 'running', 'agent.completed': 'completed', 'agent.failed': 'failed'});
    const terminal = new Set(['task.completed', 'task.failed', 'task.cancelled', 'task.interrupted']);
    return {
      ingest(event) {
        if (!event || typeof event !== 'object') return false;
        const eventId = event.id == null ? null : String(event.id);
        if (eventId !== null && seen.has(eventId)) return false;
        if (eventId !== null) seen.add(eventId);
        events.push(event);
        while (events.length > limit) { const old = events.shift(); if (old.id != null) seen.delete(String(old.id)); }
        const payload = event.payload && typeof event.payload === 'object' ? event.payload : {};
        const taskId = String(event.taskId || event.runId || payload.taskId || 'system');
        const id = taskId + ':' + String(payload.callId || payload.nodeId || 'task');
        const prior = items.get(id) || {};
        const time = Number(event.time || payload.time || 0);
        items.set(id, {id, taskId, category: typeof payload.category === 'string' ? payload.category : prior.category || 'activity',
          detail: normalizeDetail(payload.summary || payload.detail || prior.detail || ''),
          status: typeof payload.state === 'string' ? payload.state : states[event.kind] || prior.status || 'running',
          time: Number.isFinite(time) ? time : 0, phase: payload.phase || ''});
        if (terminal.has(event.kind)) {
          for (const item of items.values()) if (item.taskId === taskId) item.status = states[event.kind];
        }
        while (items.size > limit) items.delete(items.keys().next().value);
        return true;
      },
      snapshot() {
        const rows = Array.from(items.values(), item => ({...item}));
        return {events: events.slice(), items: rows, activity: processActivity(rows)};
      },
      clear() { events.length = 0; items.clear(); seen.clear(); }
    };
  }
  root.DSHState = Object.freeze({normalizeDetail, processActivity, visibleStatus, createClock, createActivityStore});
})(typeof window !== 'undefined' ? window : globalThis);
