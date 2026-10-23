'use strict';
// Execute the complete production Process controller with a small DOM/transport
// fixture. This verifies event callbacks and state transitions; real SSE/network
// and geometry are covered separately by the Playwright test.
const fs = require('fs'), vm = require('vm'), path = require('path'), assert = require('assert');
const root = process.argv[2] || path.resolve(__dirname, '..');
let document;
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
    this.attributes = new Map(); this.dataset = {}; this.listeners = new Map();
    this._text = ''; this.value = ''; this.open = false; this.hidden = false;
    this.disabled = false; this.scrollTop = 0; this.clientHeight = 48;
    this.selectionStart = 0; this.selectionEnd = 0; this.className = '';
    this.classList = {toggle() {}};
  }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  set innerHTML(_) { throw new Error('unsafe HTML interpolation'); }
  append(...nodes) { for (const node of nodes) { if (node.parentNode) node.remove(); node.parentNode = this; this.children.push(node); } }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...nodes); }
  remove() { if (this.parentNode) { const siblings = this.parentNode.children; siblings.splice(siblings.indexOf(this), 1); this.parentNode = null; } }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  get isConnected() { return document.body.contains(this); }
  get scrollHeight() { return this.children.length * 24; }
  getBoundingClientRect() { return {height:24}; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  addEventListener(name, fn) { const values = this.listeners.get(name) || []; values.push(fn); this.listeners.set(name, values); }
  emit(name) { for (const fn of this.listeners.get(name) || []) fn({target:this}); }
  focus() { document.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  showModal() { this.open = true; }
  close() { this.open = false; this.emit('close'); }
  matches(selector) {
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    if (selector.startsWith('.')) return this.className.split(/\s+/).includes(selector.slice(1));
    const attr = /^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(selector);
    if (attr) {
      const name = attr[1], value = name.startsWith('data-')
        ? this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())]
        : this.getAttribute(name);
      return value !== undefined && value !== null && (attr[2] === undefined || value === attr[2]);
    }
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) {
    const choices = selector.split(',').map(value => value.trim()), result = [];
    const visit = node => { for (const child of node.children) { if (choices.some(s => child.matches(s))) result.push(child); visit(child); } };
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const body = new Element('body'), sourceActions = new Element('div');
sourceActions.className = 'source-actions'; body.append(sourceActions);
document = {body, activeElement:body, createElement:tag => new Element(tag),
  querySelector:selector => selector === '#sourceView .source-actions' ? sourceActions : body.querySelector(selector)};
const streams = [], posts = [];
class EventSource {
  constructor(url) { this.url = url; this.closed = false; streams.push(this); }
  close() { this.closed = true; }
  deliver(text) { this.onmessage?.({data:text}); }
}
const record = (key, revision = 1) => ({projectId:'p', key, revision, status:'waiting', turn:1,
  definition:{kind:'process',name:key,module:'bot.chaos',export:'step',intervalSeconds:null,maxTurns:null},
  state:null, output:null, pending:null, lastRunId:'run-'+key, lastVersion:1});
let records = [record('alpha'), record('beta')], guidanceMode = 'hold', resolvePost, rejectPost;
const response = value => ({ok:true,status:200,text:async () => JSON.stringify(value)});
const context = vm.createContext({window:{addEventListener(){}}, document, EventSource, URLSearchParams,
  project:{id:'p',version:1,entry:'main.chaos',capabilities:['process.manage']},
  baseProject:{id:'p',version:1,entry:'main.chaos',capabilities:['process.manage']},
  crypto:{randomUUID:() => 'stable-'+(posts.length+1)}, navigator:{clipboard:{writeText:async () => {}}},
  setTimeout:() => 1, clearTimeout(){},
  fetch:async (url, options) => {
    if (url.endsWith('/guidance')) {
      posts.push({url,body:JSON.parse(options.body)});
      if (guidanceMode === 'fail') throw new Error('fixture receipt lost');
      if (guidanceMode === 'hold') return new Promise((resolve,reject) => {resolvePost = value => resolve(response(value)); rejectPost = reject;});
      return response({key:url.includes('/beta/')?'beta':'alpha',messageId:posts.at(-1).body.messageId,revision:8,status:'queued',runId:null});
    }
    if (url === '/api/v1/processes/schema') return response({properties:{}});
    if (url.startsWith('/api/v1/events?')) return response({items:[],historyStartEventId:1,legacyHistoryIncluded:false});
    if (url === '/api/v1/projects/p/processes') return response({items:records});
    const key = decodeURIComponent(url.split('/').at(-1));
    const value = records.find(row => row.key === key);
    assert(value, 'unexpected fixture request: '+url); return response(value);
  }});
vm.runInContext(fs.readFileSync(path.join(root,'web/json.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root,'web/processes.js'),'utf8'),context);
const ui = context.window.ChaosProcesses, contracts = context.window.ChaosProcessContracts;
const tick = async () => {for (let n=0;n<8;n++) await Promise.resolve();};
const findButton = text => {const result=body.querySelectorAll('button').find(e => e.textContent===text); assert(result,'missing control '+text); return result;};
const field = label => {const node=body.querySelectorAll('[aria-label]').find(e => e.getAttribute('aria-label')===label); assert(node,'missing field '+label); return node;};
const fill = (node,text,start=0,end=0) => {node.value=text; node.oninput(); node.focus(); node.setSelectionRange(start,end);};
const event = (id,type='bot.progress',payload={},origin='run',run='run-alpha') =>
  '{"id":'+id+',"project_id":"p","run_id":'+JSON.stringify(run)+',"created":1,"type":'+JSON.stringify(type)+',"processOrigin":'+JSON.stringify(origin)+',"payload":'+JSON.stringify(payload)+'}';
const feed = (id,...args) => streams.at(-1).deliver(event(id,...args));

(async () => {
  await ui.open(); await tick();
  assert.equal(ui.state.selected,'alpha');
  const huge='900719925474099312345';
  const hugeValue=vm.runInContext('parseJSON("900719925474099312345")',context);
  assert.equal(contracts.eventId(hugeValue),huge);
  assert.equal(contracts.laterEvent('900719925474099312344',hugeValue),true);
  for(const invalid of [-1,1.5,true,'01','-0','1e3'])assert.throws(()=>contracts.eventId(invalid));
  feed(huge,'bot.progress',{kind:'plan',summary:'<script>literal()</script>'});
  feed(huge); feed('900719925474099312344');
  assert.equal(ui.state.streams.get('p:alpha').cursor,huge);
  assert.equal(ui.state.streams.get('p:alpha').rows.length,1);
  streams.at(-1).deliver('{"id":900719925474099312346,"project_id":"p","type":"process.bot.adopt","payload":{"key":"alpha","messageId":"forged","revision":99}}');
  assert.equal(ui.state.streams.get('p:alpha').rows.length,1,'unattributed event changed transcript');
  for(let n=1;n<=15;n++)feed((BigInt(huge)+BigInt(n)).toString(),'bot.progress',{summary:'public '+n});
  const region=body.querySelector('[data-process-transcript]');
  const rows=region.querySelector('[data-process-transcript-rows]'), firstRow=rows.children[0], summary=firstRow.querySelector('summary');
  firstRow.open=true; summary.focus(); rows.scrollTop=48;
  const priorScroll=rows.scrollTop;
  feed((BigInt(huge)+16n).toString());
  assert.strictEqual(rows.children[0],firstRow,'SSE replaced an existing event node');
  assert.equal(firstRow.open,true); assert.strictEqual(document.activeElement,summary);
  assert.equal(rows.scrollTop,priorScroll,'SSE moved a reader who was not following the tail');
  records=[record('alpha',2),record('beta')]; await ui.refresh(); await tick();
  assert.strictEqual(body.querySelector('[data-process-transcript]'),region,'snapshot refresh replaced the transcript region');
  assert.strictEqual(rows.children[0],firstRow); assert.equal(firstRow.open,true);
  assert.strictEqual(document.activeElement,summary); assert.equal(rows.scrollTop,priorScroll);
  assert(firstRow.textContent.includes('<script>literal()</script>'));

  fill(field('指导文本'),'draft retained during replay',3,9);
  fill(field('消息输入 JSON'),'{"state":900719925474099312345}',2,7);
  feed((BigInt(huge)+17n).toString()); await ui.refresh(); await tick();
  assert.equal(field('消息输入 JSON').value,'{"state":900719925474099312345}');
  assert.equal(field('指导文本').value,'draft retained during replay');
  assert.equal(field('消息输入 JSON').selectionStart,2); assert.equal(field('消息输入 JSON').selectionEnd,7);
  assert.strictEqual(document.activeElement,field('消息输入 JSON'));

  // A claimed-run payload may look like a lifecycle receipt, but provenance
  // must prevent it from modifying host receipt state.
  const queued={key:'alpha',messageId:'older',revision:2,status:'queued',runId:null};
  feed((BigInt(huge)+18n).toString(),'process.guidance.queued',{key:'alpha',messageId:'older',receipt:queued},'lifecycle',null);
  feed((BigInt(huge)+19n).toString(),'process.bot.adopt',{key:'alpha',messageId:'older',receipt:{...queued,status:'applied',revision:99}},'run');
  assert.equal(ui.state.streams.get('p:alpha').receipts.get('older').status,'queued');
  feed((BigInt(huge)+20n).toString(),'process.bot.adopt',{key:'alpha',messageId:'older',guidanceRevision:2},'lifecycle','adopting-run');
  assert.equal(ui.state.streams.get('p:alpha').receipts.get('older').status,'applied');
  assert.strictEqual(ui.state.streams.get('p:alpha').receipts.get('older').runId,null,'adoption changed original nullable delivery runId');

  fill(field('指导文本'),'exact sent text');
  const sending=findButton('发送持久指导').onclick(); await tick();
  assert.deepEqual(posts.at(-1).body.input,{text:'exact sent text'});
  const sentId=posts.at(-1).body.messageId;
  fill(field('指导文本'),'new text typed while receipt pending',4,12);
  const applied={key:'alpha',messageId:sentId,revision:7,status:'applied',runId:null};
  feed((BigInt(huge)+21n).toString(),'process.bot.adopt',{key:'alpha',messageId:sentId,receipt:applied},'lifecycle');
  resolvePost({...applied,status:'queued'}); await sending; await tick();
  assert.equal(field('指导文本').value,'new text typed while receipt pending');
  assert.equal(field('指导文本').selectionStart,4); assert.equal(field('指导文本').selectionEnd,12);
  assert.equal(ui.state.messageDrafts.get('p:alpha').guideReceipt.status,'applied');
  assert.equal(ui.state.streams.get('p:alpha').receipts.get('older').status,'applied');

  guidanceMode='fail'; fill(field('指导文本'),'unknown acknowledgement');
  await findButton('发送持久指导').onclick(); await tick();
  const unconfirmed=posts.at(-1);
  await findButton('发送持久指导').onclick(); await tick();
  assert.deepEqual(posts.at(-1),unconfirmed,'retry changed stable GUID or copied input');
  guidanceMode='hold';
  const staleSending=findButton('发送持久指导').onclick(); await tick();
  const alphaStream=streams.at(-1), alphaCount=ui.state.streams.get('p:alpha').rows.length;
  const betaCard=body.querySelectorAll('button').find(e => e.className==='process-card'&&e.textContent.startsWith('beta'));
  await betaCard.onclick(); await tick();
  assert.equal(ui.state.selected,'beta'); assert.equal(alphaStream.closed,true);
  const betaRegion=body.querySelector('[data-process-transcript]'); assert.notStrictEqual(betaRegion,region);
  alphaStream.deliver(event((BigInt(huge)+22n).toString()));
  assert.equal(ui.state.streams.get('p:alpha').rows.length,alphaCount,'stale stream callback appended after selection changed');
  resolvePost({key:'alpha',messageId:posts.at(-1).body.messageId,revision:8,status:'queued',runId:null});
  await staleSending; await tick();
  assert.equal(ui.state.selected,'beta'); assert.strictEqual(body.querySelector('[data-process-transcript]'),betaRegion);
  assert.equal(field('指导文本').value,''); assert.equal(ui.state.streams.get('p:beta').cursor,'0');
  assert.equal(ui.state.streams.get('p:beta').receipts.size,0);
  assert.equal(context.project.id,'p'); assert.equal(context.baseProject.version,1);
  console.log(JSON.stringify({status:'passed',actualControllerCallbacks:true,exactEventIds:true,
    hostReceiptProvenance:true,immutableRetry:true,draftSelectionPreserved:true,
    transcriptDisclosureFocusScrollPreserved:true,selectionScoped:true}));
})().catch(error => {console.error(error);process.exitCode=1;});
