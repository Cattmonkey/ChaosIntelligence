"""Shipped visual Goal uses real browser effects and native SDK image messages."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import base64
import copy
import time
import hashlib
import importlib.util
import json
import tempfile
import threading
import unittest
from chaos import Framework
from tests.test_session_integration import NativeFixture

class ObservedFixture:
    def __init__(self,bad=False,dot=False):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data=b'''<title>Observed goal</title><button data-testid="advance" onclick="document.querySelector('output').textContent='count:'+(++window.n)">Advance</button><output>count:0</output><script>window.n=0</script>'''
                if dot:
                    data=('<title>Visual self-programming</title>'+''.join('<button onclick="document.querySelector(\'output\').textContent=\'count:'+str(n)+'\'">Advance '+str(n)+'</button>'for n in (1,2,3))+'<output>count:0</output>').encode()
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port);self.bad=bad
        self.native=NativeFixture(self.program if dot else self.respond)
        self.temp=tempfile.TemporaryDirectory()
        self.native.settings.update({'runtime':{'workspace':self.temp.name}},self.native.settings.public()['version'])
    def respond(self,body):
        content=[m for m in body['messages'] if m['role']=='user'][-1]['content']
        if type(content)is not list:raise AssertionError('Actual model user message must be multimodal')
        parts=[p for p in content if p['type']=='image_url']
        if len(parts)!=1 or not parts[0]['image_url']['url'].startswith('data:image/'):raise AssertionError('Missing native captured image')
        data=json.loads(next(p['text']for p in content if p['type']=='text'))
        target=next(t for t in data['observation']['targets']if t['text']=='Advance')
        action={'action':'click','ref':target['ref']}
        if self.bad:action['code']='not an action'
        return {'role':'assistant','content':json.dumps(action)}
    def program(self,body):
        user=[m for m in body['messages']if m['role']=='user'][-1]
        assert type(user['content'])is list
        assert any(part['type']=='image_url'for part in user['content'])
        value=json.loads(next(p['text']for p in user['content']if p['type']=='text'))
        proposed=copy.deepcopy(value['input']['definition'])
        requested=value['input']['buttonText']
        assert any(t['text']==requested for t in value['observation']['targets'])
        proposed['files']['behavior.chaos']=proposed['files']['behavior.chaos'].replace('Advance 1',requested).replace('Advance 2',requested)
        return {'role':'assistant','content':json.dumps(proposed)}
    def close(self):
        self.native.close();self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ObservedComputer(unittest.TestCase):
    def source(self):return (Path(__file__).resolve().parents[1]/'examples/observed-computer/main.chaos').read_text()
    def fixture(self,bad=False):
        f=ObservedFixture(bad);self.addCleanup(f.close);return f
    def execute(self,f,app):
        app.create_project('visual',source=self.source(),capabilities=['browser.control','session.manage'])
        return app.start('visual',1,{'url':f.url,'objective':'Advance the counter twice','expected':'count:2'},mode='source')
    def test_two_actual_actions_visual_native_history_and_new_run_resources(self):
        f=self.fixture()
        with Framework(settings=f.native.settings)as app:
            handle=self.execute(f,app);result=handle.result(timeout=30)
            self.assertTrue(result['done']);self.assertEqual(result['iterations'],2);self.assertIn('count:2',result['state']['text'])
            self.assertEqual(result['state']['receipt']['status'],'completed')
            self.assertEqual(len(f.native.requests),2)
            messages=f.native.requests[-1]['messages'];self.assertEqual([m['role']for m in messages],['system','user','assistant','user'])
            self.assertTrue(all(type(m['content'])is list for m in messages if m['role']=='user'))
            rows=app.computer_history('visual',run_id=handle.id,limit=50)['items'];observations=[x for x in rows if x['kind']=='observation']
            self.assertGreaterEqual(len(observations),4)
            actions=[x for x in rows if x['kind']=='action'];self.assertEqual(len(actions),2);self.assertTrue(all(x['status']=='completed'for x in actions))
            self.assertTrue(all(x['runId']==handle.id and x['sourceVersion']==1 for x in rows))
            image=app.computer_image('visual',observations[-1]['id'],variant='model')
            self.assertEqual(hashlib.sha256(base64.b64decode(image['data'])).hexdigest(),image['sha256'])
            self.assertLessEqual(len(image['data']),16000);self.assertNotIn('data',observations[-1]['data']['image']['model'])
            another=app.start('visual',1,{'url':f.url,'objective':'Advance once','expected':'count:1'},mode='source');self.assertTrue(another.result(timeout=30)['done'])
            nextrows=app.computer_history('visual',run_id=another.id)['items'];self.assertTrue(set(x['computerId']for x in rows).isdisjoint(x['computerId']for x in nextrows))
            self.assertEqual(app.agent_session('visual','visual_planner')['turn'],3)
    def test_unvalidated_model_extra_fields_never_become_browser_actions(self):
        f=self.fixture(True)
        with Framework(settings=f.native.settings)as app:
            handle=self.execute(f,app)
            with self.assertRaises(Exception):handle.result(timeout=30)
            rows=app.computer_history('visual',run_id=handle.id)['items']
            self.assertFalse(any(row['kind']=='action'for row in rows))
            self.assertTrue(any(row['kind']=='observation'and 'count:0'in row['data']['text']for row in rows))
            self.assertEqual(app.agent_session('visual','visual_planner')['turn'],1)

    def test_visual_process_adopts_two_programs_and_acts_with_each_real_snapshot(self):
        f=ObservedFixture(dot=True);self.addCleanup(f.close)
        files={p.name:p.read_text()for p in (Path(__file__).resolve().parents[1]/'examples/observed-dot').glob('*.chaos')}
        with Framework(settings=f.native.settings)as app:
            app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['browser.control','session.manage','program.adopt','process.manage'])
            app.start('dot',1,{'url':f.url},mode='source').result(timeout=15)
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                process=app.get_process('dot','observed_dot')
                if process['status']in ('completed','needs_reconciliation'):break
                time.sleep(.03)
            self.assertEqual(process['status'],'completed',process)
            self.assertEqual(process['turn'],3);self.assertEqual(app.get_project('dot')['version'],3)
            self.assertEqual(process['output']['programVersion'],3);self.assertIn('count:3',process['output']['observation']['text'])
            records=app.computer_history('dot',limit=100)['items']
            actions=[row for row in records if row['kind']=='action'];self.assertEqual(len(actions),3)
            self.assertEqual([row['sourceVersion']for row in actions],[1,2,3]);self.assertTrue(all(row['status']=='completed'for row in actions))
            events=app.events('dot',limit=300)['items'];adoptions=[row for row in events if row['type']=='program.adopted'];self.assertEqual(len(adoptions),2)
            for event in adoptions:
                output=app.get_run(event['run_id'])['output']['output']
                self.assertEqual(output['beforePolicy'],output['afterAdoptPolicy'])
                self.assertEqual(output['observation']['receipt']['status'],'completed')
            self.assertEqual(len(f.native.requests),2)
            self.assertEqual([m['role']for m in f.native.requests[1]['messages']],['system','user','assistant','user'])
            self.assertTrue(all(type(m['content'])is list for m in f.native.requests[1]['messages']if m['role']=='user'))
