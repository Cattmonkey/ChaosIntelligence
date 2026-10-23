'use strict';
// Mode revisions and source-project versions are separate CAS domains.
(()=>{
  const encode=(value,space)=>typeof stringifyJSON==='function'?stringifyJSON(value,null,space):JSON.stringify(value,null,space),decode=text=>typeof parseJSON==='function'?parseJSON(text):JSON.parse(text);
  const copy=value=>decode(encode(value));
  const exactNumber=value=>typeof ExactNumber!=='undefined'&&value instanceof ExactNumber;
  const contracts={
    policy(a){const blocked=a?.kind==='skill'&&a.status!=='ready',captured=a?.kind==='skill'&&a.checkpoint!=null;return {blocked,captured,readonly:blocked||captured||(a?.kind==='goal'&&a.checkpoint!=null)};},
    initial(kind){return kind==='plan'?{kind,name:'新计划',steps:[{id:'step1',call:'action',needs:[]}]}:kind==='goal'?{kind,objective:'',planner:'planner',verify:'verify',max_iterations:8}:{kind:'skill',actions:[{tool:'text.normalize',input:''}],parameters:[]};},
    planConcurrency(def,value){const n=Number(value);if(typeof value==='boolean'||!String(value).trim()||!Number.isInteger(n)||n<1||n>128)throw new Error('最大并发步骤必须是 1–128 的整数。');if(n===1)delete def.max_concurrency;else def.max_concurrency=n;return n;},
    planEvent(executions,event){
      const payload=event.payload||{},match=/^mode\.plan\.(started|completed|failed|cancelled|step\.(started|completed|failed|cancelled))$/.exec(event.type||'');
      if(!match||typeof payload.executionId!=='string'||!payload.executionId||typeof event.run_id!=='string')return false;
      const key=event.run_id+':'+payload.executionId,sequence=Number.isSafeInteger(event.id)?event.id:0;
      let row=executions.get(key);if(row&&sequence&&sequence<=row.lastEventId)return false;
      if(!row){row={key,runId:event.run_id,executionId:payload.executionId,name:payload.name||'计划',definitionHash:payload.definitionHash||'',maxConcurrency:payload.maxConcurrency||1,status:'running',steps:new Map(),totalSteps:null,lastEventId:0};executions.set(key,row);}
      row.lastEventId=Math.max(sequence,row.lastEventId);
      if(match[1]==='started'){if(Number.isInteger(payload.steps))row.totalSteps=payload.steps;}
      else if(match[1].startsWith('step.')){
        if(typeof payload.stepId!=='string'||row.steps.size>=128&&!row.steps.has(payload.stepId))return false;
        const previous=row.steps.get(payload.stepId),status=match[1].slice(5)==='started'?'running':match[1].slice(5);
        if(!previous||previous.status==='running'||status!=='running')row.steps.set(payload.stepId,{id:payload.stepId,call:payload.call||previous?.call||'',taskId:payload.stepTaskId||previous?.taskId||null,status,errorCode:payload.errorCode||null});
      }else row.status=match[1];
      while(executions.size>32){const oldest=[...executions.keys()].find(id=>executions.get(id).status!=='running')||executions.keys().next().value;executions.delete(oldest);}
      return true;
    },
    finishRun(executions,event){if(!/^run\.(completed|failed|cancelled|interrupted)$/.test(event.type||''))return false;let changed=false;for(const row of executions.values())if(row.runId===event.run_id&&row.status==='running'){row.status=event.type==='run.completed'?'interrupted':event.type.slice(4);for(const step of row.steps.values())if(step.status==='running')step.status=row.status;changed=true;}return changed;},
    changeKey(draft,key){if(draft.baseRevision!==null||draft.saving)return false;draft.key=key;draft.remote=null;return true;},
    renameStep(def,index,name){const previous=def.steps[index].id;def.steps[index].id=name;for(const step of def.steps)step.needs=step.needs.map(id=>id===previous?name:id);},
    removeStep(def,index){const id=def.steps[index].id;if(def.steps.some((s,i)=>i!==index&&s.needs.includes(id)))throw new Error('请先移除此步骤的依赖引用。');def.steps.splice(index,1);},
    moveAction(def,index,to){if(to<0||to>=def.actions.length)return;[def.actions[index],def.actions[to]]=[def.actions[to],def.actions[index]];for(const p of def.parameters){if(p.step===index)p.step=to;else if(p.step===to)p.step=index;}},
    removeAction(def,index){if(def.parameters.some(p=>p.step===index))throw new Error('请先移除此动作的参数绑定。');def.actions.splice(index,1);for(const p of def.parameters)if(p.step>index)p.step--;},
    paths(value){const rows=[],walk=(v,path)=>{if(rows.length>=500)return;rows.push(path);if(path.length>=64)return;if(v&&typeof v==='object'&&!exactNumber(v))for(const key of Object.keys(v)){walk(v[key],[...path,Array.isArray(v)?Number(key):key]);if(rows.length>=500)break;}};walk(value,[]);return rows;},
    committed(draft,artifact,serial){draft.baseRevision=artifact.revision;draft.baseDefinition=copy(artifact.definition);if(!draft.remote||draft.remote.revision<=artifact.revision)draft.remote=null;if(draft.serial===serial){draft.definition=copy(artifact.definition);draft.dirty=false;}},
    remote(draft,artifact){if(artifact.revision>Math.max(draft.baseRevision??0,draft.remote?.revision??0))draft.remote=copy(artifact);}
  };
  window.ChaosModeContracts=contracts;
  const panel=document.getElementById('modesPanel');if(!panel)return;
  const state={project:null,kind:'',items:[],cursor:null,key:null,detail:null,viewRevision:null,generation:0,listRequest:0,detailRequest:0,timer:null,closed:false,drafts:new Map(),draft:null,draftSequence:0,executions:new Map()};
  const kinds={goal:'目标',plan:'计划',skill:'技能'};
  const statuses={running:'执行中',ready:'可执行',completed:'已完成',done:'验收完成',exhausted:'迭代额度已用完',needs_reconciliation:'中断 · 待核对效果',interrupted:'已中断',incomplete:'录制未完成',failed:'失败',cancelled:'已取消',saved:'已保存',pending:'进行中'};
  function el(tag,text='',cls=''){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
  function button(text,fn,cls=''){const n=el('button',text,cls);n.type='button';n.onclick=fn;return n;}
  function currentProject(){try{return typeof project!=='undefined'&&project?.id||new URL(location.href).searchParams.get('project')||'workspace';}catch{return 'workspace';}}
  async function request(path,options={}){if(typeof api==='function')return api(path,options);const r=await fetch(path,{...options,headers:{'Content-Type':'application/json'},body:options.body===undefined?undefined:encode(options.body)});const data=decode(await r.text());if(!r.ok){const error=new Error(data.error?.message||data.error||'HTTP '+r.status);error.status=r.status;throw error;}return data;}
  function base(projectId=state.project){return '/api/v1/projects/'+encodeURIComponent(projectId)+'/modes';}
  function label(status){return statuses[status]||status||'已保存';}
  function note(text,error=false){status.textContent=text;status.classList.toggle('error',error);}
  function compact(value){if(value===null)return 'null';if(exactNumber(value))return String(value);if(typeof value==='string')return value.length>500?value.slice(0,500)+'…':value;if(typeof value==='object')return Array.isArray(value)?'列表 · '+value.length+' 项':'记录 · '+Object.keys(value).length+' 个字段';return String(value);}
  function fields(value,depth=0){
    if(value===null||typeof value!=='object'||exactNumber(value))return el('p',compact(value),'mode-value');
    const list=el('dl','','mode-fields'),entries=Object.entries(value);for(const [key,item]of entries.slice(0,12)){list.append(el('dt',key));const cell=el('dd');cell.append(depth<1&&item!==null&&typeof item==='object'?fields(item,depth+1):el('span',compact(item)));list.append(cell);}if(entries.length>12)list.append(el('dd','另有 '+(entries.length-12)+' 个字段'));return list;
  }
  const header=el('div','','modes-header'),title=el('div');title.append(el('h2','目标、计划与技能'),el('p','已保存的语言对象 · 当前项目'));
  const body=el('div','','modes-body');body.id='modesBody';body.hidden=true;
  const toggle=button('展开',()=>{body.hidden=!body.hidden;if(!body.hidden)replayExecutions();toggle.textContent=body.hidden?'展开':'收起';toggle.setAttribute('aria-expanded',String(!body.hidden));panel.classList.toggle('is-expanded',!body.hidden);},'modes-toggle');
  toggle.setAttribute('aria-controls',body.id);toggle.setAttribute('aria-expanded','false');
  const refreshButton=button('刷新',()=>refresh().catch(()=>{})),actions=el('div','','modes-header-actions');actions.append(refreshButton,toggle);header.append(title,actions);
  const filters=el('div','','modes-filters');filters.setAttribute('aria-label','对象类型');
  for(const [kind,name]of [['','全部'],...Object.entries(kinds)]){const b=button(name,()=>{if(state.kind===kind)return;state.kind=kind;state.key=null;state.detail=null;refresh().catch(()=>{});});b.dataset.kind=kind;filters.append(b);}
  const status=el('p','载入已保存对象…','modes-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const layout=el('div','','modes-layout'),list=el('div','','modes-list'),detail=el('div','','modes-detail');list.setAttribute('aria-label','已保存对象');detail.setAttribute('aria-label','对象详情');
  const executions=el('section','','mode-executions');executions.setAttribute('aria-label','计划执行');executions.hidden=true;
  const more=button('载入更多',()=>refresh(true).catch(()=>{}),'modes-more');more.hidden=true;layout.append(list,detail);body.append(executions,filters,status,layout,more);panel.replaceChildren(header,body);
  const creation=el('div','','mode-toolbar');for(const [kind,name]of Object.entries(kinds))creation.append(button('新建'+name,()=>newDraft(kind)));const draftsButton=button('恢复草稿',showDrafts);creation.append(draftsButton);body.insertBefore(creation,filters);
  function renderExecutions(){
    const rows=[...state.executions.values()].reverse();executions.hidden=!rows.length;executions.replaceChildren();if(!rows.length)return;
    executions.append(el('h3','计划执行'),el('p','最近观察到的调用 · 每次调用独立显示，最多保留 32 次 · 保存的定义不会随执行改变。','mode-meta'));
    for(const row of rows){
      const card=el('article','','mode-execution');card.dataset.executionId=row.executionId;card.dataset.runId=row.runId;card.dataset.status=row.status;
      const head=el('div','','mode-detail-title');head.append(el('strong',row.name),el('span',label(row.status),'mode-badge'));card.append(head);
      const running=[...row.steps.values()].filter(step=>step.status==='running').length,completed=[...row.steps.values()].filter(step=>step.status==='completed').length;
      card.append(el('p','调用 '+row.executionId+' · 运行 '+row.runId+' · 定义 '+row.definitionHash.slice(0,12),'mode-meta'),el('p','并发上限 '+row.maxConcurrency+' · 执行中 '+running+' · 完成 '+completed+(row.totalSteps===null?'':' / '+row.totalSteps),'mode-progress-label'));
      const steps=el('ul','','mode-execution-steps');for(const step of row.steps.values()){const item=el('li');item.dataset.stepId=step.id;item.dataset.status=step.status;if(step.taskId)item.dataset.stepTaskId=step.taskId;item.append(el('strong',step.id),el('span',label(step.status),'mode-badge'));if(step.call)item.append(el('span',step.call,'mode-step-call'));if(step.taskId)item.append(el('small','Task '+step.taskId));if(step.errorCode)item.append(el('small',step.errorCode));steps.append(item);}card.append(steps);executions.append(card);
    }
  }
  function replayExecutions(){if(typeof events!=='undefined'&&Array.isArray(events))for(const event of events)if(!event.project_id||event.project_id===state.project){contracts.planEvent(state.executions,event);contracts.finishRun(state.executions,event);}renderExecutions();}
  function renderList(){
    for(const b of filters.querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.kind===state.kind));
    list.replaceChildren();
    if(!state.items.length)list.append(el('p','暂无已保存对象。可在源码中构造并保存目标、计划或技能。','modes-empty'));
    for(const item of state.items){const b=button('',()=>select(item.key).catch(()=>{}),'mode-card');b.setAttribute('aria-pressed',String(item.key===state.key));b.append(el('small',(kinds[item.kind]||item.kind)+' · r'+item.revision),el('strong',item.key),el('span',label(item.status),'mode-badge'));list.append(b);}
    more.hidden=!state.cursor;
  }
  function renderDetail(){
    detail.replaceChildren();const artifact=state.detail;if(!artifact){detail.append(el('p',state.items.length?'选择一个对象查看定义和执行状态。':'目标、计划和技能共用语言运行时，执行状态来自真实记录。','modes-empty'));return;}
    const definition=artifact.definition||{},checkpoint=artifact.checkpoint||{},head=el('div','','mode-detail-title');head.append(el('h3',artifact.key),el('span',label(artifact.status),'mode-badge'));detail.append(head,el('p',(kinds[artifact.kind]||artifact.kind)+' · 修订 '+artifact.revision+(state.viewRevision?' · 历史版本':'')+(artifact.runId?' · 运行 '+String(artifact.runId).slice(0,8):''),'mode-meta'));
    const policy=contracts.policy(artifact),toolbar=el('div','','mode-toolbar');toolbar.append(button('历史版本',()=>history(artifact).catch(e=>note(e.message,true))));
    if(!policy.blocked){if(!policy.readonly)toolbar.append(button(state.viewRevision?'载入此版本到编辑器':'编辑定义',()=>editArtifact(artifact).catch(e=>note(e.message,true))));toolbar.append(button(policy.captured?'复制为新创作':'另存为新对象',()=>cloneArtifact(artifact)),button('导出源码',()=>exportArtifact(artifact)));}detail.append(toolbar);
    if(policy.blocked)detail.append(el('p','此技能未成功完成，禁止编辑、复制和导出。','mode-notice'));
    else if(policy.captured)detail.append(el('p','已完成录制的定义与证据只读；可明确复制为不携带录制证明的新创作。','mode-notice'));
    else if(policy.readonly)detail.append(el('p','此目标已有执行检查点，定义固定。修改请另存新目标，原迭代额度与证据保留。','mode-notice'));
    if(artifact.kind==='goal'){
      detail.append(el('p',definition.objective||'','mode-objective'));
      const spent=Number(checkpoint.iteration||0),limit=Number(definition.max_iterations||0);detail.append(el('p','已尝试 '+spent+' / '+limit+' 轮 · '+label(checkpoint.phase),'mode-progress-label'));
      const progress=el('progress');progress.max=Math.max(1,limit);progress.value=Math.max(0,Math.min(limit,spent));progress.setAttribute('aria-label','已消耗目标迭代额度');detail.append(progress);
      detail.append(el('p','规划：'+definition.planner+' · 验收：'+definition.verify,'mode-meta'));
      if(artifact.status==='needs_reconciliation')detail.append(el('p','上次动作的外部效果需要核对。额度与历史保留，系统不会自动重放。','mode-notice'));
      for(const [key,name]of [['state','当前状态'],['evidence','验收证据'],['results','最近结果']])if(checkpoint[key]!==undefined){detail.append(el('h4',name),fields(checkpoint[key]));}
    }else if(artifact.kind==='plan'){
      detail.append(el('p',definition.name||'计划','mode-objective'),el('p','最大并发步骤 '+(definition.max_concurrency||1)+' · 依赖全部完成后启动；这里只展示已保存定义。','mode-meta'));
      const steps=el('ol','','mode-steps');for(const step of definition.steps||[]){const li=el('li');li.append(el('strong',step.id),el('code',step.call),el('small',(step.needs||[]).length?'依赖：'+step.needs.join('、'):'起始步骤'));steps.append(li);}detail.append(steps);
    }else if(artifact.kind==='skill'){
      const parameters=definition.parameters||[];detail.append(el('p',(definition.actions||[]).length+' 个工具动作定义 · '+parameters.length+' 个显式参数'+(policy.captured?' · 有历史录制证据':' · 手工创作，无录制证明'),'mode-meta'));
      const actions=el('ol','','mode-steps');for(const [index,action]of (definition.actions||[]).entries()){const li=el('li');li.append(el('strong','动作 '+(index+1)),el('code',action.tool));const inputs=el('details');inputs.append(el('summary','动作输入'),fields(action.input));li.append(inputs);for(const p of parameters.filter(p=>p.step===index))li.append(el('small','参数 '+p.name+' → '+(p.path||[]).join('.')));actions.append(li);}detail.append(actions);
      if(parameters.length){detail.append(el('h4','参数绑定'));for(const p of parameters)detail.append(el('p',p.name+' · 动作 '+(Number(p.step)+1)+' · '+(p.path||[]).join('.'),'mode-parameter'));}
      const copy=button('复制可编辑源码',()=>exportArtifact(artifact,'function',true));copy.disabled=policy.blocked;detail.append(copy);
      if(copy.disabled)detail.append(el('p','此录制未成功完成，不作为可回放技能。','mode-notice'));
    }
    if(artifact.sourceHash)detail.append(el('p','固定源码 '+String(artifact.sourceHash).slice(0,16),'mode-meta'));
  }
  async function select(key,revision=null){
    state.key=key;state.detail=null;state.viewRevision=revision;const requestId=++state.detailRequest,generation=state.generation;renderList();renderDetail();
    try{const data=await request(base()+'/'+encodeURIComponent(key)+(revision?'?revision='+revision:''));if(generation!==state.generation||requestId!==state.detailRequest||state.key!==key||state.closed)return;state.detail=data;renderDetail();}
    catch(error){if(generation===state.generation&&requestId===state.detailRequest)note('对象读取失败：'+error.message,true);}
  }
  const editor=el('dialog','','mode-editor'),form=el('form'),editorTitle=el('h2','编辑模式定义'),editorMeta=el('p','','mode-meta'),draftStatus=el('p','','mode-draft-status'),draftFields=el('div','','mode-draft-fields'),preview=el('pre','','mode-source-preview'),footer=el('div','','mode-toolbar');
  editor.setAttribute('aria-label','模式定义编辑器');draftStatus.setAttribute('role','status');preview.hidden=true;form.append(editorTitle,editorMeta,draftStatus,draftFields,preview,footer);editor.append(form);document.body.append(editor);
  const validateButton=button('验证与预览',()=>validateDraft().catch(showDraftError)),saveButton=button('保存模式版本',()=>saveDraft().catch(showDraftError)),saveAsButton=button('另存为新对象',()=>{if(state.draft)cloneDraft(state.draft);}),reloadButton=button('载入远端（替换表单）',()=>reloadDraft().catch(showDraftError)),keepButton=button('保留本地草稿',()=>{draftMessage('本地草稿已保留。可继续编辑或另存新对象；原版本不会被自动覆盖。');}),closeButton=button('关闭并保留草稿',()=>editor.close()),discardButton=button('放弃此草稿',()=>{if(state.draft)state.drafts.delete(state.draft.id);state.draft=null;editor.close();});
  footer.append(validateButton,saveButton,saveAsButton,reloadButton,keepButton,closeButton,discardButton);form.onsubmit=e=>{e.preventDefault();saveDraft().catch(showDraftError);};
  function draftMessage(text,error=false){draftStatus.textContent=text;draftStatus.classList.toggle('error',error);}
  function showDraftError(error){draftMessage(error.message,true);}
  function currentDraft(d){return state.draft===d&&d.project===currentProject()&&!state.closed;}
  function draftChanged(d){d.serial++;d.dirty=true;d.validated=null;preview.hidden=true;renderDraftStatus();}
  function renderDraftStatus(){const d=state.draft;if(!d||!currentDraft(d))return;const conflict=d.remote&&d.remote.revision>d.baseRevision;editorMeta.textContent='模式库 '+(d.baseRevision===null?'新对象':d.key+' · 基于 r'+d.baseRevision)+(d.dirty?' · 本地草稿':' · 已保存')+'；草稿仅保留在本页内存，不会自动改写源码。';const keyInput=draftFields.querySelector('[aria-label="模式标识 key"]');if(keyInput)keyInput.disabled=d.baseRevision!==null||!!d.saving;reloadButton.hidden=!conflict;keepButton.hidden=!conflict;saveButton.disabled=!!d.saving||!!conflict;validateButton.disabled=!!d.saving;saveAsButton.disabled=!!d.saving;discardButton.disabled=!!d.saving;if(conflict)draftMessage('远端已有 r'+d.remote.revision+'；本地表单保留。载入远端会替换表单，另存不会覆盖原对象。',true);}
  function newDraft(kind){const d={id:state.project+':new:'+(++state.draftSequence),project:state.project,key:'',definition:contracts.initial(kind),baseRevision:null,baseDefinition:null,serial:0,dirty:true,remote:null,saving:false};state.drafts.set(d.id,d);openDraft(d);}
  function openDraft(d){state.draft=d;renderDraft();if(!editor.open)editor.showModal();}
  function cloneDraft(d){const next={...d,id:d.project+':new:'+(++state.draftSequence),key:d.key?d.key+'-copy':'',definition:copy(d.definition),invalidInputs:copy(d.invalidInputs||{}),fieldNames:copy(d.fieldNames||{}),baseRevision:null,baseDefinition:null,serial:0,dirty:true,remote:null,saving:false,validated:null};state.drafts.set(next.id,next);openDraft(next);draftMessage('这是新的手工创作，仅复制定义，不携带录制证据或执行检查点。');}
  function cloneArtifact(a){if(contracts.policy(a).blocked)return;cloneDraft({project:state.project,key:a.key,definition:a.definition});}
  async function editArtifact(a){
    if(contracts.policy(a).readonly)return;const projectId=state.project,generation=state.generation,id=projectId+':'+a.key,existing=state.drafts.get(id);
    if(existing){openDraft(existing);draftMessage('已恢复此对象的现有本地草稿，未用历史版本覆盖。');return;}
    const latest=state.viewRevision?await request(base(projectId)+'/'+encodeURIComponent(a.key)):a;if(generation!==state.generation||projectId!==currentProject())return;
    if(contracts.policy(latest).readonly){note('最新对象已受执行/录制保护，请另存为新对象。',true);return;}
    const d={id,project:projectId,key:a.key,definition:copy(a.definition),baseRevision:latest.revision,baseDefinition:copy(latest.definition),serial:0,dirty:!!state.viewRevision,remote:null,saving:false};state.drafts.set(id,d);openDraft(d);
  }
  function showDrafts(){const drafts=[...state.drafts.values()].filter(d=>d.project===state.project);if(!drafts.length){note('当前项目没有模式草稿。');return;}detail.replaceChildren(el('h3','本地模式草稿'));for(const d of drafts)detail.append(button((d.key||'未命名')+' · '+kinds[d.definition.kind]+(d.dirty?' · 未保存':''),()=>openDraft(d),'mode-card'));}
  function field(parent,label,value,change,{type='text',multiline=false,disabled=false}={}){const wrap=el('label','','mode-input'),caption=el('span',label),input=el(multiline?'textarea':'input'),d=state.draft;input.setAttribute('aria-label',label);input.disabled=disabled;if(!multiline)input.type=type;input.value=value??'';if(d?.invalidInputs?.[label]){input.value=d.invalidInputs[label].value;input.setCustomValidity(d.invalidInputs[label].message);}input.oninput=()=>{input.setCustomValidity('');change(input.value,input);if(d){d.invalidInputs??={};if(input.validationMessage){d.invalidInputs[label]={value:input.value,message:input.validationMessage};draftChanged(d);}else delete d.invalidInputs[label];}};wrap.append(caption,input);parent.append(wrap);return input;}
  function choice(parent,label,values,value,change){const wrap=el('label','','mode-input'),select=el('select');select.setAttribute('aria-label',label);for(const [key,name]of values){const option=el('option',name);option.value=key;select.append(option);}select.value=String(value);select.onchange=()=>change(select.value);wrap.append(el('span',label),select);parent.append(wrap);return select;}
  function mutate(d,fn){try{if(!form.reportValidity())return;fn();draftChanged(d);renderDraft();}catch(error){showDraftError(error);}}
  function renderDraft(){
    const d=state.draft;if(!d)return;draftFields.replaceChildren();preview.hidden=true;editorTitle.textContent=(d.baseRevision===null?'新建':'编辑')+kinds[d.definition.kind];draftMessage('定义由语言验证器校验；保存不会执行工具。');
    field(draftFields,'模式标识 key',d.key,(value,input)=>{if(!contracts.changeKey(d,value)){input.value=d.key;return;}draftChanged(d);},{disabled:d.baseRevision!==null||d.saving});
    if(d.definition.kind==='plan')renderPlan(d);else if(d.definition.kind==='goal')renderGoal(d);else renderSkill(d);renderDraftStatus();
  }
  function renderPlan(d){
    const def=d.definition;field(draftFields,'计划名称',def.name,value=>{def.name=value;draftChanged(d);});draftFields.append(el('p','动作绑定键由调用者的 bindings 提供，不会在这里执行或自动绑定 Agent。','mode-meta'));
    const concurrency=field(draftFields,'最大并发步骤',def.max_concurrency??1,(value,input)=>{try{contracts.planConcurrency(def,value);draftChanged(d);}catch(error){input.setCustomValidity(error.message);}},{type:'number'});concurrency.min='1';concurrency.max='128';concurrency.step='1';
    draftFields.append(el('p','1 保持原有串行顺序；2–128 允许已满足依赖的步骤同时执行。此设置随模式版本保存，实际工具线程仍受运行时限制。','mode-meta'));
    def.steps.forEach((step,index)=>{const card=el('fieldset','','mode-edit-card');card.append(el('legend','步骤 '+(index+1)));
      const id=field(card,'步骤 '+(index+1)+' 标识',step.id,()=>{});id.onchange=()=>mutate(d,()=>contracts.renameStep(def,index,id.value));id.oninput=()=>{contracts.renameStep(def,index,id.value);draftChanged(d);};
      field(card,'步骤 '+(index+1)+' 动作绑定键',step.call,value=>{step.call=value;draftChanged(d);});
      const needs=el('div','','mode-needs');needs.append(el('span','依赖步骤'));for(const [otherIndex,other]of def.steps.entries())if(otherIndex!==index){const label=el('label'),check=el('input');check.type='checkbox';check.checked=step.needs.includes(other.id);check.setAttribute('aria-label','步骤 '+(index+1)+' 依赖 '+other.id);check.onchange=()=>{step.needs=check.checked?[...step.needs,other.id]:step.needs.filter(id=>id!==other.id);draftChanged(d);};label.append(check,el('span',other.id||'未命名'));needs.append(label);}card.append(needs);
      const actions=el('div','','mode-toolbar');actions.append(button('上移步骤',()=>mutate(d,()=>{if(index>0)[def.steps[index-1],def.steps[index]]=[step,def.steps[index-1]];})),button('下移步骤',()=>mutate(d,()=>{if(index<def.steps.length-1)[def.steps[index+1],def.steps[index]]=[step,def.steps[index+1]];})),button('删除步骤',()=>mutate(d,()=>contracts.removeStep(def,index))));card.append(actions);draftFields.append(card);
    });draftFields.append(button('添加步骤',()=>mutate(d,()=>{let n=def.steps.length+1;while(def.steps.some(s=>s.id==='step'+n))n++;def.steps.push({id:'step'+n,call:'action',needs:[]});})));
  }
  function renderGoal(d){const def=d.definition;for(const [key,name]of [['objective','目标描述'],['planner','规划绑定键'],['verify','验收绑定键']])field(draftFields,name,def[key],value=>{def[key]=value;draftChanged(d);},{multiline:key==='objective'});field(draftFields,'最大迭代次数',def.max_iterations,(value,input)=>{const n=Number(value);if(!value||!Number.isInteger(n)){input.setCustomValidity('请输入整数。');return;}def.max_iterations=n;draftChanged(d);},{type:'number'});}
  function jsonEditor(parent,value,update,label,depth=0,budget={count:0}){
    const box=el('div','','mode-json');parent.append(box);budget.count++;
    if(depth>7||budget.count>160){const input=field(box,label+' JSON',encode(value,2),(text,node)=>{try{update(decode(text));node.setCustomValidity('');}catch{node.setCustomValidity('请输入有效 JSON。');}},{multiline:true});input.classList.add('mode-json-raw');return;}
    const kind=value===null?'null':Array.isArray(value)?'array':typeof value==='bigint'||exactNumber(value)?'number':typeof value;
    choice(box,label+' 类型',[['string','文本'],['number','数字'],['boolean','布尔'],['null','空值'],['array','列表'],['object','记录']],kind,next=>{update({string:'',number:0,boolean:false,null:null,array:[],object:{}}[next]);renderDraft();});
    if(kind==='string')field(box,label,value,text=>update(text),{multiline:true});
    else if(kind==='number')field(box,label,String(value),(text,input)=>{try{const n=decode(text);if(!exactNumber(n)&&typeof n!=='bigint'&&(typeof n!=='number'||!Number.isFinite(n)))throw Error();update(n);}catch{input.setCustomValidity('请输入有效 JSON 数字。');}},{type:'text'});
    else if(kind==='boolean')choice(box,label,[['true','true'],['false','false']],value,raw=>update(raw==='true'));
    else if(kind==='array'||kind==='object'){
      Object.entries(value).forEach(([key,item])=>{const row=el('div','','mode-json-entry');box.append(row);if(kind==='object'){const fieldLabel=label+' 字段名 '+key,d=state.draft,keyInput=field(row,fieldLabel,d.fieldNames?.[fieldLabel]??key,text=>{d.fieldNames??={};d.fieldNames[fieldLabel]=text;draftChanged(d);});keyInput.onchange=()=>{const next=keyInput.value;if(next!==key&&Object.hasOwn(value,next)){keyInput.setCustomValidity('字段名已存在。');d.invalidInputs??={};d.invalidInputs[fieldLabel]={value:next,message:'字段名已存在。'};draftChanged(d);return;}const entries=Object.entries(value).map(([k,v])=>[k===key?next:k,v]);if(d.fieldNames)delete d.fieldNames[fieldLabel];update(Object.fromEntries(entries));renderDraft();};}else row.append(el('strong','索引 '+key));jsonEditor(row,item,next=>{Object.defineProperty(value,key,{value:next,enumerable:true,writable:true,configurable:true});update(value);},label+'['+JSON.stringify(kind==='array'?Number(key):key)+']',depth+1,budget);row.append(button('删除值',()=>{if(kind==='array')value.splice(Number(key),1);else delete value[key];update(value);renderDraft();}));});
      box.append(button(kind==='array'?'添加列表项':'添加字段',()=>{if(kind==='array')value.push(null);else{let key='field',n=1;while(Object.hasOwn(value,key))key='field'+n++;Object.defineProperty(value,key,{value:null,enumerable:true,writable:true,configurable:true});}update(value);renderDraft();}));
    }
  }
  function renderSkill(d){
    const def=d.definition;draftFields.append(el('p','手工创作：编辑工具动作定义，不产生或继承录制证明。参数路径明确指向输入字段；不会根据文本自动替换。','mode-notice'));
    def.actions.forEach((action,index)=>{const card=el('fieldset','','mode-edit-card');card.append(el('legend','动作 '+(index+1)));const tool=field(card,'动作 '+(index+1)+' 工具名称',action.tool,value=>{action.tool=value;draftChanged(d);});tool.setAttribute('list','mode-tool-options');jsonEditor(card,action.input,value=>{action.input=value;draftChanged(d);},'动作 '+(index+1)+' 输入');const actions=el('div','','mode-toolbar');actions.append(button('上移动作',()=>mutate(d,()=>contracts.moveAction(def,index,index-1))),button('下移动作',()=>mutate(d,()=>contracts.moveAction(def,index,index+1))),button('删除动作',()=>mutate(d,()=>contracts.removeAction(def,index))));card.append(actions);draftFields.append(card);});
    const options=el('datalist');options.id='mode-tool-options';try{for(const name of Object.keys(typeof tools!=='undefined'?tools:{})){const option=el('option');option.value=name;options.append(option);}}catch{}draftFields.append(options,button('添加工具动作',()=>mutate(d,()=>def.actions.push({tool:'text.normalize',input:''}))),el('h3','显式参数绑定'));
    def.parameters.forEach((parameter,index)=>{const card=el('fieldset','','mode-edit-card');card.append(el('legend','参数 '+(index+1)));field(card,'参数 '+(index+1)+' 名称',parameter.name,value=>{parameter.name=value;draftChanged(d);});choice(card,'参数 '+(index+1)+' 动作',def.actions.map((a,i)=>[String(i),'动作 '+(i+1)+' · '+a.tool]),parameter.step,raw=>mutate(d,()=>{parameter.step=Number(raw);parameter.path=[];}));const paths=contracts.paths(def.actions[parameter.step]?.input),encoded=JSON.stringify(parameter.path);if(!paths.some(p=>JSON.stringify(p)===encoded))paths.unshift(parameter.path);choice(card,'参数 '+(index+1)+' 路径',paths.map(p=>[JSON.stringify(p),p.length?JSON.stringify(p):'[] · 整个输入']),encoded,raw=>{parameter.path=JSON.parse(raw);draftChanged(d);});field(card,'参数 '+(index+1)+' 精确路径 JSON',encoded,(text,input)=>{try{const path=JSON.parse(text);if(!Array.isArray(path))throw Error();parameter.path=path;draftChanged(d);}catch{input.setCustomValidity('路径必须为 JSON 数组。');}});card.append(button('删除参数',()=>mutate(d,()=>def.parameters.splice(index,1))));draftFields.append(card);});
    draftFields.append(button('添加参数绑定',()=>mutate(d,()=>def.parameters.push({name:'parameter'+(def.parameters.length+1),step:0,path:[]}))));
  }
  async function validateDraft(){document.activeElement?.blur();const d=state.draft;if(!d||!form.reportValidity())return;if(!d.key.trim())throw new Error('请输入模式标识 key。');const serial=d.serial,definition=copy(d.definition);draftMessage('正在使用语言验证器校验…');const result=await request('/api/v1/modes/validate',{method:'POST',body:{definition}});if(!currentDraft(d)||serial!==d.serial)throw new Error('验证期间出现新编辑，草稿已保留，请重新验证。');d.validated=result;if(window.ChaosCode)window.ChaosCode.update(preview,result.source,{language:'chaos',label:'模式定义源码'});else preview.textContent=result.source;preview.hidden=false;draftMessage('定义有效 · '+result.definitionHash.slice(0,16)+' · 仅预览，未执行。');return result;}
  async function saveDraft(){
    document.activeElement?.blur();const d=state.draft;if(!d||d.saving||!form.reportValidity())return;if(d.remote)throw new Error('远端版本已更新，请先处理冲突或另存。');if(!d.key.trim())throw new Error('请输入模式标识 key。');const serial=d.serial,key=d.key,projectId=d.project;d.saving=true;renderDraftStatus();
    try{const validated=await request('/api/v1/modes/validate',{method:'POST',body:{definition:copy(d.definition)}});if(!currentDraft(d)||d.serial!==serial)throw new Error('验证期间出现新编辑或切换项目，未提交，草稿已保留。');const artifact=await request(base(projectId)+'/'+encodeURIComponent(key),{method:'PUT',body:{definition:validated.definition,...(d.baseRevision===null?{}:{revision:d.baseRevision})}});
      d.key=key;contracts.committed(d,artifact,serial);if(!currentDraft(d))return;const nextId=projectId+':'+key;state.drafts.delete(d.id);d.id=nextId;state.drafts.set(nextId,d);state.key=key;state.viewRevision=null;if(d.serial===serial)renderDraft();draftMessage('已保存模式 r'+artifact.revision+(d.dirty?'；保存期间的新输入仍是草稿。':'；项目源码未改动。'));await refresh();
    }catch(error){if(error.status===409){try{const latest=await request(base(projectId)+'/'+encodeURIComponent(key));if(d.project===projectId)d.remote=latest;}catch{}}if(currentDraft(d))showDraftError(error);}
    finally{d.saving=false;if(currentDraft(d))renderDraftStatus();}
  }
  async function reloadDraft(){const d=state.draft;if(!d||d.saving)return;const serial=d.serial,latest=await request(base(d.project)+'/'+encodeURIComponent(d.key));if(!currentDraft(d)||d.serial!==serial)throw new Error('读取期间出现新编辑，草稿保留。');if(contracts.policy(latest).readonly)throw new Error('远端对象现在只读，请保留草稿并另存为新对象。');d.definition=copy(latest.definition);d.baseDefinition=copy(latest.definition);d.baseRevision=latest.revision;d.remote=null;d.dirty=false;d.invalidInputs={};d.fieldNames={};d.serial++;renderDraft();}
  async function history(artifact,after=0){const generation=state.generation,key=artifact.key;const data=await request(base()+'/'+encodeURIComponent(key)+'/history?after='+after+'&limit=30');if(generation!==state.generation||state.key!==key)return;if(!after)detail.replaceChildren(el('h3',key+' · 历史版本'),el('p','查看历史不会回滚已保存版本。载入编辑后保存会产生新修订。','mode-meta'));for(const row of data.items||[])detail.append(button('r'+row.revision+' · '+label(row.status),()=>select(key,row.revision),'mode-card'));if(data.nextCursor)detail.append(button('更多历史',()=>history(artifact,data.nextCursor).catch(e=>note(e.message,true))));}
  const exportDialog=el('dialog','','mode-editor mode-export'),exportBody=el('div');exportDialog.setAttribute('aria-label','模式源码导出');exportDialog.append(exportBody);document.body.append(exportDialog);let exportRequest=0;
  async function exportArtifact(artifact,format=artifact.kind==='skill'?'function':'value',copyImmediately=false){
    if(contracts.policy(artifact).blocked)return;const requestId=++exportRequest,projectId=state.project,generation=state.generation;const ideSnapshot=window.ChaosIDE?.state?copy(window.ChaosIDE.state):null;exportBody.replaceChildren(el('h2','模式源码'),el('p','正在读取固定版本…'));if(!exportDialog.open)exportDialog.showModal();
    try{const data=await request(base(projectId)+'/'+encodeURIComponent(artifact.key)+'/source?revision='+artifact.revision+'&format='+format);if(requestId!==exportRequest||generation!==state.generation||projectId!==currentProject())return;exportBody.replaceChildren(el('h2',artifact.key+' · r'+data.revision),el('p','固定版本源码副本；以后修改模式库不会自动改写它。','mode-meta'));const source=window.ChaosCode?window.ChaosCode.render(data.source,{language:'chaos',label:'导出源码'}):el('textarea');if(!window.ChaosCode){source.readOnly=true;source.value=data.source;}source.setAttribute('aria-label','导出源码');exportBody.append(source);const actions=el('div','','mode-toolbar');
      const copyCode=async()=>{try{await navigator.clipboard.writeText(data.source);note('源码已复制。');}catch{if(window.ChaosCode?.update)window.ChaosCode.update(source,data.source,{language:'chaos',label:'导出源码',analyze:false});source.focus();if(typeof source.select==='function')source.select();else{const range=document.createRange();range.selectNodeContents(source.querySelector('code')||source);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);}note('已选中源码，请使用系统复制快捷键。');}};actions.append(button('复制源码',copyCode),button('下载模块',()=>{const url=URL.createObjectURL(new Blob([data.source],{type:'text/plain;charset=utf-8'})),a=el('a');a.href=url;a.download=artifact.key+'.chaos';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
      if(window.ChaosIDE?.importModeSource)actions.append(button('加入 IDE 新模块草稿',async()=>{if(projectId!==currentProject())return;const now=window.ChaosIDE.state;if(ideSnapshot&&JSON.stringify(now)!==JSON.stringify(ideSnapshot)){exportBody.append(el('p','导出期间 IDE 已变化，保留源码预览；请重新导出后插入。','mode-notice'));return;}try{await window.ChaosIDE.importModeSource(data.source,artifact.key);exportDialog.close();note('源码已加入 IDE 本地草稿，尚未提交项目版本。');}catch(error){exportBody.append(el('p',error.message,'mode-notice'));}}));
      if(artifact.kind==='skill')actions.append(button(format==='function'?'切换为受控 Skill 值':'切换为可调用函数',()=>exportArtifact(artifact,format==='function'?'value':'function')));actions.append(button('关闭',()=>exportDialog.close()));exportBody.append(actions);if(copyImmediately)await copyCode();
    }catch(error){if(requestId===exportRequest){exportBody.replaceChildren(el('p',error.message,'mode-notice'),button('关闭',()=>exportDialog.close()));}}
  }
  async function refresh(append=false){
    if(state.closed)return;const id=currentProject();if(id!==state.project){state.project=id;state.generation++;state.items=[];state.key=null;state.detail=null;state.viewRevision=null;state.draft=null;state.executions.clear();renderExecutions();editor.close();exportDialog.close();append=false;}
    replayExecutions();const generation=state.generation,requestId=++state.listRequest,kind=state.kind;refreshButton.disabled=true;
    try{const query=new URLSearchParams({limit:'100'});if(kind)query.set('kind',kind);if(append&&state.cursor)query.set('after',state.cursor);const page=await request(base()+'?'+query);
      if(state.closed||generation!==state.generation||requestId!==state.listRequest||kind!==state.kind)return;
      const rows=Array.isArray(page)?page:page.items||[];state.items=append?[...new Map([...state.items,...rows].map(item=>[item.key,item])).values()]:rows;state.cursor=page.nextCursor||null;renderList();
      for(const d of state.drafts.values())if(d.project===id&&d.baseRevision!==null){const row=rows.find(a=>a.key===d.key);if(row)contracts.remote(d,row);}renderDraftStatus();
      if(state.key&&!state.items.some(item=>item.key===state.key)){state.key=null;state.detail=null;}
      note('已载入 '+state.items.length+' 个对象 · '+new Date().toLocaleTimeString('zh-CN'));
      if(state.key)await select(state.key,state.viewRevision);else renderDetail();
    }catch(error){if(generation===state.generation&&requestId===state.listRequest)note('对象列表暂不可用：'+error.message,true);}finally{if(generation===state.generation&&requestId===state.listRequest)refreshButton.disabled=false;}
  }
  function ingestEvent(event){if(event.project_id&&event.project_id!==currentProject())return;if(contracts.planEvent(state.executions,event)||contracts.finishRun(state.executions,event))renderExecutions();if(event.type?.startsWith('mode.plan.'))return;if(!/^(mode\.|goal\.|skill\.recording\.|run\.|graph\.saved)/.test(event.type||''))return;clearTimeout(state.timer);state.timer=setTimeout(()=>refresh().catch(()=>{}),150);}
  window.ChaosModes={refresh,ingestEvent,select,get state(){return {project:state.project,key:state.key,detail:state.detail,executions:[...state.executions.values()].map(row=>({...row,steps:[...row.steps.values()]}))};}};
  window.addEventListener('beforeunload',event=>{if([...state.drafts.values()].some(d=>d.dirty||d.saving)){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pagehide',()=>{state.closed=true;state.generation++;clearTimeout(state.timer);});
  refresh().catch(()=>{});
})();
