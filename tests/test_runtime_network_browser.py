"""Real OpenUI renderer and existing SSE against isolated program state."""
import json,os,tempfile,threading,unittest
from pathlib import Path
from chaos import Framework,Settings
from chaos.server import make_server
try:
 from playwright.sync_api import sync_playwright
except ImportError:sync_playwright=None

@unittest.skipUnless(sync_playwright,'Playwright unavailable')
class RuntimeNetworkBrowser(unittest.TestCase):
 def test_real_graph_parser_theme_selection_mobile_and_sse_preserved_view(self):
  with tempfile.TemporaryDirectory()as folder:
   app=Framework(settings=Settings(':memory:'));self.addCleanup(app.close)
   source='\n'.join('fn actor'+str(i)+'(x){'+('x+1'if i==0 else 'actor'+str(i-1)+'(x)')+'};'for i in range(55))+'\nui_artifact("page","真实运行网络",ui_node("Card",["程序状态",[ui_node("RuntimeGraph",["运行关系","radial","project","dark"])]]))'
   project=app.edit_program('workspace',app.get_project('workspace')['version'],source=source,capabilities=[])
   server=make_server(port=0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))
   origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[];runtime_requests=[]
   with sync_playwright()as pw:
    browser=pw.chromium.launch(headless=True)
    try:
     context=browser.new_context(viewport={'width':1440,'height':1100});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
     context.route('**/*',lambda r:r.continue_()if r.request.url.startswith(origin+'/')else(external.append(r.request.url),r.abort()))
     page.on('request',lambda r:runtime_requests.append(r.url)if'/runtime-view'in r.url else None)
     # Record at browser fetch initiation, not at Python's asynchronously delivered
     # request callback. A request begun by the old bound view remains legitimate.
     page.add_init_script("""
      (()=>{
       const nativeFetch=window.fetch;
       window.__runtimeFetchStarts=[];window.__runtimeBinding=null;window.__runtimeBindingSource=null;
       window.__runtimeBindingChanges=[];window.__runtimeNotifications=[];
       window.__runtimeAuditPhase='boot';
       window.fetch=function(...args){
        const input=args[0],url=new URL(typeof input==='string'?input:input.url,location.href);
        if(url.pathname.endsWith('/runtime-view')){
         const state=window.ChaosArtifacts?.state,binding=window.__runtimeBinding;
         window.__runtimeFetchStarts.push({sequence:window.__runtimeFetchStarts.length,
          phase:window.__runtimeAuditPhase,url:url.href,
          binding:binding?{...binding}:null,bindingSource:window.__runtimeBindingSource?{...window.__runtimeBindingSource}:null,
          artifact:{sameOriginal:!!state?.definition&&state.definition===state.original,
           partial:state?.partial===true,runId:state?.run?.id??null,
           version:state?.run?.version??null}});
        }
        return Reflect.apply(nativeFetch,this,args);
       };
      })();
     """)
     page.goto(origin);page.wait_for_function('()=>!!project');self.assertTrue(page.evaluate('typeof ChaosRuntimeNetwork?.install === "function"'))
     self.assertTrue(page.evaluate('()=>ChaosArtifacts.state.definition===null'))
     page.evaluate("""()=>{
      const network=ChaosRuntimeNetwork,configure=network.configure,notify=network.notify;
      // Mirror the actual predicate and observe the impending assignment before
      // calling configure: its subscribers run synchronously. Exceptions still
      // propagate and fail the browser test; no product action is suppressed.
      const valid=value=>value&&typeof value.projectId==='string'&&value.projectId.length>0&&value.projectId.length<=200&&typeof value.runId==='string'&&value.runId.length>0&&value.runId.length<=200&&Number.isSafeInteger(value.version)&&value.version>0&&value.partial!==true;
      network.configure=function(value){
       const state=ChaosArtifacts.state;
       window.__runtimeBinding=valid(value)?{projectId:value.projectId,runId:value.runId,version:value.version,partial:false}:null;
       window.__runtimeBindingSource=window.__runtimeBinding?{sameOriginal:!!state.definition&&state.definition===state.original,partial:state.partial===true,runId:state.run?.id??null,version:state.run?.version??null}:null;
       window.__runtimeBindingChanges.push({binding:window.__runtimeBinding?{...window.__runtimeBinding}:null,fetchCount:window.__runtimeFetchStarts.length,phase:window.__runtimeAuditPhase});
       return Reflect.apply(configure,this,arguments);
      };
      network.notify=function(event){
       const result=Reflect.apply(notify,this,arguments);
       window.__runtimeNotifications.push({type:event?.type??null,runId:event?.run_id??null,id:event?.id??null,connected:event?.connected??null});
       return result;
      };
      window.__runtimeAuditPhase='cold-candidate';
     }""")
     # The first RuntimeGraph ever mounted is a local candidate, with no prior
     # run or live channel whose delayed callback could confuse attribution.
     page.locator('[data-console="artifact"]').click();page.locator('.ui-artifact-source summary').first.click()
     page.get_by_label('OpenUI 源码',exact=True).fill('root=RuntimeGraph()');page.get_by_role('button',name='校验并预览',exact=True).click()
     page.wait_for_selector('[data-runtime-state="unbound"]')
     page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
     self.assertEqual(page.evaluate('window.__runtimeFetchStarts'),[])
     self.assertIsNone(page.evaluate('window.__runtimeBinding'))
     self.assertTrue(page.evaluate('window.__runtimeBindingChanges.some(change=>change.binding===null)'))
     page.locator('.ui-artifact-source summary').first.click()
     page.evaluate("window.__runtimeAuditPhase='bound-original'")
     page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&ChaosArtifacts.state.definition')
     page.locator('[data-console="artifact"]').click();page.locator('#expandConsole').click();page.wait_for_selector('.ui-runtime-network svg');page.wait_for_function('()=>document.querySelectorAll("[data-network-node-index]").length>30')
     count=page.locator('[data-network-node-index]').count();self.assertEqual(count,len(app.runtime_view('workspace')['nodes']))
     self.assertIn('游标',page.locator('.ui-network-freshness').inner_text());self.assertTrue(runtime_requests)
     page.get_by_label('选择网络节点').select_option(index=1);self.assertTrue(page.locator('.ui-network-selected code').inner_text())
     page.get_by_label('放大网络').click();self.assertIn('115%',page.get_by_label('重置网络视图').inner_text())
     screenshot=os.environ.get('CHAOS_NETWORK_AUDIT')
     page.get_by_label('全屏网络视图',exact=True).click();page.wait_for_function('()=>!!document.fullscreenElement')
     self.assertTrue(page.locator('.ui-network-metrics').is_visible());self.assertTrue(page.evaluate('()=>{const r=document.querySelector(".ui-network-metrics").getBoundingClientRect();return r.bottom<=innerHeight&&r.top>=0}'))
     if screenshot:Path(screenshot).mkdir(parents=True,exist_ok=True);page.screenshot(path=str(Path(screenshot)/'runtime-network-dark.png'))
     page.evaluate('document.exitFullscreen()');page.wait_for_function('()=>!document.fullscreenElement')
     page.get_by_role('button',name='固定实时视图',exact=True).click();old_hash=page.evaluate('ChaosArtifacts.state.definition.hash');old_text=page.locator('.ui-network-freshness').inner_text()
     app.start('workspace',project['version'],None,mode='source').result(8)
     page.wait_for_function('(old)=>document.querySelector(".ui-network-freshness")?.textContent!==old',arg=old_text)
     self.assertEqual(page.evaluate('ChaosArtifacts.state.definition.hash'),old_hash);self.assertTrue(page.evaluate('ChaosArtifacts.state.pinned'))
     page.set_viewport_size({'width':390,'height':900});self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
     if screenshot:page.screenshot(path=str(Path(screenshot)/'runtime-network-mobile.png'))
     page.set_viewport_size({'width':1440,'height':1100});page.get_by_role('button',name='取消固定',exact=True).click()
     page.locator('.ui-artifact-source summary').first.click();page.get_by_label('OpenUI 源码',exact=True).fill('root = NetworkGraph("Pure data",[{id:"a",label:"A"},{id:"b",label:"B"}],[{id:"ab",source:"a",target:"b"}],"layered","light")');page.get_by_role('button',name='校验并预览',exact=True).click();page.wait_for_selector('.ui-network-theme-light');self.assertEqual(page.locator('[data-network-node-index]').count(),2)
     self.assertEqual(page.locator('.ui-network-theme-light').evaluate('(n)=>getComputedStyle(n).backgroundColor'),'rgb(247, 249, 252)')
     page.get_by_label('全屏网络视图',exact=True).click();page.wait_for_function('()=>!!document.fullscreenElement')
     if screenshot:page.screenshot(path=str(Path(screenshot)/'runtime-network-light.png'))
     page.evaluate('document.exitFullscreen()');page.wait_for_function('()=>!document.fullscreenElement')
     # A locally compiled runtime component has no authority to bind a run.
     page.evaluate("window.__runtimeAuditPhase='local-runtime-candidate'")
     page.get_by_label('OpenUI 源码',exact=True).fill('root=RuntimeGraph()');page.get_by_role('button',name='校验并预览',exact=True).click();page.wait_for_selector('[data-runtime-state="unbound"]')
     self.assertIsNone(page.evaluate('window.__runtimeBinding'))
     # The matching old run and connection transitions enter the real public
     # notification implementation. Neither can authorize a local component.
     probe=page.evaluate("""async()=>{
      const before=window.__runtimeFetchStarts.length,run=ChaosArtifacts.state.run;
      ChaosRuntimeNetwork.notify({type:'run.completed',project_id:project.id,run_id:run.id});
      ChaosRuntimeNetwork.notify({connected:false,projectId:project.id});
      ChaosRuntimeNetwork.notify({connected:true,projectId:project.id});
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return {before,after:window.__runtimeFetchStarts.length,runId:run.id,binding:window.__runtimeBinding,revokedAt:window.__runtimeBindingChanges.at(-1).fetchCount};
     }""")
     self.assertTrue(probe['runId']);self.assertIsNone(probe['binding']);self.assertEqual(probe['after'],probe['before']);self.assertEqual(probe['after'],probe['revokedAt'])
     # Also deliver actual matching project SSE through the existing connection.
     # Pinning preserves this candidate while the genuine new run completes.
     page.get_by_role('button',name='固定实时视图',exact=True).click()
     actual=app.start('workspace',project['version'],None,mode='source');actual.result(8)
     page.wait_for_function('(id)=>window.__runtimeNotifications.some(event=>event.type==="run.completed"&&event.runId===id&&Number.isSafeInteger(event.id))',arg=actual.id)
     page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
     self.assertTrue(page.evaluate('ChaosArtifacts.state.pinned'));self.assertTrue(page.locator('[data-runtime-state="unbound"]').is_visible())
     self.assertEqual(page.evaluate('window.__runtimeFetchStarts.length'),probe['after'])
     starts=page.evaluate('window.__runtimeFetchStarts')
     self.assertTrue(starts,'the same recorder must observe real bound requests')
     for request in starts:
      self.assertIsNotNone(request['binding'],request)
      self.assertTrue(request['bindingSource']['sameOriginal'],request);self.assertFalse(request['bindingSource']['partial'],request)
      self.assertEqual(request['binding']['projectId'],'workspace',request)
      self.assertEqual(request['binding']['runId'],request['bindingSource']['runId'],request)
      self.assertEqual(request['binding']['version'],request['bindingSource']['version'],request)
      self.assertFalse(request['binding']['partial'],request)
     self.assertFalse([request for request in starts if request['phase']=='cold-candidate'])
     self.assertEqual(app.get_project('workspace')['version'],project['version']);self.assertEqual(errors,[]);self.assertEqual(external,[])
    finally:
     if os.environ.get('CHAOS_NETWORK_AUDIT') and 'page' in locals() and not page.is_closed():
      audit=Path(os.environ['CHAOS_NETWORK_AUDIT']);audit.mkdir(parents=True,exist_ok=True)
      (audit/'browser-diagnostic.json').write_text(json.dumps({'errors':errors,'external':external,'runtimeRequests':runtime_requests,'browserFetchStarts':page.evaluate('window.__runtimeFetchStarts??[]'),'bindingChanges':page.evaluate('window.__runtimeBindingChanges??[]'),'runtimeNotifications':page.evaluate('window.__runtimeNotifications??[]'),'status':page.locator('.ui-artifact-toolbar').inner_text()if page.locator('.ui-artifact-toolbar').count()else '', 'runtimeStates':page.locator('[data-runtime-state]').evaluate_all('(xs)=>xs.map(x=>({state:x.dataset.runtimeState,text:x.textContent.slice(0,1000)}))')},ensure_ascii=False,indent=2))
      page.screenshot(path=str(audit/'browser-final.png'))
     browser.close()
