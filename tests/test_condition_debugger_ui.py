"""Real Condition recovery controls in an isolated native-language workspace."""
import json,os,shutil,subprocess,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,Settings
from chaos.server import make_server,static_path
ROOT=Path(__file__).resolve().parents[1]
RESTORE='''var seen=[];
let value=restart_case(fn(){with exit_stack() as stack {stack.callback(fn(){seen=append(seen,"cleanup")});error_condition(condition("missing","需要提供一个值",{fallback:42}))}},[{name:"use",report:"提供值",function:fn(value:Int){seen=append(seen,"restart");value}}]);
[value,seen]'''
REPEATED='''map(fn(i){restart_case(fn(){error_condition(condition("missing","需要提供一个值",{fallback:i}))},[{name:"use",report:"提供值",function:fn(value:Int){value}}])},[40,41])'''
NONE='error_condition(condition("missing","没有声明恢复出口",{fallback:42}))'
PRECISION='restart_case(fn(){error_condition(condition("missing","需要精确整数"))},[{name:"use",report:"提供值",function:fn(value:Int,*,scale:Number){if value==9007199254740993 && type_of(scale)=="Number" && scale==2.0 {42}else{-1}}}])'
class ConditionDebuggerUI(unittest.TestCase):
 def check(self,scenario):
  self.assertTrue(shutil.which('node'),'Node.js is required for new UI tests')
  with tempfile.TemporaryDirectory(prefix='chaos-condition-debugger-ui-') as directory:
   folder=Path(directory);env={k:v for k,v in os.environ.items() if not k.startswith('CHAOS_MODEL_')};env['CHAOS_CONFIG']=str(folder/'settings.json')
   with patch.dict(os.environ,env,clear=True):
    settings=Settings(':memory:',client_factory=lambda config:(_ for _ in ()).throw(AssertionError('UI recovery must not call a model')))
    with Framework(settings=settings) as app:
     app.create_project('condition',source=REPEATED if scenario in ('stale','late','poll') else NONE if scenario=='none' else PRECISION if scenario=='precision' else RESTORE)
     def asset(filename):
      proposed=ROOT/'web'/filename
      return proposed if proposed.is_file() else static_path(filename)
     with patch('chaos.server.static_path',asset):
      server=make_server('127.0.0.1',0,framework=app,state_dir=folder/'state',update_home=folder/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
      try:
       result=subprocess.run([shutil.which('node'),str(ROOT/'tests/condition_debugger_controls.cjs'),'http://127.0.0.1:'+str(server.server_port),scenario],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
       self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue(json.loads(result.stdout)['passed'])
      finally:server.shutdown();thread.join(3);server.server_close();self.assertFalse(thread.is_alive())
 def test_desktop_mobile_exact_restore_cleanup_and_draft_preservation(self):self.check('restore')
 def test_delayed_duplicate_attach_cannot_reset_finished_run(self):self.check('attach')
 def test_invalid_json_and_argument_error_preserve_pause_and_inputs(self):self.check('invalid')
 def test_previous_pause_callback_cannot_invoke_new_restart(self):self.check('stale')
 def test_delayed_same_run_response_cannot_overwrite_later_pause(self):self.check('late')
 def test_delayed_poll_cannot_overwrite_later_pause(self):self.check('poll')
 def test_cancelled_pause_cannot_execute_selected_restart(self):self.check('cancel')
 def test_no_restart_decline_uses_original_condition_error(self):self.check('none')
 def test_default_false_option_is_omitted_from_legacy_debug_request(self):self.check('default')
 def test_exact_large_int_and_number_tokens_reach_real_restart(self):self.check('precision')
