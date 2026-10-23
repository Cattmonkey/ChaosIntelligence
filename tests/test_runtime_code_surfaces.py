import json,unittest
from chaos import Framework
from chaos.ide_language import analyze
from chaos.native_specs import spec_for,SHAPES
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
class CodeSurfaces(unittest.TestCase):
 def test_static_signature_inert_and_shadowed(self):
  source='let c=code_from("fn(x:Int,/,*,bonus:Int=tool(\\\"forbidden\\\",2))->Int{x+bonus}");let f=function_from(c);f('
  help_=analyze(source)['signatureHelp'];self.assertEqual([p['name'] for p in help_['parameters']],['x','bonus'])
  shadow=analyze('let function_from=fn(item,scope){item};function_from(')['signatureHelp'];self.assertEqual([p['name']for p in shadow['parameters']],['item','scope'])
 def test_metadata_and_roundtrip(self):
  self.assertEqual(len(SHAPES['CodeInfo']),7)
  for name in ('code_from','code_ir','code_source','code_edit','code_info','function_from'):self.assertIsNotNone(spec_for(name))
  s='let c=code_from("fn recur(n){if n==0{1}else{n*recur(n-1)}}");function_from(c)(5)'
  for text in (s,format_source(s),format_program(parse_program(s))):self.assertEqual(Framework.evaluate(text),120)
 def test_pure_macro_to_single_function(self):
  self.assertEqual(Framework.evaluate('let c=code_from("macro build(){quote{fn(x){x+1}}};build!()");function_from(c)(41)'),42)
 def test_static_edit_is_conservative(self):
  h=analyze('let c=code_from("fn(x){x}");let d=code_edit(c,[]);let f=function_from(d);f(')['signatureHelp']
  self.assertTrue(h is None or h['parameters']==[])
