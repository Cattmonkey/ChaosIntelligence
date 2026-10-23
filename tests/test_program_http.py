"""Native Program definitions and verified adoption share source, SDK and HTTP."""
from pathlib import Path
import copy
import tempfile
import threading
import unittest
from chaos import Client, Framework, Settings
from chaos.client import APIError, ValidationError, ConflictError
from chaos.server import make_server


def definition(source='input+2'):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class ProgramHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port))
        self.project=self.client.create_project('self_code',source='input',capabilities=['program.adopt'])
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_definition_validation_snapshot_source_and_schema_are_pure(self):
        before=self.client.events('self_code')['lastEventId'];value=definition()
        checked=self.client.validate_program(value,name='候选程序')
        self.assertEqual(checked,self.app.validate_program(value,name='候选程序'))
        self.assertEqual(checked['definition'],value)
        self.assertTrue(checked['summary']['valid'])
        self.assertEqual(Framework.evaluate('import "saved.chaos" as S;program_info(S.候选程序)',files={'saved.chaos':checked['source']}),value)
        self.assertEqual(self.client.program_schema(),self.app.program_schema())
        snap=self.client.program_snapshot('self_code')
        self.assertEqual(snap,self.app.program_snapshot('self_code'))
        self.assertEqual(snap['version'],1);self.assertEqual(snap['definition'],definition('input'))
        self.assertEqual(self.client.events('self_code')['lastEventId'],before)

    def test_adoption_runs_actual_cases_and_keeps_grants_and_existing_ir_api(self):
        before=self.client.program_snapshot('self_code')
        candidate=definition();cases=[{'input':40,'expect':42},{'input':0,'expect':2}]
        adopted=self.client.adopt_program('self_code',1,candidate,expected_hash=before['definitionHash'],cases=cases,reason='verified arithmetic')
        project=adopted['project'];self.assertEqual(project['version'],2)
        self.assertEqual(project['capabilities'],['program.adopt']);self.assertEqual(project['name'],self.project['name'])
        self.assertEqual(project['source'],'input+2');self.assertTrue(adopted['receipt'])
        self.assertEqual(self.client.start('self_code',2,40,mode='source').result(),42)
        self.assertEqual(self.client.program('self_code')['program']['schema'],'chaos.program/1')
        events=self.client.events('self_code')['items'];kinds=[e['type']for e in events]
        self.assertIn('program.adopted',kinds);self.assertIn('graph.saved',kinds)
        audit=next(e['payload']for e in events if e['type']=='program.adopted')
        self.assertNotIn('files',audit);self.assertNotIn('cases',audit)

    def test_failed_cases_and_forged_report_do_not_change_project(self):
        snap=self.client.program_snapshot('self_code')
        with self.assertRaises(ValidationError):self.client.adopt_program('self_code',1,definition(),expected_hash=snap['definitionHash'],cases=[{'input':40,'expect':999}])
        body={'version':1,'definition':definition(),'expectedHash':snap['definitionHash'],'cases':[{'input':40,'expect':42}],'passed':True}
        with self.assertRaises(ValidationError):self.client._request('POST','/projects/self_code/program-adopt',body)
        self.assertEqual(self.client.get_project('self_code')['version'],1)
        self.assertFalse(any(e['type']=='program.adopted'for e in self.client.events('self_code')['items']))

    def test_stale_source_hash_and_version_are_conflicts(self):
        snap=self.client.program_snapshot('self_code');cases=[{'input':40,'expect':42}]
        with self.assertRaises(ConflictError):self.client.adopt_program('self_code',1,definition(),expected_hash='0'*64,cases=cases)
        self.client.edit_program('self_code',1,source='input+1')
        with self.assertRaises(ConflictError):self.client.adopt_program('self_code',1,definition(),expected_hash=snap['definitionHash'],cases=cases)
        self.assertEqual(self.client.get_project('self_code')['source'],'input+1')
        self.assertEqual(self.client.get_project('self_code')['version'],2)

    def test_closed_definition_and_transport_cannot_install_authority(self):
        value=definition();bad=[]
        for field in ('capabilities','origin','apiKey'):
            bad.append({**value,field:[] if field=='capabilities'else'forged'})
        missing=copy.deepcopy(value);missing['files']={};bad.append(missing)
        for item in bad:
            with self.subTest(fields=list(item)),self.assertRaises(ValidationError):self.client.validate_program(item)
        with self.assertRaises(ValidationError):self.client._request('POST','/programs/validate',{'definition':value,'projectId':'elsewhere'})
        with self.assertRaises(ValidationError):self.client.validate_program(value,name='program_from')
        denied=self.client.create_project('denied',source='input',capabilities=[]);snap=self.client.program_snapshot('denied')
        with self.assertRaises(APIError):self.client.adopt_program('denied',1,value,expected_hash=snap['definitionHash'],cases=[{'input':40,'expect':42}])
        self.assertEqual(self.client.get_project('denied'),denied)

    def test_pure_test_module_cannot_call_effects(self):
        value=definition('input');value['files']['test.chaos']='tool("memory.put",{key:"not_written",value:1})';value['languageVersions']['test.chaos']=2
        snap=self.client.program_snapshot('self_code')
        with self.assertRaises(ValidationError):self.client.adopt_program('self_code',1,value,expected_hash=snap['definitionHash'],cases=[{'entry':'test.chaos','input':None,'expect':1}])
        self.assertEqual(self.client.get_project('self_code')['version'],1)
        self.assertFalse(any(e['type']=='tool.started'for e in self.client.events('self_code')['items']))

    def test_large_valid_bundle_reports_export_limit_without_rejecting_definition(self):
        value=definition('//'+('a'*60000)+'\ninput')
        value['files']['other.chaos']='//'+('b'*60000)+'\n42';value['languageVersions']['other.chaos']=2
        checked=self.client.validate_program(value)
        self.assertEqual(checked['definition'],value);self.assertTrue(checked['summary']['valid'])
        self.assertIsNone(checked['source']);self.assertTrue(checked['sourceError'])

    def test_openapi_new_program_contract_preserves_existing_endpoints(self):
        spec=self.client._request('GET','/openapi.json');schemas=spec['components']['schemas'];paths=spec['paths']
        self.assertEqual(schemas['ProgramDefinition'],self.client.program_schema())
        self.assertFalse(schemas['ProgramAdoption']['additionalProperties'])
        self.assertIn('null',schemas['ValidatedProgram']['properties']['source']['type'])
        response=paths['/programs/schema']['get']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(response,{'$ref':'#/components/schemas/ProgramDefinitionSchema'})
        self.assertEqual(paths['/projects/{projectId}/program']['get']['operationId'],'getProgram')
        self.assertEqual(paths['/teams/validate']['post']['operationId'],'validateTeam')
        self.assertEqual(paths['/projects/{projectId}/program-adopt']['post']['operationId'],'adoptProgram')
