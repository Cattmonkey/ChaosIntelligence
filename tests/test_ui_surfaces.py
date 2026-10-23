"""The same immutable UI language data drives native code, HTTP and OpenUI React."""
from workbench_actions import run_options as workbench_run_options
import hashlib,json,os,shutil,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from chaos import Framework,Client,Settings,Registry,ProviderSpec,Final
from chaos.ide_language import analyze,format_source
from chaos.native_specs import spec_for
from chaos.program import parse_program
from chaos.server import make_server
try:
 from playwright.sync_api import sync_playwright
except ImportError:sync_playwright=None
ROOT=Path(__file__).resolve().parent.parent
OPENUI='''heading = Heading("Research overview",1)
metrics = Stack([Metric("Checks","12","All verified"),Metric("Speed","3.2x","Local computation")],"row","medium")
evidence = Card("Evidence",[Table(["Name","Value"],[["Beta",2],["Alpha",1]]),BarChart("Coverage",["Functions","Agents"],[8,12])])
tabs = Tabs([Tab("Summary",[Text("Native UI artifacts")]),Tab("Details",[Disclosure("Method",[Code("fn twice(x:Int){x*2}","chaos")])])])
root = Stack([heading,metrics,evidence,tabs],"column","medium")'''
SOURCE='ui_from_openui('+json.dumps(OPENUI)+',kind="report",title="Research report")'
SLIDES='root = Slides([Slide("First",[Text("Native functions")]),Slide("Second",[Text("Agent composition")]) ],1000)'
class UISurfaces(unittest.TestCase):
 def test_metadata_alias_signatures_and_awaited_result(self):
  for name in ['ui_node','ui_artifact','ui_from_openui','ui_info','ui_definition','ui_source','ui_program','ui_export','ui_prompt','ui_validate','ui_generate','ui_dispatch']:self.assertIsNotNone(spec_for(name))
  self.assertEqual(spec_for('ui_generate').awaited_type,'UIArtifact');self.assertTrue(all(p.mode=='keyword_only'for p in spec_for('ui_generate').parameters[2:]))
  result=analyze('let compile=ui_from_openui;compile(');self.assertEqual(result['signatureHelp']['parameters'][0]['name'],'source')
  result=analyze('let doc=ui_from_openui("root=Text(\\\"x\\\")");let info=ui_info(doc);info.');self.assertEqual({c['label']for c in result['completions']},{'kind','title','hash','libraryHash','nodeCount','actionNames'})
  self.assertEqual(analyze(SOURCE)['diagnostics'],[])
 def test_native_source_structure_format_and_export_roundtrip(self):
  definition=Framework.evaluate('ui_definition('+SOURCE+')');self.assertEqual(definition['kind'],'report');self.assertEqual(set(definition),{'schema','kind','title','source','root','libraryHash','hash'})
  self.assertEqual(Framework.evaluate('ui_definition('+format_source(SOURCE)+')'),definition)
  self.assertEqual(parse_program(SOURCE)['semanticHash'],parse_program(format_source(SOURCE))['semanticHash'])
  exported=Framework.evaluate('ui_program('+SOURCE+')');self.assertEqual(Framework.evaluate('ui_definition('+exported.strip()+')'),definition)
  value=Framework.evaluate('ui_validate('+SOURCE+',[{component:"Table",minimum:1},{component:"Slides",minimum:1}])');self.assertFalse(value['passed']);self.assertEqual([v['passed']for v in value['checks']],[True,False])
 def test_vendor_distribution_identity_and_no_remote_renderer(self):
  manifest=json.loads((ROOT/'vendor/openui/provenance.json').read_text());self.assertEqual(hashlib.sha256((ROOT/'web/vendor/openui-bundle.min.js').read_bytes()).hexdigest(),manifest['installedBundleSha256'])
  self.assertEqual(manifest['version'],'0.1.7');self.assertIn('MIT',(ROOT/'vendor/openui/LICENSE').read_text());text=(ROOT/'web/artifacts.js').read_text();self.assertIn('publishObservability:false',text);self.assertIn('toolProvider:null',text)
 @unittest.skipUnless(shutil.which('node'),'Node unavailable')
 def test_frontend_policy_and_stream_commit_lifecycle(self):
  result=subprocess.run(['node',str(ROOT/'tests/ui_artifact_controls.cjs')],capture_output=True,text=True,timeout=10);self.assertEqual(result.returncode,0,result.stderr)
class UIHTTP(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.app=Framework(Path(self.tmp.name)/'data',settings=Settings(':memory:'));self.addCleanup(self.app.close)
  self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.tmp.name)/'control',update_home=Path(self.tmp.name)/'updates');self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)
  self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
 def close(self):self.server.shutdown();self.thread.join(3);self.server.server_close()
 def test_sdk_http_cli_and_native_have_one_definition(self):
  p=self.client.create_project('ui',source=SOURCE);wire=self.client.start('ui',p['version'],None,mode='source').result();definition=Framework.evaluate('ui_definition('+SOURCE+')');self.assertEqual(wire,{'$ui':definition})
  self.assertEqual(self.client._request('POST','/ui/compile',{'source':OPENUI,'kind':'report','title':'Research report'}),definition)
  process=subprocess.run([sys.executable,'-m','chaos','run','-'],input=SOURCE,capture_output=True,text=True,timeout=15);self.assertEqual(process.returncode,0,process.stderr);self.assertEqual(json.loads(process.stdout),wire)
 def test_export_and_requirement_verification_use_shared_schema(self):
  definition=Framework.ui_compile(OPENUI,kind='report',title='Research report');schema=self.client._request('GET','/ui/schema');self.assertEqual(schema['libraryHash'],definition['libraryHash']);self.assertEqual(len(schema['libraryJSONSchema']['$defs']),16)
  result=self.client._request('POST','/ui/validate',{'definition':definition,'requirements':[{'component':'Table','minimum':1}]});self.assertTrue(result['passed'])
  exported=self.client._request('POST','/ui/export',{'definition':definition,'format':'html'});self.assertIn('<table',exported['content']);self.assertEqual(exported['hash'],definition['hash'])
 @unittest.skipUnless(sync_playwright,'Playwright unavailable')
 def test_actual_openui_parser_preview_edit_reports_slides_and_streaming(self):
  project=self.app.get_project('workspace');project.update(source=SOURCE,files={'main.chaos':SOURCE},entry='main.chaos');self.app.save_project(project,from_view='source')
  folder=os.environ.get('CHAOS_UI_AUDIT');output=Path(folder)if folder else None
  if output:output.mkdir(parents=True,exist_ok=True)
  errors=[];external=[]
  with sync_playwright()as pw:
   browser=pw.chromium.launch(headless=True)
   try:
    context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
    context.route('**/*',lambda r:r.continue_()if r.request.url.startswith(self.origin+'/')else(external.append(r.request.url),r.abort()))
    def shot(name):
     if output:
      page.wait_for_function('()=>{const n=document.querySelector("#console");return !n.classList.contains("artifact-active")||Math.abs(n.getBoundingClientRect().height-innerHeight*(n.classList.contains("expanded")?.65:.55))<2}');page.screenshot(path=str(output/(name+'.png')))
    page.goto(self.origin);page.wait_for_function('()=>!!project');page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&ChaosArtifacts.state.definition?.kind==="report"');page.locator('[data-console="artifact"]').click();page.locator('#expandConsole').click()
    # Report viewing retains the full expanded area, and output collapse also works for artifacts.
    page.wait_for_function('()=>document.querySelector("#console").getBoundingClientRect().height>=640')
    page.locator('#wbOutputToggle').click();page.wait_for_function('()=>document.querySelector("#console").getBoundingClientRect().height<40')
    page.locator('#wbOutputToggle').click();page.wait_for_function('()=>document.querySelector("#console").getBoundingClientRect().height>=640')
    page.wait_for_selector('.ui-card');self.assertEqual(page.locator('.ui-metric').count(),2);self.assertEqual(page.locator('.ui-chart-bar').count(),2);shot('ui-report')
    # The real upstream parser must agree with the native canonical tree.
    page.wait_for_function('()=>!!ChaosArtifacts.state.parse?.root');actual=page.evaluate('()=>{function n(v){if(Array.isArray(v))return v.map(n);if(v&&typeof v==="object"){if(v.type==="element")return {type:"element",typeName:v.typeName,props:n(v.props),partial:false};return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,n(x)]))}return v}return n(ChaosArtifacts.state.parse.root)}');definition=page.evaluate('ChaosArtifacts.state.definition');self.assertEqual(actual,definition['root'])
    page.locator('.ui-table-scroll th button').first.click();self.assertEqual(page.locator('.ui-table-scroll tbody tr').first.inner_text(),'Alpha\t1');page.get_by_role('tab',name='Details',exact=True).click();page.locator('.ui-disclosure summary').click();self.assertIn('fn twice',page.locator('.ui-artifact-preview .ui-code').inner_text());shot('ui-interaction');page.locator('.ui-artifact-source summary').nth(1).click();page.get_by_label('产物要求 JSON',exact=True).fill('[{"component":"Table","minimum":1}]');page.get_by_role('button',name='验证要求',exact=True).click();page.wait_for_function('()=>document.querySelector("[data-passed]")?.dataset.passed==="true"');page.locator('.ui-artifact-source summary').nth(1).click()
    with page.expect_download()as download:page.locator('[data-ui-export="html"]').click()
    html=Path(self.tmp.name)/'report.html';download.value.save_as(html);self.assertIn('Research report',html.read_text())
    page.locator('.ui-artifact-source summary').first.click();page.get_by_label('OpenUI 源码',exact=True).fill('root = Heading("Edited draft",1)');page.get_by_role('button',name='校验并预览',exact=True).click();page.wait_for_function('()=>document.querySelector(".ui-artifact-preview h1")?.textContent==="Edited draft"');self.assertEqual(page.locator('.ui-artifact-preview h1').inner_text(),'Edited draft');self.assertEqual(page.evaluate('ChaosArtifacts.state.original.hash'),definition['hash']);shot('ui-source-edit')
    page.get_by_label('产物类型',exact=True).select_option('slides');page.get_by_label('OpenUI 源码',exact=True).fill(SLIDES);page.get_by_role('button',name='校验并预览',exact=True).click();page.wait_for_selector('.ui-slide');page.get_by_role('button',name='下一页',exact=True).click();self.assertIn('Second',page.locator('.ui-slide').inner_text());page.get_by_role('button',name='播放演示',exact=True).click();page.wait_for_function('()=>document.querySelector(".ui-slide")?.textContent.includes("First")');page.get_by_role('button',name='暂停演示',exact=True).click();shot('ui-slides')
    page.set_viewport_size({'width':390,'height':900});self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'));shot('ui-mobile');page.get_by_role('button',name='加入 .chaos 草稿',exact=True).click();page.wait_for_function('()=>dirty&&Object.keys(project.files).some(p=>p.startsWith("modules/ui_artifact"))');self.assertEqual(self.client.get_project('workspace')['files'],{'main.chaos':SOURCE});page.set_viewport_size({'width':1440,'height':1000})
    # The real configured model effect streams through persisted events and SSE.
    settings=self.app._engine.settings;settings.update({'providers':{'text':{'apiKey':'fictional-local-test-key'}},'roles':{'executor':{'model':'test-model'}}},1)
    release=threading.Event();requests=[]
    class Stream:
     def __iter__(self):
      yield {'choices':[{'index':0,'delta':{'content':'root = Heading("Streaming preview",1)'},'finish_reason':None}]}
      release.wait(10)
      yield {'choices':[{'index':0,'delta':{'content':None},'finish_reason':'stop'}]}
     def close(self):release.set()
    stream=Stream()
    fake=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:requests.append(kw)or stream)),close=lambda:None)
    generation='agent designer{provider:"model",tools:[]};await ui_generate(designer,"Local preview",title="Streaming")'
    current=self.app.get_project('workspace');current.update(source=generation,files={'main.chaos':generation});self.app.save_project(current,from_view='source')
    with patch.object(settings,'client',return_value=fake):
     page.reload();page.wait_for_function('()=>!!project');page.locator('#runButton').click();page.locator('[data-console="artifact"]').click();page.locator('#expandConsole').click();page.wait_for_function('()=>window.ChaosArtifacts?.state.partial&&document.querySelector(".ui-artifact-preview h1")?.textContent==="Streaming preview"');self.assertEqual(page.evaluate('ChaosArtifacts.state.definition'),None);self.assertTrue(page.locator('[data-ui-export="html"]').is_disabled());shot('ui-streaming');release.set();page.wait_for_function('()=>!busy&&!launching&&ChaosArtifacts.state.definition?.title==="Streaming"');self.assertTrue(requests[0]['stream'])
    # A literal action invokes the original saved Chaos source in a new run.
    actionui='root = ActionButton("Inspect payload","inspect",{"answer":42})'
    action='let document=ui_from_openui('+json.dumps(actionui)+');if input!=null{await ui_dispatch(document,input.event,{inspect:agent_tool("inspect",fn(payload){payload})})}else{document}'
    current=self.app.get_project('workspace');current.update(source=action,files={'main.chaos':action});self.app.save_project(current,from_view='source');page.reload();page.wait_for_function('()=>!!project');workbench_run_options(page);page.locator('#jsonInput').check();page.locator('#taskInput').fill('null');page.locator('#runButton').click();page.wait_for_function('()=>!busy&&!launching&&ChaosArtifacts.state.definition');page.locator('[data-console="artifact"]').click();page.wait_for_selector('.ui-artifact-preview button');page.wait_for_function('()=>ChaosArtifacts.actionAllowed()');page.get_by_role('button',name='Inspect payload',exact=True).click();page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel").textContent.includes("answer")');self.assertEqual(json.loads(page.locator('#resultPanel').inner_text()),{'answer':42})
    self.assertEqual(errors,[]);self.assertEqual(external,[])
    if output:(output/'browser-result.json').write_text(json.dumps({'status':'passed','sourceRoot':str(ROOT),'nativeUpstreamCanonicalEqual':True,'definitionHash':definition['hash'],'libraryHash':definition['libraryHash'],'reportExport':True,'interactiveTableTabsDisclosure':True,'sourceEditImmutableOriginal':True,'slidesAutoplay':True,'partialNotCommitted':True,'mobileNoOverflow':True,'errors':errors,'externalRequests':external,'screenshots':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in output.glob('*.png')}},indent=2)+'\n')
   finally:browser.close()
if __name__=='__main__':unittest.main()
