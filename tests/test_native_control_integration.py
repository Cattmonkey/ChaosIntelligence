import unittest
from chaos import Framework, Settings
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
SOURCE='let f=compile_function(fn(xs){var total=0;for x in xs{total=total+(x+1)*(x+1)};total},target="auto-control");[f([0,1,2]),length(compile_info(f)),compile_info(f).parameterGuards]'
class ControlIntegration(unittest.TestCase):
 def test_source_ir_format_and_normal_async_runtime(self):
  self.assertEqual(Framework.evaluate(SOURCE),[14,12,[]])
  self.assertEqual(Framework.evaluate(format_source(SOURCE)),[14,12,[]])
  self.assertEqual(Framework.evaluate(format_program(parse_program(SOURCE))),[14,12,[]])
  with Framework(settings=Settings(':memory:')) as app:
   p=app.create_project('control',source=SOURCE,capabilities=[])
   self.assertEqual(app.start(p['id'],p['version'],None,trace=False).result(10),[14,12,[]])
 def test_concurrent_original_task_contexts(self):
  src='let v=context_var("role",0);let f=compile_function(fn(n){var i=0;while i<n{i=i+1};i},target="auto-control");async fn worker(n){with context_bind([{variable:v,value:n}]) as b{await sleep(0);[f(n),v.get()]}};[await gather([worker(2),worker(3)]),v.get()]'
  self.assertEqual(Framework.evaluate(src),[[[2,2],[3,3]],0])
