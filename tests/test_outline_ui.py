"""Real parser navigation and stale targets in an isolated browser workspace."""
import json,os,shutil,subprocess,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from chaos import Framework,Settings
from chaos.server import make_server,static_path
ROOT=Path(__file__).resolve().parents[1]
SOURCE='let note="😀";\nfn first(x){x+1}\nfn second(x){fn inner(y){y+2};inner(x)}\nexport {first,second}'
class FakeGateway:
 def __init__(self,requests):self.requests=requests;self.chat=NS(completions=self);self.step=0
 def create(self,**kwargs):
  self.requests.append(kwargs);self.step+=1
  if self.step==1:
   names={t['function']['description'].split(':',1)[0]:t['function']['name'] for t in kwargs['tools']}
   message=NS(content=None,tool_calls=[NS(id='write',function=NS(name=names['draft.write'],arguments=json.dumps({'path':'lib/worker.chaos','source':SOURCE.replace('y+2','y+3')})))])
  else:message=NS(content='已修改选中的函数，请检查差异。',tool_calls=[])
  return NS(choices=[NS(message=message)])
 def close(self):pass
class OutlineUI(unittest.TestCase):
 def check(self,scenario):
  with tempfile.TemporaryDirectory(prefix='chaos-outline-ui-') as directory:
   folder=Path(directory);env={k:v for k,v in os.environ.items() if not k.startswith('CHAOS_MODEL_')};env['CHAOS_CONFIG']=str(folder/'settings.json');requests=[]
   with patch.dict(os.environ,env,clear=True):
    settings=Settings(':memory:',client_factory=lambda config:FakeGateway(requests));settings.update({'providers':{'text':{'baseURL':'https://model.invalid/v1','apiKey':'isolated_fixture_secret'}},'runtime':{'timeout':10}},1)
    with Framework(settings=settings) as app:
     for name in ['draft','other']:app.create_project(name,source='import "lib/worker.chaos" as W;W.second(40)',files={'lib/worker.chaos':SOURCE})
     def asset(filename):
      proposed=ROOT/'web'/filename
      return proposed if proposed.is_file() else static_path(filename)
     with patch('chaos.server.static_path',asset):
      server=make_server('127.0.0.1',0,framework=app,state_dir=folder/'state',update_home=folder/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
      try:
       result=subprocess.run([shutil.which('node'),str(ROOT/'tests/outline_controls.cjs'),'http://127.0.0.1:'+str(server.server_port),scenario],cwd=ROOT,env=env,capture_output=True,text=True,timeout=75)
       self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue(json.loads(result.stdout)['passed'])
       if scenario=='assistant':self.assertTrue(requests)
       else:self.assertEqual(requests,[])
      finally:server.shutdown();thread.join(3);server.server_close();self.assertFalse(thread.is_alive())
 def test_navigation_selection_assistant_apply_desktop_mobile(self):self.check('assistant')
 def test_keyboard_filter_nested_name_and_utf16_boundaries(self):self.check('keyboard')
 def test_outline_rejects_stale_source_file_project_entry_and_dialect(self):self.check('stale')
 def test_diagnostic_click_uses_original_snapshot_and_exact_range(self):self.check('diagnostic')
 def test_reference_click_rejects_stale_source_and_remote_version(self):self.check('reference')
 def test_late_outline_response_cannot_move_newer_selection(self):self.check('late')
