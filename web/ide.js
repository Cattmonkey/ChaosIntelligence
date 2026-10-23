'use strict';
const ChaosIDEContracts=Object.freeze({
 snapshotMatches:(before,after)=>['projectId','epoch','file','revision','editRevision','source','context'].every(key=>before[key]===after[key]),
 navigationTarget(value,files,allowEmpty=false){
  if(!value||!Object.hasOwn(files,value.file)||typeof files[value.file]!=='string'||!Number.isInteger(value.from)||!Number.isInteger(value.to)||value.from<0||(allowEmpty?value.to<value.from:value.to<=value.from)||value.to>files[value.file].length)return null;
  const text=files[value.file];for(const offset of [value.from,value.to]){const previous=text.charCodeAt(offset-1),next=text.charCodeAt(offset);if(previous>=0xD800&&previous<=0xDBFF&&next>=0xDC00&&next<=0xDFFF)return null;}return value;
 },
 definitionTarget(value,files){return value&&Object.hasOwn(files,value.file)&&Number.isInteger(value.from)&&Number.isInteger(value.to)&&value.from>=0&&value.to>value.from&&value.to<=files[value.file].length?value:null;},
 formatChanges(before,after){
  if(before===after)return [];
  // Match the preserved non-whitespace stream; only replace changed gaps so
  // selections within names, strings and comments remain attached to text.
  const changes=[];let a=0,b=0;
  while(a<before.length||b<after.length){const from=a,start=b;while(a<before.length&&/\s/u.test(before[a]))a++;while(b<after.length&&/\s/u.test(after[b]))b++;if(before.slice(from,a)!==after.slice(start,b))changes.push({from,to:a,insert:after.slice(start,b)});if(a===before.length&&b===after.length)return changes;if(before[a]!==after[b])break;a++;b++;}
  // Legacy dialect normalization may insert syntax. The backend parser checks
  // equivalence; retain every unchanged prefix/suffix instead of replacing all.
  let from=0,to=before.length,end=after.length;while(from<to&&from<end&&before[from]===after[from])from++;while(to>from&&end>from&&before[to-1]===after[end-1]){to--;end--;}return [{from,to,insert:after.slice(from,end)}];
 },
 compileRequest(body){if(!body||!Object.hasOwn(body.files,body.entry))throw new Error('入口文件不存在');return {source:body.files[body.entry],files:{...body.files},entry:body.entry,languageVersions:{...body.languageVersions}};},
 visibleEntries(files,collapsed=[]){const folders=new Set(),hidden=new Set(collapsed);for(const path of files){const parts=path.split('/');for(let i=1;i<parts.length;i++)folders.add(parts.slice(0,i).join('/'));}return [...folders].map(path=>({path,directory:true})).concat(files.map(path=>({path,directory:false}))).sort((a,b)=>a.path.localeCompare(b.path)).filter(item=>{const parts=item.path.split('/').slice(0,-1);return !parts.some((_,i)=>hidden.has(parts.slice(0,i+1).join('/')));});}
});
const ChaosProgramContracts=Object.freeze({
 jsonTokens(text){
  // JSON lexical colors only. The backend owns fixture validation and execution.
  const tokens=[],pattern=/"(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|[^"\\\u0000-\u001f])*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|\b(?:true|false|null)\b|[{}\[\]:,]/g;
  for(const match of text.matchAll(pattern)){const raw=match[0],from=match.index,type=raw[0]==='"'?(/^\s*:/.test(text.slice(from+raw.length))?'property':'string'):/^-?[0-9]/.test(raw)?'number':/^[a-z]/.test(raw)?'keyword':'punctuation';tokens.push({from,to:from+raw.length,type});}
  return tokens;
 },
 historyMatches(ticket,current){return ticket.projectId===current.projectId&&ticket.epoch===current.epoch&&ticket.request===current.request;},
 historyPage(previous,page){const items=new Map(previous.map(item=>[item.version,item]));for(const item of page.items)items.set(item.version,item);return [...items.values()].sort((a,b)=>a.version-b.version);},
 changes(base,candidate){return [...new Set([...Object.keys(base.files),...Object.keys(candidate.files)])].sort().flatMap(path=>{const before=Object.hasOwn(base.files,path)?base.files[path]:null,after=Object.hasOwn(candidate.files,path)?candidate.files[path]:null,beforeVersion=base.languageVersions[path],afterVersion=candidate.languageVersions[path];return before===after&&beforeVersion===afterVersion?[]:[{path,before,after,beforeVersion,afterVersion}];})}
});
(()=>{
 const cm=window.ChaosCM,source=$('#sourceEditor'),mount=$('#ideEditorMount');
 const state={editor:null,editorFile:null,projectId:null,documents:new Map(),refactorDialects:new Map(),openFiles:[],selectedPath:null,selectedDirectory:false,collapsed:new Set(),analysis:null,analysisContext:null,revision:0,analysisTimer:null,analysisAbort:null,analysisRequest:0,contextEpoch:0,bridgePending:false,bridgeWriting:false,compositionEpoch:0,formatTask:null,submitting:false,sessionId:null,job:null,jobWatch:null,chatEpoch:0,historyRequest:0};
 const nativeValue=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value');
 const make=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
 const sourceText=()=>state.editor?state.editor.state.doc.toString():nativeValue.get.call(source);
 const clamp=(x,max)=>Math.max(0,Math.min(Number.isFinite(x)?x:0,max));
 const keyFor=file=>(project?.id||'')+':'+file;
 const signaturePanel=make('section','ide-signature');signaturePanel.id='ideSignatureHelp';signaturePanel.hidden=true;signaturePanel.setAttribute('role','status');signaturePanel.setAttribute('aria-label','调用签名与当前参数');$('.ide-editor-status').before(signaturePanel);
 const signatureButton=make('button','','参数提示');signatureButton.id='ideSignatureButton';signatureButton.title='调用签名与当前参数 · Ctrl / ⌘ Shift Space';signatureButton.onclick=()=>refreshAnalysis(true);$('#ideSearch').after(signatureButton);
 const definitionButton=make('button','','转到定义');definitionButton.id='ideDefinitionButton';definitionButton.title='转到光标处名称的源码定义 · F12';definitionButton.onclick=guarded(goToDefinition);signatureButton.after(definitionButton);
 function showSignature(help,explicit=false){signaturePanel.replaceChildren();signaturePanel.hidden=!help&&!explicit;if(!help){if(explicit)signaturePanel.append(make('span','','当前位置没有可推断的调用签名。'));return;}const label=make('code','ide-signature-label',help.label),parameters=make('div','ide-signature-parameters');for(const [index,parameter] of (help.parameters||[]).entries()){const chip=make('code','ide-signature-parameter',parameter.label);if(index===help.activeParameter){chip.classList.add('active');chip.setAttribute('aria-current','true');}parameters.append(chip);}signaturePanel.append(label,parameters);const current=help.parameters?.[help.activeParameter];signaturePanel.append(make('span','ide-signature-note',current?'当前参数：'+current.name+' · '+current.type:'第 '+(help.activeArgument+1)+' 个实参 · 静态分析无法确定参数映射'));}
 function codePreview(text,options={},cls=''){const pre=window.ChaosCode?window.ChaosCode.render(text,{language:'chaos',...options}):make('pre','',text);if(cls)pre.classList.add(cls);return pre;}
 async function request(path,body,signal){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:stringifyJSON(body),signal});const data=parseJSON(await r.text());if(!r.ok){const error=new Error(data.error?.message||data.error||'请求失败');error.details=data.error?.details||data.details;error.status=r.status;throw error;}return data;}
 function report(error){if(error.name!=='AbortError')toast(error.message,true);}
 function updateCursor(){if(!state.editor)return;const pos=state.editor.state.selection.main.head,line=state.editor.state.doc.lineAt(pos);$('#ideCursor').textContent='行 '+line.number+'，列 '+(pos-line.from+1);updateChatContext();}
 function languageVersions(file,text){const versions={...project.program?.sourceVersions};for(const [path,content] of Object.entries(project.files)){const current=path===file?text:content,refactored=state.refactorDialects.get(path);versions[path]=refactored?.text===current?refactored.version:current!==baseProject?.files?.[path]?2:(versions[path]??project.program?.languageVersion??1);}return versions;}
 function analysisBody(position){const file=activeFile||project.entry,text=sourceText();return {source:text,file,files:{...project.files,[file]:text},entry:project.entry,position,languageVersions:languageVersions(file,text)};}
 function analysisSnapshot(){if(!project)return null;const body=analysisBody();return {projectId:project.id,epoch:state.contextEpoch,file:body.file,revision:state.revision,editRevision:editSerial,source:body.source,context:stringifyJSON([body.entry,body.files,body.languageVersions])};}
 function snapshotCurrent(ticket){return !!ticket&&!!project&&ChaosIDEContracts.snapshotMatches(ticket,analysisSnapshot());}
 async function analyze(position,signal){const ticket=analysisSnapshot(),body=analysisBody(position);try{const result=await request('/api/language/analyze',body,signal);return snapshotCurrent(ticket)?result:null;}catch(error){if(!snapshotCurrent(ticket))return null;throw error;}}
 let compilationDialog=null,compilationTicket=null,compilationSerial=0,compilationAbort=null;
 function compilationContextChanged(){
  if(!compilationDialog?.open||!compilationTicket)return;
  if(!snapshotCurrent(compilationTicket)){
   compilationAbort?.abort();compilationSerial++;compilationTicket=null;
   compilationDialog.querySelector('[data-compile-status]').textContent='工作区已变化，报告已过期；当前草稿已保留。请重新编译。';
   const result=compilationDialog.querySelector('[data-compile-result]');result.replaceChildren();delete result.dataset.artifactHash;
  }
 }
 async function compileDraft(){
  if(!project||!state.editor||snapshotView||controlDraft||syntaxDirty)throw new Error('请先回到源码并完成结构输入；当前草稿保留。');
  const ticket=analysisSnapshot(),body=ChaosIDEContracts.compileRequest(analysisBody()),serial=++compilationSerial;
  compilationAbort?.abort();const controller=new AbortController();compilationAbort=controller;compilationTicket=ticket;
  if(!compilationDialog){compilationDialog=make('dialog','compilation-dialog');compilationDialog.id='compilationDialog';compilationDialog.setAttribute('aria-label','完整草稿编译报告');document.body.append(compilationDialog);compilationDialog.addEventListener('close',()=>{compilationAbort?.abort();compilationSerial++;compilationTicket=null;});}
  const heading=make('div','compilation-heading'),close=make('button','','关闭');close.onclick=()=>compilationDialog.close();heading.append(make('h2','','完整草稿编译报告'),close);
  const status=make('p','compilation-status','正在编译全部模块与纯宏…');status.dataset.compileStatus='';status.setAttribute('role','status');
  const result=make('div','compilation-result');result.dataset.compileResult='';
  const refresh=make('button','','重新编译当前草稿');refresh.onclick=guarded(compileDraft);
  compilationDialog.replaceChildren(heading,make('p','ide-small','读取当前完整源码草稿与方言；报告不会保存或运行程序。'),status,result,refresh);
  if(!compilationDialog.open)compilationDialog.show();
  try{
   const report=await request('/api/language/compile',body,controller.signal);
   if(serial!==compilationSerial||!compilationDialog.open)return;
   if(!snapshotCurrent(ticket)){compilationContextChanged();return;}
   if(report.schema!=='chaos.compilation/1'||report.entry!==body.entry)throw new Error('编译报告与请求不匹配。');
   renderCompilation(result,report);status.textContent='已编译当前草稿 · '+report.entry;result.dataset.artifactHash=report.artifactHash;
  }catch(error){if(serial===compilationSerial&&compilationDialog.open&&error.name!=='AbortError')status.textContent='编译失败：'+error.message;}
 }
 function renderCompilation(mount,report){
  mount.replaceChildren();const metrics=make('dl','compilation-metrics');
  for(const [label,value]of [['模块',report.summary.moduleCount],['语法节点',report.summary.nodeCount],['解析复用',report.summary.cacheHits],['新解析',report.summary.cacheMisses],['跳过缓存',report.summary.cacheBypasses]]){const pair=make('div');pair.append(make('dt','',label),make('dd','',String(value)));metrics.append(pair);}
  mount.append(metrics,make('p','ide-small','目标：可移植 AST · 编译器 '+report.compiler.version),make('code','compilation-hash','构建标识 '+report.artifactHash));
  const effectLabels={read:'读取',raise:'可能抛错',control:'控制流',binding:'绑定',state:'状态修改',protocol:'动态协议',call:'调用',unknown:'未知行为',allocation:'分配',suspend:'挂起',resource:'资源管理',module:'模块载入'};
  const effects=(label,values)=>make('p','compilation-effects',label+'：'+(values.length?values.map(value=>effectLabels[value]||value).join(' · '):'无已标记操作'));
  mount.append(make('p','ide-small','按语法结构保守标记；动态调用保持未知，报告不代表已证明纯函数或可重排。'));
  mount.append(effects('执行屏障',report.summary.effects),effects('函数体内潜在屏障',report.summary.latentEffects));
  const modules=make('div','compilation-modules');
  const cacheLabels={hit:'复用解析',miss:'新解析',bypass:'跳过缓存'};
  for(const item of report.modules){const row=make('details','compilation-module'),name=make('summary','',item.file+' · '+(cacheLabels[item.cacheStatus]||item.cacheStatus)+' · '+item.analysis.nodeCount+' 节点');row.append(name,make('p','','依赖：'+(item.dependencies.join(' · ')||'无')),make('p','','运行导出：'+(item.exports.join(' · ')||'无')),make('p','','宏导出：'+(item.macroExports.join(' · ')||'无')),effects('执行屏障',item.analysis.effects),effects('潜在屏障',item.analysis.latentEffects),make('code','compilation-hash','源码 '+item.sourceHash),make('code','compilation-hash','依赖 '+item.dependencyHash));modules.append(row);}
  mount.append(modules);const passes=make('details','compilation-module');passes.append(make('summary','','编译步骤与实现边界'));
  for(const pass of report.passes)passes.append(make('p','',pass.name+' · '+pass.status));
  for(const limitation of report.limitations)passes.append(make('p','ide-small',limitation));mount.append(passes);
 }
 const compileButton=make('button','','编译报告');compileButton.id='compileSource';compileButton.onclick=guarded(compileDraft);$('#checkSource').after(compileButton);
 new MutationObserver(compilationContextChanged).observe($('#syncBadge'),{childList:true,subtree:true});
 async function goToDefinition(){
  if(!project||!state.editor)return;
  const ticket=analysisSnapshot(),position=state.editor.state.selection.main.head,result=await analyze(position);
  // A delayed result must not change a newer draft, file, project or selection.
  if(!result||!snapshotCurrent(ticket)||position!==state.editor.state.selection.main.head)return;
  const target=ChaosIDEContracts.definitionTarget(result.definition,analysisBody().files);
  if(!target){toast('当前位置没有可定位的源码定义。');return;}
  switchFile(target.file);
  if(state.editorFile!==target.file)return;
  state.editor.dispatch({selection:{anchor:target.from,head:target.to},scrollIntoView:true});state.editor.focus();
  toast('已定位 '+target.file+' 中的定义');
 }
 function navigateSource(target,ticket,{empty=false}={}){
  if(!snapshotCurrent(ticket)||controlDraft||syntaxDirty){toast('源码已变化，请重新分析后定位；草稿已保留。',true);return false;}
  const valid=ChaosIDEContracts.navigationTarget(target,analysisBody().files,empty);
  if(!valid){toast('此位置不属于当前源码，无法定位。',true);return false;}
  switchFile(valid.file);if(state.editorFile!==valid.file)return false;
  state.editor.dispatch({selection:{anchor:valid.from,head:valid.to},scrollIntoView:true});state.editor.focus();return true;
 }
 function showDiagnostics(items=[],ticket=analysisSnapshot()){
  const list=$('#ideDiagnostics');list.replaceChildren();
  if(!items.length){const status=make('span','','未发现语法问题');status.title='静态分析不执行访问协议；类型提示不替代运行时契约。';list.append(status);return;}
  const files=analysisBody().files;
  for(const d of items){const target={file:d.file===undefined?ticket.file:d.file,from:d.from,to:d.to},valid=ChaosIDEContracts.navigationTarget(target,files,true);let location='无法定位';if(valid){const lines=files[target.file].slice(0,target.from).split(/\r\n|\r|\n/);location=(target.file!==ticket.file?target.file+' · ':'')+'L'+lines.length+':'+(lines[lines.length-1].length+1);}const button=make('button','ide-diagnostic '+(d.severity||'error'),location+' · '+d.message);button.onclick=()=>navigateSource(target,ticket,{empty:true});list.append(button);} 
 }
 let outlineDialog=null,outlineRequest=0,outlineState=null;
 const outlineButton=make('button','','导航');outlineButton.id='ideOutlineButton';outlineButton.title='当前文件的声明 · Ctrl / ⌘ Shift O';outlineButton.setAttribute('aria-haspopup','dialog');outlineButton.onclick=guarded(openOutline);$('#ideContextAssistant').before(outlineButton);
 function outlineInvalidate(){if(outlineState&&!snapshotCurrent(outlineState.ticket)){outlineState.status.textContent='源码已变化，请重新打开导航；当前草稿已保留。';for(const button of outlineDialog.querySelectorAll('[data-outline-action]'))button.disabled=true;}}
 async function openOutline(){
  if(!project||!state.editor)return;const ticket=analysisSnapshot(),position=state.editor.state.selection.main.head,serial=++outlineRequest;
  if(!outlineDialog){outlineDialog=make('dialog','ide-outline-dialog');outlineDialog.id='ideOutlineDialog';outlineDialog.setAttribute('aria-label','当前文件语义导航');document.body.append(outlineDialog);outlineDialog.addEventListener('close',()=>{outlineRequest++;outlineState=null;});}
  const heading=make('div','ide-outline-heading'),close=make('button','','关闭');close.onclick=()=>outlineDialog.close();heading.append(make('strong','','当前文件 · '+ticket.file),close);
  const input=make('input');input.type='search';input.id='ideOutlineFilter';input.placeholder='筛选声明名称';input.setAttribute('aria-label','筛选当前文件声明');
  const status=make('p','ide-outline-status','正在读取当前草稿的语义声明…'),list=make('div','ide-outline-list');list.id='ideOutlineList';list.setAttribute('role','listbox');list.setAttribute('aria-label','当前文件声明');
  const footer=make('div','ide-outline-actions'),locate=make('button','','定位名称'),select=make('button','','选中完整声明');locate.dataset.outlineAction='name';select.dataset.outlineAction='range';locate.disabled=select.disabled=true;footer.append(locate,select);
  const d={ticket,position,serial,status,input,list,locate,select,rows:[],visible:[],index:0};outlineState=d;outlineDialog.replaceChildren(heading,input,status,list,footer,make('p','ide-outline-hint','↑ ↓ 选择 · Enter 定位 · Shift Enter 选中声明；可再用“与助手编程”修改选区。'));
  function choose(full){if(outlineState!==d)return;const row=d.visible[d.index];if(!row)return;const range=full?row.node.range:row.node.selectionRange;if(navigateSource({file:ticket.file,from:range?.from,to:range?.to},ticket))outlineDialog.close();else outlineInvalidate();}
  function render(){list.replaceChildren();const query=input.value.trim().toLocaleLowerCase();d.visible=d.rows.filter(row=>(row.node.name+' '+row.node.detail).toLocaleLowerCase().includes(query));d.index=Math.max(0,Math.min(d.index,d.visible.length-1));
   for(const [index,row]of d.visible.entries()){const button=make('button','ide-outline-option');button.type='button';button.dataset.outlineAction='item';button.dataset.outlineName=row.node.name;button.id='ideOutlineOption'+index;button.setAttribute('role','option');button.setAttribute('aria-selected',String(index===d.index));button.style.paddingInlineStart=(10+Math.min(row.depth,6)*12)+'px';button.append(make('span','',row.node.name),make('small','',row.node.kind+' · '+row.node.detail));button.onclick=()=>{d.index=index;choose(false);};list.append(button);}
   input.setAttribute('aria-activedescendant',d.visible.length?'ideOutlineOption'+d.index:'');locate.disabled=select.disabled=!d.visible.length||!snapshotCurrent(ticket);if(!d.visible.length)list.append(make('p','ide-outline-status','没有匹配的源码声明。'));outlineInvalidate();}
  input.oninput=()=>{d.index=0;render();};input.onkeydown=e=>{if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();d.index=e.key==='Home'?0:e.key==='End'?d.visible.length-1:d.index+(e.key==='ArrowDown'?1:-1);render();list.querySelector('[aria-selected=true]')?.scrollIntoView({block:'nearest'});}else if(e.key==='Enter'){e.preventDefault();choose(e.shiftKey);}};locate.onclick=()=>choose(false);select.onclick=()=>choose(true);
  if(!outlineDialog.open)outlineDialog.showModal();input.focus();
  try{const result=state.analysis&&snapshotCurrent(state.analysisTicket)?state.analysis:await analyze(position);if(outlineState!==d||serial!==outlineRequest)return;if(!result||!snapshotCurrent(ticket)||position!==state.editor.state.selection.main.head){status.textContent='当前源码或选区已变化，请重新打开导航。';return;}
   const outline=result.outline;if(!outline||outline.file!==ticket.file||!Array.isArray(outline.items)){status.textContent='语言服务未提供此文件的声明信息。';return;}
   function flatten(items,depth=0){for(const node of items){d.rows.push({node,depth});if(Array.isArray(node.children))flatten(node.children,depth+1);}}flatten(outline.items);
   status.textContent='当前草稿 · '+d.rows.length+' 个声明'+(!outline.complete?' · 输入不完整，仅显示已解析声明':'')+(outline.truncated?' · 已达到显示范围上限':'');render();
  }catch(error){if(outlineState===d&&serial===outlineRequest)status.textContent='无法读取语义导航：'+error.message;}
 }
 const referencesButton=make('button','','查找引用');referencesButton.id='ideReferencesButton';referencesButton.onclick=guarded(()=>openSymbol(false));definitionButton.after(referencesButton);
 const renameSymbolButton=make('button','','重命名符号');renameSymbolButton.id='ideRenameSymbolButton';renameSymbolButton.title='跨模块符号重构 · F2';renameSymbolButton.onclick=guarded(()=>openSymbol(true));referencesButton.after(renameSymbolButton);
 let symbolDialog=null,symbolDraft=null,symbolRequest=0;
 function symbolTicket(){return {snapshot:analysisSnapshot(),position:state.editor.state.selection.main.head,savedVersion:savedProjectVersion(),version:project.version};}
 function symbolCurrent(ticket){return !!ticket&&snapshotCurrent(ticket.snapshot)&&ticket.savedVersion===savedProjectVersion()&&ticket.version===project.version&&!snapshotView&&!controlDraft&&!syntaxDirty;}
 function symbolStatus(d,message){if(symbolDraft===d)d.status.textContent=message;}
 async function openSymbol(rename){
  if(!project||!state.editor||snapshotView||controlDraft||syntaxDirty)throw new Error('请先回到源码并处理结构输入；当前草稿保留。');
  const ticket=symbolTicket(),body=analysisBody(ticket.position),requestId=++symbolRequest;
  if(!symbolDialog){symbolDialog=make('dialog','symbol-dialog');symbolDialog.id='symbolDialog';symbolDialog.setAttribute('aria-label','符号引用与重命名');document.body.append(symbolDialog);symbolDialog.addEventListener('close',()=>{symbolRequest++;symbolDraft=null;});}
  symbolDialog.replaceChildren();const d={ticket,body,candidate:null,serial:0,status:make('p','ide-small','正在分析完整草稿包…')};symbolDraft=d;
  const close=make('button','symbol-close','关闭');close.onclick=()=>symbolDialog.close();symbolDialog.append(close,make('h2','','符号引用与重命名'),make('p','ide-small','读取全部模块的真实声明与引用。预览只生成候选；应用只改本地草稿，保存仍检查项目版本。'),d.status);
  const list=make('div','symbol-references');list.setAttribute('aria-label','源码引用');symbolDialog.append(list);
  const name=make('input');name.setAttribute('aria-label','符号新名称');name.disabled=true;
  const preview=make('button','','预览重命名'),apply=make('button','primary','应用到全部草稿');preview.disabled=true;apply.disabled=true;apply.id='symbolApply';const actions=make('div','symbol-actions');actions.append(name,preview,apply);symbolDialog.append(actions);
  const diff=make('div','symbol-diff');diff.setAttribute('aria-label','重命名差异');symbolDialog.append(diff);name.oninput=()=>{d.serial++;d.candidate=null;apply.disabled=true;diff.replaceChildren();};
  if(!symbolDialog.open)symbolDialog.show();
  preview.onclick=guarded(async()=>{
   if(!symbolCurrent(ticket))throw new Error('工作区已变化，请重新查找符号；全部草稿已保留。');
   const serial=d.serial,newName=name.value;apply.disabled=true;d.candidate=null;symbolStatus(d,'验证绑定、作用域与完整候选…');
   const result=await request('/api/language/rename',{...body,newName});
   if(symbolDraft!==d||requestId!==symbolRequest||serial!==d.serial||!symbolCurrent(ticket))throw new Error('重构期间出现新编辑、切换或远端版本；全部草稿已保留。');
   d.candidate=result;diff.replaceChildren();
   for(const [file,edits]of Object.entries(result.edits)){if(!edits.length)continue;const detail=make('details','ide-change');detail.open=true;detail.append(make('summary','',file+' · '+edits.length+' 处'));detail.append(codePreview(body.files[file],{file,files:body.files,label:'修改前',languageVersions:body.languageVersions},'removed'),codePreview(result.files[file],{file,files:result.files,label:'修改后',languageVersions:result.languageVersions},'added'));diff.append(detail);}
   apply.disabled=false;symbolStatus(d,'候选已验证；检查差异后可一次应用到全部草稿。');
  });
  apply.onclick=guarded(async()=>{
   const candidate=d.candidate;if(!candidate||!symbolCurrent(ticket))throw new Error('预览已过期；请重新查找，当前草稿未修改。');
   const names=Object.keys(body.files).sort();
   if(stringifyJSON(names)!==stringifyJSON(Object.keys(candidate.files).sort())||candidate.entry!==body.entry)throw new Error('候选文件集合不匹配；未修改草稿。');
   const next={...body.files},changes=new Map();
   for(const file of names){
    const text=body.files[file],hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),x=>x.toString(16).padStart(2,'0')).join('');
    if(candidate.baseHashes[file]!==hash||candidate.languageVersions[file]!==body.languageVersions[file])throw new Error('候选来源或方言不匹配；未修改草稿。');
    const edits=(candidate.edits[file]||[]).slice().sort((a,b)=>a.from-b.from);let end=0;
    for(const edit of edits){if(!Number.isInteger(edit.from)||!Number.isInteger(edit.to)||edit.from<end||edit.to<edit.from||edit.to>text.length||typeof edit.text!=='string')throw new Error('候选编辑范围无效；未修改草稿。');end=edit.to;}
    let updated=text;for(const edit of edits.slice().reverse())updated=updated.slice(0,edit.from)+edit.text+updated.slice(edit.to);
    if(updated!==candidate.files[file])throw new Error('候选源码与编辑不一致；未修改草稿。');next[file]=updated;changes.set(file,edits.map(edit=>({from:edit.from,to:edit.to,insert:edit.text})));
   }
   if(symbolDraft!==d||candidate!==d.candidate||!symbolCurrent(ticket))throw new Error('验证期间工作区变化；全部草稿已保留。');
   const documents=new Map(state.documents);
   for(const file of names){const previous=file===activeFile?state.editor.state:documents.get(keyFor(file));if(previous&&changes.get(file).length){if(previous.doc.toString()!==body.files[file])throw new Error('编辑器草稿已变化；未修改草稿。');documents.set(keyFor(file),previous.update({changes:changes.get(file),userEvent:'input.refactor'}).state);}}
   state.bridgeWriting=true;
   try{project.files=next;project.source=next[project.entry];state.documents=documents;state.refactorDialects=new Map(names.map(file=>[file,{text:next[file],version:candidate.languageVersions[file]}]));state.editor.setState(documents.get(keyFor(activeFile))||editorState(next[activeFile]));globalThis.ChaosCodeAppearance?.sync?.(state.editor,cm);nativeValue.set.call(source,next[activeFile]);state.revision++;syntaxRequest++;programDraft=null;syntaxDirty=false;markDirty('source');}
   finally{state.bridgeWriting=false;}
   renderFiles();updateCursor();scheduleAnalysis();symbolDialog.close();toast('已原子更新全部本地草稿；保存后提交项目版本。');
  });
  try{const result=await request('/api/language/references',{...body,includeDeclaration:true});if(symbolDraft!==d||requestId!==symbolRequest||!symbolCurrent(ticket))return;
   for(const item of result.references){const row=make('button','symbol-reference',item.file+' : '+item.from+'–'+item.to+' · '+item.kind);row.onclick=()=>{if(!symbolCurrent(ticket)){symbolStatus(d,'源码或版本已变化，请重新查找；草稿已保留。');return;}if(navigateSource(item,ticket.snapshot))symbolDialog.close();};list.append(row);}
   name.value=result.target?.name||'';name.disabled=!result.target||!result.complete;preview.disabled=name.disabled;
   symbolStatus(d,result.target?(result.complete?'已找到 '+result.references.length+' 处源码引用。': '引用不完整：'+(result.limitations||[]).join('；')+'；不能安全重命名。'):'当前位置没有可重构的源码符号。');if(rename&&!name.disabled){name.focus();name.select();}
  }catch(error){symbolStatus(d,error.message);throw error;}
 }
 function sourceVersionOverrides(){return Object.fromEntries([...state.refactorDialects].filter(([file,value])=>value.text===project.files[file]).map(([file,value])=>[file,value.version]));}
 async function refreshAnalysis(explicit=false){if(!project||!state.editor)return;clearTimeout(state.analysisTimer);state.analysisAbort?.abort();const controller=new AbortController(),requestId=++state.analysisRequest,ticket=analysisSnapshot(),position=state.editor.state.selection.main.head;state.analysisAbort=controller;try{const result=await analyze(position,controller.signal);if(!result||requestId!==state.analysisRequest||!snapshotCurrent(ticket)||position!==state.editor.state.selection.main.head)return;state.analysis=result;state.analysisTicket=ticket;updateChatContext();state.editor.dispatch({effects:semanticEffect.of(result.tokens||[])});const max=state.editor.state.doc.length;const ds=(result.diagnostics||[]).filter(d=>!d.file||d.file===activeFile).map(d=>({...d,from:clamp(d.from,max),to:clamp(Math.max(d.from,d.to),max),severity:d.severity==='warning'?'warning':'error'}));state.editor.dispatch(cm.setDiagnostics(state.editor.state,ds));showDiagnostics(result.diagnostics,ticket);showSignature(result.signatureHelp,explicit);$('#ideLanguageStatus').textContent=result.complete?'Chaos · 分析已更新':'Chaos · 编辑中';}catch(error){if(error.name!=='AbortError'&&requestId===state.analysisRequest&&snapshotCurrent(ticket)){$('#ideLanguageStatus').textContent='语言服务暂不可用';showSignature(null);showDiagnostics([{message:error.message,severity:'warning',from:0,to:0}]);}}}
 function scheduleAnalysis(delay=320){state.analysis=null;outlineInvalidate();$('#ideDiagnostics').replaceChildren(make('span','','正在分析当前文件…'));updateChatContext();compilationContextChanged();state.analysisContext=analysisSnapshot()?.context??null;clearTimeout(state.analysisTimer);state.analysisAbort?.abort();state.analysisRequest++;showSignature(null);$('#ideLanguageStatus').textContent='Chaos · 语义分析中';state.analysisTimer=setTimeout(()=>refreshAnalysis(),delay);}
 const chaosLanguage=globalThis.ChaosEditor?.createLexicalLanguage?.(cm)||[];
 function completionAllowed(){return !!state.editor&&!state.editor.composing&&!state.editor.compositionStarted&&!state.editor.state.readOnly&&!source.disabled&&!source.readOnly&&!snapshotView&&!state.bridgeWriting;}
 function completionContext(){const ticket=analysisSnapshot();return ticket&&stringifyJSON([ticket.projectId,ticket.epoch,ticket.file,ticket.context,snapshotView,source.disabled,source.readOnly]);}
 const completionSource=window.ChaosEditorAssist?.createSource({analyze,allowed:completionAllowed,currentContext:completionContext})||(()=>null);
 function suggest(){if(!completionAllowed())return false;state.editor.focus();return cm.startCompletion(state.editor);}
 const suggestButton=make('button','','智能提示');suggestButton.id='ideSuggestButton';suggestButton.title='显示当前上下文的代码补全 · Ctrl Space';suggestButton.disabled=!window.ChaosEditorAssist;suggestButton.onclick=suggest;$('#formatSource').before(suggestButton);
 const theme=cm&&cm.EditorView.theme({'&':{backgroundColor:'var(--editor-bg)',color:'var(--text)'},'.cm-content':{caretColor:'var(--editor-caret)'},'.cm-cursor':{borderLeftColor:'var(--editor-caret)'},'.cm-gutters':{backgroundColor:'var(--editor-gutter)',color:'var(--muted)',border:'none'},'.cm-activeLineGutter':{backgroundColor:'var(--editor-active)',color:'var(--text)'},'.cm-activeLine':{backgroundColor:'var(--editor-active)'},'&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection':{backgroundColor:'var(--editor-selection) !important'},'.cm-tooltip':{backgroundColor:'var(--editor-tooltip)',color:'var(--text)',border:'1px solid var(--border-strong)'},'.cm-tooltip-autocomplete > ul > li[aria-selected]':{backgroundColor:'var(--selected)',color:'var(--text)'},'.cm-panels':{backgroundColor:'var(--panel2)',color:'var(--text)'},'.cm-searchMatch':{backgroundColor:'var(--editor-search)'},'.cm-searchMatch.cm-searchMatch-selected':{backgroundColor:'var(--editor-search-selected)'},'.cm-matchingBracket':{backgroundColor:'var(--editor-bracket)',outline:'1px solid var(--function-border)'},'.cm-foldPlaceholder':{backgroundColor:'var(--panel2)',borderColor:'var(--border-strong)',color:'var(--secondary)'}});
 const formatBoundaryField=cm&&cm.StateField.define({create:()=>false,update(value,tr){return tr.docChanged?tr.isUserEvent('input.format'):value;}});
 const semanticEffect=cm&&cm.StateEffect.define();
 const semanticField=cm&&cm.StateField.define({create:()=>cm.Decoration.none,update(value,tr){if(tr.docChanged)value=cm.Decoration.none;for(const effect of tr.effects)if(effect.is(semanticEffect)){const max=tr.state.doc.length;value=cm.Decoration.set(effect.value.filter(t=>t.to>t.from&&t.from>=0&&t.to<=max).map(t=>cm.Decoration.mark({class:'ide-token-'+t.type}).range(t.from,t.to)),true);}return value;},provide:field=>cm.EditorView.decorations.from(field)});
 function editorState(text){return cm.EditorState.create({doc:text,extensions:[theme,...(globalThis.ChaosCodeAppearance?.extension?.(cm)||[cm.EditorView.theme({},{dark:true})]),chaosLanguage,semanticField,formatBoundaryField,...(globalThis.ChaosEditor?.lexicalHighlights?.(cm)||[]),cm.EditorView.lineWrapping,cm.lineNumbers(),cm.highlightActiveLineGutter(),cm.history({joinToEvent:(tr,adjacent)=>adjacent&&!tr.isUserEvent('input.format')&&!tr.startState.field(formatBoundaryField)}),cm.drawSelection(),cm.EditorState.allowMultipleSelections.of(true),cm.highlightSpecialChars(),cm.indentOnInput(),cm.indentUnit.of('  '),cm.bracketMatching(),cm.closeBrackets(),cm.foldGutter(),cm.highlightActiveLine(),cm.highlightSelectionMatches(),cm.lintGutter(),cm.autocompletion({override:[completionSource]}),cm.EditorView.domEventHandlers({compositionstart(event,view){state.compositionEpoch++;cm.closeCompletion(view);return false;}}),cm.keymap.of([{key:'Mod-Shift-o',run(){guarded(openOutline)();return true;}},{key:'F2',run(){guarded(()=>openSymbol(true))();return true;}},{key:'F12',run(){guarded(goToDefinition)();return true;}},{key:'Mod-Enter',run(){guarded(startRun)();return true;}},{key:'Shift-Alt-f',run(){guarded(formatDraft)();return true;}},{key:'Ctrl-Shift-Space',run(){refreshAnalysis(true);return true;}},{key:'Mod-Shift-Space',run(){refreshAnalysis(true);return true;}},...cm.closeBracketsKeymap,...cm.defaultKeymap,...cm.historyKeymap,...cm.searchKeymap,...cm.completionKeymap,cm.indentWithTab]),cm.hoverTooltip(async(view,pos)=>{try{const data=await analyze(pos);if(!data?.hover)return null;return {pos:data.hover.from,end:data.hover.to,above:true,create:()=>({dom:make('div','ide-hover',data.hover.contents)})};}catch{return null;}}),cm.EditorView.contentAttributes.of({'aria-label':'Chaos 源代码编辑器','spellcheck':'false'}),cm.EditorView.updateListener.of(update=>{if(update.docChanged&&!state.bridgeWriting){state.refactorDialects.delete(activeFile);state.revision++;nativeValue.set.call(source,update.state.doc.toString());source.dispatchEvent(new Event('input',{bubbles:true}));state.documents.set(keyFor(activeFile),update.state);renderFiles();scheduleAnalysis();}if(update.selectionSet||update.docChanged){updateCursor();if(!update.docChanged&&!state.bridgeWriting)scheduleAnalysis(180);}})]});}
 function syncFromTextarea(){state.bridgePending=false;if(!state.editor||!project)return;const file=activeFile||project.entry,text=nativeValue.get.call(source),changedProject=state.projectId!==project.id;if(changedProject){state.contextEpoch++;state.analysisAbort?.abort();state.analysisRequest++;state.analysis=null;showSignature(null);state.openFiles=[];state.documents.clear();state.refactorDialects.clear();state.projectId=project.id;state.sessionId=null;state.chatEpoch++;stopJobWatch();state.job=null;state.submitting=false;loadChatHistory();}if(!state.openFiles.includes(file))state.openFiles.push(file);if(changedProject||state.editorFile!==file||state.editor.state.doc.toString()!==text){if(!changedProject&&state.editorFile)state.documents.set(keyFor(state.editorFile),state.editor.state);const cached=state.documents.get(keyFor(file));state.bridgeWriting=true;try{state.editor.setState(cached&&cached.doc.toString()===text?cached:editorState(text));globalThis.ChaosCodeAppearance?.sync?.(state.editor,cm);state.editorFile=file;state.revision++;}finally{state.bridgeWriting=false;}state.analysisContext=null;}if(state.analysisContext!==analysisSnapshot().context){cm.closeCompletion(state.editor);state.analysis=null;state.editor.dispatch({effects:semanticEffect.of([])});state.editor.dispatch(cm.setDiagnostics(state.editor.state,[]));$('#ideDiagnostics').replaceChildren(make('span','','正在分析当前文件…'));scheduleAnalysis();}state.selectedPath=file;state.selectedDirectory=false;renderFiles();updateCursor();}
 function queueBridge(){if(!state.bridgePending){state.bridgePending=true;queueMicrotask(syncFromTextarea);}}
 Object.defineProperty(source,'value',{configurable:true,get(){return nativeValue.get.call(this)},set(value){nativeValue.set.call(this,value);queueBridge();}});
 source.addEventListener('input',()=>{if(!state.editor||state.editor.state.doc.toString()!==nativeValue.get.call(source))queueBridge();});
 function switchFile(file){if(!project||!(file in project.files))return;if(typeof flushControls==='function')flushControls();if(state.editor&&activeFile)state.documents.set(keyFor(activeFile),state.editor.state);activeFile=file;onLanguageProject();syncFromTextarea();state.editor?.focus();}
 function renderFiles(){if(!project)return;const files=Object.keys(project.files).sort(),tree=$('#ideFileTree'),tabs=$('#ideFileTabs');tree.replaceChildren();tabs.replaceChildren();state.openFiles=state.openFiles.filter(f=>f in project.files);if(activeFile&&!state.openFiles.includes(activeFile))state.openFiles.push(activeFile);for(const file of state.openFiles){const tab=make('button','ide-file-tab',file.split('/').pop());tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(file===activeFile));tab.title=file;if(typeof baseProject!=='undefined'&&baseProject?.files[file]!==project.files[file])tab.append(make('span','ide-tab-dirty','●'));tab.onclick=()=>switchFile(file);tabs.append(tab);}const entries=ChaosIDEContracts.visibleEntries(files,[...state.collapsed]);for(const item of entries){const ancestors=item.path.split('/').slice(0,-1);if(ancestors.some((_,i)=>state.collapsed.has(ancestors.slice(0,i+1).join('/'))))continue;const row=make('button','ide-file-row'+(state.selectedPath===item.path?' selected':''));row.style.paddingLeft=(8+ancestors.length*12)+'px';row.title=item.path;row.dataset.path=item.path;row.dataset.directory=String(item.directory);row.setAttribute('role','treeitem');if(item.directory)row.setAttribute('aria-expanded',String(!state.collapsed.has(item.path)));row.append(make('span','',item.directory?(state.collapsed.has(item.path)?'▸':'▾'):'ƒ'),make('span','file-label',item.path.split('/').pop()));if(item.path===project.entry)row.append(make('span','entry-badge','入口'));row.onclick=()=>{state.selectedPath=item.path;state.selectedDirectory=item.directory;if(item.directory){state.collapsed.has(item.path)?state.collapsed.delete(item.path):state.collapsed.add(item.path);renderFiles();}else switchFile(item.path);};tree.append(row);}$('#ideProjectScope').textContent=project.name+' · '+files.length+' 个模块';updateChatContext();}
 let fileOperation=null;
 function openFileDialog(operation,directory=false){fileOperation={operation,directory,path:state.selectedPath||activeFile};$('#ideFileDialogTitle').textContent=operation==='rename'?'重命名'+(directory?'目录':'模块'):directory?'新建目录与入口模块':'新建模块';$('#ideFilePath').value=operation==='rename'?fileOperation.path:'';$('#ideFilePath').placeholder=directory?'lib':'lib/math.chaos';$('#ideFileDialogHelp').textContent=directory?'目录内自动建立 __init__.chaos。所有路径均属于当前项目包。':'使用项目内相对路径和 .chaos 扩展名；重命名会同步更新导入。';$('#ideFileDialog').showModal();$('#ideFilePath').focus();}
 async function fileAction(body){const committed=await save(),serial=editSerial;const result=await request('/api/projects/'+encodeURIComponent(project.id)+'/files',{version:committed.version,...body});if(serial===editSerial&&!dirty&&!controlDraft){if(body.operation==='create')activeFile=body.directory?body.path+'/__init__.chaos':body.path;else if(body.operation==='rename'&&activeFile===body.path)activeFile=body.newPath;else if(body.operation==='rename'&&body.directory&&activeFile.startsWith(body.path+'/'))activeFile=body.newPath+activeFile.slice(body.path.length);else if(!(activeFile in result.files))activeFile=result.entry;}acceptRemote(result);queueBridge();toast('项目文件已更新');return result;}
 $('#addModule').onclick=()=>openFileDialog('create');$('#ideNewFolder').onclick=()=>openFileDialog('create',true);$('#ideRename').onclick=()=>openFileDialog('rename',state.selectedDirectory);$('#ideDelete').onclick=guarded(async()=>{const path=state.selectedPath||activeFile;if(!confirm('删除项目内'+(state.selectedDirectory?'目录及其中模块':'模块')+'「'+path+'」？'))return;await fileAction({operation:'delete',path,directory:state.selectedDirectory});});$('#ideFileDismiss').onclick=()=>$('#ideFileDialog').close();$('#ideFileForm').onsubmit=guarded(async e=>{e.preventDefault();const path=$('#ideFilePath').value.trim();if(!path)return;const body=fileOperation.operation==='rename'?{operation:'rename',path:fileOperation.path,newPath:path,directory:fileOperation.directory}:{operation:'create',path,directory:fileOperation.directory,source:fileOperation.directory?undefined:'// '+path+'\nexport {};\n'};await fileAction(body);$('#ideFileDialog').close();});$('#moduleSelect').onchange=guarded(e=>switchFile(e.target.value));
 function replaceDraft(text){const before=sourceText(),edits=ChaosIDEContracts.formatChanges(before,text);if(!edits.length)return;if(state.editor){const changes=state.editor.state.changes(edits),selection=state.editor.state.selection.map(changes);state.editor.dispatch({changes,selection,userEvent:'input.format'});}else{const start=source.selectionStart,end=source.selectionEnd;source.value=text;source.setSelectionRange(Math.min(start,text.length),Math.min(end,text.length));source.dispatchEvent(new Event('input',{bubbles:true}));}}
 async function formatDraft(){if(!project)return;if(state.editor?.composing||state.editor?.compositionStarted)throw new Error("请先完成输入法组字，再格式化或保存。");const compositionEpoch=state.compositionEpoch,ticket=analysisSnapshot(),file=ticket.file,text=ticket.source;try{const result=await request('/api/language/format-source',{source:text,file,languageVersion:languageVersions(file,text)[file],lineWidth:88,indentSize:2});if(!snapshotCurrent(ticket))throw new Error('格式化期间工作区或源码已变化，已保留当前草稿。');if(compositionEpoch!==state.compositionEpoch||state.editor?.composing||state.editor?.compositionStarted)throw new Error('格式化期间发生输入法组字；已保留当前草稿，请重试。');replaceDraft(result.source);scheduleAnalysis();return result;}catch(error){if(snapshotCurrent(ticket))showDiagnostics([{from:0,to:0,severity:'error',message:error.message}]);throw error;}}
 try{$('#formatOnSave').checked=localStorage.getItem('chaos.ide.formatOnSave')!=='false';}catch{}
 $('#formatOnSave').onchange=()=>{try{localStorage.setItem('chaos.ide.formatOnSave',String($('#formatOnSave').checked));}catch{}};
 $('#formatSource').onclick=guarded(formatDraft);$('#checkSource').onclick=guarded(refreshAnalysis);$('#ideSearch').onclick=()=>{if(state.editor){cm.openSearchPanel(state.editor);}};
 async function formatBeforeSave(){if(!project||!state.editor||snapshotView||state.editor.state.readOnly||source.disabled||source.readOnly||state.bridgeWriting||!$('#formatOnSave').checked||syntaxDirty||(dirty?editFrom!=='source':view!=='source'))return;if(state.formatTask)return state.formatTask;state.formatTask=formatDraft();try{await state.formatTask;}finally{state.formatTask=null;}}
 function savedProjectVersion(){return typeof remoteProject!=='undefined'&&remoteProject?.id===project.id&&remoteProject.version>project.version?remoteProject.version:typeof baseProject!=='undefined'&&baseProject?.id===project.id?baseProject.version:project.version;}
 function editorBaseVersion(){return typeof baseProject!=='undefined'&&baseProject?.id===project.id?baseProject.version:project.version;}
 function assistantBlockReason(){
  if(!project||!state.editor)return '项目尚未载入';
  if(snapshotView)return '执行快照只读；请返回编辑版本';
  if(saveTask)return '正在保存；请等待完成';
  if(remoteProject?.id===project.id&&remoteProject.version>project.version)return '远端版本已更新；先处理版本冲突，草稿已保留';
  if(syntaxDirty||controlDraft||dirty&&editFrom!=='source')return '请先处理结构或画布草稿，再发送源码';
  if(stringifyJSON(project.capabilities)!==stringifyJSON(baseProject?.capabilities))return '项目权限有未保存修改；请先保存或处理';
  if(project.name!==baseProject?.name)return '项目设置有未保存修改；请先处理';
  return '';
 }
 function editorContext(){
  const reason=assistantBlockReason();if(reason)throw new Error(reason+'。');
  const body=analysisBody(),selection=state.editor.state.selection.main;
  return {files:body.files,entry:body.entry,languageVersions:body.languageVersions,activeFile:body.file,selection:{anchor:selection.anchor,head:selection.head}};
 }
 function contentIdentity(context){return stringifyJSON([context.entry,Object.entries(context.files).sort(),Object.entries(context.languageVersions).sort()]);}
 function chatTicket(){return {projectId:project.id,epoch:state.chatEpoch};}
 function chatCurrent(ticket){return project?.id===ticket.projectId&&state.chatEpoch===ticket.epoch;}
 function workspaceTicket(context){return {...chatTicket(),version:editorBaseVersion(),content:contentIdentity(context),editRevision:editSerial,capabilities:stringifyJSON(project.capabilities)};}
 function workspaceCurrent(ticket){
  if(!chatCurrent(ticket)||ticket.version!==editorBaseVersion()||ticket.editRevision!==editSerial||ticket.capabilities!==stringifyJSON(project.capabilities)||assistantBlockReason())return false;
  return ticket.content===contentIdentity(editorContext());
 }
 function updateChatContext(){
  if(!project||!state.editor)return;
  const selection=state.editor.state.selection.main,doc=state.editor.state.doc,from=doc.lineAt(selection.from).number,to=doc.lineAt(selection.to).number;
  const position=selection.empty?'光标 L'+from:'选区 L'+from+'–'+to;
  const analysis=state.analysis&&snapshotCurrent(state.analysisTicket)?' · 诊断 '+(state.analysis.diagnostics||[]).length:'';
  const text=(activeFile||project.entry)+' · '+position+' · '+(dirty?'当前草稿':'已保存')+' · 基于 v'+editorBaseVersion()+analysis;
  $('#ideEditorContext').textContent=text;$('#ideEditorContext').title=text;
  const reason=assistantBlockReason();$('#ideChatContext').textContent=reason||text+' · 发送完整源码包';
  $('#ideChatContext').classList.toggle('blocked',!!reason);
 }
 new MutationObserver(updateChatContext).observe($('#syncBadge'),{childList:true,subtree:true});
 function assistantPath(id='',projectId=project.id){return '/api/projects/'+encodeURIComponent(projectId)+'/assistant'+(id?'/'+encodeURIComponent(id):'');}
 function agentState(message,busy=false){$('#ideAssistantState').textContent=message;$('#ideAssistantState').classList.toggle('busy',busy);$('#ideChatSend').disabled=busy;$('#ideNewChat').disabled=busy;$('#ideChatCancel').hidden=!busy;$('.ide-assistant').setAttribute('aria-busy',String(busy));}
 function message(role,text){const row=make('div','ide-chat-message '+role);row.append(make('h4','',role==='user'?'你':'系统编程助手'),make('p','',text));$('#ideChatHistory').append(row);return row;}
 function renderJob(job){
  if(job.projectId!==project?.id)return;
  const history=$('#ideChatHistory');let panel=history.querySelector('[data-job="'+CSS.escape(job.id)+'"]');
  if(!panel){message('user',job.prompt);panel=message('assistant','');panel.dataset.job=job.id;}
  const signature=stringifyJSON([job.status,job.reply,job.error,job.changes,job.entry,job.validation,job.appliedVersion]);
  if(panel.dataset.render===signature)return;
  const following=history.scrollHeight-history.scrollTop-history.clientHeight<48,position=history.scrollTop;
  const expanded=new Set([...panel.querySelectorAll('.ide-change[open]')].map(row=>row.querySelector('summary')?.textContent));
  const progress=panel.querySelector('.ide-job-progress');
  panel.dataset.render=signature;panel.replaceChildren(make('h4','','系统编程助手'));
  if(progress)panel.append(progress);
  const origin=job.editorContext;
  panel.append(make('p','ide-proposal-context',origin?'输入草稿 · v'+job.baseVersion+' · '+origin.activeFile+' · '+origin.draftHash.slice(0,12):'已保存项目 · v'+job.baseVersion));
  if(job.reply)panel.append(make('p','',job.reply));
  if(job.error)panel.append(make('p','ide-validation invalid',typeof job.error==='string'?job.error:stringifyJSON(job.error)));
  for(const change of job.changes||[]){const detail=make('details','ide-change');detail.append(make('summary','',change.path+(change.before===null?' · 新建':change.after===null?' · 删除':' · 修改')));if(change.before!==null)detail.append(codePreview(change.before,{file:change.path,label:origin?'输入草稿':'修改前',languageVersions:origin?.languageVersions},'removed'));if(change.after!==null)detail.append(codePreview(change.after,{file:change.path,label:'提案',languageVersions:{[change.path]:2}},'added'));panel.append(detail);}
  if(job.entry!==job.baseEntry)panel.append(make('p','ide-validation','入口：'+job.baseEntry+' → '+job.entry));
  if(job.validation){const ok=job.validation.ok;panel.append(make('div','ide-validation'+(ok?'':' invalid'),ok?'提案通过程序校验':'校验未通过：'+(job.validation.errors||[]).map(e=>typeof e==='string'?e:e.message||stringifyJSON(e)).join('；')));}
  if((job.changes?.length||job.entry!==job.baseEntry)&&job.validation?.ok&&!job.appliedVersion&&['completed','succeeded','ready'].includes(job.status)){
   const apply=make('button','primary ide-apply','应用并保存'),status=make('p','ide-apply-status');status.setAttribute('role','status');
   apply.onclick=guarded(async()=>{
    const scope=chatTicket();if(job.projectId!==scope.projectId)throw new Error('提案属于其他项目。');
    const reason=assistantBlockReason();if(reason){status.textContent=reason;return;}
    if(!origin&&(dirty||controlDraft)){status.textContent='此旧提案基于已保存版本；请先处理本地草稿。';return;}
    if(job.baseVersion!==editorBaseVersion()){status.textContent='提案版本已过期；请基于当前草稿重新请求。';return;}
    const current=editorContext(),ticket=workspaceTicket(current);apply.disabled=true;status.textContent='校验输入草稿与项目版本…';
    try{
     const body={version:job.baseVersion};if(origin)body.editorContext=current;
     const result=await request(assistantPath(job.id,scope.projectId)+'/apply',body);
     if(!chatCurrent(scope))return;
     if(result.id!==scope.projectId)throw new Error('应用响应与项目不匹配。');
     if(workspaceCurrent(ticket))setProject(result);else acceptRemote(result);
     job.appliedVersion=result.version;renderJob(job);queueBridge();toast(dirty||controlDraft?'提案已保存；期间的新草稿保留，请处理版本提示。':'提案已应用并保存到 v'+result.version);
    }catch(error){if(chatCurrent(scope))status.textContent=(error.status===409?'输入草稿或版本已变化；当前草稿保留，请重新请求。 ':'未应用：')+error.message;}
    finally{if(chatCurrent(scope))apply.disabled=false;}
   });panel.append(apply,status);
  }
  if(job.appliedVersion)panel.append(make('div','ide-validation','已应用并保存到 v'+job.appliedVersion));
  for(const row of panel.querySelectorAll('.ide-change'))if(expanded.has(row.querySelector('summary')?.textContent))row.open=true;
  history.scrollTop=following?history.scrollHeight:position;
 }
 async function loadChatHistory(){if(!project)return;const ticket=chatTicket(),requestId=++state.historyRequest,current=()=>chatCurrent(ticket)&&requestId===state.historyRequest;try{const data=await request(assistantPath('',ticket.projectId));if(!current())return;$('#ideChatHistory').replaceChildren();const jobs=(Array.isArray(data)?data:data.items||[]).slice().sort((a,b)=>Number(a.createdAt)-Number(b.createdAt)).slice(-12);for(const job of jobs)renderJob(job);const latest=jobs[jobs.length-1];state.sessionId=latest?.sessionId||null;const running=jobs.slice().reverse().find(job=>['queued','pending','running','validating'].includes(job.status));if(running){state.job=running;watchJob(running.id,ticket);}else agentState(latest?'已恢复最近对话 · 可继续发送':'发送当前文件、选区与完整源码草稿');}catch(error){if(current())agentState('编程助手暂不可用：'+error.message);}}
 function stopJobWatch(){const watch=state.jobWatch;state.jobWatch=null;if(watch){watch.stream?.close();clearTimeout(watch.timer);}}
 function watching(watch){return state.jobWatch===watch&&chatCurrent(watch.ticket)&&state.job?.id===watch.id;}
 function renderJobActivity(watch,event){
  const history=$('#ideChatHistory'),panel=history.querySelector('[data-job="'+CSS.escape(watch.id)+'"]');if(!panel)return;
  const following=history.scrollHeight-history.scrollTop-history.clientHeight<48,position=history.scrollTop;
  let progress=panel.querySelector('.ide-job-progress');
  if(!progress){progress=make('details','ide-job-progress');progress.append(make('summary'),make('ol'));panel.append(progress);}
  const payload=event.payload||{};
  const detail=event.type==='plugin.status'?[payload.pluginId,payload.state].filter(Boolean).join(' · '):event.type==='route.selected'?[payload.provider,payload.model,payload.backend].filter(Boolean).join(' · '):'';
  const summary=(typeof payload.summary==='string'?payload.summary:typeof payload.detail==='string'?payload.detail:detail||event.type).slice(0,300);
  watch.latest=summary;watch.eventCount++;
  progress.querySelector('summary').textContent='执行过程 · '+watch.eventCount+' 条 · '+summary;
  const row=make('li','',summary);row.dataset.eventId=String(event.id);row.title=event.type;progress.querySelector('ol').append(row);
  const list=progress.querySelector('ol');while(list.children.length>40)list.firstElementChild.remove();
  history.scrollTop=following?history.scrollHeight:position;
  agentState((watch.connected?'实时 · ':'恢复连接中 · ')+summary,true);
 }
 async function refreshJob(watch){
  if(!watching(watch))return;
  if(watch.pending){watch.again=true;return watch.pending;}
  clearTimeout(watch.timer);
  const work=(async()=>{
   try{
    const job=await request(assistantPath(watch.id,watch.ticket.projectId));
    if(!watching(watch))return;
    if(job.id!==watch.id||job.projectId!==watch.ticket.projectId)throw Error('任务响应身份不匹配');
    state.job=job;state.sessionId=job.sessionId||state.sessionId;renderJob(job);
    if(['queued','pending','running','validating'].includes(job.status)){
      agentState((watch.connected?'实时 · ':'恢复连接中 · ')+(watch.latest||'系统智能体正在处理 · '+job.status),true);
    }else if(watch.terminalSeen){
      stopJobWatch();state.job=null;
      agentState(job.status==='cancelled'?'已停止':job.status==='interrupted'?'执行已中断 · 请检查现有结果后重新发送':job.error?'任务结束，请查看错误':'已完成 · 检查差异后应用并保存');
    }else{
      agentState('结果已返回 · 正在同步最后的执行事件…',true);
    }
   }catch(error){if(watching(watch))agentState('连接暂时中断，正在恢复；原任务仍被跟踪，可继续编辑或请求停止。',true);}
  })();
  watch.pending=work;
  await work;watch.pending=null;
  if(watching(watch)){const delay=watch.again?0:5000;watch.again=false;watch.timer=setTimeout(()=>refreshJob(watch),delay);}
 }
 function watchJob(id,ticket){
  stopJobWatch();
  const watch={id,ticket,stream:null,timer:null,pending:null,again:false,connected:false,latest:'',eventCount:0,terminalSeen:false};
  state.jobWatch=watch;
  watch.stream=window.ChaosRealtime.subscribe({url:assistantPath(id,ticket.projectId)+'/events/stream',
   onState(value){if(!watching(watch))return;watch.connected=value==='connected';agentState(watch.connected?'实时已连接 · '+(watch.latest||'等待执行进度'):'连接暂时中断，正在恢复原任务…',true);if(watch.connected)refreshJob(watch);},
   onEvent(event){if(!watching(watch))return;if(event.taskId!==id)throw Error('任务事件身份不匹配');renderJobActivity(watch,event);if(['task.completed','task.failed','task.cancelled','task.interrupted'].includes(event.type))watch.terminalSeen=true;if(event.type.startsWith('task.'))return refreshJob(watch);}
  });
  refreshJob(watch);
 }
 $('#ideChatForm').onsubmit=guarded(async e=>{
  e.preventDefault();if(state.job||state.submitting)return;const raw=$('#ideChatPrompt').value,prompt=raw.trim();if(!prompt)return;
  let context;try{context=editorContext();}catch(error){agentState(error.message);return;}
  const ticket=workspaceTicket(context);state.historyRequest++;state.submitting=ticket;agentState('正在发送 v'+ticket.version+' 的当前源码草稿…',true);
  try{const job=await request(assistantPath('',ticket.projectId),{version:ticket.version,prompt,sessionId:state.sessionId||undefined,editorContext:context});if(!chatCurrent(ticket))return;if(job.projectId!==ticket.projectId)throw new Error('助手响应与项目不匹配。');state.job=job;state.sessionId=job.sessionId||state.sessionId;if($('#ideChatPrompt').value===raw)$('#ideChatPrompt').value='';renderJob(job);watchJob(job.id,ticket);}
  catch(error){if(chatCurrent(ticket))agentState(error.message);}
  finally{if(state.submitting===ticket)state.submitting=false;}
 });
 $('#ideChatCancel').onclick=guarded(async()=>{const job=state.job,ticket=chatTicket();if(job){await request(assistantPath(job.id,ticket.projectId)+'/cancel',{});if(chatCurrent(ticket)&&state.job?.id===job.id)agentState('正在停止当前任务…',true);}});
 $('#ideNewChat').onclick=()=>{if(state.job||state.submitting)return toast('请先停止当前任务或等待完成。',true);stopJobWatch();state.sessionId=null;state.chatEpoch++;$('#ideChatHistory').replaceChildren();agentState('新的对话 · 发送当前源码草稿');};
 const compactLayout=window.matchMedia('(max-width:850px)'),layout=$('.ide-layout');
 let desktopAssistantOpen=false;
 function setAssistant(open,focus=false){
  layout.classList.toggle('assistant-closed',!open);$('#ideAssistantToggle').setAttribute('aria-expanded',String(open));
  if(compactLayout.matches&&open){layout.classList.remove('files-open');$('#ideFilesToggle').setAttribute('aria-expanded','false');}
  if(focus){if(open)$('#ideChatPrompt').focus();else if(state.editor)state.editor.focus();else source.focus();}
  state.editor?.requestMeasure();
 }
 $('#ideAssistantToggle').onclick=()=>{const open=layout.classList.contains('assistant-closed');if(!compactLayout.matches)desktopAssistantOpen=open;setAssistant(open,true);};
 $('#ideContextAssistant').onclick=()=>{if(!compactLayout.matches)desktopAssistantOpen=true;setAssistant(true,true);updateChatContext();};
 $('#ideAssistantClose').onclick=()=>{if(!compactLayout.matches)desktopAssistantOpen=false;setAssistant(false,true);};
 $('#ideFilesToggle').onclick=()=>{setAssistant(false);const open=layout.classList.toggle('files-open');$('#ideFilesToggle').setAttribute('aria-expanded',String(open));if(open)$('#ideFileTree').querySelector('button')?.focus();else state.editor?.focus();state.editor?.requestMeasure();};
 $('#ideAssistantPanel').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();$('#ideAssistantClose').click();}});
 compactLayout.addEventListener('change',()=>{const open=!compactLayout.matches&&desktopAssistantOpen,returnFocus=(!open&&$('#ideAssistantPanel').contains(document.activeElement))||(compactLayout.matches&&$('#ideFilesPanel').contains(document.activeElement));layout.classList.remove('files-open');$('#ideFilesToggle').setAttribute('aria-expanded','false');setAssistant(open,returnFocus);});
 setAssistant(false);

 function visible(){const active=$('#sourceView').classList.contains('active');document.body.classList.toggle('ide-source-active',active);if(active){queueBridge();state.editor?.requestMeasure();}}
 new MutationObserver(visible).observe($('#sourceView'),{attributes:true,attributeFilter:['class']});
 if(cm){try{state.editor=new cm.EditorView({state:editorState(nativeValue.get.call(source)),parent:mount});globalThis.ChaosCodeAppearance?.bind?.(state.editor,cm);$('#sourceView').classList.add('ide-enhanced');queueBridge();}catch(error){toast('编辑器载入失败，仍可使用基础源码输入：'+error.message,true);}}else toast('编辑器资源未载入，已保留基础源码输入。',true);
 visible();
 // Explicit Program candidates are local buffers, independent of source and run snapshots.
 const programDrafts=new Map();let programDialog=null,programDraft=null,programOpenRequest=0,programViewEpoch=0;
 const programPath=id=>'/api/v1/projects/'+encodeURIComponent(id);
 function programStatus(d,text){if(programDraft===d&&programDialog)programDialog.querySelector('[data-program-status]').textContent=text;}
 function programContext(d){const current=project?.id===d.projectId?savedProjectVersion():null;return '候选基准 v'+d.base.version+' · 工作区已保存 v'+(current??'—')+(runVersion!==null&&runSnapshot?.id===d.projectId?' · 当前运行快照 v'+runVersion:'')+(dirty||controlDraft?' · 本地源码/结构草稿保留':'');}
 function refreshProgramContext(){if(programDraft&&programDialog?.open){programDialog.querySelector('[data-program-context]').textContent=programContext(programDraft);programButtons(programDraft);}}
 function programButtons(d){if(programDraft!==d||!programDialog)return;for(const button of programDialog.querySelectorAll('[data-program-replace]'))button.disabled=d.adopting||snapshotView||project?.id!==d.projectId;const allowed=project?.id===d.projectId&&project.capabilities?.includes('program.adopt');programDialog.querySelector('[data-program-adopt]').disabled=!d.validated||d.adopting||snapshotView||!allowed;programDialog.querySelector('[data-program-copy]').disabled=!d.validated?.source;programDialog.querySelector('[data-program-permission]').textContent=allowed?'项目已授予 program.adopt；采用时仍检查当前权限和版本。':'项目未授予 program.adopt。可编辑与验证候选；此面板不会授予权限。';}
 function changeProgramDraft(d){d.serial++;d.dirty=true;d.validated=null;programStatus(d,'候选已修改；请重新校验。');if(programDraft===d){programDialog.querySelector('[data-program-diff]').replaceChildren();programButtons(d);}}
 function programField(parent,label,value,change,rows=0,json=false){const field=make('div','program-candidate-field',label),input=make(rows?'textarea':'input');input.setAttribute('aria-label',label);input.value=value;if(rows)input.rows=rows;field.append(input);
 let preview=null,code=null;const paint=()=>{if(!preview?.open)return;const text=input.value,tokens=ChaosProgramContracts.jsonTokens(text),fragment=document.createDocumentFragment(),lines=text.split('\n');let offset=0,index=0;lines.forEach((line,lineIndex)=>{const row=make('span','chaos-code-line');row.dataset.line=String(lineIndex+1);const end=offset+line.length;let cursor=offset;while(index<tokens.length&&tokens[index].from<end){const token=tokens[index++];row.append(document.createTextNode(text.slice(cursor,token.from)),make('span','chaos-token-'+token.type,text.slice(token.from,token.to)));cursor=token.to;}row.append(document.createTextNode(text.slice(cursor,end)+(lineIndex<lines.length-1?'\n':'')));fragment.append(row);offset=end+1;});code.replaceChildren(fragment);};
 if(json){const format=make('button','','格式化 JSON'),status=make('span','ide-small');format.type='button';format.setAttribute('aria-label','格式化 '+label);status.setAttribute('role','status');format.onclick=()=>{try{const value=stringifyJSON(parseJSON(input.value),null,2);input.value=value;change(value);paint();status.textContent='JSON 已格式化。';}catch(error){status.textContent='JSON 无效，原草稿已保留：'+error.message;}};field.append(format,status);preview=make('details');preview.dataset.programJsonPreview='';preview.append(make('summary','','JSON 高亮预览'));const pre=make('pre','chaos-code');pre.setAttribute('aria-label',label+'高亮预览');pre.dataset.language='json';code=make('code');pre.append(code);preview.append(pre);preview.ontoggle=paint;field.append(preview);}
 input.oninput=()=>{change(input.value);paint();};parent.append(field);return input;}
 async function openProgramCandidate(){if(!project)return;const id=project.id,requestId=++programOpenRequest;let d=programDrafts.get(id);if(!d){const [base,schema]=await Promise.all([request(programPath(id)+'/program-snapshot'),request('/api/v1/programs/schema')]);if(project?.id!==id||requestId!==programOpenRequest)return;d={projectId:id,base,schema,definitionText:stringifyJSON(base.definition,null,2),casesText:'[\n  {"input": null, "expect": null}\n]',reason:'',serial:0,dirty:false,validated:null,adopting:false,receipts:[]};programDrafts.set(id,d);}programDraft=d;programViewEpoch++;if(!programDialog){programDialog=make('dialog','program-candidate-dialog');programDialog.setAttribute('aria-label','程序候选预览');document.body.append(programDialog);programDialog.addEventListener('close',()=>{programViewEpoch++;if(programDraft?.history){programDraft.history.detailLoading=false;programDraft.history.loading=false;}});}renderProgramCandidate();programDialog.showModal();loadProgramHistory(d,true);}
 function renderProgramCandidate(){const d=programDraft;programDialog.replaceChildren(make('h2','','程序候选预览'),make('p','ide-small','候选是独立的页面内存草稿，关闭可保留，刷新会丢失。校验只编译，不执行；采用会重新运行测试与显式夹具并以版本和定义哈希提交。正在执行的程序保持原快照。'));
 const context=make('p','program-candidate-context',programContext(d));context.dataset.programContext='';const permission=make('p','ide-small');permission.dataset.programPermission='';const status=make('p','program-candidate-status','编辑明确的程序定义 JSON，或载入已保存的项目。');status.dataset.programStatus='';status.setAttribute('role','status');programDialog.append(context,permission,status);
 const tools=make('div','program-candidate-actions'),load=make('button','','用已保存程序替换候选'),rebase=make('button','','刷新基准，保留候选'),file=make('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','导入程序定义 JSON 文件');file.onchange=async()=>{const selected=file.files?.[0];if(!selected||d.adopting||snapshotView)return;const serial=d.serial,epoch=programViewEpoch;try{if(selected.size>1200000)throw new Error('候选定义文件超过 1.2 MB。');const text=await selected.text();if(programDraft!==d||d.serial!==serial||epoch!==programViewEpoch||project?.id!==d.projectId)throw new Error('读取期间出现新编辑，当前草稿保留。');d.definitionText=text;changeProgramDraft(d);renderProgramCandidate();}catch(error){programStatus(d,error.message);}finally{file.value='';}};
 const refresh=async replace=>{if(d.adopting||snapshotView||project?.id!==d.projectId)return;const serial=d.serial,epoch=programViewEpoch;try{const base=await request(programPath(d.projectId)+'/program-snapshot');if(programDraft!==d||d.serial!==serial||epoch!==programViewEpoch||project?.id!==d.projectId)throw new Error('载入期间出现新编辑，当前草稿保留。');d.base=base;if(replace)d.definitionText=stringifyJSON(base.definition,null,2);changeProgramDraft(d);renderProgramCandidate();programStatus(d,replace?'已载入已保存程序；本地源码草稿未改动。':'基准已刷新，候选内容保留；请重新校验差异。');}catch(error){programStatus(d,error.message);}};load.onclick=()=>refresh(true);rebase.onclick=()=>refresh(false);load.dataset.programReplace='';rebase.dataset.programReplace='';file.dataset.programReplace='';tools.append(load,rebase,file);programDialog.append(tools);const history=make('section','program-history');history.dataset.programHistory='';programDialog.append(history);renderProgramHistory(d);
 const fields=make('div','program-candidate-fields');programField(fields,'候选程序定义 JSON',d.definitionText,value=>{d.definitionText=value;changeProgramDraft(d);},15,true);programField(fields,'测试用例 JSON（可选 fixtures）',d.casesText,value=>{d.casesText=value;changeProgramDraft(d);},9,true);programDialog.append(fields);programField(programDialog,'采用原因',d.reason,value=>{d.reason=value;changeProgramDraft(d);});
 const hint=make('p','ide-small','用例格式：[{"input": 21, "expect": 42, "entry": "tests/example.chaos"}]。entry 可省略。fixtures.agents 填写 definition（完整 Agent 定义）、input、actions：[ {tool, input, expect}, …, {final} ]；fixtures.tools 填写 name、input、output。两种夹具可用 times 重复同一脚本。语言工具函数仍实际执行，真实模型与外部工具不会运行。夹具不匹配、未消费或测试失败均不提交。');programDialog.append(hint);const diff=make('div','program-candidate-diff');diff.dataset.programDiff='';programDialog.append(diff);const receipts=make('div','program-candidate-receipts');for(const receipt of d.receipts)receipts.append(make('p','',receipt));programDialog.append(receipts);
 const actions=make('div','program-candidate-actions program-candidate-footer'),validate=make('button','','校验并预览差异'),adopt=make('button','primary','采用为项目新版本'),copy=make('button','','复制程序模块源码'),download=make('button','','导出候选 JSON'),close=make('button','','关闭并保留');adopt.dataset.programAdopt='';copy.dataset.programCopy='';
 validate.onclick=async()=>{const serial=d.serial;try{const definition=parseJSON(d.definitionText);programStatus(d,'共享语言校验器检查完整程序包…');const result=await request('/api/v1/programs/validate',{definition});if(programDraft!==d||d.serial!==serial||project?.id!==d.projectId)return;d.validated={...result,serial};renderProgramDiff(d);programStatus(d,'编译校验通过 · '+result.definitionHash.slice(0,12)+'；测试和显式夹具会在采用时重跑。'+(result.source===null?' 普通模块导出不可用：'+(result.sourceError||'定义超过单模块源码上限')+'；仍可采用或导出 JSON。':''));programButtons(d);}catch(error){if(programDraft===d&&d.serial===serial){d.validated=null;programStatus(d,error.message);programButtons(d);}}};
 adopt.onclick=async()=>{if(!d.validated||d.adopting||snapshotView||project?.id!==d.projectId)return;const serial=d.serial,validated=d.validated;let cases;try{cases=parseJSON(d.casesText);}catch(error){programStatus(d,'测试用例 JSON 无效：'+error.message);return;}d.adopting=true;programButtons(d);programStatus(d,'正在重新验证、运行测试与显式夹具并尝试版本提交…');try{const result=await request(programPath(d.projectId)+'/program-adopt',{version:d.base.version,expectedHash:d.base.definitionHash,definition:validated.definition,cases,reason:d.reason});const message='候选 '+validated.definitionHash.slice(0,12)+' 已采用为 v'+result.project.version+'；旧运行仍属于原快照。';d.receipts.push(message);if(project?.id===d.projectId){acceptRemote(result.project);queueBridge();}if(d.serial===serial)d.dirty=false;if(programDraft===d){receipts.append(make('p','',message));programStatus(d,message+(d.serial===serial?'':' 采用期间的新候选输入仍已保留。'));refreshProgramContext();}}catch(error){if(programDraft===d){programStatus(d,(error.status===409?'版本或哈希冲突，候选与本地草稿均保留；可刷新基准后重新检查。 ':'采用失败，未确认提交。 ')+error.message);if(error.details)receipts.append(make('pre','',stringifyJSON(error.details,null,2)));}if(error.status===409&&project?.id===d.projectId)await refreshRemote().catch(()=>{});}finally{d.adopting=false;programButtons(d);}};
 copy.onclick=async()=>{try{if(d.validated)await navigator.clipboard.writeText(d.validated.source);programStatus(d,'已复制普通语言模块源码；未修改工作区。');}catch(error){programStatus(d,error.message);}};download.onclick=()=>{const blob=new Blob([d.definitionText],{type:'application/json'}),url=URL.createObjectURL(blob),a=make('a');a.href=url;a.download='program-candidate.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};close.onclick=()=>programDialog.close();actions.append(validate,adopt,copy,download,close);programDialog.append(actions);if(d.validated)renderProgramDiff(d);programButtons(d);}
 function programHistoryState(d){return d.history||(d.history={items:[],nextCursor:null,currentVersion:null,firstAvailableVersion:null,request:0,detailRequest:0,selected:null,revision:null,loading:false,detailLoading:false,error:'',open:true});}
 function historyTicket(d,kind){const h=programHistoryState(d);return {projectId:d.projectId,epoch:programViewEpoch,request:++h[kind]};}
 function historyCurrent(d,ticket,kind){return programDraft===d&&programDialog?.open&&ChaosProgramContracts.historyMatches(ticket,{projectId:project?.id,epoch:programViewEpoch,request:programHistoryState(d)[kind]});}
 async function loadProgramHistory(d,reset=false){const h=programHistoryState(d);if(project?.id!==d.projectId||(!reset&&h.nextCursor===null))return;const ticket=historyTicket(d,'request'),after=reset?0:h.nextCursor;h.loading=true;h.error='';renderProgramHistory(d);try{const page=await request(programPath(d.projectId)+'/program-history?after='+encodeURIComponent(after)+'&limit=20');if(!historyCurrent(d,ticket,'request'))return;h.items=ChaosProgramContracts.historyPage(reset?[]:h.items,page);h.nextCursor=page.nextCursor;h.currentVersion=page.currentVersion;h.firstAvailableVersion=page.firstAvailableVersion;}catch(error){if(historyCurrent(d,ticket,'request'))h.error=error.message;}finally{if(historyCurrent(d,ticket,'request')){h.loading=false;renderProgramHistory(d);}}}
 async function selectProgramRevision(d,version){const h=programHistoryState(d),ticket=historyTicket(d,'detailRequest');h.selected=version;h.revision=null;h.detailLoading=true;h.error='';renderProgramHistory(d);try{const revision=await request(programPath(d.projectId)+'/program-revisions/'+version);if(!historyCurrent(d,ticket,'detailRequest')||h.selected!==version)return;h.revision=revision;}catch(error){if(historyCurrent(d,ticket,'detailRequest'))h.error=error.message;}finally{if(historyCurrent(d,ticket,'detailRequest')){h.detailLoading=false;renderProgramHistory(d);}}}
 function renderProgramHistory(d){if(programDraft!==d||!programDialog)return;const mount=programDialog.querySelector('[data-program-history]');if(!mount)return;const h=programHistoryState(d);mount.replaceChildren();const details=make('details');details.open=h.open;details.ontoggle=()=>{if(details.isConnected)h.open=details.open;};details.append(make('summary','','不可变程序历史 · 浏览与比较'));const note=make('p','ide-small',h.currentVersion===null?'读取此项目已保存的历史；选择版本不会修改候选或源码。':'已保存 v'+h.currentVersion+' · 可用历史从 v'+h.firstAvailableVersion+' 开始。历史只包含程序包，不恢复权限、配置或运行状态。');details.append(note);const tools=make('div','program-candidate-actions'),refresh=make('button','','刷新历史'),more=make('button','','更多历史');refresh.disabled=h.loading;more.disabled=h.loading||h.nextCursor===null;refresh.onclick=()=>loadProgramHistory(d,true);more.onclick=()=>loadProgramHistory(d);tools.append(refresh,more);if(h.loading)tools.append(make('span','','读取中…'));details.append(tools);if(h.error)details.append(make('p','ide-validation invalid',h.error));const grid=make('div','program-history-grid'),list=make('div','program-history-list');list.setAttribute('aria-label','程序历史版本');const kinds={created:'创建',saved:'保存',patched:'图形修改',adopted:'候选采用',migration:'迁移'};for(const item of h.items){const button=make('button','program-history-version');button.dataset.programVersion=item.version;button.setAttribute('aria-pressed',String(h.selected===item.version));button.append(make('strong','','v'+item.version+' · '+(kinds[item.kind]||item.kind)),make('code','',item.definitionHash),make('span','',item.entry+' · '+new Date(item.created*1000).toLocaleString()));if(item.reason)button.append(make('span','',item.reason));if(item.runId)button.append(make('span','','运行 '+item.runId));button.onclick=()=>selectProgramRevision(d,item.version);list.append(button);}if(!h.items.length&&!h.loading)list.append(make('p','ide-small','尚无可用历史。'));const preview=make('div','program-history-preview');preview.dataset.programHistoryPreview='';if(h.detailLoading)preview.append(make('p','','正在读取 v'+h.selected+'…'));else if(h.revision){const rev=h.revision;preview.append(make('h3','','历史 v'+rev.origin.version+' · '+rev.origin.definitionHash),make('p','ide-small','以下比较使用当前候选基准 v'+d.base.version+'；载入历史不会更换此基准。'));renderProgramComparison(preview,d.base.definition,rev.definition,'当前基准','历史');const load=make('button','primary','以 v'+rev.origin.version+' 替换候选');load.dataset.programReplace='';load.disabled=d.adopting||snapshotView||project?.id!==d.projectId;load.onclick=()=>{if(programDraft!==d||project?.id!==d.projectId||d.adopting||snapshotView||h.revision!==rev)return;d.definitionText=stringifyJSON(rev.definition,null,2);d.candidateOrigin={...rev.origin};changeProgramDraft(d);renderProgramCandidate();programStatus(d,'历史 v'+rev.origin.version+' 已载入候选；采用基准仍为 v'+d.base.version+'，源码草稿未改动。请校验并以当前测试采用为新版本。');};preview.append(load,make('p','ide-small','此按钮只替换候选 JSON；当前测试用例、采用原因和源码草稿均保留。'));}else preview.append(make('p','ide-small','选择一个版本查看入口、文件和逐文件方言差异。'));grid.append(list,preview);details.append(grid);mount.append(details);}
 function renderProgramComparison(diff,base,candidate,beforeLabel,afterLabel){diff.append(make('p','ide-small','入口：'+base.entry+' → '+candidate.entry));const changes=ChaosProgramContracts.changes(base,candidate);if(!changes.length)diff.append(make('p','','没有源码或方言变更。'));for(const change of changes){const detail=make('details','ide-change');detail.append(make('summary','',change.path+' · '+(change.before===null?'新增':change.after===null?'删除':'修改')+' · 方言 '+(change.beforeVersion??'—')+' → '+(change.afterVersion??'—')));if(change.before!==null)detail.append(codePreview(change.before,{file:change.path,label:beforeLabel,entry:base.entry,files:base.files,languageVersions:base.languageVersions},'removed'));if(change.after!==null)detail.append(codePreview(change.after,{file:change.path,label:afterLabel,entry:candidate.entry,files:candidate.files,languageVersions:candidate.languageVersions},'added'));diff.append(detail);}}
 function renderProgramDiff(d){const diff=programDialog.querySelector('[data-program-diff]');diff.replaceChildren(make('h3','','已保存基准 → 候选'));renderProgramComparison(diff,d.base.definition,d.validated.definition,'基准','候选');}
 const programButton=make('button','','程序候选');programButton.id='programCandidateButton';programButton.onclick=guarded(openProgramCandidate);$('#sourceView .source-actions').append(programButton);new MutationObserver(refreshProgramContext).observe($('#syncBadge'),{childList:true,subtree:true});window.addEventListener('beforeunload',event=>{if([...programDrafts.values()].some(d=>d.dirty)){event.preventDefault();event.returnValue='';}});

 function importSourceModule(text,suggestedName='saved_value',directory='modules'){
  if(!['modules','modes','agents','teams','processes'].includes(directory))throw new Error('无效的模块目录。');
  if(!project||typeof text!=='string'||!text.trim()||text.length>1000000)throw new Error('没有可导入的源码。');
  if(snapshotView||saveTask||syntaxDirty||controlDraft||(dirty&&editFrom!=='source'))throw new Error('请先完成当前结构、图形或保存操作；源码仍保留在预览中。');
  const stem=String(suggestedName).replace(/[^A-Za-z0-9_-]/g,'_').slice(0,48)||'saved_mode';
  let file=directory+'/'+stem+'.chaos',index=2;
  while(Object.prototype.hasOwnProperty.call(project.files,file)){file=directory+'/'+stem+'_'+index+'.chaos';index++;}
  project.files[file]=text;
  // Adding a module is a local source edit. Existing drafts and the committed base stay intact.
  view='source';document.body.classList.remove('syntax-view-active');
  $$('[data-view]').forEach(button=>button.classList.toggle('active',button.dataset.view==='source'));
  $$('.view').forEach(panel=>panel.classList.toggle('active',panel.id==='sourceView'));
  markDirty('source');switchFile(file);state.selectedPath=file;state.selectedDirectory=false;
  renderFiles();syncState();toast('源码已加入本地模块草稿；保存后才会提交。');return file;
 }
 function importModeSource(text,name='saved_mode'){return importSourceModule(text,name,'modes');}
 window.ChaosIDE={goToDefinition,sourceVersionOverrides,importSourceModule,formatBeforeSave,formatDraft,switchFile,importModeSource,refresh:queueBridge,analyze:refreshAnalysis,editor:()=>state.editor,get state(){return {file:state.editorFile,revision:state.revision,source:sourceText()};}};
})();
