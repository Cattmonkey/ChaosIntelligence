'use strict';
/* Presentation only: every command delegates to the existing editor/runtime. */
(()=>{
 const byId=id=>document.getElementById(id),body=document.body,layout=document.querySelector('.ide-layout');
 const dialog=byId('wbCommands'),input=byId('wbCommandInput'),list=byId('wbCommandList'),vault=byId('wbLegacyActions');
 const media=window.matchMedia('(max-width:850px)'),storageKey='chaos.workbench.v1';
 let preferences={sidebar:true,output:false};
 try{const raw=localStorage.getItem(storageKey);const saved=raw&&raw.length<=1024?JSON.parse(raw):null;if(saved&&typeof saved==='object'&&!Array.isArray(saved))for(const key of ['sidebar','output'])if(typeof saved[key]==='boolean')preferences[key]=saved[key];}catch{}
 function persist(){try{localStorage.setItem(storageKey,JSON.stringify(preferences));}catch{}}
 function focusEditor(){const editor=window.ChaosIDE?.editor();if(editor)editor.focus();else byId('sourceEditor').focus();}
 function output(open){body.classList.toggle('wb-output-collapsed',!open);preferences.output=open;byId('wbOutputToggle').setAttribute('aria-expanded',String(open));byId('wbOutputToggle').title=open?'收起输出':'展开输出';persist();}
 function sidebar(open){preferences.sidebar=open;body.classList.toggle('wb-sidebar-hidden',!open);if(media.matches){layout.classList.toggle('files-open',open);byId('ideFilesToggle').setAttribute('aria-expanded',String(open));}persist();refresh();}
 function runPanel(open){byId('wbRunPanel').hidden=!open;body.classList.toggle('wb-run-open',open);refresh();if(open)byId('taskInput').focus();else focusEditor();}
 async function showView(name){const target=document.querySelector('[data-view="'+name+'"]');if(!target)return false;const changing=!target.classList.contains('active');if(changing)await target.onclick();refresh();if(changing&&name==='graph'&&target.classList.contains('active'))requestAnimationFrame(fit);return target.classList.contains('active');}
 function click(id){const target=byId(id);if(!target||target.disabled)return false;target.click();return true;}
 async function sourceAction(id){if(await showView('source'))click(id);}
 const commands=[];
 function add(id,label,action,options={}){commands.push(Object.freeze({id,label,action,...options}));}
 function target(id,label,targetId,options={}){add(id,label,()=>options.source?sourceAction(targetId):click(targetId),{...options,target:targetId});}
 add('files','文件：资源管理器',async()=>{runPanel(false);const already=byId('sourceView').classList.contains('active');if(await showView('source')){if(media.matches&&!layout.classList.contains('assistant-closed'))click('ideAssistantClose');sidebar(already?(media.matches?!layout.classList.contains('files-open'):!preferences.sidebar):true);if(preferences.sidebar)byId('ideFileTree').querySelector('button')?.focus();}});
 add('file.quickOpen','文件：快速打开…',()=>openPalette('files'),{shortcut:'⌘ / Ctrl P'});
 add('search','编辑：查找当前文件',async()=>{runPanel(false);await sourceAction('ideSearch');},{target:'ideSearch'});
 add('edit.appearance','编辑：代码配色',()=>window.ChaosCodeAppearance.open(byId('wbCommandButton')),{available:()=>typeof window.ChaosCodeAppearance?.open==='function'});
 add('run.options','运行：输入与调试设置',()=>runPanel(byId('wbRunPanel').hidden));
 add('run.close','运行：关闭输入面板',()=>runPanel(false));
 add('run.guide','运行：追加当前运行指导',()=>{runPanel(true);byId('runGuidanceInput').focus();},{available:()=>typeof busy!=='undefined'&&busy&&typeof activeRunId!=='undefined'&&!!activeRunId});
 target('run.start','运行：保存并运行入口程序','runButton');
 target('run.stop','运行：停止当前运行','cancelButton',{available:()=>!byId('cancelButton').classList.contains('hidden')});
 add('agent','智能体：打开 / 关闭编程助手',async()=>{runPanel(false);if(await showView('source'))click('ideAssistantToggle');});
 add('graph','视图：关系画布',async()=>{runPanel(false);await showView('graph');});
 add('view.source','视图：源码编辑器',()=>showView('source'));
 add('view.structure','视图：程序结构',()=>showView('structure'));
 add('view.proof','视图：结构验证',()=>showView('proof'));
 add('view.reset','视图：恢复默认布局',()=>{runPanel(false);sidebar(true);if(media.matches){layout.classList.remove('files-open');byId('ideFilesToggle').setAttribute('aria-expanded','false');}output(false);if(!layout.classList.contains('assistant-closed'))click('ideAssistantClose');body.classList.remove('wb-inspector-open','wb-modes-open');body.classList.toggle('wb-graph-tools',!media.matches);refresh();});
 add('output.toggle','视图：展开 / 收起输出',()=>output(body.classList.contains('wb-output-collapsed')));
 add('sidebar.toggle','视图：显示 / 隐藏侧栏',()=>{if(!byId('wbRunPanel').hidden)runPanel(false);else if(body.dataset.workbenchView==='graph'){body.classList.toggle('wb-graph-tools');refresh();}else sidebar(media.matches?!layout.classList.contains('files-open'):!preferences.sidebar);},{shortcut:'⌘ / Ctrl B'});
 add('graph.tools','画布：显示 / 隐藏构建模块',async()=>{if(await showView('graph'))body.classList.toggle('wb-graph-tools');});
 add('graph.inspector','画布：显示 / 隐藏节点配置',async()=>{if(await showView('graph')){body.classList.toggle('wb-inspector-open');refresh();}});
 add('project.capabilities','项目：权限与设备',()=>openPermissions());
 add('settings','系统：配置模型、工具与后台任务',()=>{location.href='/control';});
 add('docs','帮助：语言与开发文档',()=>{location.href='/docs';});
 for(const [id,label,element,source]of [
  ['file.new','文件：新建模块','addModule',true],['file.folder','文件：新建目录','ideNewFolder',true],['file.rename','文件：重命名当前文件','ideRename',true],['file.delete','文件：删除当前文件','ideDelete',true],['file.entry','文件：设为程序入口','setEntry',true],['file.save','文件：保存全部草稿','saveButton',false],
  ['edit.suggest','编辑：智能代码提示','ideSuggestButton',true],['edit.format','编辑：格式化源码','formatSource',true],['edit.check','语言：检查源码','checkSource',true],['edit.compile','语言：完整编译报告','compileSource',true],['edit.outline','语言：当前文件声明','ideOutlineButton',true],['edit.signature','语言：参数提示','ideSignatureButton',true],['edit.definition','语言：转到定义','ideDefinitionButton',true],['edit.references','语言：查找引用','ideReferencesButton',true],['edit.rename','语言：重命名符号','ideRenameSymbolButton',true],['edit.example','语言：载入示例','languageDemo',true],
  ['debug','调试：打开 / 关闭源码调试器','debugToggle',true],['program.candidate','程序：候选、验证与采用','programCandidateButton',true],['team','智能体：编写团队','teamAuthorButton',true],['processes','智能体：持续进程','processesButton',true],['sessions','智能体：原生会话','agentSessionsButton',true],['computers','智能体：电脑观察','computersButton',true],
  ['project.export','项目：导出 JSON','exportButton',false],['project.import','项目：导入 JSON','importButton',false],['project.reload','项目：重新载入已保存版本','reloadButton',false],['project.backup','项目：导出备份草稿','restoreDraft',false],['run.snapshot','运行：查看执行快照','viewSnapshot',false],['help','帮助：快速开始','helpButton',false],['graph.optimize','画布：平台调整关系','optimizeButton',false],['graph.fit','画布：适应画布','fitButton',false],['graph.hierarchy','画布：显示管理关系','hierarchyButton',false],['graph.validate','画布：重新验证','validateButton',false],['structure.save','结构：保存程序结构','applyProgram',false]
 ])target(id,label,element,{source});
 for(const [id,label,element]of [['format.save','编辑：切换保存时格式化','formatOnSave'],['run.trace','运行：切换逐表达式追踪','expressionTrace'],['run.follow','运行：切换跟踪最新运行','followRuns']])add(id,label,()=>{click(element);refresh();},{target:element});
 for(const [tab,label]of [['events','执行事件'],['result','运行结果'],['artifact','页面、报告与演示'],['history','运行历史']])add('output.'+tab,'输出：'+label,()=>{output(true);document.querySelector('[data-console="'+tab+'"]').click();});
 add('modes','智能体：目标、计划与录制技能',()=>{const open=!body.classList.contains('wb-modes-open');body.classList.toggle('wb-modes-open',open);if(open){if(byId('modesBody').hidden)document.querySelector('#modesPanel .modes-toggle').click();byId('modesPanel').scrollIntoView({block:'nearest'});document.querySelector('#modesPanel button')?.focus();}else focusEditor();});
 // One transient permission editor; applying changes only the current project
 // draft. The original Save/Run controller remains the sole versioned writer.
 const permissionDialog=byId('wbPermissions');let permissionRequest=0;
 const permissionNode=(tag,text)=>{const element=document.createElement(tag);element.textContent=text;return element;};
 function permissionNotice(text){byId('wbPermissionStatus').textContent=text;}
 function closePermissions(){permissionRequest++;permissionDialog.close();focusEditor();}
 byId('wbPermissionClose').onclick=closePermissions;
 permissionDialog.addEventListener('cancel',()=>{permissionRequest++;});
 async function openPermissions(){
  if(!project)return;const request=++permissionRequest,id=project.id,version=project.version,original=JSON.stringify(project.capabilities||[]),selectedCaps=new Set(project.capabilities||[]);
  const list=byId('wbPermissionList'),devices=byId('wbPermissionDevices'),apply=byId('wbPermissionApply');list.replaceChildren();devices.replaceChildren();apply.disabled=true;
  byId('wbPermissionProject').textContent=project.name+' · '+id+' · 草稿基于 v'+version;
  permissionNotice('读取能力与静态设备清单…');if(!permissionDialog.open)permissionDialog.showModal();
  const current=()=>request===permissionRequest&&project?.id===id&&project.version===version&&JSON.stringify(project.capabilities||[])===original;
  try{
   const catalog=await api('/api/v1/capabilities');if(request!==permissionRequest)return;
   if(!current()){permissionNotice('项目或版本已经改变；请关闭后重新打开，原草稿保持不变。');return;}
   const metadata=catalog.tools||{},names=[...new Set([...Object.keys(metadata).filter(name=>metadata[name].scope!=='platform'&&name!=='graph.patch'),...selectedCaps])].sort();
   const readonly=typeof snapshotView!=='undefined'&&snapshotView;
   for(const name of names){const spec=metadata[name],row=permissionNode('label',''),input=document.createElement('input'),copy=permissionNode('span','');input.type='checkbox';input.checked=selectedCaps.has(name);input.disabled=readonly;input.dataset.projectCapability=name;input.setAttribute('aria-label','项目权限 '+name);input.onchange=()=>{if(input.checked)selectedCaps.add(name);else selectedCaps.delete(name);};copy.append(permissionNode('strong',name+' · '+(spec?.label||'未注册权限')),permissionNode('small',spec?.description||'当前宿主未注册；可保留现有草稿或取消此项。'));row.append(input,copy);list.append(row);}
   for(const [kind,resources]of [['Device',catalog.devices],['Robot',catalog.robots],['Computer',catalog.computers]])for(const [name,resource]of Object.entries(resources||{})){const row=permissionNode('li','');row.append(permissionNode('strong',name),permissionNode('span',' · '+(kind==='Device'?'':kind+' · ')+resource.profile),permissionNode('p',resource.description||'未提供描述'));devices.append(row);}
   if(!devices.children.length)devices.append(permissionNode('li','当前服务未注册 Device／Robot；由可信宿主启动配置提供。'));
   permissionNotice(readonly?'执行快照只读，不能修改项目权限。':'应用只更新项目草稿；随后点击保存或运行，按原项目版本提交。不会自动开启工具、连接设备或执行输入。');
   apply.disabled=readonly;
   apply.onclick=()=>{
    if(!current()||snapshotView||saveTask){permissionNotice('项目、版本或保存状态已经改变；请关闭后重新打开。已有源码和权限草稿已保留。');apply.disabled=true;return;}
    const next=[...selectedCaps].sort();if(JSON.stringify(next)!==JSON.stringify([...project.capabilities].sort())){project.capabilities=next;markDirty(editFrom||(project.authoring==='workflow'?'graph':'source'));}
    closePermissions();toast('项目权限已应用到草稿；保存或运行后生效。');
   };
  }catch(error){if(request===permissionRequest)permissionNotice('无法读取能力清单：'+error.message);}
 }
 function available(command){const target=command.target&&byId(command.target);return (!command.target||!!target&&!target.disabled)&&(!command.available||command.available());}
 async function execute(id){const command=commands.find(item=>item.id===id);if(!command||!available(command))return false;try{await command.action();refresh();return true;}catch(error){toast(error.message,true);return false;}}
 // Menus and the command palette share the same registered commands and controls.
 const menuDefinitions=[
  ['file','文件',[
   ['file.new','file.folder','file.quickOpen'],['file.save','file.entry','file.rename','file.delete'],
   ['project.capabilities'],['project.import','project.export','project.reload','project.backup']]],
  ['edit','编辑',[
   ['search','edit.suggest','edit.format','format.save','edit.check','edit.appearance'],
   ['edit.outline','edit.signature','edit.definition','edit.references','edit.rename'],['edit.compile']]],
  ['view','视图',[
   ['view.source','graph','view.structure','view.proof'],
   ['sidebar.toggle','output.toggle','agent','view.reset'],['graph.tools','graph.inspector']]],
  ['run','运行',[
   ['run.start','run.stop','run.options','run.guide','debug'],
   ['output.result','output.events','output.artifact','output.history','run.snapshot'],['run.trace','run.follow']]],
  ['agent','智能体',[
   ['agent','team','modes'],['program.candidate','processes','sessions','computers'],['settings']]],
  ['help','帮助',[
   ['help','docs','edit.example'],['settings']]]
 ];
 const menuBar=byId('wbMenubar'),menuHeads=byId('wbMenuHeads');
 let openMenu=-1;
 function menuButtons(index){return [...byId('wbMenu-'+menuDefinitions[index][0]).querySelectorAll('[data-menu-command]')];}
 function setMenuHead(index){for(const [i,button]of [...menuHeads.children].entries())button.tabIndex=i===index?0:-1;}
 function closeMenu(restore=false){if(openMenu<0)return;const index=openMenu;openMenu=-1;byId('wbMenu-'+menuDefinitions[index][0]).hidden=true;menuHeads.children[index].setAttribute('aria-expanded','false');if(restore)menuHeads.children[index].focus();}
 function updateMenus(){
  for(const row of menuBar.querySelectorAll('[data-menu-command]')){
   const command=commands.find(item=>item.id===row.dataset.menuCommand),control=command?.target&&byId(command.target);
   row.setAttribute('aria-disabled',String(!command||!available(command)));
   if(control?.type==='checkbox'){
    row.setAttribute('role','menuitemcheckbox');row.setAttribute('aria-checked',String(control.checked));
    row.querySelector('.wb-menu-check').textContent=control.checked?'✓':'';
   }
  }
 }
 function positionMenu(index){
  const popup=byId('wbMenu-'+menuDefinitions[index][0]),head=menuHeads.children[index],bar=menuBar.getBoundingClientRect();
  popup.style.left=Math.max(4,Math.min(head.getBoundingClientRect().left-bar.left,bar.width-popup.offsetWidth-4))+'px';
 }
 function showMenu(index,last=false){
  closeMenu();openMenu=index;setMenuHead(index);updateMenus();
  const popup=byId('wbMenu-'+menuDefinitions[index][0]);popup.hidden=false;
  menuHeads.children[index].setAttribute('aria-expanded','true');positionMenu(index);
  const rows=menuButtons(index);rows[last?rows.length-1:0]?.focus();
 }
 for(const [index,[id,label,groups]]of menuDefinitions.entries()){
  const head=document.createElement('button');head.type='button';head.id='wbMenuHead-'+id;head.textContent=label;
  head.setAttribute('role','menuitem');head.setAttribute('aria-haspopup','menu');head.setAttribute('aria-expanded','false');head.setAttribute('aria-controls','wbMenu-'+id);head.tabIndex=index===0?0:-1;
  head.onclick=()=>openMenu===index?closeMenu(true):showMenu(index);
  head.onkeydown=event=>{
   if(event.isComposing)return;
   if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();showMenu(index,event.key==='ArrowUp');}
   else if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
    event.preventDefault();const next=(index+(event.key==='ArrowRight'?1:-1)+menuDefinitions.length)%menuDefinitions.length;
    if(openMenu>=0)showMenu(next);else{setMenuHead(next);menuHeads.children[next].focus();}
   }else if(event.key==='Escape'){event.preventDefault();closeMenu(true);}
  };
  const popup=document.createElement('div');popup.id='wbMenu-'+id;popup.className='wb-menu-popup';popup.hidden=true;
  popup.setAttribute('role','menu');popup.setAttribute('aria-labelledby',head.id);
  for(const [groupIndex,group]of groups.entries()){
   if(groupIndex){const separator=document.createElement('div');separator.setAttribute('role','separator');popup.append(separator);}
   for(const commandId of group){
    const command=commands.find(item=>item.id===commandId),row=document.createElement('button');
    row.type='button';row.tabIndex=-1;row.dataset.menuCommand=commandId;row.setAttribute('role','menuitem');
    const check=document.createElement('span');check.className='wb-menu-check';check.setAttribute('aria-hidden','true');
    const text=document.createElement('span');text.textContent=command.label.slice(command.label.indexOf('：')+1);
    const shortcut=document.createElement('kbd');shortcut.textContent=command.shortcut||({'file.save':'⌘ / Ctrl S','search':'⌘ / Ctrl F','edit.format':'Shift Alt F','run.start':'⌘ / Ctrl Enter','edit.definition':'F12'}[commandId]||'');
    row.append(check,text,shortcut);
    row.onclick=()=>{if(!available(command))return;closeMenu(true);execute(commandId);};
    popup.append(row);
   }
  }
  popup.onkeydown=event=>{
   if(event.isComposing)return;
   const rows=menuButtons(index),current=rows.indexOf(document.activeElement);
   if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?rows.length-1:(current+(event.key==='ArrowDown'?1:-1)+rows.length)%rows.length;rows[next]?.focus();
   }else if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
    event.preventDefault();showMenu((index+(event.key==='ArrowRight'?1:-1)+menuDefinitions.length)%menuDefinitions.length);
   }else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeMenu(true);}
   else if(event.key==='Tab')closeMenu(true);
  };
  menuHeads.append(head);menuBar.append(popup);
 }
 document.addEventListener('pointerdown',event=>{if(!menuBar.contains(event.target))closeMenu();});
 document.addEventListener('focusin',event=>{if(!menuBar.contains(event.target))closeMenu();});
 window.addEventListener('resize',()=>{if(openMenu>=0)positionMenu(openMenu);});
 document.addEventListener('keydown',event=>{
  if(event.key==='F10'&&!event.shiftKey&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.isComposing&&!document.querySelector('dialog[open]')){
   event.preventDefault();closeMenu();setMenuHead(0);menuHeads.children[0].focus();
  }
 });
 // Move the actual controls, preserving listeners, state, IDs and disabled flags.
 byId('wbRunOptions').append(document.querySelector('.input-section'),document.querySelector('.source-run-options'));
 const sync=document.querySelector('.sync-status');byId('wbRunHistory').append(sync.querySelector('label'),byId('viewSnapshot'));
 document.querySelector('.statusbar').prepend(byId('syncBadge'),byId('runVersionNote'));document.querySelector('.statusbar').append(byId('connection'));sync.remove();
 for(const id of ['restoreDraft','exportButton','importButton','importFile','reloadButton','helpButton','inspectorToggle'])vault.append(byId(id));
 const fileHeading=document.querySelector('#ideFilesPanel .ide-pane-heading');fileHeading.append(byId('addModule'));byId('addModule').textContent='＋';byId('addModule').setAttribute('aria-label','新建模块文件');
 const outputToggle=document.createElement('button');outputToggle.id='wbOutputToggle';outputToggle.textContent='⌄';outputToggle.setAttribute('aria-label','展开或收起输出');outputToggle.onclick=()=>execute('output.toggle');document.querySelector('.console-tabs').append(outputToggle);
 const originalConsoleTab=consoleTab;consoleTab=function(id){output(true);return originalConsoleTab(id);};
 byId('runButton').addEventListener('click',()=>output(true));
 byId('wbRunPanel').addEventListener('keydown',event=>{if(event.key==='Escape'&&!event.isComposing){event.preventDefault();runPanel(false);}});
 byId('ideFileTree').addEventListener('click',event=>{if(media.matches&&event.target.closest('[data-directory="false"]')){layout.classList.remove('files-open');byId('ideFilesToggle').setAttribute('aria-expanded','false');focusEditor();}});
 byId('ideFilesPanel').addEventListener('keydown',event=>{if(event.key==='Escape'&&media.matches){layout.classList.remove('files-open');byId('ideFilesToggle').setAttribute('aria-expanded','false');focusEditor();}});
 byId('canvas').addEventListener('pointerdown',event=>{if(event.button===0&&body.dataset.workbenchView==='graph'&&event.target.closest('.node,.edge')&&!event.target.closest('[data-port]')){body.classList.add('wb-inspector-open');refresh();}});
 byId('palette').addEventListener('click',event=>{if(body.dataset.workbenchView==='graph'&&event.target.closest('[data-kind]')&&typeof canEditGraph==='function'&&canEditGraph())body.classList.add('wb-inspector-open');});
 const inspectorClose=document.createElement('button');inspectorClose.id='wbInspectorClose';inspectorClose.textContent='×';inspectorClose.setAttribute('aria-label','关闭节点配置');inspectorClose.onclick=()=>{body.classList.remove('wb-inspector-open');byId('canvas').focus();};document.querySelector('.inspector-title').append(inspectorClose);
 // Dynamic feature buttons keep their original node and handler in the action bank.
 const advanced=document.querySelector('#sourceView .source-actions');
 function collectActions(){for(const node of [...advanced.children])vault.append(node);for(const id of ['ideSignatureButton','ideDefinitionButton','ideReferencesButton','ideRenameSymbolButton'])if(byId(id)&&byId(id).parentElement!==vault)vault.append(byId(id));}
 collectActions();new MutationObserver(collectActions).observe(advanced,{childList:true});
 byId('wbEditorActions').append(...[byId('ideSuggestButton'),byId('formatSource'),byId('codeAppearanceButton'),byId('formatOnSave').closest('label'),byId('checkSource')].filter(Boolean));
 byId('formatOnSave').closest('label').title='保存时自动格式化当前模块 · Ctrl / ⌘ S';
 byId('formatSource').title='格式化当前文件 · Shift Alt F';byId('checkSource').title='检查当前草稿，不保存或运行';
 const originalFilesToggle=byId('ideFilesToggle').onclick;byId('ideFilesToggle').onclick=()=>{if(media.matches){originalFilesToggle();preferences.sidebar=layout.classList.contains('files-open');persist();refresh();}else sidebar(!preferences.sidebar);};
 byId('wbCommandButton').onclick=()=>openPalette('commands');
 document.querySelectorAll('[data-command]').forEach(button=>button.onclick=()=>execute(button.dataset.command));
 let mode='commands',items=[],selected=0,previousFocus=null;
 function fileItems(){return [...byId('moduleSelect').options].map(option=>({id:option.value,label:option.textContent,action:async()=>{if(await showView('source')){window.ChaosIDE.switchFile(option.value);if(media.matches)layout.classList.remove('files-open');focusEditor();}}}));}
 function results(){const query=input.value.trim().toLocaleLowerCase();items=(mode==='files'?fileItems():commands).filter(item=>item.label.toLocaleLowerCase().includes(query)||item.id.toLocaleLowerCase().includes(query));selected=Math.min(selected,Math.max(0,items.length-1));list.replaceChildren();byId('wbCommandTitle').textContent=mode==='files'?'快速打开文件':'命令面板';input.setAttribute('aria-label',mode==='files'?'搜索文件路径':'搜索命令名称');for(const [index,item]of items.entries()){const row=document.createElement('button');row.type='button';row.id='wbOption'+index;row.setAttribute('role','option');row.setAttribute('aria-selected',String(index===selected));row.className='wb-command-option';row.tabIndex=-1;const enabled=mode==='files'||available(item);row.setAttribute('aria-disabled',String(!enabled));row.textContent=item.label;const hint=document.createElement('small');hint.textContent=enabled?(item.target&&byId(item.target)?.type==='checkbox'?(byId(item.target).checked?'已开启':'已关闭'):(item.shortcut||'')):'当前不可用';row.append(hint);row.onclick=()=>choose(index);list.append(row);}if(!items.length){const empty=document.createElement('p');empty.className='wb-note';empty.textContent=mode==='files'?'没有匹配的文件':'没有匹配的命令';list.append(empty);}input.setAttribute('aria-activedescendant',items.length?'wbOption'+selected:'');}
 function openPalette(kind='commands'){if(document.querySelector('dialog[open]')&&!dialog.open)return;mode=kind;previousFocus=document.activeElement;input.value='';selected=0;results();if(!dialog.open)dialog.showModal();input.focus();}
 function closePalette(restore=true){dialog.close();if(restore){if(previousFocus?.isConnected&&previousFocus.getClientRects().length)previousFocus.focus();else focusEditor();}}
 async function choose(index){const item=items[index];if(!item||mode!=='files'&&!available(item))return;const fileMode=mode==='files';closePalette(false);if(fileMode)await item.action();else await execute(item.id);}
 input.oninput=()=>{selected=0;results();};input.onkeydown=event=>{if(event.isComposing)return;if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();if(items.length){selected=(selected+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;results();byId('wbOption'+selected)?.scrollIntoView({block:'nearest'});}}else if(event.key==='Enter'){event.preventDefault();choose(selected);}};
 byId('wbCommandClose').onclick=()=>closePalette();dialog.addEventListener('cancel',event=>{event.preventDefault();closePalette();});
 function refresh(){const active=document.querySelector('.view.active');body.dataset.workbenchView=active?.id.replace(/View$/,'')||'source';const assistant=body.dataset.workbenchView==='source'&&!layout.classList.contains('assistant-closed');for(const button of document.querySelectorAll('#wbActivity [data-command]')){const id=button.dataset.command,on=id==='files'?body.dataset.workbenchView==='source'&&byId('wbRunPanel').hidden&&(media.matches?layout.classList.contains('files-open'):preferences.sidebar):id==='agent'?assistant:id==='graph'?body.dataset.workbenchView==='graph':id==='run.options'?!byId('wbRunPanel').hidden:false;button.setAttribute('aria-pressed',String(on));}if(dialog.open)results();if(openMenu>=0)updateMenus();byId('wbRunEntry').textContent=typeof project!=='undefined'&&project?'运行入口：'+project.entry+'。运行前保存草稿；下方输入传给入口程序。':'输入传给当前入口文件。运行前保存草稿。';}
 new MutationObserver(refresh).observe(layout,{attributes:true,attributeFilter:['class']});for(const section of document.querySelectorAll('.view'))new MutationObserver(refresh).observe(section,{attributes:true,attributeFilter:['class']});
 document.addEventListener('keydown',event=>{if(event.isComposing||event.altKey)return;const mod=event.ctrlKey||event.metaKey;if(!mod||document.querySelector('dialog[open]')&&!dialog.open)return;const key=event.key.toLowerCase();if(key==='p'){event.preventDefault();event.stopImmediatePropagation();openPalette(event.shiftKey?'commands':'files');}else if(key==='b'&&!event.shiftKey){event.preventDefault();event.stopImmediatePropagation();execute('sidebar.toggle');}},true);
 new MutationObserver(refresh).observe(byId('moduleSelect'),{childList:true,subtree:true});
 media.addEventListener('change',()=>{layout.classList.remove('files-open');body.classList.remove('wb-graph-tools');refresh();});
 if(!media.matches)body.classList.add('wb-graph-tools');
 body.classList.toggle('wb-sidebar-hidden',!preferences.sidebar);output(preferences.output);refresh();
 window.ChaosWorkbench=Object.freeze({commands:Object.freeze(commands),execute,openPalette});
})();
