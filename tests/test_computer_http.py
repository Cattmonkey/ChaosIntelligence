"""Computer evidence APIs remain read-only facades over the language runtime."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Framework,Client,Settings
from chaos.client import APIError,ValidationError
from chaos.server import make_server

class ComputerHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
        self.client.create_project('p',source='null',capabilities=['browser.control'])
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def cli(self,*args):
        result=subprocess.run([sys.executable,'-m','chaos','computer',*args,'--server',self.origin],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
    def test_shared_structural_validation_and_cli(self):
        self.assertEqual(self.client.computer_schema(),self.app.computer_schema())
        spec={'action':'scroll','deltaX':0,'deltaY':120}
        self.assertEqual(self.client.validate_computer_action(spec),spec)
        self.assertEqual(self.cli('validate','--spec-json',json.dumps(spec)),spec)
        self.assertEqual(self.cli('schema'),self.app.computer_schema())
        self.assertEqual(self.cli('history','p'),{'items':[],'nextCursor':None})
        self.assertEqual(self.client.computer_history('p'),self.app.computer_history('p'))
        for forged in ({'action':'click','ref':'e1','snapshotId':1},{'action':'run','code':'print(1)'},{'action':'scroll','deltaX':True,'deltaY':2}):
            with self.subTest(spec=forged),self.assertRaises(ValidationError):self.client.validate_computer_action(forged)
        with self.assertRaises(ValidationError):self.client._request('POST','/computers/validate',{'spec':spec,'recordId':'forged'})
        self.assertEqual(self.client.computer_history('p')['items'],[])
    def test_closed_read_transport_and_project_scope(self):
        for path in ('/projects/p/computer-records/no','/projects/p/computer-records/no/image'):
            with self.subTest(path=path),self.assertRaises(APIError):self.client._request('GET',path)
        for path,query in [('/projects/p/computer-records',{'file':'/tmp/x'}),('/projects/p/computer-records/no',{'input':1}),('/projects/p/computer-records/no/image',{'path':'/tmp/x'})]:
            with self.subTest(path=path),self.assertRaises(ValidationError):self.client._request('GET',path,query=query)
        with self.assertRaises(APIError):self.client._request('POST','/projects/p/computer-records',{'kind':'observation'})
        with self.assertRaises(APIError):self.client.computer_history('absent')
    def test_openapi_and_assets(self):
        spec=self.client._request('GET','/openapi.json');paths=spec['paths'];schemas=spec['components']['schemas']
        self.assertFalse(schemas['ComputerActionValidation']['additionalProperties'])
        self.assertEqual(len(schemas['ComputerActionSpec']['oneOf']),5)
        for path in ('/computers/schema','/computers/validate','/projects/{projectId}/computer-records','/projects/{projectId}/computer-records/{recordId}/image','/projects/{projectId}/program-adopt','/projects/{projectId}/agent-sessions'):
            self.assertIn(path,paths)
        import urllib.request
        for path in ('/computers.js','/computers.css'):
            with urllib.request.urlopen(self.origin+path) as response:self.assertEqual(response.status,200)
