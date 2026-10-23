import unittest
from chaos import Framework
from chaos.ide_language import analyze
from chaos.native_specs import spec_for
from chaos.program import parse_program,format_program
from tests.test_module_values import literal
class ModuleValueSurfaces(unittest.TestCase):
 def test_shared_native_signature_and_lexical_shadow_are_inert(self):
  self.assertEqual(spec_for('module_from').result_type,'Record')
  h=analyze('module_from(')['signatureHelp'];self.assertEqual([p['name'] for p in h['parameters']],['program','bindings'])
  h=analyze('let module_from=fn(source,options){source};module_from(')['signatureHelp'];self.assertEqual([p['name'] for p in h['parameters']],['source','options'])
 def test_program_ir_roundtrip_and_closed_source_analysis(self):
  files={'entry.chaos':'import "lib.chaos" as L;import "std/math" as M;fn f(x:Int)->Int{M.abs(L.f(x))};export{f}','lib.chaos':'fn f(x:Int)->Int{x+2};export{f}'}
  s='module_from('+literal(files,'entry.chaos')+').f(40)';self.assertEqual(Framework.evaluate(s),42);self.assertEqual(Framework.evaluate(format_program(parse_program(s))),42)
  result=analyze(files['entry.chaos'],file='entry.chaos',files=files,entry='entry.chaos',language_versions={n:2 for n in files});self.assertFalse([v for v in result['diagnostics'] if v['severity']=='error'])
