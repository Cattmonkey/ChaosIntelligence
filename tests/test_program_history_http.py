"""One immutable Program history through SDK, HTTP and CLI."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Client,Framework,Settings
from chaos.client import APIError,ConflictError,ValidationError
from chaos.server import make_server


class ProgramHistoryHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
        self.client.create_project('history',source='input*input',capabilities=['program.adopt','text.stats'])
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def cli(self,*args):
        result=subprocess.run([sys.executable,'-m','chaos','program',*args,'--server',self.origin],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)

    def test_read_only_pagination_exact_revision_and_cli_match_sdk(self):
        self.client.edit_program('history',1,source='input*2')
        self.client.edit_program('history',2,source='input+2')
        before=self.client.events('history')['lastEventId']
        first=self.client.program_history('history',limit=1)
        self.assertEqual(first,self.app.program_history('history',limit=1))
        self.assertEqual(first['firstAvailableVersion'],1);self.assertEqual(first['currentVersion'],3)
        self.assertEqual(first['nextCursor'],1);self.assertEqual([row['version']for row in first['items']],[1])
        rest=self.client.program_history('history',after=first['nextCursor'])
        self.assertEqual([row['version']for row in rest['items']],[2,3]);self.assertIsNone(rest['nextCursor'])
        old=self.client.program_revision('history',1)
        self.assertEqual(old,self.app.program_revision('history',1))
        self.assertEqual(old['definition']['files']['main.chaos'],'input*input')
        self.assertEqual(old['origin']['definitionHash'],first['items'][0]['definitionHash'])
        self.assertEqual(old['metadata'],first['items'][0])
        self.assertEqual(self.cli('history','history','--limit','1'),first)
        self.assertEqual(self.cli('revision','history','1'),old)
        self.assertEqual(self.cli('snapshot','history'),self.client.program_snapshot('history'))
        self.assertEqual(self.client.events('history')['lastEventId'],before)
        self.assertNotIn('definition',first['items'][0]);self.assertNotIn('capabilities',old)

    def test_historical_definition_restores_as_new_tested_version_without_old_authority(self):
        original=self.client.program_revision('history',1)
        current=self.client.edit_program('history',1,source='input*2')
        current['name']='Keep current name';current['capabilities']=['program.adopt']
        current=self.client.save_project(current,from_view='source')
        base=self.client.program_snapshot('history')
        cases=[{'input':3,'expect':9},{'input':2,'expect':4}]
        with self.assertRaises(ValidationError):
            self.client.adopt_program('history',3,original['definition'],expected_hash=base['definitionHash'],cases=[{'input':3,'expect':0}])
        self.assertEqual(len(self.client.program_history('history')['items']),3)
        result=self.client.adopt_program('history',3,original['definition'],expected_hash=base['definitionHash'],cases=cases,reason='restore tested historical implementation')
        self.assertEqual(result['project']['version'],4)
        self.assertEqual(result['project']['name'],'Keep current name')
        self.assertEqual(result['project']['capabilities'],['program.adopt'])
        self.assertEqual(self.client.program_revision('history',4)['definition'],original['definition'])
        self.assertEqual(self.client.program_revision('history',1),original)
        self.assertEqual(self.client.start('history',4,3,mode='source').result(),9)
        with self.assertRaises(ConflictError):
            self.client.adopt_program('history',3,original['definition'],expected_hash=base['definitionHash'],cases=cases)
        self.assertEqual(self.client.program_history('history')['currentVersion'],4)

    def test_read_routes_validate_parameters_methods_and_project_scope(self):
        for path in ('/projects/history/program-history?limit=0','/projects/history/program-history?limit=101','/projects/history/program-history?after=-1','/projects/history/program-history?unknown=x','/projects/history/program-revisions/0','/projects/history/program-revisions/1?projectId=elsewhere','/projects/history/program-revisions/true'):
            with self.subTest(path=path),self.assertRaises(ValidationError):self.client._request('GET',path)
        for path in ('/projects/missing/program-history','/projects/history/program-revisions/999'):
            with self.subTest(path=path),self.assertRaises(APIError):self.client._request('GET',path)
        with self.assertRaises(APIError):self.client._request('POST','/projects/history/program-history',{})
        self.assertEqual(self.client.program_history('history')['currentVersion'],1)

    def test_openapi_is_additive_and_describes_same_closed_history(self):
        spec=self.client._request('GET','/openapi.json');schemas=spec['components']['schemas'];paths=spec['paths']
        self.assertEqual(paths['/projects/{projectId}/program-history']['get']['operationId'],'programHistory')
        self.assertEqual(paths['/projects/{projectId}/program-revisions/{version}']['get']['operationId'],'programRevision')
        self.assertEqual(paths['/projects/{projectId}/program-adopt']['post']['operationId'],'adoptProgram')
        self.assertEqual(paths['/projects/{projectId}/program']['get']['operationId'],'getProgram')
        self.assertEqual(set(schemas['ProgramRevision']['required']),set(self.client.program_revision('history',1)))
        self.assertFalse(schemas['ProgramHistory']['additionalProperties'])
