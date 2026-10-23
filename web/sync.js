'use strict';
// One committed base, one editable draft, and a monotonic remote revision.
let flushingControls=false;
let baseProject=null, remoteProject=null, saveTask=null, refreshRequest=0, launching=false;
let controlDraft=false, followRuns=true, runSnapshot=null, runVersion=null, snapshotView=false, runRequest=0;
const pendingFields=new Map();
const originalSetProject=setProject, originalRenderInspector=renderInspector;
const projectId=new URL(location.href).searchParams.get('project')||'workspace';
const expressionTrace=$('#expressionTrace');
try{expressionTrace.checked=localStorage.getItem('chaos.ide.expressionTrace')!=='false';}catch{}
expressionTrace.onchange=()=>{try{localStorage.setItem('chaos.ide.expressionTrace',String(expressionTrace.checked));}catch{}};
function runIdentityLabel(snapshot,version){return snapshot?.execution?.kind==='executable'?'编译文件 '+String(snapshot.execution.imageHash).slice(0,12)+' · 宿主 v'+version:'执行快照 v'+version;}
function syncState(){
  const conflict=remoteProject&&remoteProject.version>project.version;
  $('#syncBanner').classList.toggle('hidden',!conflict);
  $('#syncText').textContent=conflict?'远端 v'+remoteProject.version+' 已提交；当前草稿基于 v'+project.version+'，已保留。':'';
  $('#syncBadge').textContent=saveTask?'保存中':conflict?'版本冲突':dirty||controlDraft?'本地草稿':'已同步 v'+(project?.version||'');
  $('#runVersionNote').textContent=runVersion===null?'':(runIdentityLabel(runSnapshot,runVersion)+' · 工作区 v'+project.version+(runVersion!==project.version?' · 状态仅属于执行快照':''));
  $('#viewSnapshot').disabled=!runSnapshot;
  document.body.classList.toggle('run-snapshot',snapshotView);
  $('#inspectorContent').querySelectorAll('input,textarea,select,button').forEach(el=>el.disabled=snapshotView||view!=='graph');
  document.body.classList.toggle('graph-edit-disabled',snapshotView||view!=='graph'||project?.authoring!=='workflow');
}
setProject=function(p){
  if(baseProject&&p.id===baseProject.id&&p.version<baseProject.version)return false;
  baseProject=clone(p);remoteProject=remoteProject?.version>p.version?remoteProject:null;
  pendingFields.clear();controlDraft=false;refreshRequest++;
  originalSetProject(p);syncState();return true;
};
const originalMarkDirty=markDirty;
markDirty=function(origin='graph'){originalMarkDirty(origin);syncState();};
function changedControl(key,apply){
  pendingFields.set(key,apply);controlDraft=true;markDirty(view==='structure'?'source':'graph');
}
function flushControls(){
  if(flushingControls)return;flushingControls=true;
  const pending=[...pendingFields.entries()];
  try{for(const [key,apply]of pending){apply();if(pendingFields.get(key)===apply)pendingFields.delete(key);}controlDraft=pendingFields.size>0;if(pending.some(([key])=>key==='inspector'))renderInspector();}catch(error){controlDraft=true;throw error;}finally{flushingControls=false;}
}
function acceptRemote(p){
  if(!project||p.id!==project.id||p.version<=baseProject.version)return;
  if(dirty||controlDraft||saveTask){if(!remoteProject||p.version>remoteProject.version)remoteProject=p;syncState();return;}
  setProject(p);
}
async function refreshRemote(){
  const request=++refreshRequest,id=project.id;
  const p=await api('/api/projects/'+id);
  if(request!==refreshRequest||p.id!==project.id)return;
  acceptRemote(p);
}
api=async function(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers},body:options.body===undefined?undefined:stringifyJSON(options.body)});
  const body=parseJSON(await response.text());
  if(!response.ok){const error=new Error(typeof body.error==='object'?body.error.message:body.error||'HTTP '+response.status);error.status=response.status;error.details=body.error?.details;throw error;}
  return body;
};
save=async function({formatClean=false}={}){
  if(saveTask)return saveTask;
  const work=async()=>{
    let committed=false;
    for(let pass=0;pass<8;pass++){
      if(window.ChaosEditor?.formatBeforeSave)await window.ChaosEditor.formatBeforeSave();
      flushControls();
      if(!dirty&&(!formatClean||view!=='source'||committed))return clone(baseProject);
      if(remoteProject?.version>project.version){if(!dirty)return clone(baseProject);throw new Error('远端版本已更新，请先处理版本冲突；本地草稿已保留。');}
      if(window.ChaosIDE?.formatBeforeSave)await window.ChaosIDE.formatBeforeSave();
      if(!dirty)return clone(baseProject);
      const serial=editSerial,file=activeFile||project.entry,asIR=syntaxDirty;
      const body=asIR?{version:project.version,file,program:clone(programDraft),capabilities:clone(project.capabilities)}:
        {id:project.id,version:project.version,name:project.name,graph:clone(project.graph),source:project.source,files:clone(project.files),entry:project.entry,capabilities:clone(project.capabilities),editFrom:editFrom||'source',sourceVersions:window.ChaosIDE?.sourceVersionOverrides?.()};
      if(asIR&&!body.program)throw new Error('程序结构尚未载入，请稍后再保存。');
      let next;
      try{next=await api('/api/projects/'+project.id+(asIR?'/program':''),{method:asIR?'POST':'PUT',body});}
      catch(error){if(error.status===409)await refreshRemote();throw error;}
      committed=true;
      if(serial===editSerial&&!controlDraft){setProject(next);}
      else{
        baseProject=clone(next);project.version=next.version;
        if(remoteProject?.version<=next.version)remoteProject=null;
        // More input remains a draft; the next pass commits it before a run starts.
      }
    }
    throw new Error('保存期间仍有持续输入。草稿已保留，请停止输入后再次保存。');
  };
  saveTask=work();syncState();
  try{return await saveTask;}finally{saveTask=null;syncState();}
};
saveLanguageDraft=()=>save();
$('#saveButton').onclick=guarded(()=>save({formatClean:true}));$('#applyProgram').onclick=guarded(()=>save());
function draftDocument(){return {...clone(project),draftProgram:(syntaxDirty||(view==='structure'&&pendingFields.size))?clone(programDraft):null,draftFile:activeFile,draftEditFrom:editFrom,draftPending:pendingFields.size>0,draftControls:{syntaxVersion:2,syntaxTextareas:[...$('#syntaxFields').querySelectorAll('textarea')].map(el=>({label:el.getAttribute('aria-label'),value:el.value})),syntax:[...$('#syntaxFields').querySelectorAll('input')].filter(el=>!el.closest('.agent-config-editor')).map(el=>el.value),syntaxAgentInputs:[...$('#syntaxFields').querySelectorAll('.agent-config-editor input')].map(el=>({label:el.getAttribute('aria-label'),value:el.value,checked:el.checked})),selection:syntaxSelection,nodeId:selected,edgeId:selectedEdge,inspector:[...$('#inspectorContent').querySelectorAll('[data-field],[data-tool]')].map(el=>({field:el.dataset.field,tool:el.dataset.tool,value:el.value,checked:el.checked}))}};}
function downloadDraft(){
  const data=draftDocument(),blob=new Blob([stringifyJSON(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download='chaos-'+project.id+(dirty?'-draft':'')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('#exportButton').onclick=downloadDraft;$('#exportConflict').onclick=downloadDraft;
$('#acceptRemote').onclick=guarded(async()=>{
  // Retain even syntactically invalid edits before an explicit reload.
  if(dirty||controlDraft)localStorage.setItem('chaos-draft:'+project.id,stringifyJSON(draftDocument()));
  const generation=editSerial,p=await api('/api/projects/'+project.id);
  if(generation!==editSerial)throw new Error('载入期间出现新编辑，已保留草稿；请再次选择载入。');
  setProject(p);toast('已载入远端版本；原草稿保留在本机，可用“导出备份草稿”取回。');
});
$('#restoreDraft').onclick=()=>{
  const saved=localStorage.getItem('chaos-draft:'+project.id);if(!saved)return toast('暂无备份草稿');
  const blob=new Blob([saved],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download='chaos-'+project.id+'-backup.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('#reloadButton').onclick=guarded(async()=>{
  if(dirty||controlDraft){remoteProject=await api('/api/projects/'+project.id);syncState();return toast('草稿已保留。请导出，或选择“备份草稿并载入远端”。');}
  await refreshRemote();toast('已检查服务器最新版本');
});
$('#formatSource').onclick=guarded(async()=>{
  const committed=await save(),generation=editSerial,file=activeFile;
  const current=await api('/api/projects/'+project.id+'/program?file='+encodeURIComponent(file));
  if(generation!==editSerial||file!==activeFile||current.version!==committed.version)throw new Error('格式化期间程序已变化，请重试。');
  const next=await api('/api/projects/'+project.id+'/program',{method:'POST',body:{version:committed.version,program:current.program,file}});
  if(generation===editSerial&&!controlDraft&&file===activeFile)setProject(next);
  else{baseProject=clone(next);project.version=next.version;syncState();toast('格式化已保存；新输入仍作为草稿保留。');}
});
// Unapplied inspector values count as edits, and save/run commits them.
$('#inspectorContent').addEventListener('input',e=>{
  if(e.target.matches('[data-field],[data-tool]'))changedControl('inspector',()=>($('#applyNode')||$('#applyEdge'))?.click());
});
$('#inspectorContent').addEventListener('change',e=>{
  if(e.target.matches('[data-field],[data-tool]'))changedControl('inspector',()=>($('#applyNode')||$('#applyEdge'))?.click());
});
const previousSelectNode=selectNode;
selectNode=function(id){if(controlDraft)flushControls();previousSelectNode(id);};
renderInspector=function(){
  if(snapshotView){$('#inspectorContent').innerHTML='<p class="readonly-note">正在查看执行快照 v'+runVersion+'。返回编辑版本后可修改。</p>';return;}
  if(pendingFields.has('inspector'))return;
  originalRenderInspector();
};
const previousSyntaxProject=onLanguageProject;
onLanguageProject=function(){
  $('#syntaxTree').replaceChildren();$('#syntaxFields').replaceChildren();
  previousSyntaxProject();
};
scalarInput=function(value,callback,type='str'){
  const row=element('div','syntax-list-head'),input=element('input'),button=element('button','','应用');
  input.value=type==='literal'?(value?.$number??stringifyJSON(value)):value;
  const commit=()=>{
    const raw=input.value.trim(),numeric=/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/.test(raw);
    const encoded=numeric&&(/[.eE]/.test(raw)||BigInt(raw)>9007199254740991n||BigInt(raw)<-9007199254740991n);
    const parsed=type==='literal'?(encoded?{$number:raw}:parseJSON(raw)):input.value;
    if(type==='literal'&&parsed!==null&&typeof parsed==='object'&&!encoded)throw new Error('常量必须为标量');
    pendingFields.delete(input);callback(parsed);controlDraft=pendingFields.size>0;
  };
  input.oninput=()=>changedControl(input,commit);button.onclick=guarded(commit);
  row.append(input,button);return row;
};
const priorChooseKind=chooseKind;
chooseKind=function(current,callback,patterns=false){const select=priorChooseKind(current,kind=>{flushControls();callback(kind);},patterns);select.addEventListener('change',()=>{controlDraft=pendingFields.size>0;});return select;};
function graphForView(){return snapshotView&&runSnapshot&&runSnapshot.execution?.kind!=='executable'?runSnapshot.graph:project.graph;}
function stateForNode(id){return snapshotView||(!dirty&&!controlDraft&&runVersion===project.version)?nodeStates[id]:'idle';}
function showRun(run){
  currentRun=run.id;runVersion=run.version;runSnapshot=run.snapshot;nodeStates={};
  if(runSnapshot?.execution?.kind==='executable')snapshotView=false;
  activeRunId=['running','queued'].includes(run.status)?run.id:null;setBusy(!!activeRunId);
  renderRunOutput(run.output,run.error,run.outputJson,run);
  $('#runStatus').textContent=(stateNames[run.status]||run.status)+' · '+runIdentityLabel(runSnapshot,run.version);
  syncState();renderGraph();
}
async function fetchRun(id){
  const request=++runRequest,run=await api('/api/runs/'+id),saved=[];
  let cursor=0;
  for(let count=0;count<100;count++){
    const page=await api('/api/v1/events?projectId='+encodeURIComponent(project.id)+'&runId='+encodeURIComponent(id)+'&after='+cursor+'&limit=300');
    if(request!==runRequest)return;
    saved.push(...page.items);if(!page.nextCursor)break;cursor=page.nextCursor;
  }
  if(request!==runRequest)return;
  const byId=new Map([...events,...saved].map(e=>[String(e.id),e]));events=[...byId.values()].sort((a,b)=>window.ChaosRealtime.compareIds(a.id,b.id)).slice(-1500);
  showRun(run);saved.forEach(applyRunEvent);window.ChaosRunGuidance?.refreshScopes();renderGraph();renderEvents();syncState();
}
applyRunEvent=function(event){
  const p=event.payload;if(event.run_id!==currentRun)return;
  if(event.type==='ui.delta')window.ChaosArtifacts?.delta(p,{id:event.run_id,version:runVersion,projectId:project.id});
  if(event.type==='bot.progress'&&activeRunId===event.run_id){$('#resultPanel').textContent='执行中 · '+(runGuidanceSummary(event)||'公开进度')+'\n\n'+stringifyJSON(p.data,null,2);}
  if(event.type.startsWith('node.')&&!p.executionKind&&runSnapshot?.execution?.kind!=='executable')nodeStates[p.nodeId]=event.type==='node.started'?'running':event.type.split('.')[1];
  if(event.type.startsWith('run.')){
    const status=event.type.split('.')[1];$('#runStatus').textContent=(stateNames[status]||status)+' · '+runIdentityLabel(runSnapshot,p.version);
    if(['completed','failed','cancelled','interrupted'].includes(status)){
      setBusy(false);activeRunId=null;renderRunOutput(p.output,p.error,p.outputJson,{id:event.run_id,version:p.version,status,projectId:project.id,snapshot:runSnapshot});
      $('#budgetInfo').textContent=(p.steps||0).toLocaleString()+' 步 · 运行已结束';
    }
  }
  if(event.type==='program.step'&&!p.executionKind&&p.sourceKind==='module'&&!dirty&&!controlDraft&&p.version===project.version&&p.location?.file===activeFile&&programDraft){
    const match=[...syntaxIndex().values()].find(({node})=>node.kind===p.kind&&node.span?.line===p.location.line&&node.span?.column===p.location.column);
    $$('.syntax-row.executing').forEach(el=>el.classList.remove('executing'));
    if(match)$('#syntaxTree [data-syntax="'+match.node.id+'"]')?.classList.add('executing');
    $('#structureHint').textContent='执行 v'+p.version+' · '+p.location.file+':'+p.location.line+' · '+p.kind;
  }
};
openEvents=function(){
  eventSource?.close();
  const eventProjectId=project.id;
  let stream;
  const current=()=>eventSource===stream&&project?.id===eventProjectId;
  stream=window.ChaosRealtime.subscribe({url:'/api/events?projectId='+encodeURIComponent(eventProjectId),after:lastEvent,
    onState(state){
      if(!current())return;
      const connected=state==='connected';
      window.ChaosRuntimeNetwork?.notify({connected,projectId:eventProjectId});
      $('#connection').textContent=connected?'实时已连接':'连接中断 · 正在恢复事件';
      if(connected){window.ChaosRunGuidance?.refreshScopes();refreshRemote().catch(()=>{});}
    },
    async onEvent(event){
      if(!current()||window.ChaosRealtime.compareIds(event.id,lastEvent)<=0)return;
      if(event.type==='run.queued'&&followRuns&&!launching)await fetchRun(event.run_id);
      if(!current())return;
      if(!events.some(item=>String(item.id)===String(event.id)))events.push(event);
      if(events.length>1500)events.shift();
      window.ChaosRuntimeNetwork?.notify(event);
      applyRunEvent(event);window.ChaosRunGuidance?.ingestEvent(event);renderEvents();
      window.ChaosModes?.ingestEvent(event);
      if(event.type.startsWith('node.'))renderGraph();
      if(['graph.saved','graph.revised','program.migrated'].includes(event.type)&&event.payload.version>baseProject.version)await refreshRemote();
      if(!current())return;
      if(event.type.startsWith('run.'))await loadHistory();
      if(current())lastEvent=event.id;
    }
  });
  eventSource=stream;
};
startRun=async function(){
  if(launching||busy)return;
  launching=true;setBusy(true);
  try{
    const committed=await save();
    if(dirty||controlDraft)throw new Error('仍有未提交草稿，运行未启动。');
    const serial=editSerial;
    const input=$('#jsonInput').checked?{inputJson:$('#taskInput').value}:{input:$('#taskInput').value};
    const run=await api('/api/runs',{method:'POST',body:{projectId:committed.id,version:committed.version,...input,mode:'source',trace:expressionTrace.checked,autoOptimize:$('#autoOptimize').checked}});
    followRuns=true;$('#followRuns').checked=true;showRun(run);
    $('#snapshotHint').textContent='执行快照 v'+run.version+(serial!==editSerial?' · 新编辑保留为草稿':'');
    await fetchRun(run.id);await refreshRemote();consoleTab('result');
  }catch(error){setBusy(false);if(error.status===409)await refreshRemote();throw error;}
  finally{launching=false;syncState();}
};
$('#runButton').onclick=guarded(startRun);
loadHistory=async function(){
  const rows=await api('/api/runs?projectId='+project.id);history=rows;
  $('#historyPanel').innerHTML=rows.map(r=>'<div class="history-row"><span>'+new Date(r.created*1000).toLocaleTimeString('zh-CN')+'</span><code>'+esc(r.id.slice(0,8))+'</code><span>v'+r.version+'</span><span>'+esc(stateNames[r.status]||r.status)+'</span><button data-run="'+esc(r.id)+'">查看</button></div>').join('')||'<div class="empty-console">还没有运行记录</div>';
  $$('[data-run]').forEach(el=>el.onclick=guarded(async()=>{
    if(activeRunId)return toast('运行仍在进行，请先等待完成或取消，再切换历史。',true);
    followRuns=false;$('#followRuns').checked=false;await fetchRun(el.dataset.run);consoleTab('result');
  }));
};
$('#followRuns').onchange=guarded(async()=>{followRuns=$('#followRuns').checked;if(followRuns){await loadHistory();if(history[0])await fetchRun(history[0].id);}});
function showSourceSnapshot(snapshot,version){
  const dialog=$('#snapshotDialog'),pre=$('#snapshotSource');
  dialog.querySelector('[data-snapshot-files]')?.remove();
  if(snapshot.execution?.kind==='executable'){
    const execution=snapshot.execution;
    const summary={kind:'executable',imageHash:execution.imageHash,entry:execution.entry,
      authorityProject:snapshot.id,authorityVersion:version,
      modules:(execution.image?.objects||[]).map(item=>({module:item.module,objectHash:item.objectHash,languageVersion:item.languageVersion}))};
    window.ChaosCode.update(pre,JSON.stringify(summary,null,2),{language:'json',label:'编译文件执行快照 · '+execution.entry+' · 宿主项目 v'+version});
    dialog.showModal();return;
  }
  const controls=document.createElement('label'),select=document.createElement('select');
  controls.dataset.snapshotFiles='';controls.className='snapshot-file-controls';
  controls.append(document.createTextNode('执行快照 v'+version+' · 模块 '));
  select.setAttribute('aria-label','执行快照模块');
  const entry=snapshot.entry||'main.chaos',files={...snapshot.files,[entry]:snapshot.source};
  const languageVersions=Object.fromEntries(Object.keys(files).map(file=>[file,snapshot.program?.sourceVersions?.[file]??snapshot.program?.languageVersion??1]));
  for(const file of Object.keys(files).sort()){const option=document.createElement('option');option.value=file;option.textContent=file;option.selected=file===entry;select.append(option);}
  const show=()=>{const file=select.value;window.ChaosCode.update(pre,files[file],{language:'chaos',label:'执行快照 v'+version+' · '+file,file,entry,files,languageVersions});};
  select.onchange=show;controls.append(select);pre.before(controls);show();dialog.showModal();
}
$('#viewSnapshot').onclick=()=>{if(runSnapshot?.execution?.kind==='executable'||runSnapshot?.authoring==='source'){showSourceSnapshot(runSnapshot,runVersion);return;}snapshotView=!snapshotView;$('#viewSnapshot').textContent=snapshotView?'返回编辑版本':'查看执行快照';syncState();renderGraph();renderInspector();if(view==='graph')fit();};
$('#closeSnapshot').onclick=()=>$('#snapshotDialog').close();
$('#palette').addEventListener('click',e=>{if(project.authoring!=='workflow'||snapshotView||view!=='graph'){e.stopImmediatePropagation();toast('请在关系画布编辑工作流；函数程序请使用程序结构。',true);}},true);
window.addEventListener('keydown',e=>{if(snapshotView&&e.key==='Delete')e.stopImmediatePropagation();},true);
async function bootSync(){
  const data=await api('/api/bootstrap?projectId='+encodeURIComponent(projectId));tools=data.tools;
  setProject(data.project);lastEvent=data.eventCursor;await loadHistory();
  if(history[0])await fetchRun(history[0].id);
  openEvents();requestAnimationFrame(fit);
}
bootSync().catch(e=>toast('工作区加载失败：'+e.message,true));

document.addEventListener('pointerdown',e=>{
  if(e.target.closest('#exportButton,#exportConflict,#acceptRemote,#restoreDraft,#importButton,#codeAppearanceDialog,[data-code-appearance]'))return;
  if((pendingFields.has('inspector')&&!e.target.closest('#inspectorContent'))||(pendingFields.size&&!pendingFields.has('inspector')&&![...pendingFields.keys()].some(key=>key===e.target))){
    try{flushControls();}catch(error){e.preventDefault();e.stopImmediatePropagation();toast(error.message,true);}
  }
},true);
const previousEventMessage=eventMessage;
eventMessage=function(event){
  const p=event.payload;
  if(p.nodeId&&!p.error&&!p.reason){const graph=!p.executionKind&&runSnapshot?.execution?.kind!=='executable'&&event.run_id===currentRun?runSnapshot?.graph:null;return (graph?.nodes.find(n=>n.id===p.nodeId)?.label||p.nodeId)+(p.tool?' → '+p.tool:'');}
  return previousEventMessage(event);
};
$('#importFile').onchange=guarded(async e=>{
  const file=e.target.files[0];if(!file)return;
  const generation=editSerial;
  if(file.size>4000000)throw new Error('项目文件超过 4 MB');
  const data=parseJSON(await file.text());
  if(generation!==editSerial)throw new Error('读取文件期间出现新编辑，导入已取消。');
  if(data.draftControls){
    if(!data.files||typeof data.files!=='object'||typeof data.entry!=='string'||typeof data.files[data.entry]!=='string')throw new Error('草稿缺少源码文件或入口');
    if(data.draftProgram)await languageInfo();
    if((data.draftControls.syntaxTextareas?.length||data.draftControls.syntaxAgentInputs?.length)&&typeof loadAgentConfiguration==='function')await loadAgentConfiguration();
    if(generation!==editSerial)throw new Error('读取草稿期间出现新编辑，导入已取消。');
    if((dirty||controlDraft)&&!confirm('导入会替换当前草稿；先自动保留本机备份，是否继续？'))return;
    if(dirty||controlDraft)localStorage.setItem('chaos-draft:'+project.id,stringifyJSON(draftDocument()));
    pendingFields.clear();controlDraft=false;syntaxRequest++;
    project={...project,name:data.name||project.name,graph:data.graph||project.graph,files:clone(data.files),entry:data.entry,source:data.files[data.entry]};
    activeFile=data.draftFile in project.files?data.draftFile:project.entry;
    programDraft=data.draftProgram?clone(data.draftProgram):null;syntaxDirty=!!programDraft;
    view=programDraft?'structure':data.draftEditFrom==='graph'?'graph':'source';
    syntaxSelection=data.draftControls.selection||programDraft?.root?.id;
    $('#sourceEditor').value=project.files[activeFile];lineNumbers();
    $('#moduleSelect').innerHTML=Object.keys(project.files).map(f=>`<option value="${esc(f)}" ${f===activeFile?'selected':''}>${esc(f)}</option>`).join('');
    $('#syntaxTree').replaceChildren();$('#syntaxFields').replaceChildren();
    if(programDraft)renderSyntax();renderGraph();
    markDirty(data.draftEditFrom==='graph'&&!programDraft?'graph':'source');restoreRawControls(data);
    $$('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));$$('.view').forEach(el=>el.classList.toggle('active',el.id===view+'View'));
    document.body.classList.toggle('syntax-view-active',view==='structure');syncState();
    e.target.value='';toast('草稿已恢复；修正后保存即可同步');return;
  }
  if(data.draftProgram){
    const formatted=await api('/api/language/format',{method:'POST',body:{program:data.draftProgram}});
    data.files={...data.files,[data.draftFile||data.entry]:formatted.source};data.source=data.files[data.entry];
  }
  await api('/api/validate',{method:'POST',body:{graph:data.graph,source:data.source,files:data.files,entry:data.entry}});
  if(generation!==editSerial)throw new Error('验证期间出现新编辑，导入已取消。');
  if((dirty||controlDraft)&&!confirm('导入会替换当前草稿；先自动保留本机备份，是否继续？'))return;
  if(dirty||controlDraft)localStorage.setItem('chaos-draft:'+project.id,stringifyJSON(draftDocument()));
  pendingFields.clear();controlDraft=false;syntaxDirty=false;programDraft=null;syntaxRequest++;
  $('#syntaxTree').replaceChildren();$('#syntaxFields').replaceChildren();
  project={...project,graph:data.graph||project.graph,name:data.name||project.name,source:data.source,files:data.files||{[data.entry||'main.chaos']:data.source},entry:data.entry||'main.chaos'};
  activeFile=project.entry;$('#sourceEditor').value=project.source;markDirty(data.authoring==='workflow'&&!data.draftProgram&&data.draftEditFrom!=='source'?'graph':'source');
  await save();restoreRawControls(data);e.target.value='';toast('项目已导入并保存');
});

function restoreRawControls(data){
  const raw=data.draftControls;if(!data.draftPending||!raw)return;
  if(data.draftProgram){
    activeFile=data.draftFile||project.entry;programDraft=clone(data.draftProgram);syntaxSelection=raw.selection||programDraft.root.id;
    view='structure';renderSyntax();
    // Keep the original generic input order across schema/registry changes; projection fields use labels.
    const inputs=[...$('#syntaxFields').querySelectorAll('input')].filter(el=>!el.closest('.agent-config-editor'));
    (raw.syntax||[]).forEach((value,i)=>{if(inputs[i]){inputs[i].value=value;inputs[i].dispatchEvent(new Event('input',{bubbles:true}));}});
    const agentInputs=[...$('#syntaxFields').querySelectorAll('.agent-config-editor input')];
    for(const saved of raw.syntaxAgentInputs||[]){const input=agentInputs.find(el=>el.getAttribute('aria-label')===saved.label);if(input){input.value=saved.value;if(input.type==='checkbox')input.checked=!!saved.checked;input.dispatchEvent(new Event('input',{bubbles:true}));}}
    const textareas=[...$('#syntaxFields').querySelectorAll('textarea')];
    for(const saved of raw.syntaxTextareas||[]){const input=textareas.find(el=>el.getAttribute('aria-label')===saved.label);if(input){input.value=saved.value;input.dispatchEvent(new Event('input',{bubbles:true}));}}
  }else if(raw.nodeId||raw.edgeId){
    selected=raw.nodeId;selectedEdge=raw.edgeId;view='graph';renderInspector();
    for(const saved of raw.inspector||[]){
      const el=[...$('#inspectorContent').querySelectorAll('[data-field],[data-tool]')].find(e=>saved.field?e.dataset.field===saved.field:e.dataset.tool===saved.tool);
      if(el&&!el.readOnly){el.value=saved.value;el.checked=saved.checked;el.dispatchEvent(new Event('input',{bubbles:true}));}
    }
  }
  $$('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));$$('.view').forEach(el=>el.classList.toggle('active',el.id===view+'View'));
  document.body.classList.toggle('syntax-view-active',view==='structure');syncState();
}
