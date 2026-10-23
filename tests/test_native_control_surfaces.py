import unittest
from chaos.ide_language import analyze
from chaos.documentation import Documentation
from chaos.native_specs import spec_for
class ControlSurfaces(unittest.TestCase):
 def test_same_signature_and_target_help(self):
  self.assertEqual([p.name for p in spec_for('compile_function').parameters],['fn','target'])
  help_=analyze('let f=compile_function;f(input,target=')['signatureHelp']
  self.assertEqual([p['name'] for p in help_['parameters']],['fn','target'])
  self.assertIn('native-control',spec_for('compile_function').summary)
 def test_query_shapes_and_lexical_shadow_remain(self):
  self.assertEqual({c['label'] for c in analyze('compile_info(input).')['completions']},{'schema','status','requestedTarget','backend','target','compilerVersion','parameterGuards','nodeCount','nativeCalls','interpreterCalls','fallbackReason','lastFallbackReason'})
  self.assertEqual(analyze('fn compile_function(x){x};compile_function(')['signatureHelp']['parameters'][0]['name'],'x')
 def test_guide_is_catalogued(self):
  self.assertIn('auto-control',Documentation().page('native-control-regions')['markdown'])
