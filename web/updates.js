/* Updates own their configuration draft; model settings remain untouched. */
(() => {
  'use strict';
  const panel = document.getElementById('updatesPanel');
  if (!panel) return;
  const parse = typeof parseJSON === 'function' ? parseJSON : JSON.parse;
  const stringify = typeof stringifyJSON === 'function' ? stringifyJSON : JSON.stringify;
  const $ = id => document.getElementById(id);
  const activePhases = new Set(['queued', 'testing', 'checking', 'preparing', 'downloading', 'verifying', 'installing', 'waiting_idle', 'waiting-idle', 'restarting', 'health_check', 'health-check', 'healthcheck', 'rolling_back', 'rolling-back']);
  const labels = {queued: '更新请求已排队', testing: '正在确认新版本可用', ready: '新版本已准备好', restart_required: '等待服务重启', active: '服务已恢复', idle: '等待检查', checking: '正在检查版本', available: '发现新版本', update_available: '发现新版本', up_to_date: '已是最新版本', preparing: '正在准备更新', downloading: '正在下载', verifying: '正在验证发布包', installing: '正在安装', waiting_idle: '等待当前任务完成', 'waiting-idle': '等待当前任务完成', restarting: '正在重启服务', health_check: '正在确认服务恢复', 'health-check': '正在确认服务恢复', healthcheck: '正在确认服务恢复', healthy: '服务已恢复', completed: '更新完成', rolling_back: '正在回滚', 'rolling-back': '正在回滚', rolled_back: '已恢复上一版本', failed: '更新未完成', error: '更新未完成', unmanaged: '需要启用托管安装', unconfigured: '未配置更新来源'};
  const state = {status: null, config: null, dirty: false, saving: false, conflict: false, command: '', awaitingStatus: false, disconnected: false, timer: null, epoch: 0, notice: '', noticeError: false};
  const element = (tag, attrs = {}, ...children) => {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else el.setAttribute(key, value);
    }
    for (const child of children.flat()) if (child !== null && child !== undefined) el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return el;
  };
  function field(label, id, attrs, helper) {
    return element('div', {class: 'field'}, element('label', {for: id}, label), element('input', {id, ...attrs}), helper ? element('p', {class: 'helper'}, helper) : null);
  }
  panel.replaceChildren(
    element('div', {class: 'panel-heading'}, element('div', {}, element('div', {class: 'eyebrow'}, 'SOFTWARE UPDATES'), element('h2', {}, '让系统，持续向前。'), element('p', {}, '检查新版本，一键更新。更新会等待当前任务完成，并在重启后确认服务恢复。')), element('span', {class: 'panel-icon', 'aria-hidden': 'true'}, '↥')),
    element('section', {class: 'update-summary', 'aria-label': '软件版本'},
      element('div', {class: 'update-summary-head'}, element('span', {id: 'updateHeadline', role: 'status'}, '正在读取更新状态…'), element('span', {id: 'updateMode', class: 'tag'}, '检测中')),
      element('div', {class: 'update-versions'}, element('div', {}, element('span', {}, '当前版本'), element('strong', {id: 'updateCurrent'}, '—')), element('span', {class: 'update-arrow', 'aria-hidden': 'true'}, '→'), element('div', {}, element('span', {}, '可用版本'), element('strong', {id: 'updateAvailable'}, '尚未检查'))),
      element('p', {id: 'updateMessage', class: 'helper'}),
      element('progress', {id: 'updateProgress', max: 100, 'aria-label': '更新进度', hidden: ''}),
      element('div', {class: 'update-actions'}, element('button', {id: 'updateCheck', type: 'button', disabled: ''}, '检查更新'), element('button', {id: 'updateApply', type: 'button', class: 'primary', disabled: ''}, '一键更新'), element('button', {id: 'updateRollback', type: 'button', class: 'quiet', disabled: ''}, '回滚上一版本')),
      element('p', {id: 'updatePrevious', class: 'helper'})),
    element('div', {id: 'updateNotice', class: 'update-notice', role: 'status', hidden: ''}),
    element('div', {id: 'updateEnrollment', class: 'note-card', hidden: ''}, element('strong', {}, '首次使用：启用托管安装'), element('p', {}, '当前为源码或普通安装。请由部署者完成一次托管安装，再使用一键更新。现有项目、配置与任务数据会继续保留。'), element('pre', {id: 'updateEnrollCommand', hidden: ''})),
    element('details', {id: 'updateReleaseNotes', hidden: ''}, element('summary', {}, '查看版本说明'), element('pre', {id: 'updateChangelog', class: 'update-changelog'})),
    element('form', {id: 'updateConfigForm', class: 'update-config'},
      element('div', {class: 'section-label'}, '更新设置', element('span', {}, '独立保存')),
      field('更新来源地址', 'updateFeedURL', {type: 'url', placeholder: 'https://example.com/releases/stable.json', autocomplete: 'off', spellcheck: 'false'}, '由部署者提供发布地址。未配置时不会自动检查，也不会显示为最新版本。'),
      element('div', {class: 'check-field'}, element('input', {id: 'updateAutoCheck', type: 'checkbox'}), element('label', {for: 'updateAutoCheck'}, '自动检查新版本')),
      field('检查间隔 / 秒', 'updateInterval', {type: 'number', min: 60, max: 604800, step: 1, value: 3600}, '自动检查只发现新版本；安装由你点击“一键更新”开始。'),
      field('已信任的发布者指纹', 'updatePublisher', {type: 'text', readonly: '', placeholder: '尚未设置', spellcheck: 'false'}, '发布包需通过已信任发布者的签名验证。此处仅显示，由部署者设置。'),
      element('div', {id: 'updateConfigConflict', class: 'update-notice warning', hidden: ''}, element('span', {}, '更新设置已被其他页面修改。当前草稿已保留。'), element('button', {id: 'updateReloadConfig', type: 'button'}, '载入最新设置')),
      element('div', {class: 'update-config-actions'}, element('button', {id: 'updateSaveConfig', type: 'submit', disabled: ''}, '保存更新设置'), element('span', {id: 'updateConfigState', class: 'helper'}, '载入中…')))
  );
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, {...options, signal: controller.signal, headers: {'Content-Type': 'application/json', ...options.headers}});
      const raw = await response.text();
      let data;
      try { data = raw ? parse(raw) : {}; } catch (_) { throw new Error(`服务响应无法读取（HTTP ${response.status}）`); }
      if (!response.ok) {
        const error = new Error(data.error?.message || (typeof data.error === 'string' ? data.error : '') || data.message || `请求失败（HTTP ${response.status}）`);
        error.status = response.status;
        throw error;
      }
      return data;
    } finally { clearTimeout(timer); }
  }
  function busy() { return Boolean(state.command || state.awaitingStatus || activePhases.has(state.status?.phase) || state.status?.busy || state.status?.restartRequired); }
  function notify(message, error = false) { state.notice = message; state.noticeError = error; render(); }
  function installConfig(config) {
    state.config = {...config}; state.dirty = false; state.conflict = false;
    $('updateFeedURL').value = config.feedURL || '';
    $('updateAutoCheck').checked = Boolean(config.autoCheck);
    $('updateInterval').value = config.intervalSeconds ?? 3600;
  }
  function normalize(data) {
    const status = data.status && typeof data.status === 'object' ? data.status : data;
    if (!status.config || typeof status.config !== 'object') return status;
    const phase = status.phase || status.state || 'idle';
    return {...status, phase: status.health?.ok && status.health?.rolledBackFrom && ['idle', 'active', 'healthy'].includes(phase) ? 'rolled_back' : phase, config: {...status.config, version: status.version ?? status.config.version}, availableVersion: status.availableVersion || status.latestVersion, publisherFingerprint: status.publisherFingerprint || status.config.keyId};
  }
  function accept(data) {
    // Command endpoints may return an acknowledgement; only status objects replace state.
    const status = normalize(data);
    if (!status.config || typeof status.config !== 'object') return;
    state.status = status;
    state.disconnected = false;
    if (!state.config || (!state.dirty && !state.saving)) installConfig(status.config);
    else if (!state.saving && status.config.version !== state.config.version) state.conflict = true;
  }
  function render() {
    const status = state.status || {}, configured = status.configured ?? Boolean(status.config?.feedURL), active = busy();
    const phase = status.phase || 'idle';
    $('updateCurrent').textContent = status.currentVersion || '—';
    $('updateAvailable').textContent = status.availableVersion || (configured ? '尚未发现' : '未配置');
    $('updateMode').textContent = !state.status ? '检测中' : status.managed ? '托管安装' : '普通安装';
    let headline = labels[phase] || '更新状态已读取';
    if (!state.status) headline = '正在读取更新状态…';
    else if (!configured && !active) headline = status.config?.feedURL ? '需要设置信任的发布者' : '未配置更新来源';
    else if (status.availableVersion && status.availableVersion !== status.currentVersion && !active && !['failed', 'error', 'rolled_back'].includes(phase)) headline = '新版本已就绪';
    if (state.disconnected) headline = active ? '等待服务重新连接…' : '暂时无法连接更新服务';
    $('updateHeadline').textContent = headline;
    const error = typeof status.error === 'string' ? status.error : status.error?.message;
    $('updateMessage').textContent = state.disconnected ? (active ? '更新状态已保留，正在自动重连。请保持此页面打开。' : '正在自动重连；恢复连接后会刷新实际状态。') : error || status.message || (phase === 'rolled_back' ? `新版本 ${status.health?.rolledBackFrom || ''} 未能正常启动，已自动恢复当前版本。` : '') || (status.restartRequired ? '更新已准备好，等待服务重启。' : '');
    $('updateMessage').classList.toggle('update-error', Boolean(error));
    $('updateProgress').hidden = !active;
    if (Number.isFinite(status.progress)) $('updateProgress').value = Math.max(0, Math.min(100, status.progress));
    else $('updateProgress').removeAttribute('value');
    const unavailable = !state.status || state.disconnected || active || state.saving;
    $('updateCheck').disabled = unavailable || !configured || state.dirty;
    $('updateApply').disabled = unavailable || !status.managed || !configured || !status.availableVersion || status.available === false || status.availableVersion === status.currentVersion || state.dirty;
    $('updateRollback').disabled = unavailable || !status.managed || !status.previousVersion;
    $('updateCheck').textContent = phase === 'checking' || state.command === 'check' ? '正在检查…' : '检查更新';
    $('updateApply').textContent = state.command === 'apply' ? '正在提交…' : '一键更新';
    $('updatePrevious').textContent = status.previousVersion ? `可回滚至 ${status.previousVersion} · 项目与配置数据保留` : '更新前会保留上一版本，便于回滚。';
    $('updateEnrollment').hidden = !state.status || status.managed;
    const enrollment = status.enrollCommand || status.enrollmentCommand || 'chaos update adopt';
    $('updateEnrollCommand').hidden = !enrollment;
    $('updateEnrollCommand').textContent = typeof enrollment === 'string' ? enrollment : '';
    const changelog = status.changelog || status.availableRelease?.changelog || '';
    $('updateReleaseNotes').hidden = !changelog;
    $('updateChangelog').textContent = typeof changelog === 'string' ? changelog : '';
    $('updatePublisher').value = status.publisherFingerprint || status.config?.publisherFingerprint || '';
    $('updateNotice').hidden = !state.notice;
    $('updateNotice').textContent = state.notice;
    $('updateNotice').className = `update-notice${state.noticeError ? ' error' : ''}`;
    $('updateConfigConflict').hidden = !state.conflict;
    $('updateSaveConfig').disabled = !state.config || !state.dirty || state.saving || state.conflict || active || state.disconnected;
    $('updateSaveConfig').textContent = state.saving ? '保存中…' : '保存更新设置';
    $('updateConfigState').textContent = state.conflict ? '存在版本冲突 · 草稿已保留' : state.saving ? '正在保存…' : state.dirty ? '有未保存修改' : state.config ? '更新设置已保存' : '载入中…';
    for (const id of ['updateFeedURL', 'updateAutoCheck', 'updateInterval']) $(id).disabled = !state.config || state.saving || active;
  }
  function schedule(delay) {
    clearTimeout(state.timer);
    state.timer = setTimeout(refresh, delay ?? (busy() || state.disconnected ? 1000 : 30000));
  }
  async function refresh() {
    const epoch = state.epoch;
    try { const result = await api('/api/updates'); if (epoch === state.epoch) { accept(result); if (result.config || result.status?.config) state.awaitingStatus = false; } }
    catch (error) { if (epoch === state.epoch) { state.disconnected = true; if (!state.status) { state.notice = error.status === 404 ? '当前服务尚未提供更新功能。请先升级或启用托管安装。' : error.message; state.noticeError = true; } } }
    finally { if (epoch === state.epoch) { render(); schedule(); } }
  }
  async function command(action) {
    if (busy() || state.disconnected || !state.status) return;
    clearTimeout(state.timer); state.epoch++; state.command = action; state.awaitingStatus = true; state.notice = ''; render();
    try {
      const result = await api(`/api/updates/${action}`, {method: 'POST', body: '{}'});
      accept(result);
      state.notice = action === 'check' ? '检查请求已提交。' : action === 'rollback' ? '回滚已开始，请等待服务恢复。' : '更新已开始，请等待服务恢复。';
      state.noticeError = false;
    } catch (error) {
      state.notice = error.status ? error.message : '请求结果尚未确认，正在重新读取服务状态。不会自动重复提交。';
      state.noticeError = true;
      state.disconnected = !error.status; if (error.status) state.awaitingStatus = false;
    } finally { state.command = ''; render(); schedule(250); }
  }
  async function saveConfig(event) {
    event.preventDefault();
    if (!state.config || state.saving || state.conflict || !state.dirty || busy()) return;
    const feedURL = $('updateFeedURL').value.trim(), intervalSeconds = Number($('updateInterval').value);
    if (!Number.isInteger(intervalSeconds) || intervalSeconds < 60 || intervalSeconds > 604800) { notify('检查间隔须为 60 至 604800 秒的整数。', true); return; }
    if (feedURL) { try { const url = new URL(feedURL); if (url.protocol !== 'https:') throw new Error(); } catch (_) { notify('请填写有效的 HTTPS 更新来源地址。', true); return; } }
    state.saving = true; render();
    try {
      const response = normalize(await api('/api/updates/config', {method: 'PATCH', body: stringify({version: state.config.version, feedURL, autoCheck: $('updateAutoCheck').checked, intervalSeconds})}));
      state.saving = false;
      const config = response.config || response.status?.config || (response.version !== undefined ? response : null);
      if (config) installConfig(config);
      else { state.dirty = false; await refresh(); }
      accept(response);
      notify('更新设置已保存。');
    } catch (error) {
      if (error.status === 409) state.conflict = true;
      notify(error.status === 409 ? '更新设置版本冲突。当前草稿已保留，请载入最新设置后再修改。' : error.message, true);
    } finally { state.saving = false; render(); schedule(250); }
  }
  for (const id of ['updateFeedURL', 'updateAutoCheck', 'updateInterval']) $(id).addEventListener('input', () => { state.dirty = true; render(); });
  for (const action of ['Check', 'Apply', 'Rollback']) $(`update${action}`).addEventListener('click', () => command(action.toLowerCase()));
  $('updateConfigForm').addEventListener('submit', saveConfig);
  $('updateReloadConfig').addEventListener('click', async () => {
    try { const data = await api('/api/updates'); const status = normalize(data); if (status.config) { installConfig(status.config); accept(status); notify('已载入最新更新设置。'); } }
    catch (error) { notify(error.message, true); }
  });
  document.querySelector('[data-tab="updates"]')?.addEventListener('click', () => { if (!state.command && !state.saving) { clearTimeout(state.timer); refresh(); } });
  render(); refresh();
})();
