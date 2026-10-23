/* Configuration is explicit data. Prompts are never inspected for routing keywords. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const parse = typeof parseJSON === 'function' ? parseJSON : JSON.parse;
  const stringify = typeof stringifyJSON === 'function' ? stringifyJSON : JSON.stringify;
  const clone = value => parse(stringify(value));
  const state = { settings: null, draft: null, changes: {}, secrets: {providers: {}, agents: {}, roles: {}}, provider: 'text', tab: 'provider', dirty: false, generation: 0, saving: false, remote: null, status: null, tasks: [], selectedTask: null, detailRequest: 0 };
  const providerTitles = {text: '文本与推理', image1k: '图像 · 1K', image4k: '图像 · 4K', grok: 'Grok 服务'};
  const roleTitles = {planner: '顶层规划', executor: '执行与编码', reviewer: '独立复核', image1k: '图像 · 1K', image4k: '图像 · 4K', image: '图像生成', grok: 'Grok 智能体'};
  const roleDescriptions = {planner: '复杂任务、科研与全局规划', executor: '工具调用、代码执行与批量处理', reviewer: '验证结果、发现风险与复核代码'};
  const statuses = {queued: '排队中', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断'};
  const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(path, {...options, signal: controller.signal, headers: {'Content-Type': 'application/json', ...options.headers}});
      const text = await response.text();
      let data; try { data = text ? parse(text) : {}; } catch (_) { throw new Error(`服务响应不是有效 JSON（HTTP ${response.status}）`); }
      if (!response.ok) { const error = new Error(data.error?.message || data.error || data.message || `HTTP ${response.status}`); error.status = response.status; throw error; }
      return data;
    } finally { clearTimeout(timer); }
  }
  function node(tag, attrs = {}, ...children) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'class') element.className = value;
      else if (key.startsWith('on')) element.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === 'text') element.textContent = value;
      else if (value !== undefined && value !== null) element.setAttribute(key, value);
    }
    for (const child of children.flat()) if (child !== undefined && child !== null) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return element;
  }
  function notice(message, error = false) { $('controlNotice').textContent = message; $('controlNotice').className = `notice${error ? ' error' : ''}`; $('controlNotice').hidden = !message; }
  function replace(target, ...children) { target.replaceChildren(...children.filter(value => value !== null && value !== undefined)); }
  function models(provider) { return (state.draft?.providers?.[provider]?.models || []).map(value => typeof value === 'string' ? value : value.id).filter(Boolean); }
  function pathSet(object, path, value) { let cursor = object; for (const part of path.slice(0, -1)) { if (!Object.prototype.hasOwnProperty.call(cursor, part) || cursor[part] === null || typeof cursor[part] !== 'object') Object.defineProperty(cursor, part, {value: {}, enumerable: true, configurable: true, writable: true}); cursor = cursor[part]; } Object.defineProperty(cursor, path[path.length - 1], {value, enumerable: true, configurable: true, writable: true}); }
  function edit(path, value) { pathSet(state.draft, path, value); pathSet(state.changes, path, value); state.dirty = true; state.generation++; renderState(); }
  function secret(collection, id, value) { state.secrets[collection][id] = value; state.dirty = true; state.generation++; renderState(); }
  function providerOptions(includeInherit = false) {
    return [...(includeInherit ? [['', '继承角色配置']] : []), ...Object.keys(state.draft.providers || {}).map(id => [id, providerTitles[id] || id])];
  }
  function selectField(label, current, options, onChange, helper) {
    const id = `field-${Math.random().toString(36).slice(2)}`;
    const input = node('select', {id, onChange: event => onChange(event.target.value)});
    for (const [value, title] of options) input.append(node('option', {value}, title));
    if (current && !options.some(([value]) => value === current)) input.append(node('option', {value: current}, current));
    input.value = current || '';
    return node('div', {class: 'field'}, node('label', {for: id}, label), input, helper ? node('p', {class: 'helper'}, helper) : null);
  }
  function textField(label, value, onChange, {type = 'text', help = '', placeholder = '', min, max, step} = {}) {
    const id = `field-${Math.random().toString(36).slice(2)}`;
    const input = node('input', {id, type, value: value ?? '', placeholder, min, max, step, autocomplete: type === 'password' ? 'new-password' : 'off', spellcheck: 'false'});
    input.addEventListener('input', () => onChange(type === 'number' ? (input.value === '' ? '' : Number(input.value)) : input.value));
    return node('div', {class: 'field'}, node('label', {for: id}, label), input, help ? node('p', {class: 'helper'}, help) : null);
  }
  function checkField(label, checked, onChange) {
    const id = `field-${Math.random().toString(36).slice(2)}`;
    const input = node('input', {id, type: 'checkbox'}); input.checked = Boolean(checked);
    input.addEventListener('change', () => onChange(input.checked));
    return node('div', {class: 'check-field'}, input, node('label', {for: id}, label));
  }
  function modelField(label, current, provider, onChange, inherit = false) {
    const id = `models-${Math.random().toString(36).slice(2)}`;
    const wrapper = textField(label, current, onChange, {placeholder: inherit ? '留空继承角色选择' : '从已发现模型中选择，或输入模型 ID', help: models(provider).length ? `${models(provider).length} 个已发现模型 · 支持输入其他兼容模型` : '先探测可用模型，也可直接输入服务商模型 ID'});
    wrapper.querySelector('input').setAttribute('list', id);
    wrapper.append(node('datalist', {id}, models(provider).map(model => node('option', {value: model}))));
    return wrapper;
  }
  function heading(eyebrow, title, description, icon) {
    return node('div', {class: 'panel-heading'}, node('div', {}, node('div', {class: 'eyebrow'}, eyebrow), node('h2', {}, title), node('p', {}, description)), node('span', {class: 'panel-icon', 'aria-hidden': 'true'}, icon));
  }
  function renderState() {
    $('settingsVersion').textContent = state.settings ? `v${state.settings.version}` : '连接中';
    $('configState').textContent = state.saving ? '正在提交配置…' : state.remote ? `远端 v${state.remote.version} · 本地修改已保留` : state.dirty ? '有未保存修改' : '配置已保存 · 新任务立即使用';
    $('configState').className = state.dirty || state.remote ? 'dirty' : '';
    $('saveSettings').disabled = !state.settings || !state.dirty || state.saving || Boolean(state.remote);
    $('saveSettings').textContent = state.saving ? '保存中…' : '保存并热生效';
    $('configConflict').hidden = !state.remote;
  }
  function renderProviders() {
    const target = $('providerList'); target.replaceChildren();
    Object.entries(state.draft.providers || {}).forEach(([id, provider], index) => {
      const button = node('button', {class: `provider-button${state.provider === id ? ' active' : ''}`, 'aria-pressed': state.provider === id, onClick: () => { state.provider = id; switchTab('provider'); renderProviders(); renderProvider(); }},
        node('span', {class: 'provider-symbol'}, ['◈', '▧', '▥', '✳'][index % 4]), node('span', {class: 'provider-copy'}, node('b', {}, providerTitles[id] || id), node('small', {}, provider.hasKey ? '凭据已保存' : '尚未配置凭据')), node('span', {class: `provider-dot${provider.hasKey ? ' ready' : ''}`}));
      target.append(button);
    });
  }
  function renderProvider() {
    const id = state.provider, provider = state.draft.providers?.[id];
    if (!provider) { $('providerPanel').replaceChildren(node('p', {class: 'empty'}, '暂无服务配置。')); return; }
    const target = $('providerPanel');
    const keyField = textField('API Key', state.secrets.providers[id] || '', value => secret('providers', id, value), {type: 'password', placeholder: provider.hasKey ? '已保存 · 留空保留原密钥' : '输入服务商密钥', help: '密钥仅提交到本系统后台；响应、导出和任务事件不会返回密钥。'});
    keyField.querySelector('input').disabled = Boolean(provider.clearKey);
    const discover = node('button', {onClick: () => discoverModels(id)}, '探测模型 ↻');
    target.replaceChildren(heading('PROVIDER CONNECTION', providerTitles[id] || id, '统一服务地址与默认模型。角色和智能体可单独选择服务与模型；留空项沿继承关系生效。', '◈'),
      textField('Base URL', provider.baseURL, value => edit(['providers', id, 'baseURL'], value), {placeholder: 'https://example.com/v1', help: '使用兼容 OpenAI 的服务地址。配置保存后，后续调用立即生效。'}),
      keyField,
      checkField('清除已保存密钥', provider.clearKey, value => { edit(['providers', id, 'clearKey'], value); if (value) state.secrets.providers[id] = ''; renderProvider(); }),
      modelField('默认模型', provider.model || '', id, value => edit(['providers', id, 'model'], value)),
      node('div', {class: 'inline-field'}, discover, node('span', {class: 'helper'}, `${models(id).length} 个已发现模型`)),
      node('div', {class: 'model-grid'}, models(id).map(model => node('button', {class: `model-chip${model === provider.model ? ' selected' : ''}`, onClick: () => { edit(['providers', id, 'model'], model); renderProvider(); }}, model))),
      node('div', {class: 'note-card'}, '配置继承顺序：智能体覆盖 → 角色路由 → 服务默认。每轮迭代启动时绑定配置快照；正在执行的一轮保持稳定，下一轮使用最新配置。'));
  }
  async function discoverModels(id) {
    if (state.dirty) { notice('请先保存当前配置，再探测服务端已保存的连接。', true); return; }
    const generation = state.generation;
    $('refreshModels').disabled = true; notice('正在探测服务可用模型…');
    try {
      const result = await api('/api/models/discover', {method: 'POST', body: stringify({provider: id})});
      if (state.generation === generation && !state.dirty) await refreshSettings();
      notice(`探测完成：${(result.models || []).length} 个可用模型。${state.dirty ? '当前草稿已保留。' : ''}`);
    } catch (error) { notice(error.message, true); }
    finally { $('refreshModels').disabled = false; }
  }
  function renderRoles() {
    const roles = state.draft.roles || {};
    const cards = Object.entries(roles).map(([id, role]) => node('section', {class: 'card'},
      node('div', {class: 'card-header'}, node('h3', {}, roleTitles[id] || id), node('small', {}, id)),
      node('div', {class: 'field-grid'}, selectField('模型服务', role.provider || 'text', providerOptions(), value => { edit(['roles', id, 'provider'], value); renderRoles(); }, roleDescriptions[id]), modelField('首选模型', role.model || '', role.provider || 'text', value => edit(['roles', id, 'model'], value))),
      checkField('自动发现并升级适用模型', role.autoUpgrade, value => edit(['roles', id, 'autoUpgrade'], value)),
      textField('候选模型家族（逗号分隔）', (role.families || []).join(', '), value => edit(['roles', id, 'families'], value.split(',').map(v => v.trim()).filter(Boolean)), {help: '启用自动升级后，按发现目录选择这些家族的较新版本，并在失败时尝试候选模型。'}),
      selectField('执行框架', role.backend || '', [['', '继承系统默认'], ['native', '原生工具循环'], ['codex', 'Codex Harness']], value => edit(['roles', id, 'backend'], value || null))));
    replace($('rolesPanel'), heading('ROLE ROUTING', '分工清晰，能力共享。', '按角色配置首选模型与备选。模型 ID 来自服务发现或手动输入，任务内容不会被关键词规则分派。', '⌘'), ...cards, !cards.length ? node('p', {class: 'empty'}, '服务尚未返回角色配置。') : null);
  }
  function agentConnectionFields(id, agent) {
    const key = textField('独立 API Key', state.secrets.agents[id] || '', value => secret('agents', id, value), {type: 'password', placeholder: agent.hasKey ? '已保存 · 留空保留' : '使用继承的凭据', help: '只为此智能体设置；留空保留现值。选择清除后恢复角色 / 服务继承。'});
    key.querySelector('input').disabled = Boolean(agent.clearKey);
    return [textField('独立 Base URL', agent.baseURL || '', value => edit(['agents', id, 'baseURL'], value || null), {placeholder: '留空继承服务地址'}), key,
      checkField('清除此智能体独立密钥', agent.clearKey, value => { edit(['agents', id, 'clearKey'], value); if (value) state.secrets.agents[id] = ''; renderAgents(); }),
      selectField('自动升级策略', agent.autoUpgrade === undefined || agent.autoUpgrade === null ? '' : String(agent.autoUpgrade), [['', '继承角色策略'], ['true', '自动升级'], ['false', '固定首选模型']], value => edit(['agents', id, 'autoUpgrade'], value === '' ? null : value === 'true')),
      textField('独立候选家族（逗号分隔）', (agent.families || []).join(', '), value => edit(['agents', id, 'families'], value.trim() ? value.split(',').map(v => v.trim()).filter(Boolean) : null), {help: '留空继承角色家族。独立地址或凭据使用明确模型 ID，避免沿用其他服务的探测结果。'})];
  }
  function renderAgents() {
    const entries = Object.entries(state.draft.agents || {}).filter(([, value]) => value !== null);
    const agentName = node('input', {'aria-label': '新智能体 ID', placeholder: '智能体 ID，例如 researcher', maxlength: 128, autocomplete: 'off'});
    const add = node('button', {onClick: () => {
      const id = agentName.value.trim();
      if (!id || id === '__proto__' || id === 'constructor' || id === 'prototype') { notice('请输入有效的智能体 ID。', true); return; }
      if (state.draft.agents?.[id]) { notice('该智能体已存在，可直接修改其配置。', true); return; }
      edit(['agents', id], {role: 'executor'}); renderAgents(); renderTaskChoices();
    }}, '＋ 添加');
    const cards = entries.map(([id, agent]) => node('section', {class: 'card'},
      node('div', {class: 'card-header'}, node('h3', {}, id), node('button', {class: 'text-button remove-agent', onClick: () => { delete state.secrets.agents[id]; edit(['agents', id], null); renderAgents(); renderTaskChoices(); }}, '移除覆盖')),
      node('div', {class: 'field-grid'}, selectField('继承角色', agent.role || 'executor', Object.keys(state.draft.roles || {}).map(role => [role, roleTitles[role] || role]), value => { edit(['agents', id, 'role'], value); renderAgents(); }), selectField('执行框架', agent.backend || '', [['', '继承角色 / 系统'], ['native', '原生工具循环'], ['codex', 'Codex Harness']], value => edit(['agents', id, 'backend'], value || null))),
      node('div', {class: 'field-grid'}, selectField('独立模型服务', agent.provider || '', providerOptions(true), value => { edit(['agents', id, 'provider'], value || null); renderAgents(); }), modelField('独立模型', agent.model || '', agent.provider || state.draft.roles?.[agent.role || 'executor']?.provider || 'text', value => edit(['agents', id, 'model'], value || null), true)),
      ...agentConnectionFields(id, agent)));
    replace($('agentsPanel'), heading('AGENT OVERRIDES', '统一起步，逐个细化。', '智能体默认继承角色配置。按需要指定独立服务和模型，或将任务交给外部 Codex CLI。', 'ƒ'), node('div', {class: 'inline-field'}, agentName, add), ...cards,
      !cards.length ? node('p', {class: 'empty'}, '尚无独立覆盖。所有任务使用角色和服务默认配置。') : null,
      node('div', {class: 'note-card purple'}, 'Codex Harness 使用独立 CLI 子进程与本系统配置，不需要 Codex App。后台浏览器使用独立会话；执行脚本不会移动系统光标。'));
  }
  function renderRuntime() {
    const runtime = state.draft.runtime || {};
    const fields = [
      ['maxConcurrent', '最大并行任务', 1, 8], ['maxSteps', '每次任务最大步骤', 1, 100], ['timeout', '单轮任务时间预算 / 秒', 5, 3600], ['intervalSeconds', '持续任务默认间隔 / 秒', 1, 3600], ['discoveryInterval', '模型自动探测间隔 / 秒', 60, 86400]
    ].map(([key, label, min, max]) => textField(label, runtime[key], value => edit(['runtime', key], value), {type: 'number', min, max, step: 1}));
    const status = state.status || {};
    const plugins = Array.isArray(status.plugins) ? status.plugins : Array.isArray(state.draft.plugins) ? state.draft.plugins : [];
    const backends = Array.isArray(status.backends) ? status.backends : Object.entries(status.backends || {}).map(([id, value]) => typeof value === 'object' ? {id, ...value} : {id, available: Boolean(value)});
    const tools = Array.isArray(status.tools) ? status.tools : Object.entries(status.tools || {}).map(([id, value]) => typeof value === 'object' ? {id, ...value} : {id, available: Boolean(value)});
    const items = [...backends, ...tools, ...plugins].map(item => typeof item === 'string' ? {id: item, available: true} : item);
    const pluginCards = items.map(item => {
      const visible = item.pluginId && window.DSHState ? window.DSHState.visibleStatus(item, item.packages?.[0]?.packageId, []) : null;
      const description = item.description || item.reason || (item.missingServices?.length ? `等待服务：${item.missingServices.join(', ')}` : item.error) || (item.pluginId ? 'DeepSeek Harness 生命周期 · 已注册扩展' : '由服务端运行状态提供');
      const label = item.enabled === false ? '未启用' : item.available === false ? '不可用' : visible === 'running' ? '运行中' : visible === 'client-pending' ? '等待界面扩展' : item.state === 'failed' ? '失败' : item.state === 'pending' ? '等待依赖' : item.pluginId ? '已停止' : '可用';
      const enabled = (state.draft.plugins || []).find(value => value.id === item.id)?.enabled ?? item.enabled;
      return node('div', {class: `plugin${item.available === false || item.enabled === false ? ' unavailable' : ''}`}, node('div', {}, node('span', {}, item.label || item.name || item.id || '扩展'), node('span', {class: 'status-tag'}, label)), node('small', {}, description), item.pluginId ? checkField('启用扩展', enabled, value => { const entries = (state.draft.plugins || []).filter(entry => entry.id !== item.id); entries.push({id: item.id, enabled: value}); edit(['plugins'], entries); }) : null);
    });
    replace($('runtimePanel'), heading('RUNTIME & CAPABILITIES', '后台运行，前台从容。', '配置并行度、执行预算与工具能力。状态来自后台实际探测，界面不会模拟工具可用性。', '⌁'),
      node('div', {class: 'field-grid'}, ...fields, selectField('默认执行框架', runtime.backend || 'native', [['native', '原生工具循环'], ['codex', 'Codex Harness']], value => edit(['runtime', 'backend'], value))),
      textField('脚本与任务工作目录', runtime.workspace || '', value => edit(['runtime', 'workspace'], value), {placeholder: '由服务端设置工作目录'}),
      textField('Codex CLI 路径', runtime.codexPath || 'codex', value => edit(['runtime', 'codexPath'], value), {help: '独立可执行程序；使用本系统的 Base URL、Key 与角色模型。'}),
      checkField('启用代码解释器', runtime.codeEnabled, value => edit(['runtime', 'codeEnabled'], value)),
      checkField('启用后台浏览器', runtime.browserEnabled, value => edit(['runtime', 'browserEnabled'], value)),
      checkField('允许持续迭代任务', runtime.continuousEnabled, value => edit(['runtime', 'continuousEnabled'], value)),
      node('div', {class: 'note-card'}, '代码解释器在配置的工作目录启动子进程。后台浏览器通过独立页面会话操作；需要宿主环境已安装对应依赖。启用能力不等于完整操作系统隔离。'),
      node('div', {class: 'section-label'}, '框架与工具可用性', node('button', {class: 'text-button', onClick: refreshStatus}, '↻ 刷新')),
      node('div', {class: 'plugin-grid'}, ...pluginCards), !pluginCards.length ? node('p', {class: 'helper'}, '正在等待服务端能力状态。') : null,
      node('details', {}, node('summary', {}, '查看服务端公开配置'), node('pre', {class: 'settings-code'}, stringify(state.settings, null, 2))));
  }
  function switchTab(tab) { state.tab = tab; document.querySelectorAll('[data-tab]').forEach(button => { button.classList.toggle('active', button.dataset.tab === tab); button.setAttribute('aria-current', button.dataset.tab === tab ? 'page' : 'false'); }); document.querySelectorAll('.config-panel').forEach(panel => panel.classList.toggle('active', panel.id === `${tab}Panel`)); }
  function renderAll() { renderProviders(); renderProvider(); renderRoles(); renderAgents(); renderRuntime(); renderTaskChoices(); renderState(); }
  function installSettings(settings) { state.settings = clone(settings); state.draft = clone(settings); state.changes = {}; state.secrets = {providers: {}, agents: {}, roles: {}}; state.dirty = false; state.remote = null; if (!state.draft.providers?.[state.provider]) state.provider = Object.keys(state.draft.providers || {})[0]; renderAll(); }
  async function refreshSettings() {
    const response = await api('/api/settings');
    if (state.settings && response.version < state.settings.version) return;
    if (state.settings && (state.dirty || state.saving)) { if (response.version !== state.settings.version) state.remote = response; renderState(); return; }
    if (!state.settings || response.version !== state.settings.version) installSettings(response);
  }
  async function saveSettings() {
    if (state.saving || state.remote || !state.dirty) return;
    const generation = state.generation;
    const payload = clone(state.changes); payload.version = state.settings.version;
    for (const [collection, entries] of Object.entries(state.secrets)) for (const [id, value] of Object.entries(entries)) if (value && state.draft[collection]?.[id]) { pathSet(payload, [collection, id, 'apiKey'], value); pathSet(payload, [collection, id, 'clearKey'], false); }
    state.saving = true; renderState();
    try {
      const response = await api('/api/settings', {method: 'PATCH', body: stringify(payload)});
      if (state.generation === generation) installSettings(response);
      else {
        state.settings = clone(response); state.draft.version = response.version;
        for (const [collection, entries] of Object.entries(state.secrets)) for (const [id, value] of Object.entries(entries)) if (value === payload[collection]?.[id]?.apiKey) delete state.secrets[collection][id];
        state.remote = state.remote?.version > response.version ? state.remote : null;
      }
      notice(state.dirty ? '配置已保存。保存期间的新修改仍留在草稿中。' : '配置已持久化；新任务立即使用，正在执行的一轮保留快照，下一轮使用最新配置。');
      await refreshStatus();
    } catch (error) {
      if (error.status === 409) { try { state.remote = await api('/api/settings'); } catch (_) { /* Keep local draft even if refresh fails. */ } }
      notice(error.status === 409 ? '配置版本冲突；当前修改和待提交密钥仍保留。' : error.message, true);
    } finally { state.saving = false; renderState(); }
  }
  async function refreshStatus() {
    try { state.status = await api('/api/harness/status'); renderRuntime(); }
    catch (error) { notice(`能力状态暂不可用：${error.message}`, true); }
  }
  function renderTaskChoices() {
    const role = $('taskRole').value, agent = $('taskAgent').value;
    $('taskRole').replaceChildren(...Object.keys(state.draft?.roles || {}).map(id => node('option', {value: id}, roleTitles[id] || id)));
    if ([...$('taskRole').options].some(option => option.value === role)) $('taskRole').value = role;
    else if (state.draft?.roles?.planner) $('taskRole').value = 'planner';
    $('taskAgent').replaceChildren(node('option', {value: ''}, '默认配置'), ...Object.keys(state.settings?.agents || {}).filter(id => state.settings.agents[id]).map(id => node('option', {value: id}, id)));
    if ([...$('taskAgent').options].some(option => option.value === agent)) $('taskAgent').value = agent;
  }
  function renderTasks() {
    const items = state.tasks.slice().sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0));
    const active = items.filter(task => !terminal.has(task.status)).length;
    $('taskCount').textContent = `${active} 进行中 / ${items.length} 总计`;
    replace($('taskList'), ...items.slice(0, 50).map(task => node('article', {class: `task-card${state.selectedTask === task.id ? ' selected' : ''}`},
      node('div', {class: 'task-card-head'}, node('span', {class: `task-state ${task.status || ''}`}, statuses[task.status] || task.status || '等待状态'), node('code', {}, String(task.id).slice(-8))),
      node('p', {}, task.prompt || (typeof task.task === 'object' ? task.task.prompt : task.task) || '后台任务'),
      node('div', {class: 'task-card-footer'}, node('span', {}, `${task.agentId || task.role || task.metadata?.role || '默认智能体'} · 已完成 ${task.iteration || 0}/${task.iterations || 1} 轮`), node('button', {onClick: () => showTask(task.id)}, '详情'), !terminal.has(task.status) ? node('button', {onClick: () => cancelTask(task.id)}, '停止') : null))),
      items.length ? null : node('p', {class: 'empty'}, '暂无任务。已提交的任务会在这里持续更新。'));
    if (state.selectedTask && !$('taskDetail').hidden) { const task = items.find(item => item.id === state.selectedTask); if (task) renderTaskDetail(task); }
  }
  function renderTaskDetail(task) { $('taskDetailTitle').textContent = `任务 ${String(task.id).slice(-8)} · ${statuses[task.status] || task.status}`; $('taskDetailContent').textContent = stringify({id: task.id, status: task.status, iteration: task.iteration, output: task.output, error: task.error, metadata: task.metadata}, null, 2); }
  async function showTask(id) { const request = ++state.detailRequest; state.selectedTask = id; $('taskDetail').hidden = false; $('taskDetailTitle').textContent = '载入任务详情…'; try { const task = await api(`/api/tasks/${encodeURIComponent(id)}`); if (request !== state.detailRequest) return; renderTaskDetail(task); renderTasks(); } catch (error) { if (request === state.detailRequest) $('taskDetailContent').textContent = error.message; } }
  async function cancelTask(id) { try { await api(`/api/tasks/${encodeURIComponent(id)}/cancel`, {method: 'POST', body: '{}'}); notice('停止请求已提交；运行中的工具将在取消检查点停止。'); window.ChaosActivity?.refresh(); } catch (error) { notice(error.message, true); } }
  async function submitTask(event) {
    event.preventDefault(); const prompt = $('harnessPrompt').value.trim(); if (!prompt) return;
    if (state.dirty) { notice('有未保存配置。请先保存，使任务使用你当前选择的配置。', true); return; }
    const iterations = Number($('taskIterations').value), intervalSeconds = Number($('taskInterval').value);
    if (!Number.isInteger(iterations) || iterations < 1 || iterations > 100 || !Number.isFinite(intervalSeconds) || intervalSeconds < 1 || intervalSeconds > 3600) { notice('请填写有效的迭代次数与间隔。', true); return; }
    $('submitTask').disabled = true;
    try { const task = await api('/api/tasks', {method: 'POST', body: stringify({prompt, role: $('taskRole').value || 'planner', agentId: $('taskAgent').value || undefined, iterations, intervalSeconds})}); notice(`后台任务已提交：${String(task.id).slice(-8)}。`); window.ChaosActivity?.refresh(); }
    catch (error) { notice(error.message, true); }
    finally { $('submitTask').disabled = false; }
  }
  function exportDraft() {
    const safe = clone(state.changes);
    for (const collection of ['providers', 'agents', 'roles']) for (const value of Object.values(safe[collection] || {})) if (value) delete value.apiKey;
    const blob = new Blob([stringify({version: state.settings.version, ...safe}, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob), link = node('a', {href: url, download: 'chaos-config-draft.json'}); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    notice('已导出无密钥配置草稿；密钥输入仍仅保留在当前页面内存中。');
  }
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => switchTab(button.dataset.tab)));
  $('saveSettings').addEventListener('click', saveSettings);
  $('refreshModels').addEventListener('click', () => discoverModels(state.provider));
  $('taskForm').addEventListener('submit', submitTask);
  $('closeTaskDetail').addEventListener('click', () => { state.detailRequest++; state.selectedTask = null; $('taskDetail').hidden = true; });
  $('exportConfigDraft').addEventListener('click', exportDraft);
  $('reloadSettings').addEventListener('click', async () => { const generation = state.generation; try { const settings = await api('/api/settings'); if (generation !== state.generation) { notice('载入期间有新修改，已保留草稿。请再次选择载入远端。', true); return; } installSettings(settings); notice('已载入服务端最新配置。'); } catch (error) { notice(error.message, true); } });
  window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('chaos:activity', event => { state.tasks = event.detail.tasks || []; renderTasks(); $('serverStatus').textContent = event.detail.connected ? '后台已连接' : '连接中断 · 正在重连'; $('serverStatus').classList.toggle('offline', !event.detail.connected); });
  let settingsPolling = false;
  async function pollSettings() { if (settingsPolling) return; settingsPolling = true; try { await refreshSettings(); } catch (error) { if (!state.settings) notice(`无法载入配置：${error.message}`, true); } finally { settingsPolling = false; } }
  pollSettings().then(refreshStatus);
  setInterval(pollSettings, 3000);
})();
