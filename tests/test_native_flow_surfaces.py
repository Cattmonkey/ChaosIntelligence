import unittest
from chaos import Framework,Settings
from chaos.ide_language import analyze
from chaos.native_specs import spec_for
from chaos.documentation import Documentation
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
SOURCE='let f=compile_function(fn(n){var i=0;var total=0;while i<n{total=total+i;i=i+1};total},target="auto-flow");[f(4),compile_flow_info(f).abi,length(compile_info(f))]'
class FlowSurfaces(unittest.TestCase):
 def test_shared_shapes_signature_shadow_and_inert_query(self):
  self.assertEqual([p.name for p in spec_for('compile_function').parameters],['fn','target'])
  self.assertIn('native-flow',spec_for('compile_function').summary)
  fields={c['label'] for c in analyze('compile_flow_info(input).')['completions']}
  self.assertEqual(fields,{'schema','abi','status','instructionCount','branchCount','checkpointCount','resumeCalls','nativeResumes','portableResumes','lastPC','definitionPolicy'})
  self.assertEqual(analyze('fn compile_flow_info(x){x};compile_flow_info(')['signatureHelp']['parameters'][0]['name'],'x')
 def test_source_ir_format_and_actual_project(self):
  for src in (SOURCE,format_source(SOURCE),format_program(parse_program(SOURCE))):self.assertEqual(Framework.evaluate(src),[6,'pc-host-events/1',12])
  with Framework(settings=Settings(':memory:')) as app:
   p=app.create_project('flow',source=SOURCE,capabilities=[])
   self.assertEqual(app.start(p['id'],p['version'],None,trace=False).result(10),[6,'pc-host-events/1',12])
 def test_concurrent_original_contexts(self):
  src='let v=context_var("role",0);let f=compile_function(fn(n){var i=0;while i<n{i=i+1};i},target="auto-flow");async fn worker(n){with context_bind([{variable:v,value:n}]) as b{await sleep(0);[f(n),v.get()]}};[await gather([worker(2),worker(3)]),v.get()]'
  self.assertEqual(Framework.evaluate(src),[[[2,2],[3,3]],0])
 def test_catalogued_truthful_guide(self):
  text=Documentation().page('native-flow')['markdown'];self.assertIn('definition_changed',text);self.assertIn('尚未完成',text)
