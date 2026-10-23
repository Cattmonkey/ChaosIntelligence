import json,threading,time,unittest
from chaos import Framework,Settings
from chaos.debugger import DebugSession
class GeneratedCodeDebugger(unittest.TestCase):
 def setUp(self):self.app=Framework(':memory:',settings=Settings(':memory:'));self.addCleanup(self.app.close)
 def paused(self,run,after=0):
  end=time.monotonic()+3
  while time.monotonic()<end:
   s=run.debugger()
   if s['status']=='paused'and s['pauseId']>after:return s
   time.sleep(.003)
  self.fail(str(run.get()))
 def start(self,fragment,call='f(20)',**options):
  source='let c=code_from('+json.dumps(fragment)+');\nlet f=function_from(c);\n'+call
  p=self.app.create_project('generated-'+str(time.time_ns()),source=source);r=self.app.start_debug(p['id'],1,**options);return p,r
 def test_step_into_virtual_source_and_registered_breakpoint(self):
  fragment='fn calc(x:Int)->Int{let y=x+1;y+1}'
  p,r=self.start(fragment,call='let answer=f(20);\nanswer',pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}]);s=self.paused(r);self.assertEqual(len(s['generatedSources']),1);file=next(iter(s['generatedSources']));d=s['generatedSources'][file];self.assertEqual(set(d),{'source','languageVersion','sourceHash','semanticHash'})
  line=next(i for i,text in enumerate(d['source'].splitlines(),1)if 'let y' in text);r.debug_command('breakpoints',breakpoints=[{'file':file,'line':line}]);r.debug_command('continue',pause_id=s['pauseId']);s=self.paused(r,s['pauseId']);self.assertEqual(s['location']['file'],file);self.assertEqual(s['frames'][0]['name'],'calc');self.assertEqual(next(x['value']for x in s['frames'][0]['locals']if x['name']=='x'),20)
  s['generatedSources'][file]['source']='corrupt';self.assertEqual(r.debugger()['generatedSources'][file]['source'],d['source']);self.assertEqual(r.get()['snapshot']['files'],p['files']);r.debug_command('breakpoints',breakpoints=[]);r.debug_command('step_out',pause_id=s['pauseId']);s=self.paused(r,s['pauseId']);self.assertEqual(s['location']['file'],'main.chaos');r.debug_command('continue',pause_id=s['pauseId']);self.assertEqual(r.result(3),22)
 def test_queries_do_not_register_and_defaults_enter_after_registration(self):
  fragment='fn calc(x=seed()){x*2}'
  source='var calls=0;fn seed(){calls=calls+1;21};\nlet c=code_from('+json.dumps(fragment)+');\nlet f=function_from(c,{seed:seed});\n[f(),calls]'
  p=self.app.create_project('default-'+str(time.time_ns()),source=source);r=self.app.start_debug(p['id'],1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}]);s=self.paused(r);self.assertEqual(s['generatedSources'],{});r.debug_command('step_into',pause_id=s['pauseId']);s=self.paused(r,s['pauseId']);self.assertTrue(s['generatedSources']);self.assertIn(s['location']['file'],s['generatedSources']);r.debug_command('continue',pause_id=s['pauseId']);self.assertEqual(r.result(3),[42,1])
 def test_unknown_sources_rejected_and_cancel_cleans_live_frames(self):
  p,r=self.start('fn(x){x+1}');s=self.paused(r)
  with self.assertRaises(ValueError):r.debug_command('breakpoints',breakpoints=[{'file':'code/unknown.chaos','line':1}])
  r.cancel();self.assertEqual(r.wait(3)['status'],'cancelled');self.assertEqual(r.debugger()['frames'],[])
 def test_four_flavors_run_under_registered_debug_sources(self):
  for fragment,call,expected in [('fn*(x){yield x;yield x+1}','collect(f(20))',[20,21]),('async fn*(x){await sleep(0);yield x}','await acollect(f(20))',[20]),('async fn(x){await sleep(0);x+22}','await f(20)',42)]:
   p,r=self.start(fragment,call);s=self.paused(r);r.debug_command('continue',pause_id=s['pauseId']);self.assertEqual(r.result(3),expected);self.assertTrue(r.debugger()['generatedSources'])
 def test_redacted_sources_and_no_runtime_owner_in_snapshot(self):
  from chaos.code_values import make,request,construct
  from chaos.language import Evaluator
  p={'id':'p','version':1,'entry':'main.chaos','files':{'main.chaos':'null'}};d=DebugSession('r',p,{'pauseOnEntry':False,'breakpoints':[],'pauseTimeout':1},threading.Event(),lambda *a:None,lambda x:json.loads(json.dumps(x).replace('secret123','[redacted]')))
  e=Evaluator(source_handler=d.register_source);c=make(e,'fn(){"secret123"}');e.suspension.drive(construct(e,request(e,c,{})));raw=d.snapshot()['generatedSources'];self.assertTrue(raw);self.assertNotIn('owner',json.dumps(raw));self.assertEqual(len(raw),1);self.assertNotIn('secret123',json.dumps(raw))
