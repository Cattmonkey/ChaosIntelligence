'use strict';
const assert=require('assert');
const {chromium}=require('playwright');
const [origin,scenario]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage(),errors=[],external=[];
 page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(String(error)));
 await context.route('http**://**',route=>route.request().url().startsWith(origin+'/')?route.continue():(external.push(route.request().url()),route.abort()));
 async function goto(id){await page.goto(origin+'/?project='+id);await page.waitForFunction(id=>window.ChaosWorkbench&&window.ChaosIDE?.editor()&&project?.id===id,id);}
 async function command(id){await page.locator('#wbCommandButton').click();await page.locator('#wbCommandInput').fill(id);await page.locator('#wbCommandList [role=option]').first().click();await page.locator('#wbCommands').waitFor({state:'hidden'});}
 async function permissions(){await page.locator('#wbMenuHead-file').click();await page.locator('[data-menu-command="project.capabilities"]').click();await page.locator('[data-project-capability="device.control"]').waitFor();}
 async function apply(name){await page.locator('[data-project-capability="'+name+'"]').check();await page.locator('#wbPermissionApply').click();await page.locator('#wbPermissions').waitFor({state:'hidden'});}
 async function saved(id){return page.evaluate(async id=>await (await fetch('/api/v1/projects/'+id)).json(),id);}
 async function edit(source){const content=page.locator('#ideEditorMount .cm-content[contenteditable=true]');await content.click();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(source);}
 async function save(){await page.locator('#saveButton').click();await page.waitForFunction(()=>!saveTask&&!dirty&&!controlDraft);}
 async function remote(id,source){return page.evaluate(async ({id,source})=>{const p=await (await fetch('/api/v1/projects/'+id)).json();p.source=source;p.files[p.entry]=source;p.editFrom='source';const r=await fetch('/api/v1/projects/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(!r.ok)throw Error(await r.text());return r.json();},{id,source});}
 try{
  if(scenario==='drafts'){
   await goto('drafts');if(await page.locator('#formatOnSave').isChecked())await command('format.save');
   await edit('let unsaved = 40; unsaved + 2');const before=await page.evaluate(()=>({source:ChaosIDE.editor().state.doc.toString(),version:project.version}));
   await permissions();assert((await page.locator('#wbPermissionDevices').textContent()).includes('audit_desk'));assert((await page.locator('#wbPermissionDevices').textContent()).includes('A static test device'));
   assert.equal((await saved('drafts')).version,1);await apply('device.control');
   assert.deepEqual(await page.evaluate(()=>({source:ChaosIDE.editor().state.doc.toString(),version:project.version})),before);
   assert.deepEqual((await saved('drafts')).capabilities,[]);await save();let persisted=await saved('drafts');assert(persisted.capabilities.includes('device.control'));assert.equal(persisted.source,before.source);assert.equal(persisted.version,2);
   await permissions();await page.locator('[data-project-capability="session.manage"]').check();await page.locator('#wbPermissionClose').click();assert(!(await page.evaluate(()=>project.capabilities)).includes('session.manage'));
   await goto('ir');await command('view.structure');await page.waitForFunction(()=>programDraft&&view==='structure');
   const literal=await page.evaluate(()=>[...syntaxIndex().values()].find(x=>x.node.kind==='literal'&&x.node.value===40).node.id);
   await page.locator('[data-syntax="'+literal+'"]').click();const input=page.locator('#syntaxFields input').first();assert.equal(await input.inputValue(),'40');await input.fill('41');await page.locator('#syntaxFields').getByRole('button',{name:'应用',exact:true}).click();await page.waitForFunction(()=>syntaxDirty);
   const irBefore=await page.evaluate(()=>JSON.stringify(programDraft));await permissions();await apply('session.manage');assert.equal(await page.evaluate(()=>JSON.stringify(programDraft)),irBefore);
   const writes=[];page.on('request',request=>{if(['PUT','POST'].includes(request.method())&&request.url().includes('/api/projects/ir'))writes.push(request);});
   await save();persisted=await saved('ir');assert(persisted.capabilities.includes('session.manage'));assert(persisted.source.includes('41'));assert.equal(persisted.version,2);assert.equal(writes.length,1,'IR + permissions must commit atomically');
   await permissions();await page.setViewportSize({width:390,height:900});assert(await page.locator('#wbPermissions').evaluate(el=>el.scrollWidth<=el.clientWidth));await page.keyboard.press('Escape');
  }else if(scenario==='filters'){
   await goto('filters');await command('graph');await page.locator('[data-node="tool"]').click();
   assert.equal(await page.locator('[data-field="tool"] option[value="device.control"]').count(),0);
   await page.locator('[data-node="agent"]').click();assert.equal(await page.locator('#inspectorContent [data-tool="device.control"]').count(),0);
   await page.locator('[data-node="function"]').click();assert.equal(await page.locator('#inspectorContent [data-tool="device.control"]').count(),1);
   assert(await page.evaluate(()=>callableCapability('graph.patch',tools,true)));assert(!(await page.evaluate(()=>callableCapability('graph.patch',tools,false))));
   await command('team');await page.locator('#teamAuthorDialog').waitFor({state:'visible'});assert.equal(await page.locator('[aria-label="成员 1 能力范围 device.control"]').count(),1);
   await page.locator('[aria-label="成员 1 新增配置"]').selectOption('tools');await page.locator('#teamAuthorDialog').getByRole('button',{name:'添加配置字段',exact:true}).click();assert.equal(await page.locator('[aria-label="成员 1 工具能力 device.control"]').count(),0);await page.keyboard.press('Escape');
   await goto('legacy');await command('view.structure');await page.waitForFunction(()=>programDraft&&view==='structure');const agent=await page.evaluate(()=>[...syntaxIndex().values()].find(x=>x.node.kind==='agent').node.id);
   await page.locator('[data-syntax="'+agent+'"]').click();await page.locator('[aria-label="智能体工具 device.control"]').waitFor();assert(await page.locator('[aria-label="智能体工具 device.control"]').isChecked());assert((await page.locator('.agent-config-editor').textContent()).includes('原生权限不是工具'));
   await page.locator('[aria-label="智能体工具 device.control"]').click();assert(!(await page.evaluate(()=>staticAgentDefinition(syntaxIndex().get(syntaxSelection).node).config.tools)).includes('device.control'));assert.equal((await saved('legacy')).version,1);
   // A real completed workflow supplies the snapshot; no fake snapshot assignment.
   await goto('filters');await page.locator('#runButton').click();await page.waitForFunction(()=>runSnapshot&&!busy&&!launching);await command('graph');await command('run.snapshot');await page.waitForFunction(()=>snapshotView);
   await permissions();assert(await page.locator('#wbPermissionApply').isDisabled());assert(await page.locator('[data-project-capability="device.control"]').isDisabled());await page.keyboard.press('Escape');
  }else if(scenario==='conflict'){
   await goto('conflict');if(await page.locator('#formatOnSave').isChecked())await command('format.save');await edit('let pending = 42; pending');await permissions();await apply('device.control');
   await remote('conflict','84');await page.evaluate(()=>refreshRemote());const local=await page.evaluate(()=>({source:ChaosIDE.editor().state.doc.toString(),capabilities:project.capabilities,version:project.version}));
   await page.locator('#saveButton').click();await page.waitForFunction(()=>!saveTask&&document.querySelector('#syncBadge').textContent.includes('冲突'));
   assert.deepEqual(await page.evaluate(()=>({source:ChaosIDE.editor().state.doc.toString(),capabilities:project.capabilities,version:project.version})),local);assert.equal((await saved('conflict')).source,'84');assert.deepEqual((await saved('conflict')).capabilities,[]);
   // A clean project updates underneath an open transient permission dialog.
   await goto('drafts');await permissions();await page.locator('[data-project-capability="device.control"]').check();await remote('drafts','21');await page.evaluate(()=>refreshRemote());await page.locator('#wbPermissionApply').click();assert(await page.locator('#wbPermissions').isVisible());assert(await page.locator('#wbPermissionApply').isDisabled());assert(!(await page.evaluate(()=>project.capabilities)).includes('device.control'));assert.equal(await page.evaluate(()=>ChaosIDE.editor().state.doc.toString()),'21');
  }else throw Error('Unknown scenario');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);process.stdout.write(JSON.stringify({passed:true,scenario,errors,externalHTTP:external})+'\n');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
