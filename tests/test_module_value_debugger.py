import json,time,threading,unittest
from chaos import Framework,Settings
from chaos.language import Evaluator,ResourceLimit
from chaos.debugger import DebugSession
from tests.test_module_values import literal,definition
class ModuleValueDebugger(unittest.TestCase):
 def paused(self,r,after=0):
  end=time.monotonic()+3
  while time.monotonic()<end:
   v=r.debugger()
   if v['status']=='paused' and v['pauseId']>after:return v
   time.sleep(.002)
  self.fail(str(r.get()))
 def test_actual_module_locals_breakpoints_and_context_are_copied(self):
  with Framework(':memory:',settings=Settings(':memory:')) as app:
   files={'entry.chaos':'import "lib.chaos" as L;\nlet f=L.add;\nexport{f}','lib.chaos':'fn add(x:Int)->Int {\n let value=x+seed;\n value\n}\nexport{add}'}
   source='let p='+literal(files,'entry.chaos')+';\nlet m=module_from(p,{seed:2});\nm.f(40)'
   p=app.create_project('module-debug',source=source);r=app.start_debug(p['id'],1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}]);s=self.paused(r)
   self.assertEqual(len(s['generatedSourceContexts']),1);ctx=next(iter(s['generatedSourceContexts'].values()));self.assertEqual(ctx['entry'],'entry.chaos');self.assertEqual(set(ctx['virtualFiles']),set(files))
   virtual=ctx['virtualFiles']['lib.chaos'];self.assertEqual(s['generatedSources'][virtual]['source'],files['lib.chaos']);self.assertEqual(len(s['generatedSources'][virtual]),4)
   r.debug_command('breakpoints',breakpoints=[{'file':virtual,'line':2}]);r.debug_command('continue',pause_id=s['pauseId']);s=self.paused(r,s['pauseId']);self.assertEqual(s['location']['file'],virtual);self.assertEqual(s['frames'][0]['name'],'add');self.assertEqual(next(x['value']for x in s['frames'][0]['locals']if x['name']=='x'),40)
   ctx['virtualFiles'].clear();self.assertEqual(len(next(iter(r.debugger()['generatedSourceContexts'].values()))['virtualFiles']),2);self.assertEqual(r.get()['snapshot']['files'],p['files']);r.debug_command('breakpoints',breakpoints=[]);r.debug_command('continue',pause_id=s['pauseId']);self.assertEqual(r.result(3),42)
 def test_sink_batch_quota_failure_consumes_no_evaluator_entries(self):
  from chaos.generated_sources import Source
  from chaos.module_values import request,construct
  from chaos.program_values import builtins
  project={'id':'p','version':1,'entry':'main.chaos','files':{'main.chaos':'null'}};d=DebugSession('r',project,{'pauseOnEntry':False,'breakpoints':[],'pauseTimeout':1},threading.Event(),lambda *a:None)
  e=Evaluator(source_handler=d.register_source);other=Evaluator()
  for i in range(63):other.run('function_from(code_from("fn(){'+str(i)+'}"))')
  for value in other.generated_sources.values.values():d.register_source(value)
  p=builtins(e)['program_from'](definition({'main.chaos':'import "lib.chaos" as L;let f=L.f;export{f}','lib.chaos':'fn f(){42};export{f}'}))
  with self.assertRaises(ResourceLimit):e.suspension.drive(construct(e,request(e,p,{})))
  self.assertEqual(e.generated_sources.values,{});self.assertEqual(len(d.generated_sources.values),63)
