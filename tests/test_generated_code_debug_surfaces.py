import json,unittest
from chaos import Framework
from chaos.program import parse_program,format_program
from chaos.ide_language import analyze
class GeneratedDebugSurfaces(unittest.TestCase):
 def test_source_ir_format_and_code_signature(self):
  source='let c=code_from("fn calc(x:Int,/,*,bonus:Int=2)->Int{x+bonus}");let f=function_from(c);f(40)'
  self.assertEqual(Framework.evaluate(source),42);self.assertEqual(Framework.evaluate(format_program(parse_program(source))),42)
  s=analyze('let c=code_from("fn(x,*,bonus=2){x+bonus}");let f=function_from(c);f(');self.assertEqual([p['name']for p in s['signatureHelp']['parameters']],['x','bonus'])
 def capabilities(self):
  from chaos import Settings
  with Framework(':memory:',settings=Settings(':memory:'))as app:return app.capabilities()
 def test_schema_additive_debug_source_capability(self):self.assertTrue(self.capabilities()['debugger']['generatedSources'])
