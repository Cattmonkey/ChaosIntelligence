'use strict';

// One code-only preference drives readers and every live editor without
// reparsing text or replacing an editor state. Storage is optional.
(() => {
  const key='chaos.codeAppearance', ids=new Set(['dark','paper','contrast']);
  const labels={dark:'暗色',paper:'纸白',contrast:'高对比'};
  let panel=null,opener=null;
  const listeners=new Set(), adapters=new WeakMap(), bindings=new WeakMap();
  const normalize=value=>ids.has(value)?value:'dark';
  let current='dark';try{current=normalize(localStorage.getItem(key));}catch{}
  function publish(id,persist){
    const changed=current!==id;current=id;
    const root=globalThis.document?.documentElement;if(root?.dataset)root.dataset.codePalette=id;
    if(persist)try{localStorage.setItem(key,id);}catch{}
    refreshControls();
    if(changed)for(const callback of [...listeners])callback(id);
    return current;
  }
  function set(id){return ids.has(id)?publish(id,true):current;}
  function subscribe(callback){if(typeof callback!=='function')throw new TypeError('Appearance subscriber must be a function');listeners.add(callback);callback(current);return()=>listeners.delete(callback);}
  function adapter(cm){
    if(!cm?.StateField?.define||!cm?.StateEffect?.define||!cm?.EditorView?.darkTheme?.from)return null;
    if(!adapters.has(cm)){
      const effect=cm.StateEffect.define();
      const field=cm.StateField.define({create:()=>current,update(value,tr){for(const item of tr.effects)if(item.is(effect))value=item.value;return value;}});
      adapters.set(cm,{field,effect,extension:[field,cm.EditorView.darkTheme.from(field,id=>id!=='paper')]});
    }
    return adapters.get(cm);
  }
  function extension(cm){return adapter(cm)?.extension||[];}
  function sync(view,cm){const item=adapter(cm);if(!item||!view)return;const value=view.state.field(item.field,false);if(value!==undefined&&value!==current)view.dispatch({effects:item.effect.of(current)});}
  function bind(view,cm){
    if(!adapter(cm))return()=>{};
    bindings.get(view)?.();let alive=true,pending=false,unsubscribe;
    const queue=()=>{if(!alive||pending)return;pending=true;queueMicrotask(()=>{pending=false;if(alive)sync(view,cm);});};
    const release=()=>{if(!alive)return;alive=false;unsubscribe?.();globalThis.window?.removeEventListener?.('pagehide',hide);bindings.delete(view);};
    const hide=event=>{if(!event.persisted)release();};
    unsubscribe=subscribe(queue);bindings.set(view,release);globalThis.window?.addEventListener?.('pagehide',hide);return release;
  }
  publish(current,false);
  globalThis.window?.addEventListener?.('storage',event=>{
    if(event.storageArea){try{if(event.storageArea!==localStorage)return;}catch{return;}}
    if(event.key===key||event.key===null)publish(normalize(event.newValue),false);
  });
  function refreshControls(){
    for(const button of globalThis.document?.querySelectorAll?.('[data-code-appearance]')||[]){
      button.textContent='配色 · '+labels[current];
      button.setAttribute('aria-expanded',String(Boolean(panel?.dialog.open&&button===opener)));
    }
    for(const radio of panel?.radios||[])radio.checked=radio.value===current;
  }
  function createButton({className=''}={}){
    const button=globalThis.document?.createElement?.('button');if(!button)return null;
    button.type='button';button.className=className;button.dataset.codeAppearance='';
    button.textContent='配色 · '+labels[current];button.title='代码语义配色：暗色、纸白或高对比';
    button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','codeAppearanceDialog');button.setAttribute('aria-expanded','false');
    button.onclick=()=>open(button);return button;
  }
  function ensurePanel(){
    if(panel)return panel;
    const doc=globalThis.document;if(!doc?.body)return null;
    const make=(tag,cls,text)=>{const element=doc.createElement(tag);if(cls)element.className=cls;if(text!==undefined)element.textContent=text;return element;};
    const dialog=make('dialog','code-appearance-dialog');
    dialog.id='codeAppearanceDialog';dialog.setAttribute('aria-labelledby','codeAppearanceTitle');
    const heading=make('div','code-appearance-heading'),close=make('button','','完成');close.type='button';close.setAttribute('aria-label','关闭代码配色');close.onclick=()=>dialog.close();
    const title=make('strong','','代码配色');title.id='codeAppearanceTitle';heading.append(title,close);
    const choices=make('fieldset','code-appearance-choices');choices.append(make('legend','','仅改变代码区域'));const radios=[];
    for(const id of ids){const label=make('label'),radio=make('input');radio.type='radio';radio.name='codeAppearance';radio.value=id;radio.onchange=()=>{if(radio.checked)set(id);};label.append(radio,doc.createTextNode(labels[id]));choices.append(label);radios.push(radio);}
    const legend=make('details','code-appearance-legend');legend.id='codeAppearanceLegend';legend.append(make('summary','','颜色代表什么？'));
    const roles=[['keyword','关键字','fn'],['function','函数','transform'],['parameter','参数','value'],['type','类型','Int'],['property','属性／字段名','"result"'],['variable','变量','total'],['string','字符串','"hello"'],['number','数字','42'],['operator','运算符','+'],['punctuation','标点','()'],['comment','注释','// 说明']];
    for(const [role,label,example]of roles){const row=make('div','code-appearance-role'),sample=make('code','chaos-token-'+role,example);row.dataset.role=role;row.append(sample,make('span','',label));legend.append(row);}
    const previewHost=make('section','code-appearance-preview');previewHost.id='codeAppearancePreview';
    const previewStatus=make('p','code-appearance-preview-title','实时预览 · 打开时分析示例，不执行代码');previewStatus.setAttribute('role','status');previewHost.append(previewStatus);
    let preview=null;
    function previewCode(){
      if(preview||!globalThis.ChaosCode)return;
      previewStatus.textContent='正在分析配色示例…';
      const source='// 智能体也是可组合函数\nfn prepare(input: String) -> String {\n  let result = {answer: input, score: 42};\n  result.answer\n}\nagent analyst {\n  provider: "local", prompt: "提炼要点", tools: []\n}\nanalyst(prepare("任务"))';
      preview=ChaosCode.render(source,{label:'语义配色预览',file:'palette-preview.chaos',languageVersion:2});preview.tabIndex=-1;previewHost.append(preview);
      preview.ready.then(()=>{previewStatus.textContent=preview.dataset.highlight==='ready'?'实时预览 · 来自语言分析，不执行示例':'分析暂不可用 · 保留原始示例，稍后重新打开可重试';});
    }
    dialog.append(heading,choices,previewHost,make('p','code-appearance-note','默认深色中，颜色跟随真实绑定：函数与智能体为暖金色，变量为浅蓝色，参数为暖橙色，属性为蓝紫色。切换主题不改变源码。'),legend);doc.body.append(dialog);
    panel={dialog,radios,preview:()=>{if(preview?.dataset.highlight==='unavailable'){preview.remove();preview=null;}previewCode();}};
    dialog.addEventListener('close',()=>{
      if(dialog.open)return;
      const target=opener;opener=null;refreshControls();
      const active=doc.activeElement;
      if(target?.isConnected&&!target.disabled&&target.getClientRects().length&&(!active||active===doc.body||dialog.contains(active)))target.focus({preventScroll:true});
    });
    return panel;
  }
  function open(trigger){
    const doc=globalThis.document;if(panel?.dialog.open)return false;
    const modals=[...(doc?.querySelectorAll?.('dialog[open]')||[])];
    if(modals.length&&!modals.some(dialog=>dialog.contains(trigger)))return false;
    const item=ensurePanel();if(!item)return false;
    opener=trigger?.isConnected?trigger:doc.activeElement;
    item.preview();refreshControls();
    if(opener?.isConnected)opener.focus({preventScroll:true});
    item.dialog.showModal();refreshControls();item.radios.find(radio=>radio.checked)?.focus();return true;
  }
  window.ChaosCodeAppearance=Object.freeze({get:()=>current,set,subscribe,extension,bind,sync,createButton,open});
  function controls(){
    const doc=globalThis.document,format=doc?.getElementById?.('formatSource'),host=format?.parentElement||doc?.querySelector?.('.ide-editor-status');
    if(!host||doc.getElementById?.('codeAppearanceButton'))return;
    const button=createButton();button.id='codeAppearanceButton';if(format)format.after(button);else host.append(button);
  }
  controls();if(globalThis.document?.readyState==='loading')globalThis.document.addEventListener?.('DOMContentLoaded',controls,{once:true});
})();


// Shared read-only renderer. Chaos token identities come only from the server's
// actual parser/analysis; unavailable analysis keeps the original source visible.
(() => {
  const kinds = new Set(['keyword','string','number','comment','operator','punctuation','variable','function','parameter','type','property']);
  const records = new WeakMap();
  function node(tag, cls, text) {
    const result = document.createElement(tag);
    if (cls) result.className = cls;
    if (text !== undefined) result.textContent = text;
    return result;
  }
  function languageOf(value) {
    const language = String(value || 'text').trim().split(/\s+/)[0].toLowerCase();
    return language === 'py' ? 'python' : language;
  }
  function boundary(text, offset) {
    return Number.isInteger(offset) && offset >= 0 && offset <= text.length &&
      !(offset > 0 && offset < text.length && /[\uD800-\uDBFF]/.test(text[offset - 1]) && /[\uDC00-\uDFFF]/.test(text[offset]));
  }
  function paint(code, text, tokens = [], symbols = [], referenceHints = []) {
    const details = new Map([...symbols, ...referenceHints].filter(item => item && typeof item.detail === 'string' && boundary(text,item.from) && boundary(text,item.to) && item.to>item.from).map(item => [item.from + ':' + item.to, item.detail]));
    let previous = 0;
    const spans = tokens.filter(item => {
      if (!item || !kinds.has(item.type) || !boundary(text, item.from) || !boundary(text, item.to) || item.from < previous || item.to <= item.from) return false;
      previous = item.to;
      return true;
    });
    const fragment = document.createDocumentFragment();
    let offset = 0, tokenIndex = 0;
    const lines = text.split('\n');
    lines.forEach((line, index) => {
      const row = node('span', 'chaos-code-line');
      row.dataset.line = String(index + 1);
      const end = offset + line.length;
      let position = offset, current = tokenIndex;
      while (current < spans.length && spans[current].from < end) {
        const token = spans[current];
        if (token.to <= offset) { current++; continue; }
        const left = Math.max(offset, token.from), right = Math.min(end, token.to);
        row.append(document.createTextNode(text.slice(position, left)));
        const part = node('span', 'chaos-token-' + token.type, text.slice(left, right));
        const detail = details.get(token.from + ':' + token.to);
        if (detail) part.title = detail;
        row.append(part);
        position = right;
        if (token.to > end) break;
        current++;
      }
      tokenIndex = current;
      row.append(document.createTextNode(text.slice(position, end) + (index < lines.length - 1 ? '\n' : '')));
      fragment.append(row);
      offset = end + 1;
    });
    code.replaceChildren(fragment);
    return spans.length > 0;
  }
  function markLine(pre, activeLine) {
    if (!Number.isInteger(activeLine) || activeLine < 1) return;
    const row = pre.querySelector('[data-line="' + activeLine + '"]');
    if (row) { row.classList.add('current'); row.setAttribute('aria-current', 'step'); }
  }
  async function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    const previous = document.activeElement;
    const field = node('textarea', 'chaos-code-clipboard');
    field.value = text;
    field.setAttribute('aria-label', '待复制代码');
    document.body.append(field);
    try {
      field.select();
      if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
    } finally { field.remove(); previous?.focus({preventScroll:true}); }
  }
  function active(pre, state) {
    return !!state && records.get(pre) === state && !state.controller.signal.aborted &&
      pre.querySelector('code') === state.code &&
      pre.querySelector('.chaos-code-toolbar') === state.toolbar &&
      pre.querySelector('.chaos-code-hint') === state.hint;
  }
  function invalidate(pre) {
    records.get(pre)?.controller?.abort();
    records.delete(pre);
  }
  function clear(pre) {
    invalidate(pre);
    pre.replaceChildren(); pre.onclick = null; pre.onkeydown = null;
    delete pre.dataset.codeView; delete pre.dataset.highlight; delete pre.dataset.formatMode;
    pre.ready = Promise.resolve(pre);
    return pre;
  }
  function hints(pre, state) {
    if (!active(pre, state)) return;
    const items = [...state.code.querySelectorAll('[title]')];
    state.hintButton.disabled = !items.length;
    if (!items.length || state.hint.hidden) return;
    state.hintIndex = Math.max(0, Math.min(items.length - 1, state.hintIndex));
    const item = items[state.hintIndex];
    state.hintText.textContent = `${state.hintIndex + 1}/${items.length} · ${item.textContent} · ${item.title}`;
    pre.querySelectorAll('.chaos-code-selected-hint').forEach(span => span.classList.remove('chaos-code-selected-hint'));
    item.classList.add('chaos-code-selected-hint');
  }
  function showHint(pre, state, index) {
    if (!active(pre, state)) return;
    state.hintIndex = index; state.hint.hidden = false;
    state.hintButton.setAttribute('aria-expanded', 'true'); hints(pre, state);
  }
  function closeHint(pre, state) {
    if (!active(pre, state)) return;
    state.hint.hidden = true; state.hintButton.setAttribute('aria-expanded', 'false');
    pre.querySelectorAll('.chaos-code-selected-hint').forEach(span => span.classList.remove('chaos-code-selected-hint'));
  }
  function display(pre, state, formatted) {
    if (!active(pre, state)) return;
    const value = formatted ? state.formatted : state.original;
    state.code.replaceChildren(...[...value.childNodes].map(child => child.cloneNode(true)));
    state.readable = formatted;
    pre.dataset.codeView = formatted ? 'formatted' : 'original';
    pre.dataset.highlight = value.dataset.highlight || 'plain';
    if (state.formatButton) {
      state.formatButton.textContent = formatted ? '原始排版' : '格式化阅读';
      state.formatButton.setAttribute('aria-pressed', String(formatted));
    }
    closeHint(pre, state); hints(pre, state);
    markLine(pre, state.options.activeLine);
  }
  async function analyzed(pre, state, code, source, formatted = false) {
    const options = state.options;
    const file = options.file || 'main.chaos';
    const body = {source, file, entry:options.entry || file};
    if (options.files) body.files = {...options.files, [file]:source};
    if (options.languageVersions) body.languageVersions = {...options.languageVersions};
    else if (options.languageVersion) body.languageVersions = {[file]:options.languageVersion};
    if (formatted) body.languageVersions = {...body.languageVersions, [file]:2};
    code.dataset.highlight = 'pending';
    try {
      const response = await fetch('/api/language/analyze', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal:state.controller.signal});
      if (!response.ok) throw new Error('Analysis unavailable');
      const data = await response.json();
      if (!active(pre, state) || state.controller.signal.aborted) return;
      const highlighted = paint(code, source, Array.isArray(data.tokens) ? data.tokens : [], Array.isArray(data.symbols) ? data.symbols : [], Array.isArray(data.referenceHints) ? data.referenceHints : []);
      code.dataset.highlight = highlighted ? 'ready' : 'plain';
    } catch {
      if (!active(pre, state)) return;
      code.dataset.highlight = 'unavailable';
    }
    if (state.readable === formatted) display(pre, state, formatted);
  }
  async function readable(pre, state) {
    if (!active(pre, state)) return;
    if (state.readable) { display(pre, state, false); state.status.textContent = '原始源码排版'; return; }
    if (state.formatted) { display(pre, state, true); state.status.textContent = '格式化阅读 · 复制仍为原始代码'; return; }
    state.formatButton.disabled = true;
    state.status.textContent = '正在生成可读排版…';
    try {
      const options = state.options, file = options.file || 'main.chaos';
      const response = await fetch('/api/language/format-source', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source:state.source,file,languageVersion:options.languageVersions?.[file] ?? options.languageVersion ?? 2,lineWidth:88,indentSize:2}), signal:state.controller.signal});
      if (!response.ok) throw new Error('源码暂不能格式化');
      const data = await response.json();
      if (!active(pre, state) || state.controller.signal.aborted) return;
      if (typeof data.source !== 'string' || data.source.length > 300000) throw new Error('格式化结果不可用');
      const code = node('code'); paint(code, data.source); state.formatted = code;
      display(pre, state, true); state.status.textContent = '格式化阅读 · 复制仍为原始代码';
      state.formatButton.disabled = false;
      await analyzed(pre, state, code, data.source, true);
    } catch(error) {
      if (active(pre, state) && error.name !== 'AbortError') state.status.textContent = '保留原始源码 · ' + error.message;
    } finally {
      if (active(pre, state)) state.formatButton.disabled = false;
    }
  }
  function controls(pre, state, options) {
    pre.querySelector('.chaos-code-toolbar')?.remove();
    pre.querySelector('.chaos-code-hint')?.remove();
    const toolbar = state.toolbar = node('span', 'chaos-code-toolbar');
    const label = node('span', 'code-label', options.label || state.language);
    const status = state.status = node('span', 'chaos-code-status');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const wrap = node('button', 'chaos-code-wrap', '自动折行');
    wrap.type = 'button'; wrap.setAttribute('aria-label', '切换代码自动折行');
    function wrapped(value) { if (!active(pre, state)) return; pre.classList.toggle('chaos-code-wrapped', value); wrap.setAttribute('aria-pressed', String(value)); }
    wrap.addEventListener('click', () => wrapped(wrap.getAttribute('aria-pressed') !== 'true'));
    const button = node('button', 'copy-code', '复制');
    button.type = 'button'; button.setAttribute('aria-label', '复制 ' + (options.label || state.language) + ' 代码');
    button.addEventListener('click', async () => {
      if (!active(pre, state)) return;
      try { await copy(state.source); if(active(pre,state))status.textContent = '已复制原始代码'; }
      catch { if(active(pre,state))status.textContent = '无法访问剪贴板，请选择原始排版代码后手动复制'; }
    });
    const help = state.hintButton = node('button', 'chaos-code-help', '代码提示');
    help.type = 'button'; help.setAttribute('aria-expanded', 'false');
    const hint = state.hint = node('span', 'chaos-code-hint'); hint.hidden = true;
    hint.setAttribute('aria-label', '代码类型与调用提示');
    const previous = node('button', '', '上一项'), next = node('button', '', '下一项'), close = node('button', '', '关闭提示');
    for(const control of [previous,next,close])control.type='button';
    const detail = state.hintText = node('span', 'chaos-code-hint-text'); detail.setAttribute('role', 'status'); detail.setAttribute('aria-live', 'polite');
    previous.onclick = () => showHint(pre,state,state.hintIndex - 1); next.onclick = () => showHint(pre,state,state.hintIndex + 1);
    close.onclick = () => {if(!active(pre,state))return;closeHint(pre,state);help.focus();};
    help.onclick = () => hint.hidden ? showHint(pre,state,0) : closeHint(pre,state);
    hint.append(previous,next,detail,close);
    toolbar.append(label,status,wrap);
    if(state.canFormat){const format=state.formatButton=node('button','chaos-code-format','格式化阅读');format.type='button';format.setAttribute('aria-pressed','false');format.onclick=()=>readable(pre,state);toolbar.append(format);}
    toolbar.append(help,button);
    const appearance=window.ChaosCodeAppearance?.createButton({className:'chaos-code-appearance'});if(appearance)toolbar.append(appearance);
    pre.prepend(toolbar); pre.append(hint);
    wrapped(Boolean(options.wrap));
    // One focusable code region, rather than one tab stop per token. Pointer
    // users can select tokens directly; keyboard users navigate their hints.
    pre.onclick = event => {
      if (!active(pre, state)) return;
      const token = event.target.closest?.('code [title]');
      if(token && pre.contains(token))showHint(pre,state,[...pre.querySelector('code').querySelectorAll('[title]')].indexOf(token));
    };
    pre.onkeydown = event => {
      if (!active(pre, state)) return;
      if(event.key==='Escape'&&!hint.hidden){const returnFocus=hint.contains(document.activeElement);closeHint(pre,state);if(returnFocus)help.focus();event.preventDefault();return;}
      if(event.target===pre && ['ArrowLeft','ArrowRight'].includes(event.key) && !help.disabled){showHint(pre,state,hint.hidden?0:state.hintIndex+(event.key==='ArrowRight'?1:-1));event.preventDefault();}
    };
    pre.setAttribute('aria-description','聚焦代码后使用左右方向键浏览类型和调用提示，Escape 关闭提示。');
    hints(pre,state);
  }
  function initialize(pre, options, dynamic) {
    const code = pre.querySelector('code'); invalidate(pre); if (!code) return null;
    const template = pre.querySelector('template[data-code-formatted]');
    const state = {code, source:code.textContent, language:languageOf(pre.dataset.language || options.language), controller:new AbortController(), options:JSON.parse(JSON.stringify(options)), original:code.cloneNode(true), formatted:null, readable:false, hintIndex:0};
    state.original.dataset.highlight = pre.dataset.highlight || 'plain';
    if(template){state.formatted=node('code');state.formatted.append(template.content.cloneNode(true));state.formatted.dataset.highlight=state.formatted.querySelector('[class*="chaos-token-"]')?'ready':'plain';}
    state.canFormat = state.language==='chaos' && state.source.length <=100000 && !Number.isInteger(options.activeLine) && options.analyze!==false && (dynamic || !!state.formatted);
    records.set(pre,state); pre.classList.add('chaos-code'); pre.tabIndex=0; pre.dataset.codeView='original';
    pre.setAttribute('aria-label',(options.label || state.language)+' 代码');
    controls(pre,state,options); pre.ready=Promise.resolve(pre); return state;
  }
  function enhance(pre, options = {}) {
    if(!active(pre,records.get(pre)))initialize(pre,options,false);
    return pre;
  }
  function update(pre, source, options = {}) {
    const text=String(source ?? ''), language=languageOf(options.language || 'chaos');
    records.get(pre)?.controller?.abort();
    const code=node('code');paint(code,text);pre.replaceChildren(code);
    pre.dataset.language=language;pre.dataset.highlight='plain';delete pre.dataset.formatMode;
    const state=initialize(pre,options,true);markLine(pre,options.activeLine);
    if(language!=='chaos'||options.analyze===false||text.length>100000)return pre;
    pre.dataset.highlight='pending';
    pre.ready=analyzed(pre,state,state.original,text).then(()=>pre); return pre;
  }
  function render(source, options = {}) { return update(node('pre', 'chaos-code'), source, options); }
  window.ChaosCode = Object.freeze({render, update, enhance, clear, source:pre => active(pre,records.get(pre)) ? records.get(pre).source : pre.querySelector('code')?.textContent ?? ''});
})();
