"""One symbol engine across pure HTTP/SDK and the programming-agent proposal."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.client import ConflictError,ValidationError
from chaos.language import Evaluator
from chaos.openapi import specification
from chaos.program import parse_program
from chaos.server import make_server

SOURCE='// 🙂 keep comment answer\nimport "lib.chaos" as L;let alias=L.answer;let text="answer";alias(40)'
LIBRARY='// 🧠 keep library\r\nfn answer(value:Int)->Int{value+2};export {answer}'
REVERSE='import "lib.chaos" as Other;fn consume(){Other.answer(1)};export {consume}'
FILES={'main.chaos':SOURCE,'lib.chaos':LIBRARY,'reverse.chaos':REVERSE}
POSITION=len(SOURCE[:SOURCE.index('L.answer')+2].encode('utf-16-le'))//2

class SymbolSurfaces(unittest.TestCase):
    def test_http_sdk_shared_candidate_pure_then_existing_save_cas_ir(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            p=app.create_project('symbols',source=SOURCE,files=FILES,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));opts={'files':FILES,'position':POSITION,'language_versions':{'main.chaos':2,'lib.chaos':1,'reverse.chaos':1}}
                with patch.object(Evaluator,'run',side_effect=AssertionError('refactoring must not run')):
                    refs=client.references(SOURCE,**opts);self.assertEqual(refs,Framework.references(SOURCE,**opts));self.assertTrue(refs['complete'])
                    self.assertEqual({x['file']for x in refs['references']},set(FILES));self.assertEqual(len(refs['references']),4)
                    candidate=client.rename(SOURCE,new_name='respond',**opts);self.assertEqual(candidate,Framework.rename(SOURCE,new_name='respond',**opts))
                self.assertEqual(app.get_project('symbols'),p);self.assertEqual(app.runs('symbols')['items'],[])
                self.assertIn('let text="answer"',candidate['files']['main.chaos']);self.assertIn('let alias=L.respond',candidate['files']['main.chaos'])
                saved=client.save_project({**p,'files':candidate['files'],'source':candidate['files'][p['entry']],'sourceVersions':candidate['languageVersions']})
                self.assertEqual(saved['version'],p['version']+1);self.assertEqual(saved['program']['sourceVersions'],opts['language_versions'])
                self.assertEqual(app.program('symbols')['program']['semanticHash'],parse_program(candidate['files']['main.chaos'])['semanticHash'])
                with self.assertRaises(ConflictError):client.save_project({**p,'files':candidate['files'],'source':candidate['files'][p['entry']]})
                self.assertEqual(client.start('symbols',saved['version'],None,mode='source').result(),42)
                for path,body in [('/language/rename',{'source':SOURCE,'files':FILES,'position':POSITION,'newName':'respond','commit':True}),('/language/references',{'source':SOURCE,'files':FILES,'position':True})]:
                    with self.assertRaises(ValidationError):client._request('POST',path,body)
            finally:server.shutdown();thread.join(3);server.server_close()
    def test_openapi_fields_and_no_hidden_commit_route(self):
        api=specification();schemas=api['components']['schemas']
        self.assertIn('/language/references',api['paths']);self.assertIn('/language/rename',api['paths'])
        self.assertFalse(schemas['SymbolRenameRequest']['additionalProperties']);self.assertEqual(set(schemas['SymbolRename']['required']),{'target','newName','edits','baseHashes','files','entry','languageVersions'})
        self.assertIn('limitations',schemas['SymbolReferences']['properties']);self.assertIn('sourceVersions',schemas['Project']['properties'])
    def test_programming_agent_calls_same_engine_only_changes_proposal(self):
        from tests.test_ide_agent import IDEAgentTests
        fixture=IDEAgentTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        p=fixture.app.get_project('project');versions={'main.chaos':2,'lib.chaos':1,'reverse.chaos':1};p=fixture.app.save_project({**p,'source':SOURCE,'files':FILES,'sourceVersions':versions},from_view='source')
        def inspect_refs(request):
            messages=[x for x in request['messages']if x.get('role')=='tool'];refs=json.loads(messages[-1]['content']);self.assertTrue(refs['complete']);self.assertEqual(len(refs['references']),4)
            return [('draft.rename',{'path':'main.chaos','position':POSITION,'newName':'respond'})]
        job=fixture.start([[('draft.references',{'path':'main.chaos','position':POSITION})],inspect_refs,[('draft.validate',{}),('draft.evaluate',{})],'Renamed the proven binding.'])
        result=fixture.wait(job);self.assertEqual(result['status'],'completed',result.get('error'));self.assertTrue(result['validation']['ok']);self.assertEqual(result['checks'][0]['output'],42)
        self.assertEqual({x['path']for x in result['changes']},set(FILES));self.assertEqual(fixture.app.get_project('project')['version'],p['version'])
        self.assertEqual(result['validation']['sourceVersions'],versions)
        continued=fixture.start([[('draft.validate',{}),('draft.evaluate',{})],'Preserved renamed draft.'],session=result['sessionId']);resumed=fixture.wait(continued)
        self.assertEqual(resumed['validation']['sourceVersions'],versions);self.assertEqual(resumed['checks'][0]['output'],42)
        saved=fixture.service.apply('project',continued['id'],p['version']);self.assertEqual(saved['version'],p['version']+1);self.assertEqual(saved['program']['sourceVersions'],versions)
        self.assertEqual(saved['files'],Framework.rename(SOURCE,files=FILES,position=POSITION,new_name='respond')['files'])
