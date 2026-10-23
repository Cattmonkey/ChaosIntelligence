"""Real language responses cannot outlive their exact read-only code view."""
from workbench_actions import command as workbench_command, button as workbench_button, view as workbench_view, run_options as workbench_run_options
import importlib.util,json,os,tempfile,threading,time,unittest
from pathlib import Path
from chaos import Framework,Settings
from chaos.server import make_server
GATE="(targetSource) => {\n  const nativeFetch = window.fetch.bind(window);\n  window.lateJSON = [];\n  window.fetch = async (input, init) => {\n    const response = await nativeFetch(input, init);\n    const url = typeof input === 'string' ? input : input.url;\n    if (!url.includes('/api/language/')) return response;\n    const body = JSON.parse(init.body || '{}');\n    if (targetSource && body.source !== targetSource) return response;\n    const data = await response.json();\n    const item = {url, source:body.source, signal:init.signal, data, released:false, requested:false, resumed:false};\n    const delayed = new Promise(resolve => item.resolve = resolve);\n    window.lateJSON.push(item);\n    return {ok:response.ok,status:response.status,json:()=>{item.requested=true;return delayed.then(value=>{item.resumed=true;return value;});}};\n  };\n  window.releaseLate = index => {\n    const item=lateJSON[index];item.released=true;item.resolve(item.data);\n  };\n}"
@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class CodeLifecycleUI(unittest.TestCase):
 def setUp(self):
  from playwright.sync_api import sync_playwright
  self.tmp=tempfile.TemporaryDirectory(prefix='chaos-lifecycle-test-');root=Path(self.tmp.name)
  self.app=Framework(root/'runtime.sqlite',settings=Settings(':memory:'));self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=root/'control',update_home=root/'updates');self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.origin='http://127.0.0.1:'+str(self.server.server_port)
  self.pw=sync_playwright().start();self.browser=self.pw.chromium.launch(headless=True);self.context=self.browser.new_context(permissions=['clipboard-read','clipboard-write']);self.page=self.context.new_page();self.errors=[];self.external=[]
  self.page.on('pageerror',lambda error:self.errors.append(str(error)))
  self.page.route('**/*',lambda route:route.continue_()if route.request.url.startswith(self.origin+'/')else(self.external.append(route.request.url),route.abort()))
  self.page.goto(self.origin+'/code.css');self.page.set_content('<!doctype html><main id="host"></main>');self.page.add_script_tag(url=self.origin+'/code.js');self.page.evaluate(GATE,None)
 def tearDown(self):
  try:self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
  finally:self.browser.close();self.pw.stop();self.server.shutdown();self.thread.join(3);self.server.server_close();self.app.close();self.tmp.cleanup()
 def render(self,source='let old=41;old+1',append=True):
  self.page.evaluate('([source,append])=>{window.reader=ChaosCode.render(source);window.pending=reader.ready;if(append)document.querySelector("#host").append(reader)}',[source,append]);self.page.wait_for_function('()=>lateJSON.length===1')
 def release(self,index=None):
  selected=self.page.evaluate('(index)=>lateJSON.flatMap((item,i)=>{if(index!==null&&index!==i)return [];releaseLate(i);return [i]})',index)
  self.page.wait_for_function('(selected)=>selected.every(i=>!lateJSON[i].requested||lateJSON[i].resumed)',arg=selected)
  self.page.evaluate('()=>new Promise(resolve=>setTimeout(resolve,0))')
 def test_external_clear_keeps_empty_source_after_unabortable_analysis(self):
  self.render();self.page.evaluate('()=>reader.replaceChildren()');self.release();self.page.evaluate('()=>pending')
  self.assertEqual(self.page.locator('pre').evaluate('p=>p.childNodes.length'),0);self.assertEqual(self.page.evaluate('ChaosCode.source(reader)'),'')
 def test_replace_code_enhance_does_not_repaint_or_reuse_old_controls(self):
  self.render();self.page.evaluate('()=>{window.oldWrap=reader.querySelector(".chaos-code-wrap");const fresh=document.createElement("code");fresh.textContent="let fresh=42;fresh";reader.replaceChildren(fresh);ChaosCode.enhance(reader);window.before=reader.className;oldWrap.click()}');self.release();self.page.evaluate('()=>pending')
  self.assertEqual(self.page.locator('code').text_content(),'let fresh=42;fresh');self.assertEqual(self.page.evaluate('ChaosCode.source(reader)'),'let fresh=42;fresh');self.assertEqual(self.page.locator('.chaos-code-toolbar').count(),1);self.assertTrue(self.page.evaluate('reader.className===before'))
 def test_clear_cancels_but_delivered_format_and_retained_handlers_are_inert(self):
  self.render();self.release();self.page.evaluate('()=>pending');self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.page.wait_for_function('()=>lateJSON.length===2')
  self.page.evaluate('()=>{window.oldFormat=reader.querySelector(".chaos-code-format");window.oldCopy=reader.querySelector(".copy-code");window.oldWrap=reader.querySelector(".chaos-code-wrap");window.copied=[];navigator.clipboard.writeText=async source=>copied.push(source);ChaosCode.clear(reader);ChaosCode.clear(reader)}')
  self.assertTrue(self.page.evaluate('lateJSON[1].signal.aborted'));self.release();self.page.evaluate('()=>{oldFormat.onclick();oldCopy.click();oldWrap.click()}');self.page.evaluate('()=>reader.ready')
  self.assertEqual(self.page.locator('pre').evaluate('p=>p.childNodes.length'),0);self.assertEqual(self.page.evaluate('ChaosCode.source(reader)'),'');self.assertEqual(self.page.evaluate('copied'),[]);self.assertEqual(self.page.evaluate('lateJSON.length'),2)
 def test_formatted_analysis_and_old_controls_cannot_change_new_debug_view(self):
  self.render();self.release();self.page.evaluate('()=>pending');self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.page.wait_for_function('()=>lateJSON.length===2');self.release(1);self.page.wait_for_function('()=>lateJSON.length===3&&reader.dataset.codeView==="formatted"')
  self.page.get_by_role('button',name='原始排版',exact=True).click();self.page.evaluate('()=>{window.oldFormat=reader.querySelector(".chaos-code-format");ChaosCode.update(reader,"44",{activeLine:1,analyze:false});window.currentReady=reader.ready;oldFormat.onclick()}');self.release(2)
  self.assertEqual(self.page.locator('code').text_content(),'44');self.assertEqual(self.page.locator('[aria-current="step"]').get_attribute('data-line'),'1');self.assertEqual(self.page.evaluate('ChaosCode.source(reader)'),'44');self.assertTrue(self.page.evaluate('reader.ready===currentReady'));self.assertEqual(self.page.evaluate('lateJSON.length'),3)
 def test_detached_initial_render_and_toolbar_recovery_preserve_contract(self):
  self.render(append=False);self.release();self.page.evaluate('()=>pending');self.assertFalse(self.page.evaluate('reader.isConnected'));self.assertEqual(self.page.evaluate('reader.dataset.highlight'),'ready')
  self.page.evaluate('()=>{document.querySelector("#host").append(reader);reader.querySelector(".chaos-code-toolbar").remove();ChaosCode.enhance(reader);ChaosCode.enhance(reader)}');self.assertEqual(self.page.locator('.chaos-code-toolbar').count(),1);self.assertEqual(self.page.evaluate('ChaosCode.source(reader)'),'let old=41;old+1')
 def test_actual_debugger_terminal_clear_discards_old_parsed_responses(self):
  source='let value=41;\nvalue+1';project=self.app.create_project('lifecycle-debugger',source=source,capabilities=[]);run=self.app.start_debug(project['id'],project['version'],pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}]);deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   state=run.debugger()
   if state['status']=='paused':break
   time.sleep(.005)
  self.assertEqual(state['status'],'paused');self.page.goto(self.origin+'/?project='+project['id']);self.page.locator('.cm-editor').wait_for(state='attached');workbench_view(self.page,'source');self.page.evaluate(GATE,source);self.page.evaluate('run=>showRun(run)',run.get());self.page.wait_for_function('()=>ChaosDebugger.state.data?.status==="paused"&&lateJSON.some(item=>item.requested&&!item.released)');old=self.page.evaluate('()=>lateJSON.flatMap((item,i)=>item.requested&&!item.released?[i]:[])');self.page.locator('[data-debug-command="continue"]').click();self.page.wait_for_function('()=>ChaosDebugger.state.data?.status==="completed"');self.release()
  self.assertTrue(self.page.evaluate('(old)=>old.some(i=>lateJSON[i].resumed)',old))
  self.assertEqual(run.wait(5)['status'],'completed');self.assertEqual(self.page.locator('#debugSource').evaluate('p=>p.childNodes.length'),0);self.assertEqual(self.page.evaluate('ChaosCode.source(document.querySelector("#debugSource"))'),'');self.assertEqual(self.app.get_project(project['id'])['source'],source);self.assertFalse(self.page.evaluate('dirty'))
  output=os.environ.get('CHAOS_LIFECYCLE_AUDIT')
  if output:
   path=Path(output);path.mkdir(parents=True,exist_ok=True);self.page.locator('#debugPanel').screenshot(path=str(path/'debugger-terminal.png'))
if __name__=='__main__':unittest.main()
