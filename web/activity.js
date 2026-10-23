/* One async activity channel per page. Events are explicit protocol data. */
(() => {
  'use strict';
  if (window.ChaosActivity) return;
  const parse = typeof parseJSON === 'function' ? parseJSON : JSON.parse;
  const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
  const labels = {'task.queued': '任务排队', 'task.started': '开始执行', 'task.iteration': '开始迭代', 'task.completed': '任务完成', 'task.failed': '执行失败', 'task.cancelled': '任务已取消', 'task.interrupted': '任务已中断', 'agent.thinking': '模型正在处理', 'agent.observing': '检查工具结果', 'agent.tool_started': '工具开始执行', 'agent.tool_completed': '工具执行完成', 'agent.tool_failed': '工具执行失败', 'agent.completed': '智能体完成', 'route.selected': '已选择执行模型', 'plugin.status': '扩展状态更新'};
  const store = window.DSHState?.createActivityStore(400);
  const state = {tasks: [], events: [], cursor: 0, busy: false, timer: null, rerun: false, connected: false, expanded: false, collapsed: false, selected: null, clockTask: null, disposeClock: null, lastRender: '', revision: 0, refreshTimer: null, stream: null};
  function element(tag, className, text) { const value = document.createElement(tag); if (className) value.className = className; if (text !== undefined) value.textContent = text; return value; }
  const container = element('aside', 'chaos-activity'); container.setAttribute('aria-label', '后台执行活动'); container.hidden = true;
  const panel = element('div', 'chaos-activity-panel');
  const header = element('div', 'chaos-activity-header');
  const dot = element('span', 'chaos-activity-dot');
  const title = element('span', '', '后台执行');
  const clock = element('span', 'chaos-activity-clock');
  const collapse = element('button', 'chaos-activity-button', '−'); collapse.type = 'button'; collapse.title = '收起活动卡片'; collapse.setAttribute('aria-label', '收起活动卡片');
  header.append(dot, title, clock, collapse);
  const body = element('div', 'chaos-activity-body');
  const iteration = element('div', 'chaos-activity-iteration');
  const summary = element('div', 'chaos-activity-summary'); summary.setAttribute('role', 'status'); summary.setAttribute('aria-live', 'polite');
  const counts = element('div', 'chaos-activity-counts');
  const footer = element('div', 'chaos-activity-footer');
  const history = element('button', 'chaos-activity-button', '执行轨迹'); history.type = 'button'; history.setAttribute('aria-expanded', 'false');
  const link = element('a', 'chaos-activity-button', '控制台 ↗'); link.href = '/control';
  const cancel = element('button', 'chaos-activity-button chaos-activity-cancel', '停止'); cancel.type = 'button';
  footer.append(history, link, cancel);
  const historyList = element('ol', 'chaos-activity-history'); historyList.hidden = true;
  body.append(iteration, summary, counts, footer, historyList); panel.append(header, body);
  const bubble = element('button', 'chaos-activity-bubble', '◉ 后台活动'); bubble.type = 'button'; bubble.hidden = true;
  container.append(panel, bubble); document.body.append(container);
  collapse.addEventListener('click', () => { state.collapsed = true; panel.hidden = true; bubble.hidden = false; });
  bubble.addEventListener('click', () => { state.collapsed = false; panel.hidden = false; bubble.hidden = true; });
  history.addEventListener('click', () => { state.expanded = !state.expanded; historyList.hidden = !state.expanded; history.setAttribute('aria-expanded', String(state.expanded)); });
  cancel.addEventListener('click', async () => {
    if (!state.selected) return;
    cancel.disabled = true;
    try { await request(`/api/tasks/${encodeURIComponent(state.selected)}/cancel`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'}); refresh(); }
    catch (_) { summary.textContent = '停止请求未送达，请在控制台重试。'; }
    finally { cancel.disabled = false; }
  });
  async function request(path, options = {}) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try { const response = await fetch(path, {...options, signal: controller.signal}); if (!response.ok) throw new Error(`HTTP ${response.status}`); return parse(await response.text()); }
    finally { clearTimeout(timer); }
  }
  function ingest(event) {
    const id = window.ChaosRealtime.eventId(event.id);
    if (window.ChaosRealtime.compareIds(id, state.cursor) <= 0) return;
    const normalized = {...event, kind: event.kind || event.type, time: event.time || event.createdAt};
    state.cursor = id;
    if (event.type.startsWith('task.')) state.revision++;
    state.events.push(normalized);
    if (state.events.length > 400) state.events.shift();
    store?.ingest(normalized);
  }
  function eventText(event) {
    const payload = event.payload || {};
    const description = typeof payload.summary === 'string' ? payload.summary : typeof payload.detail === 'string' ? payload.detail : '';
    const detail = event.kind === 'plugin.status' ? [payload.pluginId, payload.state].filter(Boolean).join(' · ') : event.kind === 'route.selected' ? [payload.provider, payload.model, payload.backend].filter(Boolean).join(' · ') : '';
    const text = description || detail || labels[event.kind || event.type] || event.kind || event.type || '执行状态更新';
    return window.DSHState ? window.DSHState.normalizeDetail(text) : String(text).slice(0, 160);
  }
  function render() {
    const active = state.tasks.filter(task => !terminal.has(task.status));
    const sorted = state.tasks.slice().sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0));
    const task = active.find(value => value.id === state.selected) || active[0] || sorted[0];
    if (!task) { container.hidden = true; state.disposeClock?.(); state.disposeClock = null; state.clockTask = null; return; }
    container.hidden = false; state.selected = task.id;
    const taskEvents = state.events.filter(event => event.taskId === task.id || event.runId === task.id);
    const latest = taskEvents[taskEvents.length - 1];
    const signature = `${task.id}:${task.status}:${task.iteration}:${latest?.id}:${state.connected}:${active.length}`;
    if (signature === state.lastRender) return;
    state.lastRender = signature;
    const running = !terminal.has(task.status);
    panel.classList.toggle('running', running); panel.classList.toggle('offline', !state.connected);
    dot.classList.toggle('running', running);
    title.textContent = state.connected ? running ? `后台执行 · ${active.length} 个任务` : '后台执行已结束' : '等待连接恢复';
    bubble.textContent = running ? `◉ ${active.length} 个后台任务` : '◉ 最近任务';
    const iterationEvent = taskEvents.slice().reverse().find(event => event.kind === 'task.iteration');
    iteration.textContent = `${String(task.id).slice(-8)} · ${running ? '迭代' : '完成'} ${running ? iterationEvent?.payload?.iteration || task.iteration || 0 : task.iteration || 0} / ${task.iterations || 1}`;
    summary.textContent = latest ? eventText(latest) : running ? '任务已提交，等待后台事件。' : (task.status === 'completed' ? '任务已完成。' : '任务已结束，详情见控制台。');
    const snapshot = store?.snapshot();
    const perTaskItems = snapshot?.items.filter(item => item.taskId === task.id) || [];
    const activity = window.DSHState?.processActivity(perTaskItems) || snapshot?.activity;
    counts.replaceChildren(...(activity?.counts || []).slice(0, 4).map(item => element('span', '', `${item.kind} ${item.count}`)));
    if (activity?.runningDetail && running) summary.textContent = activity.runningDetail;
    cancel.hidden = !running;
    historyList.replaceChildren(...taskEvents.slice(-30).reverse().map(event => {
      const row = element('li'); row.append(element('span', '', labels[event.kind] || event.kind), element('p', '', eventText(event))); return row;
    }));
    if (state.clockTask !== task.id || !running || !state.disposeClock) {
      state.disposeClock?.(); state.disposeClock = null; state.clockTask = task.id;
      if (running && window.DSHState) { const time = Number(task.createdAt); state.disposeClock = window.DSHState.createClock(clock, time < 1e12 ? time * 1000 : time); }
      else clock.textContent = '';
    }
    summary.classList.remove('changed'); requestAnimationFrame(() => summary.classList.add('changed'));
  }
  function dispatch() { window.dispatchEvent(new CustomEvent('chaos:activity', {detail: {tasks: state.tasks, events: state.events, activity: store?.snapshot().activity, connected: state.connected}})); }
  async function poll() {
    if (state.busy) { state.rerun = true; return; }
    state.busy = true; clearTimeout(state.timer);
    const revision = state.revision;
    try {
      const result = await request('/api/tasks');
      if (revision === state.revision) state.tasks = Array.isArray(result) ? result : result.items || [];
      else state.rerun = true;
      render(); dispatch();
    } catch (_) { /* The stream and last snapshot remain usable during a read failure. */ }
    finally {
      state.busy = false; const delay = state.rerun ? 0 : state.connected ? 10000 : 2000;
      state.rerun = false; state.timer = setTimeout(poll, delay);
    }
  }
  function refreshSoon() {
    if (state.refreshTimer !== null) return;
    state.refreshTimer = setTimeout(() => { state.refreshTimer = null; refresh(); }, 100);
  }
  function refresh() { if (state.busy) { state.rerun = true; return; } clearTimeout(state.timer); poll(); }
  window.ChaosActivity = Object.freeze({refresh, snapshot: () => ({tasks: state.tasks.slice(), events: state.events.slice(), cursor: state.cursor, connected: state.connected})});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('online', refresh);
  state.stream = window.ChaosRealtime.subscribe({url: '/api/tasks/events/stream',
    onState(value) { state.connected = value === 'connected'; render(); dispatch(); if (state.connected) refreshSoon(); },
    onEvent(event) { ingest(event); render(); dispatch(); if (event.type.startsWith('task.')) refreshSoon(); }
  });
  window.addEventListener('pagehide', event => { if (event.persisted) return; state.stream.close(); clearTimeout(state.timer); clearTimeout(state.refreshTimer); });
  poll();
})();
