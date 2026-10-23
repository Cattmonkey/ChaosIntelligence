"""Durable guide uses the existing Process panel and real mobile workbench."""
import importlib.util,json,shutil,subprocess,tempfile,threading,unittest
from pathlib import Path
from chaos import Framework,Settings
from chaos.server import make_server
from tests.test_durable_bot_surfaces import STEP,DEFINITION,INITIAL,wait
from workbench_actions import button as workbench_button,view as workbench_view
ROOT=Path(__file__).resolve().parents[1]
class DurableBotUIControls(unittest.TestCase):
 def test_actual_process_controls_preserve_guidance_receipt_order_and_bounds(self):
  node=shutil.which('node');self.assertIsNotNone(node,'Node required for durable guidance surfaces');p=subprocess.run([node,str(ROOT/'tests/durable_bot_controls.cjs'),str(ROOT)],capture_output=True,text=True,timeout=10);self.assertEqual(p.returncode,0,p.stdout+p.stderr);r=json.loads(p.stdout);self.assertEqual(r['status'],'passed');self.assertTrue(r['receiptStagesMonotonic']);self.assertTrue(r['noActionSuccessClaim'])
class DurableBotUI(unittest.TestCase):
 @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
 def test_real_process_guidance_and_actual_bot_result_in_existing_mobile_panel(self):
  from playwright.sync_api import sync_playwright
  with tempfile.TemporaryDirectory()as folder,Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:'))as app:
   app.create_project('p',source='null',files={'bot.chaos':STEP},capabilities=['process.manage']);app.create_process('p',1,'worker',DEFINITION,state=INITIAL);wait(app,1)
   server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
   try:
    with sync_playwright()as p:
     browser=p.chromium.launch(headless=True)
     try:
      context=browser.new_context(viewport={'width':390,'height':844});context.route('**/*',lambda route:route.continue_()if route.request.url.startswith(origin+'/')else(external.append(route.request.url),route.abort()));page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)));page.goto(origin+'/?project=p');page.locator('.cm-editor').wait_for();workbench_view(page,'source');workbench_button(page,'processesButton');dialog=page.get_by_role('dialog',name='持续进程',exact=True);dialog.get_by_role('button').filter(has_text='durable bot').first.click();page.wait_for_function('()=>ChaosProcesses.state.record?.key==="worker"')
      text='请核对新的计划 <script>window.guideInjected=true</script>';dialog.get_by_role('textbox',name='消息输入 JSON',exact=True).fill(json.dumps({'text':text},ensure_ascii=False));dialog.get_by_role('textbox',name='幂等消息 ID（可选）',exact=True).fill('ui-guide');dialog.get_by_role('button',name='追加持久指导',exact=True).click();page.wait_for_function('()=>ChaosProcesses.state.record?.turn===2');record=app.get_process('p','worker');self.assertEqual(record['state']['context'],{'count':2,'latest':{'text':text}});self.assertEqual(app.guide_process('p','worker',{'text':text},message_id='ui-guide')['status'],'applied');self.assertEqual(dialog.get_by_role('textbox',name='消息输入 JSON',exact=True).input_value(),json.dumps({'text':text},ensure_ascii=False));self.assertIsNone(page.evaluate('window.guideInjected'));self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'));self.assertEqual(errors,[]);self.assertEqual(external,[])
     finally:browser.close()
   finally:server.shutdown();server.server_close();thread.join(3)
