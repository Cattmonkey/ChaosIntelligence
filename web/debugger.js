'use strict';
// A debugger owns a fixed saved run. Viewing a pause never changes editor drafts.
(()=>{
  const $d=id=>document.getElementById(id), panel=$d('debugPanel');
  if(!panel)return;
  const state={runId:null,run:null,data:null,frame:0,timer:null,generation:0,busy:false,attempted:null,error:null,recoveryTicket:null,selection:null,requests:0,applied:0,cancelRequested:false};
  const names={running:'运行中',paused:'已暂停',completed:'执行完成',failed:'执行失败',cancelled:'已取消',interrupted:'服务重启中断'};
  function status(text,error=false){$d('debugStatus').textContent=text;$d('debugStatus').classList.toggle('error',error);}
  function points(text){return text.split(/[\n,]/).map(s=>s.trim()).filter(Boolean).map(item=>{const match=/^(.+):(\d+)$/.exec(item);if(!match)throw new Error('断点格式为 项目文件:行号，每行一个，例如 main.chaos:3');return {file:match[1],line:Number(match[2])};});}
  function node(tag,text,cls){const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;}
  function ticket(){return {runId:state.runId,generation:state.generation,pauseId:state.data?.pauseId,status:state.data?.status};}
  function currentTicket(value){return !!value&&value.runId===state.runId&&value.generation===state.generation&&value.pauseId===state.data?.pauseId&&value.status===state.data?.status;}
  function accept(data,request){
    if(!currentRequest(request)||request.order<state.applied||data?.runId!==request.runId||!Number.isSafeInteger(data.pauseId)||data.pauseId<(state.data?.pauseId??0))return false;
    if(state.data&&['completed','failed','cancelled','interrupted'].includes(state.data.status)&&['running','paused'].includes(data.status))return false;
    state.applied=request.order;state.data=data;return true;
  }
  function requestTicket(){return {...ticket(),order:++state.requests};}
  function currentRequest(value){return value.runId===state.runId&&value.generation===state.generation;}
  function recovery(value=state.data){return value?.status==='paused'&&value.reason==='condition'&&value.conditionRecovery&&typeof value.conditionRecovery==='object'?value.conditionRecovery:null;}
  function strictJSON(text,kind){
    if(text.length>250000)throw new Error('恢复参数 JSON 超过 250000 字符');
    const lossless=typeof parseJSON==='function'&&typeof ExactNumber==='function',value=lossless?parseJSON(text):JSON.parse(text),record=item=>item!==null&&typeof item==='object'&&!Array.isArray(item)&&(Object.getPrototypeOf(item)===null||Object.getPrototypeOf(item)===Object.prototype);
    if(kind==='args'?!Array.isArray(value):!record(value))throw new Error(kind==='args'?'args 必须是 JSON 数组，例如 [42]':'kwargs 必须是 JSON 对象，例如 {}');
    const pending=[[value,0]];let nodes=0;
    while(pending.length){
      const [item,depth]=pending.pop();if(++nodes>20000||depth>64)throw new Error('恢复参数 JSON 超过 20000 节点或 64 层');
      // The shared codec keeps number tokens as immutable leaves. Integer
      // tokens stay exact through stringifyJSON; the backend enforces bit bounds.
      if(lossless&&item instanceof ExactNumber){if(/[.eE]/.test(item.raw)&&!Number.isFinite(Number(item.raw)))throw new Error('恢复参数数字必须有限');continue;}
      if(typeof item==='number'){
        if(!Number.isFinite(item))throw new Error('恢复参数数字必须有限');
        if(!lossless&&Number.isInteger(item)&&!Number.isSafeInteger(item))throw new Error('缺少精确 JSON 编码器，无法安全传递此整数');
      }
      if(item&&typeof item==='object'){const entries=Array.isArray(item)?item:Object.values(item);if(entries.length>10000)throw new Error('恢复参数容器超过 10000 项');for(const child of entries)pending.push([child,depth+1]);}
    }
    return value;
  }
  function renderRecovery(){
    const section=$d('debugRecovery'),summary=$d('debugRecoverySummary'),choices=$d('debugRestarts'),submit=$d('debugRestart');if(!section||!summary||!choices||!submit)return;
    const info=recovery(),now=ticket();section.hidden=!info;
    if(!currentTicket(state.recoveryTicket)){state.recoveryTicket=now;state.selection=null;$d('debugRestartArgs').value='[]';$d('debugRestartKwargs').value='{}';}
    if(!info){choices.replaceChildren();submit.disabled=true;return;}
    const condition=info.condition||{};$d('debugConditionKind').textContent=String(condition.kind??'Condition');$d('debugConditionMessage').textContent=String(condition.message??'');$d('debugConditionData').textContent=stringifyJSON(condition.data??null);
    const items=Array.isArray(info.restarts)?info.restarts:[];
    summary.textContent='暂停 #'+now.pauseId+' · '+items.length+' / '+(info.total??items.length)+' 个恢复出口'+(info.truncated?' · 列表已截断':'')+' · 条件数据为有界只读预览';
    choices.replaceChildren();
    for(const item of items){
      if(!item||typeof item.id!=='string')continue;
      const button=node('button','','debug-restart');button.type='button';button.dataset.restartId=item.id;button.disabled=state.busy||state.cancelRequested||item.active!==true||!state.data.commands?.includes('restart');button.classList.toggle('selected',state.selection?.restartId===item.id);button.setAttribute('aria-pressed',String(state.selection?.restartId===item.id));
      button.append(node('strong',item.name===null?'匿名恢复出口':String(item.name??item.id)),node('span',String(item.report??'')),node('code',String(item.mode??'')+' · '+item.id));
      const choice={...now,restartId:item.id};button.onclick=()=>{if(state.busy||state.cancelRequested||!currentTicket(choice)||!recovery()?.restarts?.some(row=>row.id===choice.restartId&&row.active===true))return;state.selection=choice;state.error=null;render();};choices.append(button);
    }
    if(!items.length)choices.append(node('p','此处没有可选恢复出口。继续将拒绝恢复；也可以停止本次执行。','debug-empty'));
    const choice=state.selection;submit.disabled=state.busy||state.cancelRequested||!currentTicket(choice)||!items.some(row=>row.id===choice.restartId&&row.active===true)||!state.data.commands?.includes('restart');submit.onclick=()=>command('restart',choice);
    $d('debugRecoveryForm').hidden=!items.length;
  }
  function renderGroups(){
    const container=$d('debugGroups'),summary=$d('debugGroupSummary');
    if(!container||!summary)return;
    container.replaceChildren();
    const data=state.data,snapshot=data?.taskSnapshot;
    if(data?.status!=='paused'){
      summary.textContent=data&&['completed','failed','cancelled','interrupted'].includes(data.status)?'调试已结束；没有活动任务组快照。':'暂停时显示本解释器的任务组快照。';return;
    }
    const groups=snapshot?.groups||[],activeId=data.frames[state.frame]?.taskId;
    const activeTask=snapshot?.tasks?.find(task=>task.id===activeId);
    summary.textContent='暂停 #'+data.pauseId+' · 显示 '+groups.length+' / '+(snapshot?.groupTotal??0)+' 个任务组'+(snapshot?.groupsTruncated?' · 列表已截断':'');
    for(const group of groups){
      const row=node('article','','debug-task debug-group');row.dataset.groupId=group.id;
      const selected=activeTask?.groupId===group.id||activeId===group.bodyTaskId;
      row.classList.toggle('selected',selected);row.dataset.activeFrame=String(selected);
      row.append(node('strong',group.id+(selected?' · 当前调用帧所属组':''),'debug-task-id'),node('span','TaskGroup · '+group.state,'debug-task-state'));
      row.append(node('span','所有者：'+(group.ownerTaskId??'模块')+' · 作用域任务：'+(group.bodyTaskId??'尚未进入'),'debug-task-parent'));
      row.append(node('span','父组：'+(group.parentGroupId??'无'),'debug-task-parent'));
      row.append(node('code','成员（'+group.memberCount+'）：'+((group.memberTaskIds||[]).join(', ')||'无')+(group.membersTruncated?' · 仅显示前 '+group.memberTaskIds.length+' 项':''),'debug-task-members'));
      const frame=data.frames.findIndex(item=>item.taskId===group.bodyTaskId);
      if(frame>=0){const link=node('button','查看作用域调用帧','debug-task-id');link.type='button';link.onclick=()=>{state.frame=frame;render();};row.append(link);}
      const location=group.location||{};if(location.file)row.append(node('small',location.file+':'+(location.line??'')+':'+(location.column??'')));
      container.append(row);
    }
    if(!groups.length)container.append(node('p','此暂停点没有已创建的 TaskGroup。','debug-empty'));
  }
  function renderChannels(){
    const container=$d('debugChannels'),summary=$d('debugChannelSummary');
    if(!container||!summary)return;
    container.replaceChildren();
    const data=state.data,snapshot=data?.taskSnapshot;
    if(data?.status!=='paused'){
      summary.textContent=data&&['completed','failed','cancelled','interrupted'].includes(data.status)?'调试已结束；没有活动暂停快照。':'暂停时显示本解释器的通道快照。';return;
    }
    const channels=snapshot?.channels||[],activeId=data.frames[state.frame]?.taskId;
    const activeTask=(snapshot?.tasks||[]).find(task=>task.id===activeId);
    summary.textContent='暂停 #'+data.pauseId+' · 显示 '+channels.length+' / '+(snapshot?.channelTotal??0)+' 个通道'+(snapshot?.channelsTruncated?' · 列表已截断':'');
    for(const channel of channels){
      const row=node('article','','debug-task debug-channel');row.dataset.channelId=channel.id;
      const selected=activeTask?.channelId===channel.id || (activeTask?.selection?.cases||[]).some(item=>item.channelId===channel.id);
      row.classList.toggle('selected',selected);row.dataset.activeFrame=String(selected);
      row.append(node('strong',channel.id+(selected?' · 当前调用帧通道':''),'debug-task-id'),node('span','Channel · '+channel.state,'debug-task-state'));
      row.append(node('span','容量：'+channel.capacity+' · 已缓冲：'+channel.buffered,'debug-channel-counts'));
      for(const [label,count,ids,truncated] of [['发送',channel.waitingSenders,channel.sendTaskIds||[],channel.sendersTruncated],['接收',channel.waitingReceivers,channel.receiveTaskIds||[],channel.receiversTruncated]]){
        row.append(node('code','等待'+label+'（'+count+'）：'+ids.join(', ')+(truncated?' · 仅显示前 '+ids.length+' 项':''),'debug-task-members'));
      }
      const location=channel.location||{};
      if(location.file)row.append(node('small',location.file+':'+(location.line??'')+':'+(location.column??'')));
      container.append(row);
    }
    if(!channels.length)container.append(node('p','此暂停点没有已创建的 Channel。','debug-empty'));
  }
  function renderShields(){
    const container=$d('debugShields'),summary=$d('debugShieldSummary');
    if(!container||!summary)return;
    container.replaceChildren();
    const data=state.data,snapshot=data?.taskSnapshot;
    if(data?.status!=='paused'){
      summary.textContent=data&&['completed','failed','cancelled','interrupted'].includes(data.status)?'调试已结束；没有活动暂停快照。':'暂停时显示本解释器的取消屏蔽快照。';return;
    }
    const shields=snapshot?.shields||[],activeId=data.frames[state.frame]?.taskId;
    summary.textContent='暂停 #'+data.pauseId+' · 显示 '+shields.length+' / '+(snapshot?.shieldTotal??0)+' 个取消屏蔽'+(snapshot?.shieldsTruncated?' · 列表已截断':'');
    for(const shield of shields){
      const row=node('article','','debug-task debug-shield');row.dataset.shieldId=shield.id;
      const selected=shield.taskId!==null&&shield.taskId!==undefined&&shield.taskId===activeId;
      row.classList.toggle('selected',selected);row.dataset.activeFrame=String(selected);
      row.append(node('strong',shield.id+(selected?' · 当前调用帧所属任务':''),'debug-task-id'),node('span','CancelShield · '+shield.state,'debug-task-state'));
      row.append(node('span','任务：'+(shield.taskId??'尚未进入')+' · 活动深度：'+shield.depth,'debug-task-parent'));
      row.append(node('small','取消请求：'+(shield.cancellationRequested?'是':'否')+' · 取消延期：'+(shield.cancellationDeferred?'是':'否')));
      const frame=data.frames.findIndex(item=>item.taskId!==null&&item.taskId!==undefined&&item.taskId===shield.taskId);
      if(frame>=0){const link=node('button','查看所属任务调用帧','debug-task-id');link.type='button';link.onclick=()=>{state.frame=frame;render();};row.append(link);}
      const location=shield.location||{};if(location.file)row.append(node('small',location.file+':'+(location.line??'')+':'+(location.column??'')));
      container.append(row);
    }
    if(!shields.length)container.append(node('p','此暂停点没有已创建的 CancelShield。','debug-empty'));
  }
  function renderTasks(){
    renderGroups();
    renderChannels();
    renderShields();
    const container=$d('debugTasks'),summary=$d('debugTaskSummary');
    if(!container||!summary)return;
    container.replaceChildren();
    const data=state.data,snapshot=data?.taskSnapshot;
    if(data?.status!=='paused'){
      summary.textContent=data&&['completed','failed','cancelled','interrupted'].includes(data.status)?'调试已结束；没有活动暂停快照。':'暂停时显示本解释器的任务快照。';return;
    }
    const tasks=snapshot?.tasks||[],activeId=data.frames[state.frame]?.taskId;
    summary.textContent='暂停 #'+data.pauseId+' · 显示 '+tasks.length+' / '+(snapshot?.total??0)+' 个任务'+(snapshot?.truncated?' · 列表已截断':'');
    for(const task of tasks){
      const row=node('article','','debug-task');row.dataset.taskId=task.id;
      row.classList.toggle('selected',task.id===activeId);row.dataset.activeFrame=String(task.id===activeId);
      const frame=data.frames.findIndex(item=>item.taskId===task.id);
      const heading=node(frame>=0?'button':'strong',task.id+(task.id===activeId?' · 当前调用帧':''),'debug-task-id');
      if(frame>=0){heading.type='button';heading.setAttribute('aria-label','查看任务 '+task.id+' 的调用帧');heading.onclick=()=>{state.frame=frame;render();};}
      row.append(heading,node('span',task.kind+' · '+task.state+' / '+task.phase,'debug-task-state'));
      row.append(node('span','创建者：'+(task.parentTaskId??'模块'),'debug-task-parent'));
      if(task.groupId)row.append(node('span','所属组：'+task.groupId,'debug-task-group'));
      if(task.channelId)row.append(node('span','通道：'+task.channelId,'debug-task-channel'));
      if(task.timing && typeof task.timing==='object'){
        const timing=task.timing;
        if(timing.seconds!==null && timing.seconds!==undefined)row.append(node('span','计时：'+timing.seconds+' 秒','debug-task-timing'));
        if(timing.until!==null && timing.until!==undefined)row.append(node('span','等待条件：'+timing.until+' · 超时：'+(timing.timeoutSeconds===null?'无':timing.timeoutSeconds+' 秒'),'debug-task-timing'));
        else if(timing.timeoutSeconds!==null && timing.timeoutSeconds!==undefined)row.append(node('span','选择超时：'+timing.timeoutSeconds+' 秒','debug-task-timing'));
        row.append(node('span','暂停时剩余：'+(timing.remainingSeconds===null?'未计时 / 已结束 / 无期限':timing.remainingSeconds+' 秒'),'debug-task-timing'));
      }

      if(task.selection && typeof task.selection==='object'){
        const selection=task.selection,cases=selection.cases||[];
        row.append(node('span','通信候选（'+selection.caseCount+'）'+(selection.casesTruncated?' · 仅显示前 '+cases.length+' 项':''),'debug-task-selection'));
        for(const item of cases)row.append(node('code','#'+item.index+' · '+item.op+' · '+item.channelId,'debug-task-case'));
        row.append(node('span',selection.index===null?'未提交候选':'已提交候选：#'+selection.index,'debug-task-selection'));
      }

      if(task.kind==='gather'||task.memberCount){
        row.append(node('code','成员（'+task.memberCount+'）：'+(task.memberTaskIds||[]).join(', ')+(task.membersTruncated?' · 仅显示前 '+task.memberTaskIds.length+' 项':''),'debug-task-members'));
      }
      row.append(node('small','取消请求：'+(task.cancellationRequested?'是':'否')+' · 取消延期：'+(task.cancellationDeferred?'是':'否')+' · 屏蔽深度：'+(task.shieldDepth??0)+' · 已观察：'+(task.observed?'是':'否')));
      const location=task.location||{};
      if(location.file)row.append(node('small',location.file+':'+(location.line??'')+':'+(location.column??'')));
      container.append(row);
    }
    if(!tasks.length)container.append(node('p','此暂停点没有已创建的 Task。','debug-empty'));
  }
  function renderFrame(){
    const frame=state.data?.frames[state.frame];$d('debugLocals').replaceChildren();
    if(window.ChaosCode?.clear)window.ChaosCode.clear($d('debugSource'));else $d('debugSource').replaceChildren();
    if(!frame){$d('debugLocals').append(node('p','暂停后显示当前作用域的值。','debug-empty'));return;}
    for(const row of frame.locals){const line=node('div','','debug-local');line.append(node('b',row.name),node('small',row.type),node('code',stringifyJSON(row.value)));$d('debugLocals').append(line);}
    const location=frame.location||state.data.location,file=location?.file,generated=state.data?.generatedSources?.[file],source=generated?.source??state.run?.snapshot?.files?.[file];
    const context=generated?Object.values(state.data.generatedSourceContexts||{}).find(value=>Object.values(value.virtualFiles).includes(file)):null;
    const logicalFile=context?Object.keys(context.virtualFiles).find(name=>context.virtualFiles[name]===file):file;
    if(typeof source==='string'){
      const snapshot=context?{files:Object.fromEntries(Object.entries(context.virtualFiles).map(([name,id])=>[name,state.data.generatedSources[id]?.source])),entry:context.entry,program:{sourceVersions:context.languageVersions}}:generated?{files:{[file]:source},entry:file,program:{languageVersion:generated.languageVersion}}:state.run.snapshot;
      const languageVersions=Object.fromEntries(Object.keys(snapshot.files).map(name=>[name,snapshot.program?.sourceVersions?.[name]??snapshot.program?.languageVersion??1]));
      if(window.ChaosCode)window.ChaosCode.update($d('debugSource'),source,{language:'chaos',label:logicalFile,file:logicalFile,entry:snapshot.entry,files:snapshot.files,languageVersions,activeLine:location.line});
      else source.split('\n').forEach((text,index)=>{const line=node('div',String(index+1).padStart(4)+'  '+text,index+1===location.line?'debug-source-line current':'debug-source-line');$d('debugSource').append(line);});
      $d('debugSource').querySelector('.current')?.scrollIntoView({block:'nearest'});
    }
    $d('debugSourceLabel').textContent=(generated?'只读生成源码':'只读执行快照 v'+state.data.version)+' · '+(logicalFile||'未知文件');
    $d('debugSourceLabel').title=file||'';
  }
  function render(){
    const data=state.data,active=data&&['running','paused'].includes(data.status);
    $d('debugStart').disabled=state.busy||!!active||typeof busy!=='undefined'&&busy;
    panel.querySelectorAll('[data-debug-command]').forEach(el=>el.disabled=state.busy||!data?.commands.includes(el.dataset.debugCommand));
    $d('debugUpdateBreakpoints').disabled=state.busy||!active;
    renderRecovery();
    if(!data){renderTasks();return;}
    if(state.error){status(state.error,true);return;}
    const loc=data.location;status((names[data.status]||data.status)+' · 固定 v'+data.version+(loc?' · '+loc.file+':'+loc.line:'')+(data.error?' · '+data.error:''),data.status==='failed');
    $d('debugFrames').replaceChildren();
    data.frames.forEach((frame,index)=>{const button=node('button',frame.name+(frame.taskId?' · '+frame.taskId:'')+'\n'+(frame.location.file||'')+':'+(frame.location.line||''));button.type='button';button.classList.toggle('selected',index===state.frame);button.setAttribute('aria-pressed',String(index===state.frame));button.onclick=()=>{state.frame=index;render();};$d('debugFrames').append(button);});
    if(!data.frames.length)$d('debugFrames').append(node('p',active?'等待下一个暂停点…':'本次调试已结束。','debug-empty'));
    renderFrame();
    renderTasks();
  }
  async function poll(generation=state.generation){
    if(!state.runId||generation!==state.generation)return;
    const request=requestTicket();
    try{
      const data=await api('/api/runs/'+encodeURIComponent(request.runId)+'/debugger');
      if(generation!==state.generation||!currentRequest(request))return;
      const changed=data.pauseId!==state.data?.pauseId||data.status!==state.data?.status;
      if(!accept(data,request))return;
      if(changed){state.frame=0;state.error=null;render();}
      if(['running','paused'].includes(state.data?.status)){state.timer=setTimeout(()=>poll(generation),300);}
      else if(currentRun===state.runId)await fetchRun(state.runId);
    }catch(error){if(currentRequest(request)&&currentTicket(request)&&request.order>=state.applied){status('调试连接中断：'+error.message,true);state.timer=setTimeout(()=>poll(generation),1500);}}
  }
  async function attach(run,explicit=false){
    if(!run?.id||run.id===state.runId||run.id===state.attempted&&!explicit)return;
    state.attempted=run.id;
    try{
      const data=await api('/api/runs/'+encodeURIComponent(run.id)+'/debugger');
      // showRun and an explicit start may attach concurrently. Once one owns
      // this run, the other response cannot reset its generation or pause.
      if(run.id===state.runId||currentRun!==run.id)return;
      clearTimeout(state.timer);state.generation++;state.runId=run.id;state.run=run;state.data=data;state.frame=0;state.busy=false;state.error=null;state.recoveryTicket=null;state.selection=null;state.requests=0;state.applied=0;state.cancelRequested=false;
      $d('debugBreakpoints').value=data.breakpoints.map(p=>p.file+':'+p.line).join('\n');panel.hidden=false;$d('debugToggle').setAttribute('aria-expanded','true');render();poll();
    }catch(error){if(explicit)status(error.message,true);}
  }
  async function command(command,choice=null){
    if(state.busy||!state.data)return;
    const request=requestTicket(),current=()=>currentRequest(request);
    let extra={};
    try{
      if(command==='restart'){
        if(state.cancelRequested||!currentTicket(choice)||!recovery()?.restarts?.some(item=>item.id===choice.restartId&&item.active===true)||!state.data.commands?.includes('restart'))return;
        extra={restartId:choice.restartId,args:strictJSON($d('debugRestartArgs').value,'args'),kwargs:strictJSON($d('debugRestartKwargs').value,'kwargs')};
      }else if(command==='breakpoints')extra={breakpoints:points($d('debugBreakpoints').value)};
    }catch(error){state.error=error.message;render();return;}
    state.busy=true;state.error=null;if(command==='cancel')state.cancelRequested=true;render();
    try{
      const data=await api('/api/runs/'+encodeURIComponent(request.runId)+'/debugger',{method:'POST',body:{command,pauseId:request.pauseId,...extra}});
      if(!current())return;
      // A newer poll may already own a later pause or cancellation. The old
      // command must never paint that snapshot or a new recovery selection.
      if(currentTicket(request)&&accept(data,request))state.frame=0;
    }catch(error){if(current()&&currentTicket(request)){if(command==='cancel')state.cancelRequested=false;state.error=error.message;status(error.message,true);}return;}
    finally{if(current()){state.busy=false;render();}}
    if(current()){clearTimeout(state.timer);await poll(request.generation);}
  }
  async function start(){
    if(state.busy||launching||busy)return;
    const breakpoints=points($d('debugBreakpoints').value);state.busy=true;state.error=null;launching=true;render();
    try{
      const committed=await save();if(dirty||controlDraft)throw new Error('仍有未提交草稿，调试未启动。');
      const input=$('#jsonInput').checked?{inputJson:$('#taskInput').value}:{input:$('#taskInput').value};
      const run=await api('/api/runs',{method:'POST',body:{projectId:committed.id,version:committed.version,...input,mode:'source',autoOptimize:false,debug:{breakpoints,pauseOnEntry:$d('debugPauseEntry').checked,...$d('debugPauseCondition')?.checked===true?{pauseOnCondition:true}:{}}}});
      followRuns=true;$('#followRuns').checked=true;showRun(run);await attach(run,true);await fetchRun(run.id);
    }catch(error){state.error=error.message;status(error.message,true);toast(error.message,true);}
    finally{state.busy=false;launching=false;render();}
  }
  $d('debugToggle').onclick=()=>{panel.hidden=!panel.hidden;$d('debugToggle').setAttribute('aria-expanded',String(!panel.hidden));render();};
  $d('debugStart').onclick=()=>start().catch(error=>status(error.message,true));
  $d('debugUpdateBreakpoints').onclick=()=>command('breakpoints');
  panel.querySelectorAll('[data-debug-command]').forEach(el=>el.onclick=()=>command(el.dataset.debugCommand));
  // Preserve the existing run selection and SSE flow; recover a paused session after reload.
  const originalShowRun=showRun;showRun=function(run){originalShowRun(run);render();attach(run).catch(()=>{});};
  if(currentRun)api('/api/runs/'+encodeURIComponent(currentRun)).then(run=>attach(run)).catch(()=>{});
  window.addEventListener('pagehide',()=>{clearTimeout(state.timer);state.generation++;});
  window.ChaosDebugger={start,command,parseBreakpoints:points,get state(){return {runId:state.runId,data:state.data};}};
})();
