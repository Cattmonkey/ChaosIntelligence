"""Real editor/assistant interaction against isolated projects and a fake gateway."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from chaos import Framework, Settings
from chaos.server import make_server, static_path

ROOT = Path(__file__).resolve().parents[1]
PROPOSED = 'let note="😀"; let n=43; export{n}'


class FakeGateway:
    def __init__(self, requests):
        self.requests=requests
        self.chat=NS(completions=self)
        self.step=0
    def create(self, **kwargs):
        self.requests.append(kwargs)
        self.step+=1
        if self.step==1:
            tools={t['function']['description'].split(':',1)[0]:t['function']['name'] for t in kwargs['tools']}
            call=NS(id='write',function=NS(name=tools['draft.write'],arguments=json.dumps({'path':'lib/worker.chaos','source':PROPOSED})))
            message=NS(content=None,tool_calls=[call])
        else:
            message=NS(content='已检查并修改当前模块；请查看差异。',tool_calls=[])
        return NS(choices=[NS(message=message)])
    def close(self):
        pass


@unittest.skipUnless(shutil.which('node'),'Node unavailable')
class IDEEditorContextUI(unittest.TestCase):
    def browser_check(self, scenario):
        with tempfile.TemporaryDirectory(prefix='chaos-ide-context-ui-') as directory:
            folder=Path(directory)
            env={k:v for k,v in os.environ.items() if not k.startswith('CHAOS_MODEL_')}
            env['CHAOS_CONFIG']=str(folder/'settings.json')
            requests=[]
            with patch.dict(os.environ,env,clear=True):
                settings=Settings(':memory:',client_factory=lambda config:FakeGateway(requests))
                settings.update({'providers':{'text':{'baseURL':'https://model.invalid/v1','apiKey':'isolated_fixture_secret'}},'runtime':{'timeout':10}},1)
                with Framework(settings=settings) as app:
                    for key in ('draft','other'):
                        app.create_project(key,source='import "lib/worker.chaos" as W;W.n',files={'lib/worker.chaos':'let n=40;export{n}'})
                    def asset(filename):
                        proposed=ROOT/'web'/filename
                        return proposed if proposed.is_file() else static_path(filename)
                    with patch('chaos.server.static_path',asset):
                        server=make_server('127.0.0.1',0,framework=app,state_dir=folder/'state',update_home=folder/'updates')
                        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                        try:
                            result=subprocess.run([shutil.which('node'),str(ROOT/'tests/ide_editor_context_controls.cjs'),'http://127.0.0.1:'+str(server.server_port),scenario],cwd=ROOT,env=env,capture_output=True,text=True,timeout=80)
                            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                            self.assertTrue(json.loads(result.stdout)['passed'])
                            if scenario=='blocked':self.assertEqual(requests,[])
                            else:self.assertTrue(requests)
                        finally:
                            server.shutdown();thread.join(3);server.server_close()
                            self.assertFalse(thread.is_alive())

    def test_current_draft_context_review_apply_desktop_mobile(self):
        self.browser_check('draft')
    def test_late_submission_preserves_new_prompt_and_other_project(self):
        self.browser_check('submit-race')
    def test_apply_preserves_edits_created_while_request_is_in_flight(self):
        self.browser_check('apply-race')
    def test_stale_proposal_rejects_changed_source_without_commit(self):
        self.browser_check('stale')
    def test_late_apply_cannot_change_another_project_chat_or_source(self):
        self.browser_check('apply-project')
    def test_late_history_cannot_replace_a_new_session_in_same_project(self):
        self.browser_check('history-race')
    def test_pending_permissions_and_conflict_block_without_saving(self):
        self.browser_check('blocked')


if __name__=='__main__':
    unittest.main()
