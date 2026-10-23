"""The persisted Computer metadata has one read-only host contract."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Framework, Client, Settings
from chaos.client import APIError, ValidationError
from chaos.server import make_server

ROOT=Path(__file__).resolve().parents[1]
FIELDS={'id','projectId','key','backend','generation','status','driverState','heldByRun','requiresReconcile','lastUrl','createdAt','updatedAt'}

class ComputerContinuitySurfaces(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(Path(self.temp.name)/'db',settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.app.create_project('p',source='null',capabilities=['browser.control'])
        self.app.create_project('q',source='null',capabilities=[])
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def cli(self,*args):
        result=subprocess.run([sys.executable,'-m','chaos','computer',*args,'--server',self.origin],cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
    def test_readonly_metadata_pagination_sdk_cli_and_project_scope(self):
        self.assertEqual(self.client.computer_sessions('p'),{'items':[],'after':None,'hasMore':False})
        claim=self.app._store.computers.claim_session('p','form','holder-one')
        row=self.client.computer_session('p','form');self.assertEqual(set(row),FIELDS)
        self.assertEqual(row['heldByRun'],'holder-one');self.assertEqual(row['status'],'leased')
        self.assertEqual(row,self.app.computer_session('p','form'));self.assertEqual(row,self.cli('session','p','form'))
        self.app._store.computers.release_session(claim,{'storage':{'cookies':[],'origins':[{'origin':'http://fixture.invalid','localStorage':[{'name':'secret','value':'DO-NOT-EXPOSE'}]}]},'url':'http://fixture.invalid'})
        second=self.app._store.computers.claim_session('p','second','holder-two');self.app._store.computers.release_session(second)
        page=self.client.computer_sessions('p',limit=1);self.assertEqual(page['after'],1);self.assertTrue(page['hasMore']);self.assertEqual(page['items'][0]['key'],'form')
        last=self.client.computer_sessions('p',after=page['after'],limit=1);self.assertEqual(last['items'][0]['key'],'second');self.assertFalse(last['hasMore']);self.assertIsNone(last['after'])
        self.assertEqual(self.cli('sessions','p','--limit','1'),page)
        self.assertEqual(self.client.computer_sessions('q'),{'items':[],'after':None,'hasMore':False})
        with self.assertRaises(APIError):self.client.computer_session('q','form')
        metadata=json.dumps(self.client.computer_sessions('p'));self.assertNotIn('DO-NOT-EXPOSE',metadata);self.assertNotIn('storage',metadata);self.assertNotIn(claim['token'],metadata)
        self.assertEqual(self.client.computer_session('p','form')['heldByRun'],None)
    def test_closed_routes_bounds_and_unknown_fields(self):
        for params in ({'after':-1},{'limit':0},{'limit':101},{'unexpected':'x'}):
            with self.subTest(params=params),self.assertRaises(ValidationError):self.client._request('GET','/projects/p/computer-sessions',query=params)
        for key in ('invalid key','1bad','a'*129):
            with self.subTest(key=key),self.assertRaises(ValidationError):self.client.computer_session('p',key)
        with self.assertRaises(ValidationError):self.client._request('GET','/projects/p/computer-sessions/form',query={'lease':'forged'})
        for path in ('/projects/p/computer-sessions','/projects/p/computer-sessions/form','/projects/p/computer-sessions/form/close'):
            with self.subTest(path=path),self.assertRaises(APIError):self.client._request('POST',path,{'key':'forged'})
        self.assertEqual(self.client.computer_sessions('p')['items'],[])
    def test_openapi_and_documented_example_are_same_sources(self):
        from chaos.documentation import Documentation
        from chaos.language import Parser
        spec=self.client._request('GET','/openapi.json');paths=spec['paths'];schemas=spec['components']['schemas']
        for path in ('/projects/{projectId}/computer-sessions','/projects/{projectId}/computer-sessions/{key}'):
            self.assertEqual(set(paths[path]),{'get'})
        self.assertEqual(set(schemas['ComputerSession']['required']),FIELDS)
        self.assertFalse(schemas['ComputerSession']['additionalProperties'])
        source=(ROOT/'examples/continuous-computer.chaos').read_text();Parser(source).program()
        self.assertEqual(Documentation().attachment('continuous-computer.chaos'),source.encode())
        self.assertEqual(Framework.evaluate('type_of(computer_connect("form"))'),'Task')

if __name__=='__main__':unittest.main()
