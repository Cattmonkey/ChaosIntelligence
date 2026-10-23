'use strict';
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = x => parseJSON(stringifyJSON(x));
const kinds = {input:{label:'任务输入',en:'INPUT',icon:'↗'},agent:{label:'智能体',en:'AGENT',icon:'✧'},tool:{label:'工具',en:'TOOL',icon:'⌁'},function:{label:'纯函数',en:'FUNCTION',icon:'ƒ'},output:{label:'结果输出',en:'OUTPUT',icon:'↘'}};
const stateNames = {idle:'就绪',running:'执行中',completed:'已完成',failed:'失败',skipped:'已跳过',cancelled:'已取消',queued:'排队中',interrupted:'已中断'};
let project, tools={}, selected='platform', selectedEdge=null, view='source', dirty=false, busy=false, currentRun=null, nodeStates={}, events=[], lastEvent=0, history=[];
let editSerial=0, activeRunId=null, editFrom=null;
let zoom=.75, pan={x:15,y:145}, showHierarchy=true, drag=null, wire=null, toastTimer, eventSource;
async function api(path, options={}) { const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers},body:options.body===undefined?undefined:stringifyJSON(options.body)}); const body=parseJSON(await response.text()); if(!response.ok) throw new Error(body.error||`HTTP ${response.status}`); return body; }
function renderRunOutput(output,error,raw,run){
 $('#resultPanel').textContent=error||raw||stringifyJSON(output,null,2);
 window.ChaosArtifacts?.showOutput(output,error,run);
}
function toast(message,error=false){$('#toast').textContent=message;$('#toast').className='toast'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.add('hidden'),5000);$('#statusMessage').textContent=message;}
function guarded(fn){return async (...args)=>{try{await fn(...args);}catch(e){toast(e.message,true);}};}
function markDirty(origin='graph'){editFrom=origin;editSerial++;dirty=true;$('#dirtyMark').textContent='未保存';}
function syncHeader(){$('#projectName').textContent=project.name;$('#version').textContent='v'+project.version;$('#dirtyMark').textContent=dirty?'未保存':'';$('#nodeCount').textContent=project.graph.nodes.length+' 个节点';}
function setProject(p){project=clone(p);dirty=false;editFrom=null;$('#sourceEditor').value=project.source;lineNumbers();syncHeader();renderGraph();renderInspector();if(typeof onLanguageProject==='function')onLanguageProject();}
// Active run guidance shares the existing composer and event stream.
let runGuidanceRequest=null,runGuidanceDisplayRun=null,runGuidanceDisplayKey=null,runGuidanceScopeSequence=0;
const runGuidanceReceipts=new Map(),runGuidanceDrafts=new Map(),runGuidanceEnvelopes=new Map(),runGuidanceRoleProcesses=new Map();
let runGuidanceScopeState=null;
function runGuidancePicker(){const el=$('#runGuidanceTarget');return el&&typeof el.replaceChildren==='function'?el:null;}
function runGuidanceProject(){return typeof project!=='undefined'?String(project?.id||''):'';}
function runGuidanceTarget(value){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==2)return null;if(value.kind==='botScope'&&typeof value.scopeId==='string'&&/^bot-scope-[0-9a-f]{32}$/.test(value.scopeId))return {kind:'botScope',scopeId:value.scopeId};if(value.kind==='processRole'&&typeof value.roleId==='string'&&/^bot-role-[0-9a-f]{32}$/.test(value.roleId))return {kind:'processRole',roleId:value.roleId};return null;}
function selectedRunGuidanceTarget(){const value=runGuidancePicker()?.value;return value?.startsWith('bot-role-')?{kind:'processRole',roleId:value}:value?{kind:'botScope',scopeId:value}:null;}
function runGuidanceRoleProcess(target,pid=runGuidanceProject()){return target?.kind==='processRole'?runGuidanceRoleProcesses.get(JSON.stringify([pid,target.roleId])):null;}
function runGuidanceKey(runId,target=null,projectId=runGuidanceProject(),processKey=null){return target?.kind==='processRole'?JSON.stringify([projectId,'process',processKey||runGuidanceRoleProcess(target,projectId)||null,target.roleId]):JSON.stringify([projectId,runId,target?.scopeId||null]);}
function selectedRunGuidanceScope(){const target=selectedRunGuidanceTarget();return target?[...(runGuidanceScopeState?.scopes.values()||[])].find(row=>target.kind==='processRole'?row.target?.kind==='processRole'&&row.target.roleId===target.roleId:row.scopeId===target.scopeId):null;}
function rememberRunGuidance(map,key,value){map.set(key,value);while(map.size>256){const oldest=map.keys().next().value;if(oldest===key)break;map.delete(oldest);}}
function runGuidanceSummary(event){
 const p=event.payload||{};
 if(event.type==='bot.progress'){
  const title={progress:'进度',plan:'计划',result:'结果'}[p.kind]||'工作';
  const data=p.data,summary=data&&typeof data==='object'&&typeof data.summary==='string'?data.summary:typeof data==='string'?data:stringifyJSON(data);
  return title+' · '+String(summary??'').slice(0,2000);
 }
 const nativeBotTitles={'bot.scope.opened':'Bot 开始','bot.context.applied':'Bot 上下文已应用','bot.action.admitted':'Bot 动作已准入','bot.action.completed':'Bot 动作有结果','bot.scope.closed':'Bot 范围已结束'};
 const title=Object.prototype.hasOwnProperty.call(nativeBotTitles,event.type)?nativeBotTitles[event.type]:null;
 if(title){const parts=[title];if(Number.isInteger(p.revision))parts.push('r'+p.revision);if(typeof p.scopeId==='string')parts.push(p.scopeId);if(typeof p.decisionId==='string')parts.push('决策 '+p.decisionId);if(typeof p.actionId==='string')parts.push('动作 '+p.actionId);if(typeof p.status==='string')parts.push(p.status);return parts.join(' · ');}
 if(['process.guidance.queued','process.guidance.received','process.bot.receive','process.bot.adopt'].includes(event.type)&&typeof p.messageId==='string'){const title=event.type==='process.guidance.queued'?'已持久入队':event.type==='process.guidance.received'?'运行已接收并采用':event.type==='process.bot.receive'?'运行已接收':'运行已采用';return 'Process '+p.key+' · 指导 '+p.messageId+' · r'+(p.messageRevision??p.guidanceRevision??p.revision)+' · '+title;}
 if(event.type==='guidance.queued'||event.type==='guidance.received')return '指导 r'+p.revision+' · '+(event.type==='guidance.received'?'运行已接收':'已入队，等待运行接收');
 return null;
}
function acceptRunGuidanceReceipt(receipt,projectId=runGuidanceProject()){
 if(!receipt||typeof receipt.messageId!=='string'||!Number.isInteger(receipt.revision)||receipt.revision<1)return;
 const hasTarget=Object.prototype.hasOwnProperty.call(receipt,'target'),target=hasTarget?runGuidanceTarget(receipt.target):null;if(hasTarget&&!target)return;
 const durable=target?.kind==='processRole';
 if(durable){if(Object.keys(receipt).length!==6||typeof receipt.key!=='string'||!(receipt.runId===null||typeof receipt.runId==='string')||!['queued','received','applied'].includes(receipt.status))return;}
 else if(typeof receipt.runId!=='string'||!['queued','received'].includes(receipt.status))return;
 const key=runGuidanceKey(receipt.runId,target,projectId,durable?receipt.key:null),prior=runGuidanceReceipts.get(key),rank={queued:0,received:1,applied:2};
 if(prior&&(prior.revision>receipt.revision||prior.revision===receipt.revision&&rank[prior.status]>rank[receipt.status]))return;
 rememberRunGuidance(runGuidanceReceipts,key,receipt);
 const selected=selectedRunGuidanceTarget(),selectedKey=runGuidanceKey(activeRunId||currentRun,selected,projectId);
 if(projectId===runGuidanceProject()&&key===selectedKey&&(durable||receipt.runId===activeRunId||receipt.runId===currentRun)){const status=$('#runGuidanceReceipt');if(status)status.textContent='指导 r'+receipt.revision+' · '+({queued:'已入队，等待运行接收',received:'运行已接收',applied:'运行已采用；未证明动作完成'}[receipt.status]);}
}
function renderRunGuidanceTargets(){
 const select=runGuidancePicker();if(!select)return;
 const wanted=select.value,state=runGuidanceScopeState,current=state&&state.projectId===runGuidanceProject()&&state.runId===activeRunId;
 const legacy=current?[...state.scopes.values()].find(s=>s.mailbox==='run'):null;
 const option=(value,text,disabled=false)=>{const el=document.createElement('option');el.value=value;el.textContent=text;el.disabled=disabled;return el;};
 select.replaceChildren(option('',legacy?'当前运行 / 根 Bot '+String(legacy.botId||''):'当前运行（默认入口）'));
 if(current)for(const scope of state.scopes.values())if(scope.addressable&&!scope.terminal){const role=scope.target?.kind==='processRole';const value=role?scope.target.roleId:scope.scopeId;const label=role?'持久角色 '+scope.roleKey:String(scope.botId||'Bot')+(scope.parentScopeId?' · 子 Bot':' · 独立 Bot');select.append(option(value,label+' · '+value.slice(-8)));}
 if(wanted&&![...select.options].some(item=>item.value===wanted))select.append(option(wanted,'原 Bot 目标已结束或不可用 · '+wanted.slice(-8),true));
 select.value=wanted;
}
async function refreshRunGuidanceScopes(){
 if(!runGuidancePicker()||!activeRunId||!busy)return null;
 const runId=activeRunId,projectId=runGuidanceProject(),sequence=++runGuidanceScopeSequence;
 try{
  const result=await api('/api/runs/'+encodeURIComponent(runId)+'/guidance');
  if(sequence!==runGuidanceScopeSequence||activeRunId!==runId||projectId!==runGuidanceProject())return null;
  if(!result||result.runId!==runId||typeof result.active!=='boolean'||!Array.isArray(result.scopes)||result.scopes.length>32)throw Error('Bot 目标响应无效');
  const scopes=new Map();
  for(const row of result.scopes){
   if(!row||typeof row.scopeId!=='string'||!/^bot-scope-[0-9a-f]{32}$/.test(row.scopeId)||scopes.has(row.scopeId)||!['run','scoped'].includes(row.mailbox)||!Number.isInteger(row.revision)||row.revision<0||typeof row.terminal!=='boolean'||typeof row.addressable!=='boolean'||typeof row.rootScopeId!=='string'||!/^bot-scope-[0-9a-f]{32}$/.test(row.rootScopeId)||(row.parentScopeId!==null&&(typeof row.parentScopeId!=='string'||!/^bot-scope-[0-9a-f]{32}$/.test(row.parentScopeId)))||(row.botId!==null&&typeof row.botId!=='string'))throw Error('Bot 目标身份无效');
   const target=row.addressable?runGuidanceTarget(row.target):null;if(row.addressable&&(!target||row.mailbox!=='scoped'||target.kind==='botScope'&&target.scopeId!==row.scopeId)||!row.addressable&&row.target!==null)throw Error('Bot 指导目标无效');
   if(target?.kind==='processRole'){if(row.roleId!==target.roleId||typeof row.roleKey!=='string'||!/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(row.roleKey)||typeof row.processKey!=='string'||!row.processKey)throw Error('持久角色身份无效');rememberRunGuidance(runGuidanceRoleProcesses,JSON.stringify([projectId,target.roleId]),row.processKey);}
   scopes.set(row.scopeId,{...row,target});
  }
  runGuidanceScopeState={projectId,runId,active:result.active,scopes};renderRunGuidanceTargets();refreshRunGuidance();return result;
 }catch(error){if(sequence===runGuidanceScopeSequence&&activeRunId===runId&&projectId===runGuidanceProject()){runGuidanceScopeState=null;renderRunGuidanceTargets();const select=runGuidancePicker();if(select)select.title='无法确认 Bot 目标；请等待重连。'+error.message;refreshRunGuidance();}return null;}
}
function refreshRunGuidance(){
 const form=$('#runGuidanceForm');if(!form)return;
 form.hidden=!busy;
 const runId=activeRunId||currentRun,projectId=runGuidanceProject(),runKey=JSON.stringify([projectId,runId]);
 if(activeRunId&&runGuidanceDisplayRun!==runKey){
  if(runGuidanceDisplayKey!==null)rememberRunGuidance(runGuidanceDrafts,runGuidanceDisplayKey,$('#runGuidanceInput').value);
  runGuidanceDisplayRun=runKey;runGuidanceScopeState=null;runGuidanceScopeSequence++;
  const select=runGuidancePicker();if(select){if(!select.value.startsWith('bot-role-')||runGuidanceDisplayKey===null||JSON.parse(runGuidanceDisplayKey)[0]!==projectId)select.value='';renderRunGuidanceTargets();}
  refreshRunGuidanceScopes();
 }
 const key=runGuidanceKey(runId,selectedRunGuidanceTarget(),projectId);
 if(runId&&runGuidanceDisplayKey!==key){
  const input=$('#runGuidanceInput');if(runGuidanceDisplayKey!==null)rememberRunGuidance(runGuidanceDrafts,runGuidanceDisplayKey,input.value);
  if(runGuidanceDisplayKey!==null)input.value=runGuidanceDrafts.get(key)||'';
  runGuidanceDisplayKey=key;$('#runGuidanceReceipt').textContent='发送后先入队；运行接收时会更新状态。';const receipt=runGuidanceReceipts.get(key);if(receipt)acceptRunGuidanceReceipt(receipt,projectId);
 }
 const target=selectedRunGuidanceTarget(),scope=selectedRunGuidanceScope();
 const unavailable=target&&(!scope?.addressable||scope.terminal||runGuidanceScopeState?.runId!==activeRunId||runGuidanceScopeState?.projectId!==projectId);
 $('#runGuidanceSend').disabled=!busy||!activeRunId||!!runGuidanceRequest?.pending||!!unavailable;
}
async function sendRunGuidance(){
 const input=$('#runGuidanceInput'),text=input.value,runId=activeRunId,projectId=runGuidanceProject(),target=selectedRunGuidanceTarget(),key=runGuidanceKey(runId,target,projectId);
 if(!busy||!runId||runGuidanceRequest?.pending||!text.trim())return;
 if(text.length>20000)throw new Error('指导文本不能超过 20000 字符');
 if(target){const scope=selectedRunGuidanceScope();if(!scope?.addressable||scope.terminal||runGuidanceScopeState.runId!==runId||runGuidanceScopeState.projectId!==projectId)throw Error('原 Bot 目标已结束或不可用，请明确选择新的目标。');}
 let request=runGuidanceEnvelopes.get(key);
 if(!request||request.text!==text){request={key,runId,projectId,text,processKey:runGuidanceRoleProcess(target,projectId),target:target?Object.freeze({...target}):null,messageId:crypto.randomUUID()};rememberRunGuidance(runGuidanceEnvelopes,key,request);}
 runGuidanceRequest=request;request.pending=true;refreshRunGuidance();
 try{
  const body={input:{text},messageId:request.messageId};if(request.target)body.target={...request.target};
  const receipt=await api('/api/runs/'+encodeURIComponent(runId)+'/guidance',{method:'POST',body});
  const hasReceiptTarget=Object.prototype.hasOwnProperty.call(receipt||{},'target'),receiptTarget=hasReceiptTarget?runGuidanceTarget(receipt.target):null;
  const durable=request.target?.kind==='processRole';
  if(hasReceiptTarget&&!receiptTarget||!receipt||(!durable&&receipt.runId!==runId)||durable&&(receipt.key!==request.processKey||!(receipt.runId===null||typeof receipt.runId==='string'))||receipt.messageId!==request.messageId||runGuidanceKey(runId,receiptTarget,projectId,durable?receipt.key:null)!==key)throw Error('指导回执与原目标不一致；草稿和请求身份已保留。');
  acceptRunGuidanceReceipt(receipt,projectId);
  if(runGuidanceDrafts.get(key)===text)rememberRunGuidance(runGuidanceDrafts,key,'');
  if(activeRunId===runId&&projectId===runGuidanceProject()&&runGuidanceKey(runId,selectedRunGuidanceTarget(),projectId)===key){if(input.value===text){input.value='';rememberRunGuidance(runGuidanceDrafts,key,'');}consoleTab('events');}
  if(runGuidanceEnvelopes.get(key)===request)runGuidanceEnvelopes.delete(key);
  return receipt;
 }finally{request.pending=false;if(runGuidanceRequest===request)runGuidanceRequest=null;refreshRunGuidance();}
}
function ingestRunGuidanceEvent(event){
 if(!event||event.run_id!==activeRunId)return;
 if(event.processOrigin==='lifecycle'&&event.payload?.receipt)acceptRunGuidanceReceipt(event.payload.receipt);
 if(event.type==='guidance.queued'||event.type==='guidance.received')acceptRunGuidanceReceipt(event.payload);
 if(event.type==='bot.scope.opened'||event.type==='bot.scope.closed'||event.type==='bot.context.applied')refreshRunGuidanceScopes();
}
const runGuidanceSelect=runGuidancePicker();if(runGuidanceSelect)runGuidanceSelect.onchange=()=>refreshRunGuidance();
window.ChaosRunGuidance=Object.freeze({summary:runGuidanceSummary,acceptReceipt:acceptRunGuidanceReceipt,refresh:refreshRunGuidance,refreshScopes:refreshRunGuidanceScopes,ingestEvent:ingestRunGuidanceEvent,send:sendRunGuidance});
// End active run guidance.
function setBusy(value){busy=value;$('#runButton').disabled=value;$('#cancelButton').classList.toggle('hidden',!value);$('#runStatus').classList.toggle('running',value);refreshRunGuidance();}
async function save(){if(typeof saveLanguageDraft==='function'&&syntaxDirty)return saveLanguageDraft();if(!dirty)return project;const serial=editSerial;const next=await api('/api/projects/'+project.id,{method:'PUT',body:{id:project.id,version:project.version,name:project.name,graph:project.graph,source:project.source,files:project.files,entry:project.entry,capabilities:project.capabilities,editFrom:editFrom||'source'}});if(serial===editSerial){setProject(next);}else{project.version=next.version;}syncHeader();toast('程序已保存 · v'+next.version);return project;}


function selectedNode(){return project?.graph.nodes.find(n=>n.id===selected);}
function canEditGraph(){return !!project&&project.authoring==='workflow'&&view==='graph'&&!(typeof snapshotView!=='undefined'&&snapshotView);}
function selectNode(id){selected=id;selectedEdge=null;renderGraph();renderInspector();}
function transform(){$('#world').style.transform=`translate(${pan.x}px,${pan.y}px) scale(${zoom})`;$('#zoomLabel').textContent=Math.round(zoom*100)+'%';}
function fit(){const nodes=graphForView().nodes;if(!nodes.length)return;const rect=$('#canvas').getBoundingClientRect();const minX=Math.min(...nodes.map(n=>n.x)),maxX=Math.max(...nodes.map(n=>n.x+220)),minY=Math.min(...nodes.map(n=>n.y)),maxY=Math.max(...nodes.map(n=>n.y+135));zoom=Math.max(.25,Math.min(.92,(rect.width-75)/(maxX-minX),(rect.height-160)/(maxY-minY)));pan={x:(rect.width-(maxX-minX)*zoom)/2-minX*zoom,y:123+(rect.height-170-(maxY-minY)*zoom)/2-minY*zoom};transform();}
function point(e){const r=$('#canvas').getBoundingClientRect();return{x:(e.clientX-r.left-pan.x)/zoom,y:(e.clientY-r.top-pan.y)/zoom};}
function path(a,b){const delta=Math.max(50,Math.abs(b.x-a.x)*.48);return`M ${a.x} ${a.y} C ${a.x+delta} ${a.y}, ${b.x-delta} ${b.y}, ${b.x} ${b.y}`;}
function renderEdges(){if(!project)return;let out='';const map=Object.fromEntries(graphForView().nodes.map(n=>[n.id,n]));if(showHierarchy){for(const n of graphForView().nodes){const p=map[n.parentId];if(p)out+=`<path class="hierarchy-edge" d="M ${p.x+110} ${p.y+124} Q ${p.x+110} ${n.y-35} ${n.x+110} ${n.y}"/>`;}}for(const e of graphForView().edges){const a=map[e.source],b=map[e.target];if(!a||!b)continue;const status=stateForNode(e.target)==='running'?'running':stateForNode(e.source)==='completed'?'complete':'';out+=`<path class="edge ${status} ${selectedEdge===e.id?'selected':''}" data-edge="${esc(e.id)}" d="${path({x:a.x+220,y:a.y+64},{x:b.x,y:b.y+64})}"/>`;if(e.targetPort&&e.targetPort!=='input')out+=`<text class="edge-label" x="${(a.x+220+b.x)/2}" y="${(a.y+b.y)/2+53}">${esc(e.targetPort)}</text>`;}if(wire){const a=map[wire.source];if(a)out+=`<path class="edge running" d="${path({x:a.x+220,y:a.y+64},wire.point)}"/>`;}$('#edges').innerHTML=out;}
function renderGraph(){if(!project)return;$('#graphView').classList.toggle('source-program',(snapshotView&&runSnapshot?runSnapshot.authoring:project.authoring)==='source');$('#nodes').innerHTML=graphForView().nodes.map(n=>{const k=kinds[n.kind],cfg=n.config||{},platform=cfg.level==='platform',status=stateForNode(n.id)||'idle',label=platform?'控制层':cfg.level==='coordinator'?'协调层':cfg.level==='worker'?'工作层':k.en;const description=cfg.prompt||cfg.tool||cfg.source||(n.kind==='input'?'接收任务与上下文':'收集最终交付结果');return`<article class="node ${n.kind} ${platform?'platform':''} ${selected===n.id?'selected':''} ${status}" data-node="${esc(n.id)}" style="left:${n.x}px;top:${n.y}px" tabindex="0" aria-label="${esc(n.label)}"><div class="node-top"><span class="atom-icon ${n.kind}">${k.icon}</span><div><div class="node-title">${esc(n.label)}</div><div class="node-sub">${esc(n.id)} · ${label}</div></div></div><p class="node-description">${esc(description)}</p><div class="node-bottom"><span>${n.kind==='agent'?(cfg.provider==='model'?'MODEL PROVIDER':'LOCAL · 规则演示'):n.kind==='tool'?'REGISTERED TOOL':n.kind==='function'?'CHAOS FUNCTION':'DATA PORT'}</span><span class="state"><i class="tiny-dot"></i>${stateNames[status]||status}</span></div>${!platform&&n.kind!=='input'?'<span class="port in" title="输入端口" data-port="in"></span>':''}${!platform&&n.kind!=='output'?'<span class="port out" title="输出端口" data-port="out"></span>':''}</article>`;}).join('');renderEdges();transform();syncHeader();}
function field(label,name,value,options={}){const attrs=`data-field="${name}"`;let control;if(options.select){control=`<select ${attrs}>${options.select.map(([val,text,disabled])=>`<option value="${esc(val)}" ${disabled?'disabled':''} ${String(value??'')===String(val)?'selected':''}>${esc(text)}</option>`).join('')}</select>`;}else if(options.textarea){control=`<textarea ${attrs} class="${options.code?'source-field':''}" spellcheck="false">${esc(value)}</textarea>`;}else{control=`<input ${attrs} value="${esc(value)}" ${options.readonly?'readonly':''}>`;}return`<div class="field"><label>${label}${options.tag?`<span>${options.tag}</span>`:''}</label>${control}${options.help?`<p class="field-help">${options.help}</p>`:''}</div>`;}
function renderInspector(){window.ChaosEditor?.destroyWithin($('#inspectorContent'));let n=selectedNode();if(selectedEdge){let edge=project.graph.edges.find(e=>e.id===selectedEdge);if(!edge)return;$('#inspectorContent').innerHTML=`<div class="inspector-intro"><span class="atom-icon">↝</span><div><h3>数据关系</h3><small>${esc(edge.id)}</small></div></div>${field('来源','none',edge.source,{readonly:true})}${field('目标','none',edge.target,{readonly:true})}${field('目标端口','edgePort',edge.targetPort||'input',{help:'单一 input 端口直接传值；具名端口组成记录。'})}<div class="inspector-buttons"><button id="applyEdge" class="primary">应用端口</button><button id="deleteEdge" class="danger">删除连线</button></div>`;$('#applyEdge').onclick=()=>{edge=project.graph.edges.find(e=>e.id===selectedEdge);if(!edge)return;edge.targetPort=$('[data-field="edgePort"]').value.trim();markDirty();renderGraph();toast('已更新连线端口');};$('#deleteEdge').onclick=deleteSelectedEdge;return;}
if(!n){$('#inspectorContent').innerHTML='<div class="empty-inspector"><div class="big">⌘</div><h3>选择一个节点</h3><p>查看与编辑节点行为、<br>工具能力和管理关系。</p></div>';return;}const cfg=n.config||{},k=kinds[n.kind];let html=`<div class="inspector-intro"><span class="atom-icon ${n.kind}">${k.icon}</span><div><h3>${esc(n.label)}</h3><small>${k.en} CONFIGURATION</small></div></div>${field('显示名称','label',n.label)}${field('节点标识','id',n.id,{readonly:true})}`;
if(n.kind==='agent'){html+=field('智能体层级','level',cfg.level||'worker',{select:[['worker','L1 · 工作级智能体'],['coordinator','L2 · 协调级智能体'],['platform','L3 · 平台级智能体']]})+field('执行提供方','provider',cfg.provider||'local',{select:[['local','本地规则演示'],['model','已配置的模型服务']]})+field('模型路由角色','role',cfg.role||(cfg.level==='platform'?'planner':'executor'),{select:[['planner','顶层规划'],['executor','执行'],['reviewer','复核'],['grok','Grok']]})+field('路由智能体 ID','route',cfg.route||n.id)+field('功能 / 任务提示','prompt',cfg.prompt||'',{textarea:true});if(cfg.level==='platform'){html+=field('自动调整策略','policy',cfg.policy||'none',{select:[['ensure-review','输出前自动补充复核'],['model-plan','模型提案 · 受验证的关系调整'],['none','不自动修改']]})+'<div class="readonly-note">平台可调整其管理域内的关系。所有变更经过结构验证，以新版本原子提交。</div>';}else{html+=field('本地规则模式','mode',cfg.mode||'analyze',{select:[['analyze','任务结构分析'],['summarize','结果汇总'],['review','结构复核']]})+field('模型最大行动步数','maxSteps',cfg.maxSteps||4);}}
if(n.kind==='tool')html+=field('注册工具','tool',cfg.tool||'text.normalize',{select:callableChoices(tools,[cfg.tool||'text.normalize']).map(id=>[id,(tools[id]?.label||id)+' · '+id+(callableCapability(id)?'':' · 不能作为工具调用，请更换'),!callableCapability(id)])});
if(n.kind==='function')html+=field('函数体','source',cfg.source||'input',{textarea:true,code:true,help:'input 为连线传入值。最后一个表达式即返回值；默认禁用智能体与工具效果。'});
html+=field('所属智能体','parentId',n.parentId||'',{select:[['','无父级'],...project.graph.nodes.filter(p=>p.id!==n.id&&p.kind==='agent').map(p=>[p.id,p.label])]});
if(n.kind==='agent'||n.kind==='function'){html+='<div class="field"><label>工具能力 <span>EXPLICIT EFFECTS</span></label>'+(n.kind==='function'?[...new Set([...Object.keys(tools).filter(t=>t!=='graph.patch'),...(cfg.tools||[])])]:callableChoices(tools,cfg.tools||[],cfg.level==='platform')).map(t=>`<label class="tool-check"><input type="checkbox" data-tool="${esc(t)}" ${(cfg.tools||[]).includes(t)?'checked':''}> ${esc(t)}${n.kind==='agent'&&!callableCapability(t,tools,cfg.level==='platform')?' · '+esc(capabilityWarning(t)):''}</label>`).join('')+'</div>';}
html+='<div class="inspector-buttons"><button class="primary" id="applyNode">应用配置</button><button class="danger" id="deleteNode">删除</button></div>';$('#inspectorContent').innerHTML=html;
if(n.kind==='agent')$$('#inspectorContent [data-tool]').forEach(el=>{if(!callableCapability(el.dataset.tool,tools,cfg.level==='platform'))el.onchange=()=>{if(el.checked)el.checked=false;};});
$('#applyNode').onclick=()=>{n=selectedNode();if(!n)return;const values=Object.fromEntries($$('[data-field]').map(el=>[el.dataset.field,el.value]));n.label=values.label||n.id;n.parentId=values.parentId||null;for(const key of ['level','provider','prompt','mode','policy','source','tool','role','route'])if(key in values)n.config[key]=values[key];if(values.maxSteps!==undefined)n.config.maxSteps=Number(values.maxSteps);if(n.kind==='agent'||n.kind==='function')n.config.tools=$$('[data-tool]:checked').map(el=>el.dataset.tool);markDirty();renderGraph();renderInspector();toast('节点配置已应用；保存后持久化');};$('#deleteNode').onclick=deleteSelected;
if(n.kind==='function'&&window.ChaosEditor){const id=n.id,pid=project.id;window.ChaosEditor.mount($('#inspectorContent textarea.source-field'),{label:'工作流函数体',identity:()=>project.id+':'+selected+':'+project.version,readOnly:()=>snapshotView||view!=='graph'||project.id!==pid||selected!==id||project.authoring!=='workflow',visible:()=>view==='graph',changed:()=>n.config.source!==baseProject?.graph.nodes.find(item=>item.id===id)?.config?.source,context:()=>{const file=project.entry,versions={...project.program?.sourceVersions};for(const path of Object.keys(project.files))versions[path]=versions[path]??project.program?.languageVersion??2;const original=baseProject?.graph.nodes.find(item=>item.id===id)?.config?.source;const draft=$('#inspectorContent textarea.source-field')?.value??'';if(draft!==original)versions[file]=2;return {file,entry:file,files:{...project.files,[file]:draft},languageVersions:versions};}});}
}
function deleteSelectedEdge(){if(!canEditGraph()||!selectedEdge||!project.graph.edges.some(edge=>edge.id===selectedEdge))return;project.graph.edges=project.graph.edges.filter(edge=>edge.id!==selectedEdge);selectedEdge=null;markDirty();renderGraph();renderInspector();}
function deleteSelected(){if(!canEditGraph()||!selectedNode())return;project.graph.nodes=project.graph.nodes.filter(n=>n.id!==selected);project.graph.edges=project.graph.edges.filter(e=>e.source!==selected&&e.target!==selected);for(const n of project.graph.nodes)if(n.parentId===selected)n.parentId=null;selected=null;markDirty();renderGraph();renderInspector();}
function addNode(kind,pos){const id=kind+'_'+Math.random().toString(36).slice(2,8);let config={};if(kind==='agent')config={level:'worker',provider:'model',mode:'analyze',prompt:'描述这个智能体的目标与约束。',tools:[],maxSteps:4};if(kind==='function')config={source:'input',tools:[]};if(kind==='tool')config={tool:'text.normalize'};const platform=project.graph.nodes.find(n=>n.config?.level==='platform');project.graph.nodes.push({id,kind,label:kinds[kind].label,x:Math.round(pos.x-110),y:Math.round(pos.y-60),parentId:platform?.id||null,config});markDirty();selectNode(id);toast('已添加节点，连接输入与输出以形成完整路径');}
function connect(source,target){if(source===target)return toast('不能连接节点自身',true);if(project.graph.edges.some(e=>e.source===source&&e.target===target))return toast('这两个节点已连接',true);const incoming=project.graph.edges.filter(e=>e.target===target);let port='input';if(incoming.length){if(incoming.length===1&&incoming[0].targetPort==='input')incoming[0].targetPort=incoming[0].source.replace(/-/g,'_');port=source.replace(/-/g,'_');}project.graph.edges.push({id:'edge_'+Math.random().toString(36).slice(2,9),source,target,sourcePort:'value',targetPort:port});markDirty();renderGraph();toast('关系已连接 · 保存或运行时验证结构');}
$('#palette').innerHTML=Object.entries(kinds).map(([kind,k])=>`<button class="palette-item" draggable="true" data-kind="${kind}" title="拖动到画布；点击在画布中心添加"><span class="atom-icon ${kind}">${k.icon}</span><span><b>${k.label}</b><small>${k.en}</small></span><span class="drag-symbol">⠿</span></button>`).join('');
$$('.palette-item').forEach(el=>{el.ondragstart=e=>{e.dataTransfer.setData('application/chaos-node',el.dataset.kind);e.dataTransfer.effectAllowed='copy';};el.onclick=()=>{const r=$('#canvas').getBoundingClientRect();addNode(el.dataset.kind,point({clientX:r.left+r.width/2,clientY:r.top+r.height/2}));};});
$('#canvas').ondragover=e=>{e.preventDefault();e.dataTransfer.dropEffect='copy';};$('#canvas').ondrop=e=>{e.preventDefault();const kind=e.dataTransfer.getData('application/chaos-node');if(kinds[kind])addNode(kind,point(e));};
$('#canvas').onpointerdown=e=>{if(e.button!==0)return;const port=e.target.closest('[data-port]'),node=e.target.closest('[data-node]'),edge=e.target.closest('[data-edge]');if(port){e.preventDefault();if(port.dataset.port==='out'){wire={source:node.dataset.node,point:point(e)};renderEdges();}else if(wire){connect(wire.source,node.dataset.node);wire=null;renderEdges();}return;}if(node){const n=project.graph.nodes.find(n=>n.id===node.dataset.node);selectNode(n.id);drag={type:'node',id:n.id,start:point(e),x:n.x,y:n.y,moved:false};e.preventDefault();return;}if(edge){selected=null;selectedEdge=edge.dataset.edge;renderGraph();renderInspector();return;}selected=null;selectedEdge=null;renderGraph();renderInspector();drag={type:'pan',x:e.clientX,y:e.clientY,pan:{...pan}};};
window.addEventListener('pointermove',e=>{if(wire){wire.point=point(e);renderEdges();}if(!drag)return;if(drag.type==='node'){const p=point(e),n=project.graph.nodes.find(n=>n.id===drag.id);n.x=Math.max(-9500,Math.min(9500,Math.round(drag.x+p.x-drag.start.x)));n.y=Math.max(-9500,Math.min(9500,Math.round(drag.y+p.y-drag.start.y)));drag.moved=true;const el=$(`[data-node="${CSS.escape(n.id)}"]`);if(el){el.style.left=n.x+'px';el.style.top=n.y+'px';}renderEdges();}else{pan={x:drag.pan.x+e.clientX-drag.x,y:drag.pan.y+e.clientY-drag.y};transform();}});
window.addEventListener('pointerup',e=>{if(drag?.type==='node'&&drag.moved)markDirty();drag=null;if(wire){const hit=document.elementFromPoint(e.clientX,e.clientY),port=hit?.closest('[data-port="in"]');if(port){connect(wire.source,port.closest('[data-node]').dataset.node);wire=null;}else if(!hit?.closest('[data-port="out"]'))wire=null;renderEdges();}});
$('#canvas').addEventListener('wheel',e=>{e.preventDefault();if(e.ctrlKey||e.metaKey){const p=point(e),r=$('#canvas').getBoundingClientRect();zoom=Math.max(.25,Math.min(1.8,zoom*Math.exp(-e.deltaY*.002)));pan={x:e.clientX-r.left-p.x*zoom,y:e.clientY-r.top-p.y*zoom};}else{pan.x-=e.deltaX;pan.y-=e.deltaY;}transform();},{passive:false});
$('#zoomIn').onclick=()=>{zoom=Math.min(1.8,zoom+.1);transform();};$('#zoomOut').onclick=()=>{zoom=Math.max(.25,zoom-.1);transform();};$('#fitButton').onclick=fit;$('#hierarchyButton').onclick=()=>{showHierarchy=!showHierarchy;$('#hierarchyButton').classList.toggle('on',showHierarchy);renderEdges();};
function lineNumbers(){$('#lineNumbers').textContent=Array.from({length:$('#sourceEditor').value.split('\n').length},(_,i)=>i+1).join('\n');}
$('#sourceEditor').oninput=()=>{const file=typeof activeFile==='string'?activeFile:project.entry;project.files[file]=$('#sourceEditor').value;if(file===project.entry)project.source=$('#sourceEditor').value;markDirty('source');lineNumbers();};$('#sourceEditor').onscroll=()=>{$('#lineNumbers').scrollTop=$('#sourceEditor').scrollTop;};$('#sourceEditor').onkeydown=e=>{if(e.key==='Tab'){e.preventDefault();const el=e.target,start=el.selectionStart;el.setRangeText('  ',start,el.selectionEnd,'end');el.dispatchEvent(new Event('input'));}};
$$('[data-view]').forEach(el=>el.onclick=guarded(async()=>{if(dirty)await save();view=el.dataset.view;document.body.classList.toggle('syntax-view-active',view==='structure');$$('[data-view]').forEach(b=>b.classList.toggle('active',b===el));$$('.view').forEach(p=>p.classList.toggle('active',p.id===view+'View'));$('#runButton').innerHTML='<span>▶</span> '+'运行 Chaos 程序';if(view==='proof'&&project.authoring==='workflow')await validateGraph();if(view==='structure')await loadProgram();syncState();}));
function consoleTab(id){$('#console').classList.toggle('artifact-active',id==='artifact');$$('[data-console]').forEach(b=>b.classList.toggle('active',b.dataset.console===id));$$('.console-panel').forEach(p=>p.classList.toggle('active',p.id===id+'Panel'));}
$$('[data-console]').forEach(el=>el.onclick=guarded(async()=>{consoleTab(el.dataset.console);if(el.dataset.console==='history')await loadHistory();}));$('#expandConsole').onclick=()=>$('#console').classList.toggle('expanded');
const checkLabels={acyclicDataflow:['无环数据流','执行顺序可拓扑排序'],validReferences:['引用完整','节点与端点有效'],uniqueInputPorts:['端口唯一','输入来源无歧义'],strictHierarchy:['层级合法','管理关系严格向下'],singleEntryExit:['唯一入口与出口','形成明确的任务边界'],allNodesOnPath:['路径覆盖完整','执行节点均可从输入到达输出']};
async function validateGraph(){try{const proof=await api('/api/validate',{method:'POST',body:{graph:project.graph}});$('#proofResults').innerHTML=Object.entries(proof.checks).map(([k,v])=>`<div class="proof-check ${v?'':'failed'}"><span>${v?'✓':'×'}</span><div>${checkLabels[k][0]}<small>${v?checkLabels[k][1]:'未通过：请检查画布连接'}</small></div></div>`).join('');if(proof.uncovered.length)$('#proofResults').innerHTML+=`<div class="proof-check failed">未覆盖：${esc(proof.uncovered.join(', '))}</div>`;return proof;}catch(e){$('#proofResults').innerHTML=`<div class="proof-check failed">${esc(e.message)}</div>`;throw e;}}
$('#validateButton').onclick=guarded(validateGraph);$('#checkSource').onclick=guarded(async()=>{await api('/api/validate',{method:'POST',body:{graph:project.graph,source:project.source,files:project.files,entry:project.entry}});toast('程序与模块依赖检查通过；动态契约在执行时验证');});
function eventMessage(event){const progress=runGuidanceSummary(event);if(progress!==null)return progress;const p=event.payload;return (typeof p.summary==='string'?p.summary:null)||p.reason||p.error||(p.nodeId?(project.graph.nodes.find(n=>n.id===p.nodeId)?.label||p.nodeId)+(p.tool?' → '+p.tool:'')+(p.durationMs!==undefined?' · '+p.durationMs+' ms':''):p.steps!==undefined?'执行完成 · '+p.steps+' 步':p.mode==='source'?'函数程序':'工作流 · v'+(p.version??project.version));}
let eventRenderFrame=null,eventDisplayRun=null,eventFollow=true,eventUnseen=0;
function renderEvents(){
 if(eventRenderFrame!==null)return;
 eventRenderFrame=requestAnimationFrame(()=>{eventRenderFrame=null;renderEventRows();});
}
function renderEventRows(){
 const shown=currentRun?events.filter(e=>e.run_id===currentRun||e.type==='graph.revised'):events;
 for(const event of shown)if(event.run_id===currentRun&&(event.type==='guidance.queued'||event.type==='guidance.received'))acceptRunGuidanceReceipt(event.payload);
 $('#eventCount').textContent=shown.length;
 const panel=$('#eventsPanel'),follow=$('#followEvents'),rows=shown.slice(-250);
 if(eventDisplayRun!==currentRun){eventDisplayRun=currentRun;eventFollow=true;eventUnseen=0;panel.replaceChildren();}
 const position=panel.scrollTop;
 if(!rows.length){if(follow)follow.hidden=true;return;}
 const ids=new Set(rows.map(event=>String(event.id)));let removedHeight=0;
 for(const row of [...panel.children]){if(!ids.has(row.dataset.eventId)){removedHeight+=row.offsetHeight;row.remove();}}
 const existing=new Set([...panel.children].map(row=>row.dataset.eventId));let added=0;
 for(const event of rows){
   if(existing.has(String(event.id)))continue;
   const row=document.createElement('div');row.dataset.eventId=String(event.id);
   row.className='event-row'+(event.type.includes('failed')?' error':'')+(event.type==='graph.revised'?' revision':'');
   const time=document.createElement('time');time.textContent=new Date(event.created*1000).toLocaleTimeString('zh-CN',{hour12:false});
   const type=document.createElement('span');type.className='event-type';type.textContent=event.type;
   const message=document.createElement('span');message.className='event-message';message.textContent=eventMessage(event);message.title=message.textContent;
   row.append(time,type,message);panel.append(row);added++;
 }
 if(eventFollow){panel.scrollTop=panel.scrollHeight;eventUnseen=0;}else{panel.scrollTop=Math.max(0,position-removedHeight);eventUnseen+=added;}
 if(follow){follow.hidden=eventFollow||!eventUnseen;follow.textContent='跟随最新 · '+eventUnseen;}
}
$('#eventsPanel').addEventListener('scroll',()=>{const panel=$('#eventsPanel');eventFollow=panel.scrollHeight-panel.scrollTop-panel.clientHeight<32;if(eventFollow){eventUnseen=0;const button=$('#followEvents');if(button)button.hidden=true;}},{passive:true});
$('#followEvents')?.addEventListener('click',()=>{eventFollow=true;eventUnseen=0;$('#eventsPanel').scrollTop=$('#eventsPanel').scrollHeight;$('#followEvents').hidden=true;});
function applyRunEvent(event){const p=event.payload;if(event.run_id!==currentRun)return;if(event.type==='guidance.queued'||event.type==='guidance.received')acceptRunGuidanceReceipt(p);if(event.type.startsWith('node.'))nodeStates[p.nodeId]=event.type==='node.started'?'running':event.type.split('.')[1];if(event.type.startsWith('run.')){const status=event.type.split('.')[1];$('#runStatus').innerHTML='<i></i>'+esc(stateNames[status]||status)+' · v'+p.version;if(['completed','failed','cancelled','interrupted'].includes(status)){setBusy(false);activeRunId=null;renderRunOutput(p.output,p.error,p.outputJson,{id:event.run_id,version:p.version,status:event.type.split('.')[1],projectId:project.id});$('#budgetInfo').textContent=(p.steps||0).toLocaleString()+' 步 · 运行已结束';loadHistory().catch(()=>{});}}}
function openEvents(){eventSource=new EventSource('/api/events?projectId='+encodeURIComponent(project.id));eventSource.onopen=()=>{$('#connection').textContent='实时已连接';};eventSource.onerror=()=>{$('#connection').textContent='正在重连';};eventSource.onmessage=guarded(async e=>{const event=parseJSON(e.data);if(event.id<=lastEvent)return;lastEvent=event.id;events.push(event);if(events.length>1500)events.shift();applyRunEvent(event);renderEvents();if(event.type.startsWith('node.'))renderGraph();if(event.type==='graph.revised'||event.type==='graph.saved'){if(event.payload.version>project.version){if(dirty){toast('远端版本已更新；本地草稿保留，请导出后重新载入',true);}else{const generation=editSerial;const p=await api('/api/projects/'+project.id);if(dirty||generation!==editSerial){toast('新版本已到达；已保留当前草稿',true);return;}setProject(p);if(event.type==='graph.revised'){fit();toast(event.payload.reason);}}}}});}
async function startRun(){await save();const value=$('#jsonInput').checked?parseJSON($('#taskInput').value):$('#taskInput').value;setBusy(true);nodeStates={};$('#resultPanel').textContent='正在执行…';consoleTab('events');try{const run=await api('/api/runs',{method:'POST',body:{projectId:project.id,version:project.version,input:value,mode:view==='source'?'source':'graph',autoOptimize:$('#autoOptimize').checked}});currentRun=run.id;activeRunId=run.id;refreshRunGuidance();$('#snapshotHint').textContent='执行快照 v'+run.version;$('#runStatus').innerHTML='<i></i> 执行中 · v'+run.version;events.filter(e=>e.run_id===currentRun).forEach(applyRunEvent);if(run.version!==project.version){const generation=editSerial;const refreshed=await api('/api/projects/'+project.id);if(!dirty&&generation===editSerial){setProject(refreshed);fit();}else toast('运行使用新版本；本地草稿已保留，请导出或重新载入',true);}const latest=await api('/api/runs/'+run.id);if(!['queued','running'].includes(latest.status)){setBusy(false);activeRunId=null;$('#runStatus').innerHTML='<i></i>'+esc(stateNames[latest.status]||latest.status)+' · v'+latest.version;renderRunOutput(latest.output,latest.error,latest.outputJson,latest);}renderGraph();renderEvents();await loadHistory();}catch(e){setBusy(false);throw e;}}
async function loadHistory(){history=await api('/api/runs?projectId='+project.id);$('#historyPanel').innerHTML=history.length?history.map(r=>`<div class="history-row"><span>${new Date(r.created*1000).toLocaleTimeString('zh-CN')}</span><code>${r.id.slice(0,8)}</code><span>v${r.version} · ${r.mode==='source'?'源码':'图'}</span><span>${stateNames[r.status]||r.status}</span><button data-run="${r.id}">查看</button></div>`).join(''):'<div class="empty-console">还没有运行记录</div>';$$('[data-run]').forEach(el=>el.onclick=guarded(async()=>{if(busy){toast('请先等待当前运行结束再查看历史',true);return;}const run=await api('/api/runs/'+el.dataset.run);currentRun=run.id;nodeStates={};events.filter(e=>e.run_id===currentRun).forEach(applyRunEvent);renderRunOutput(run.output,run.error,run.outputJson,run);$('#snapshotHint').textContent='查看运行快照 v'+run.version;$('#runStatus').innerHTML='<i></i>'+esc(stateNames[run.status]||run.status)+' · v'+run.version;renderGraph();renderEvents();consoleTab('result');}));}
$('#runGuidanceForm').onsubmit=guarded(async event=>{event.preventDefault();await sendRunGuidance();});
$('#saveButton').onclick=guarded(save);$('#runButton').onclick=guarded(startRun);$('#cancelButton').onclick=guarded(async()=>{if(activeRunId){await api('/api/runs/'+activeRunId+'/cancel',{method:'POST',body:{}});toast('已请求取消，正在等待运行中操作结束');}});
$('#optimizeButton').onclick=guarded(async()=>{await save();const generation=editSerial;const result=await api('/api/projects/'+project.id+'/optimize',{method:'POST',body:{version:project.version}});if(!dirty&&generation===editSerial){setProject(result.project);fit();toast(result.reason);}else toast('平台已提交新版本；本地编辑已保留，请导出后重新载入',true);});
$('#exportButton').onclick=guarded(async()=>{if(dirty)await save();const blob=new Blob([stringifyJSON({...project,source:project.source},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='chaos-workspace.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('#importButton').onclick=()=>$('#importFile').click();$('#importFile').onchange=guarded(async e=>{const file=e.target.files[0];if(!file)return;if(file.size>1000000)throw new Error('项目文件超过 1 MB');const data=parseJSON(await file.text());await api('/api/validate',{method:'POST',body:{graph:data.graph,source:data.source||'',files:data.files,entry:data.entry}});if(dirty&&!confirm('导入将替换当前未保存的草稿，是否继续？'))return;syntaxDirty=false;programDraft=null;syntaxRequest++;$('#syntaxTree').replaceChildren();$('#syntaxFields').replaceChildren();project.graph=data.graph;project.source=data.source||'';project.files=data.files||{[project.entry]:project.source};project.entry=data.entry||project.entry;project.files[project.entry]=project.source;activeFile=project.entry;project.name=data.name||'导入的工作流';$('#sourceEditor').value=project.source;selected=null;lineNumbers();markDirty('source');await save();renderGraph();renderInspector();fit();toast('项目已导入并保存');e.target.value='';});
$('#reloadButton').onclick=guarded(async()=>{if(dirty&&!confirm('重新载入将丢弃未保存草稿，是否继续？'))return;setProject(await api('/api/projects/'+project.id));nodeStates={};renderGraph();fit();toast('已载入最新保存版本');});
$('#helpButton').onclick=()=>$('#helpDialog').showModal();$('#closeHelp').onclick=()=>$('#helpDialog').close();
window.addEventListener('keydown',e=>{if(!e.isComposing&&(e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();guarded(()=>save({formatClean:true}))();}if(e.key==='Escape'){wire=null;renderEdges();}if(e.key==='Delete'){const focus=document.activeElement;if(!canEditGraph()||focus?.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(focus?.tagName)||document.querySelector('dialog[open]'))return;if(selectedEdge)deleteSelectedEdge();else deleteSelected();}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});window.addEventListener('resize',()=>{if(view==='graph')fit();});
async function boot(){const data=await api('/api/bootstrap');tools=data.tools;setProject(data.project);if(data.modelConfigured)$('#providerNotice').innerHTML='◉ 模型服务已配置<p>各智能体可在执行提供方中选择模型服务。</p>';await loadHistory();const active=history.find(r=>['queued','running'].includes(r.status));if(active){currentRun=active.id;activeRunId=active.id;setBusy(true);}openEvents();requestAnimationFrame(fit);}
// boot is owned by the synchronization controller.

$('#inspectorToggle').onclick=()=>document.body.classList.toggle('show-inspector');
