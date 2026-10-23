"""Durable Process guidance shares native calls, transport and checkpoint state."""
import inspect,json,os,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,Client,Settings,Registry,Final
from chaos.client import ConflictError,ValidationError
from chaos.documentation import Documentation
from chaos.ide_language import analyze
from chaos.language import Evaluator,Native,LanguageError
from chaos.native_specs import all_specs,spec_for
from chaos.program import parse_program,format_program
from chaos.server import make_server
from chaos.suspension import Scheduler
ROOT=Path(__file__).resolve().parents[1]
STEP=(ROOT/'examples/durable-bot/bot.chaos').read_text()
DEFINITION={'kind':'process','name':'durable bot','module':'bot.chaos','export':'step','intervalSeconds':None,'maxTurns':None}
INITIAL={'goal':'three checked turns','context':{'count':0,'latest':None}}
def wait(app,turn,status='waiting'):
 end=time.monotonic()+6
 while time.monotonic()<end:
  row=app.get_process('p','worker')
  if row['turn']>=turn and row['status']==status:return row
  if row['status']=='needs_reconciliation':raise AssertionError(row)
  time.sleep(.01)
 raise AssertionError(row)
class DurableBotSurfaces(unittest.TestCase):
 def test_exact_cold_native_alias_signature_and_inert_shared_shape(self):
  env=Evaluator().env.values;spec=spec_for('process_guide')
  self.assertEqual([p.name for p in inspect.signature(env['process_guide'].call).parameters.values()],['key','input','message_id','target'])
  self.assertEqual([p.name for p in spec.parameters],['key','input','message_id','target']);self.assertEqual(spec.awaited_shape,'ProcessGuidanceReceipt')
  self.assertEqual(len([v for v in env.values()if type(v)is Native]),335);self.assertEqual(len(all_specs()),381)
  self.assertEqual(Framework.evaluate('import "std/bot" as B;[B.process_guide==process_guide,type_of(B.process_guide("worker",null))]'),[True,'Task'])
  base='import "std/bot" as B;let guide=B.process_guide;let cold=guide("worker",{text:"next"},message_id="1-guide");let receipt=await cold;'
  with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('analysis resumed')):
   info=analyze(base+'receipt.status',position=len(base)+len('receipt.'));self.assertEqual(info['diagnostics'],[])
   self.assertEqual({r['label']for r in info['completions']},{'key','runId','messageId','revision','status','target'})
   symbols={r['name']:r['type']for r in info['symbols']};self.assertEqual(symbols['cold'],'Task');self.assertEqual(symbols['receipt'],'Record')
   shadow=analyze('fn process_guide(key,input)->Int{42};let result=process_guide("worker",0)');self.assertEqual(next(r['type']for r in shadow['symbols']if r['name']=='result'),'Int')
 def test_source_ir_generated_macro_and_compiler_keep_original_cold_effect(self):
  sources=['type_of(process_guide("worker",42))','macro guide(callable,k,x){quote{unquote(callable)(unquote(k),unquote(x))}};type_of(guide!(process_guide,"worker",42))','let f=function_from(code_from("fn(k,x){process_guide(k,x)}"));type_of(f("worker",42))']
  for target in ('interpreter','auto','auto-flow'):sources.append('let f=compile_function(fn(k,x){process_guide(k,x)},target='+json.dumps(target)+');type_of(f("worker",42))')
  for source in sources:
   with self.subTest(source=source):
    formatted=format_program(parse_program(source));self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(source)['semanticHash']);self.assertEqual(Framework.evaluate(formatted),'Task')
  with self.assertRaises(LanguageError)as failure:
   Framework.evaluate('macro guide(k,x){quote{process_guide(unquote(k),unquote(x))}};type_of(guide!("worker",42))')
  self.assertEqual(failure.exception.code,'macro_error')
 def test_sdk_http_cli_same_identity_restart_and_actual_checkpoint(self):
  with tempfile.TemporaryDirectory()as folder:
   db=Path(folder)/'runtime.sqlite'
   with Framework(db,settings=Settings(':memory:'))as app:
    app.create_project('p',source='null',files={'bot.chaos':STEP},capabilities=['process.manage']);app.create_process('p',1,'worker',DEFINITION,state=INITIAL);first=wait(app,1);self.assertEqual(first['state']['context']['count'],1)
    server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin='http://127.0.0.1:'+str(server.server_port)
    try:
     client=Client(origin);value={'text':'second'};receipt=client.guide_process('p','worker',value,message_id='1-guide');self.assertEqual(set(receipt),{'key','runId','messageId','revision','status'})
     second=wait(app,2);self.assertEqual(second['state']['goal'],INITIAL['goal']);self.assertEqual(second['state']['context'],{'count':2,'latest':value});self.assertEqual(second['output']['count'],2)
     self.assertEqual(client.guide_process('p','worker',value,message_id='1-guide')['status'],'applied')
     with self.assertRaises(ConflictError):client.send_process('p','worker',value,message_id='1-guide')
     with self.assertRaises(ConflictError):client.guide_process('p','worker',{'text':'different'},message_id='1-guide')
     with self.assertRaises(ValidationError):client._request('POST','/projects/p/processes/worker/guidance',{'input':None,'lease':'forged'})
     app._engine.pause_dispatch(True)
     command=[sys.executable,'-B','-m','chaos','process','guide','p','worker','--input-json','{"text":"third"}','--message-id','2-guide','--server',origin]
     proc=subprocess.run(command,cwd=folder,env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=10);self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(json.loads(proc.stdout)['status'],'queued')
    finally:server.shutdown();server.server_close();thread.join(3)
   with Framework(db,settings=Settings(':memory:'))as restored:
    final=wait(restored,3,'completed');self.assertEqual(final['state']['goal'],INITIAL['goal']);self.assertEqual(final['state']['context'],{'count':3,'latest':{'text':'third'}});self.assertEqual(final['output']['count'],3)
    self.assertEqual(restored.guide_process('p','worker',{'text':'third'},message_id='2-guide')['status'],'applied');self.assertEqual(restored.get_process('p','worker')['turn'],3)
 def test_active_run_and_process_guide_share_actual_bot_admission_identity(self):
  registry=Registry();entered=threading.Event();release=threading.Event();plans=[];actions=[]
  @registry.provider('fixture.plan')
  def plan(request,context):
   context.checkpoint();plans.append(request.input)
   if len(plans)==1:
    entered.set()
    while not release.wait(.01):context.checkpoint()
   return Final('old'if not request.input['guidance']else request.input['guidance'][-1]['input']['text'])
  @registry.tool('fixture.act')
  def act(value,context):context.checkpoint();actions.append(value);return value
  source='agent thinker{provider:"fixture.plan",tools:[]};async fn step(ctx){fn plan(s){thinker(s)};fn execute(p,s){tool("fixture.act",p)};fn verify(s){{done:true,context:s.context,output:s.evidence}};let worker=bot(ctx.state.goal,plan,execute,verify,context=ctx.state.context);let actual=await worker(ctx.input);process_step(actual.state,actual.output,actual.done)};export{step};'
  with Framework(registry=registry,settings=Settings(':memory:'))as app:
   app.create_project('p',source='null',files={'bot.chaos':source},capabilities=['process.manage','fixture.act']);app.create_process('p',1,'worker',DEFINITION,state=INITIAL)
   try:
    self.assertTrue(entered.wait(4));row=app.get_process('p','worker');run_id=row['pending']['runId'];queued=app.guide(run_id,{'text':'new'},message_id='shared-guide');again=app.guide_process('p','worker',{'text':'new'},message_id='shared-guide');self.assertEqual(queued['messageId'],again['messageId']);self.assertEqual(queued['revision'],again['revision']);release.set();final=wait(app,1,'completed')
   finally:release.set()
   self.assertEqual(actions,['new']);self.assertGreaterEqual(len(plans),2);self.assertEqual(final['output'],['new'])
   self.assertEqual(app.guide_process('p','worker',{'text':'new'},message_id='shared-guide')['status'],'applied')
   events=app.events('p',run_id=run_id,limit=300)['items'];queued=next(e for e in events if e['type']=='process.guidance.queued');received=next(e for e in events if e['type']=='process.bot.receive');applied=next(e for e in events if e['type']=='process.bot.adopt');self.assertLess(queued['id'],received['id']);self.assertLess(received['id'],applied['id']);self.assertEqual(received['payload']['messageId'],'shared-guide');self.assertEqual(applied['payload']['messageId'],'shared-guide')
 def test_actual_stdio_lsp_keeps_shared_guide_signature_and_awaited_fields(self):
  from tests.test_lsp_integration import StdioClient
  from chaos.lsp_workspace import TextMap
  source='let enqueue=process_guide;let cold=enqueue("worker",{text:"next"},message_id="1-guide");let receipt=await cold;receipt.status'
  with tempfile.TemporaryDirectory()as directory:
   folder=Path(directory);path=folder/'main.chaos';path.write_text(source);client=StdioClient(folder)
   try:
    document={'textDocument':{'uri':path.as_uri()}};client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':source}})
    offset=source.index('enqueue("worker"')+len('enqueue(');position=TextMap(source).position(len(source[:offset].encode('utf-16-le'))//2);signature=client.request('textDocument/signatureHelp',{**document,'position':position});self.assertIn('message_id',signature['signatures'][0]['label']);self.assertIn('Task',signature['signatures'][0]['label'])
    offset=source.index('receipt.status')+len('receipt.');position=TextMap(source).position(len(source[:offset].encode('utf-16-le'))//2);completion=client.request('textDocument/completion',{**document,'position':position});items=completion.get('items',[])if isinstance(completion,dict)else completion;self.assertEqual({r['label']for r in items},{'key','runId','messageId','revision','status','target'})
    self.assertTrue(client.request('textDocument/semanticTokens/full',document)['data'])
   finally:client.close()
   self.assertEqual(path.read_text(),source)
 def test_documentation_uses_same_cold_contract_and_honest_restart_boundary(self):
  docs=Documentation(ROOT/'docs',ROOT/'examples',ROOT/'web');checked=docs.check();self.assertEqual(checked['status'],'passed');self.assertEqual(checked['pages'],101);self.assertEqual(checked['examples'],408)
  page=docs.page('processes');self.assertIn('process_guide',page['markdown']);self.assertIn('不恢复闭包',docs.page('native-bots')['markdown']);self.assertIn('defog-ai/opendot',docs.page('bot-language-design')['markdown'])
