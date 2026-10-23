import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest

from chaos.settings import Settings
from chaos.server import make_server
from chaos.client import Client
from chaos.store import Conflict

class HotConfiguration(unittest.TestCase):
    def test_two_instances_reload_and_stale_write_conflicts(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'settings.json';a=Settings(p);b=Settings(p)
            a.update({'providers':{'text':{'apiKey':'fixture-first-secret'}}},1)
            self.assertEqual(b.resolve()['apiKey'],'fixture-first-secret')
            b.update({'providers':{'text':{'apiKey':'fixture-second-secret'}}},2)
            self.assertEqual(a.resolve()['apiKey'],'fixture-second-secret')
            with self.assertRaises(Conflict):a.update({'runtime':{'maxSteps':30}},2)
    def test_agent_clear_resumes_inheritance_and_short_keys_not_exposed(self):
        s=Settings(':memory:');s.update({'providers':{'text':{'apiKey':'provider-secret'}},'agents':{'a':{'apiKey':'xyz'}}},1)
        self.assertEqual(s.public()['agents']['a']['keyHint'],'••••')
        s.update({'agents':{'a':{'clearKey':True}}},2)
        self.assertEqual(s.resolve('a')['apiKey'],'provider-secret')
    def test_duplicate_plugins_are_rejected_before_commit(self):
        s=Settings(':memory:')
        with self.assertRaises(ValueError):s.update({'plugins':[{'id':'code','enabled':True},{'id':'code','enabled':False}]},1)
        self.assertEqual(s.public()['version'],1)
    def test_role_endpoint_does_not_use_other_endpoint_catalog(self):
        s=Settings(':memory:');s.update({'providers':{'text':{'models':[{'id':'gpt-99-sol'}]}},'roles':{'executor':{'baseURL':'http://localhost:9999/v1','model':'custom'}}},1)
        self.assertEqual(s.resolve()['model'],'custom')

class HTTPControl(unittest.TestCase):
    def test_background_is_nonblocking_cancellable_and_uses_hot_config(self):
        entered=threading.Event();release=threading.Event();calls=[]
        class FakeClient:
            def __init__(self,config):
                self.config=config;self.chat=NS(completions=self);self.models=self
            def list(self):return []
            def create(self,**kwargs):
                calls.append(self.config);entered.set();release.wait(3)
                return NS(choices=[NS(message=NS(content='done',tool_calls=[]))])
            def close(self):pass
        with tempfile.TemporaryDirectory() as t:
            settings=Settings(':memory:',client_factory=FakeClient)
            server=make_server(port=0,db=Path(t)/'main.sqlite3',settings=settings,state_dir=Path(t)/'state')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=Client('http://127.0.0.1:'+str(server.server_port))
            try:
                public=client.settings();updated=client.configure({'version':public['version'],'providers':{'text':{'apiKey':'fixture-config-secret'}}})
                self.assertTrue(updated['providers']['text']['hasKey']);self.assertNotIn('fixture-config-secret',json.dumps(updated))
                start=time.monotonic();job=client.start_task('Any task prose, no required format',iterations=2,interval=1)
                self.assertLess(time.monotonic()-start,.5);self.assertTrue(entered.wait(1))
                start=time.monotonic();self.assertEqual(client.settings()['version'],updated['version']);self.assertLess(time.monotonic()-start,.5)
                client.cancel_task(job['id']);release.set()
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    current=client.task(job['id'])
                    if current['status']=='cancelled':break
                    time.sleep(.02)
                self.assertEqual(current['status'],'cancelled');self.assertIsNone(current['output'])
                events=client.task_events(task_id=job['id'])['items'];self.assertTrue(any(e['type']=='task.cancelled' for e in events))
                self.assertNotIn('fixture-config-secret',json.dumps(events))
                self.assertEqual(calls[0]['apiKey'],'fixture-config-secret')
                status=client.harness_status();self.assertTrue(any(p['id']=='native' and p['state']=='active' for p in status['plugins']))
            finally:
                release.set();server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main()

class LanguageToolBoundary(unittest.TestCase):
    def test_language_calls_real_code_with_explicit_capability(self):
        from chaos import Framework
        with tempfile.TemporaryDirectory()as t:
            s=Settings(':memory:');s.update({'runtime':{'workspace':t}},1)
            with Framework(settings=s)as app:
                p=app.create_project('code',source='tool("code.execute", {language: "python", code: "print(6*7)", timeout: 1})',capabilities=['code.execute'])
                result=app.start(p['id'],p['version'],None).result()
                self.assertEqual(result['stdout'].strip(),'42')
    def test_language_cannot_bypass_capabilities_via_codex(self):
        from chaos import Framework,RunFailed
        from unittest.mock import patch
        s=Settings(':memory:');s.update({'roles':{'executor':{'backend':'codex'}}},1)
        with Framework(settings=s)as app:
            p=app.create_project('isolated',source='agent a {provider: "model", tools: []}; a(input)',capabilities=[])
            with patch('chaos.harness.CodexHarness.run')as invoked:
                with self.assertRaises(RunFailed):app.start(p['id'],p['version'],None).result()
                invoked.assert_not_called()
