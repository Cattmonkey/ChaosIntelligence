import shutil,subprocess,unittest,tempfile,os
from pathlib import Path
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator,Parser
ROOT=Path(__file__).resolve().parent.parent
class ComputerEditorTests(unittest.TestCase):
 def kind(self,source):
  result=analyze(source);self.assertEqual(result['diagnostics'],[],result['diagnostics']);return next(v['type']for v in result['symbols']if v['name']=='answer')
 def test_cold_resources_and_callable(self):
  self.assertEqual(self.kind('let answer=computer_open()'),'Task')
  self.assertEqual(self.kind('let answer=await computer_open()'),'Computer')
  self.assertEqual(self.kind('let c=await computer_open();let answer=await computer_observe(c)'),'Observation')
  self.assertEqual(self.kind('let c=await computer_open();let answer=await c(input)'),'Observation')
  self.assertEqual(analyze('let c:Function=await computer_open();null')['diagnostics'],[])
  self.assertTrue(any(d['code']=='argument_error'for d in analyze('let c=await computer_open();c()')['diagnostics']))
 def test_pure_values_and_aliases(self):
  for expression,typ in [('computer_action(input,input)','ComputerAction'),('observation_input(input)','ObservedInput'),('computer_info(input)','Record'),('observation_info(input)','Record'),('action_info(input)','Record')]:self.assertEqual(self.kind('let answer='+expression),typ)
  self.assertEqual(self.kind('let alias=computer_open;let answer=await alias()'),'Computer')
  self.assertEqual(self.kind('fn computer_open()->String{"local"};let answer=computer_open()'),'String')
  with patch.object(Evaluator,'run',side_effect=AssertionError('analysis is pure')):self.assertEqual(self.kind('let answer=await computer_open()'),'Computer')
 def test_observation_record_completions_and_nominal_boundaries(self):
  self.assertEqual(self.kind('let answer:Int=observation_info(input).snapshotId;answer'),'Int')
  values={v['label']for v in analyze('let o=observation_info(input);o.')['completions']};self.assertTrue({'targets','viewport','image','receipt','sourceVersion'}<=values)
  self.assertTrue(analyze('let o:Observation={};null')['diagnostics'])
  self.assertTrue(analyze('let f:Function=observation_input(input);null')['diagnostics'])
 @unittest.skipUnless(shutil.which('node'),'Node unavailable')
 def test_controls_and_generated_source(self):
  with tempfile.TemporaryDirectory()as folder:
   target=str(Path(folder)/'source.chaos');subprocess.run([shutil.which('node'),str(ROOT/'tests/computer_controls.cjs'),str(ROOT)],env={**os.environ,'COMPUTER_SNIPPET':target},check=True,capture_output=True,text=True,timeout=20);source=Path(target).read_text();Parser(source).program();self.assertEqual(analyze(source)['diagnostics'],[])
