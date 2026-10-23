"""Candidate tests execute real closures while all provider effects remain explicit data."""
import copy
import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.client import ValidationError
from chaos.language import LanguageError
from chaos.program import parse_program
from chaos.program_values import canonical_hash
from chaos.server import make_server

SOURCE='''var seen=[];
fn twice(x:Int)->Int{seen=append(seen,x);x*2}
agent worker{provider:"model",tools:[]}
let bound=agent_bind(worker,[agent_tool("twice",twice,input_type="Int",output_type="Int")]);
[await bound(input),seen]'''
AGENT={'kind':'agent','name':'worker','config':{'provider':'model','tools':[]}}

def definition(source=SOURCE):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}

def cases():
    return [{'input':21,'expect':['42',[21]],'fixtures':{'agents':[{
        'definition':copy.deepcopy(AGENT),'input':21,
        'actions':[{'tool':'twice','input':21,'expect':42},{'final':'42'}]}]}}]


class AgentFixturesIntegration(unittest.TestCase):
    def app(self):
        settings=Settings(':memory:')
        app=Framework(settings=settings);self.addCleanup(app.close)
        client=patch.object(settings,'client',side_effect=AssertionError('Fixture evaluation reached production client'))
        self.client_guard=client.start();self.addCleanup(client.stop)
        return app

    def test_sdk_rechecks_actual_closure_before_cas(self):
        app=self.app();app.create_project('candidate',source='input',capabilities=['program.adopt'])
        before=app.program_snapshot('candidate')
        bad=cases();bad[0]['fixtures']['agents'][0]['actions'][0]['expect']=99
        with self.assertRaises(LanguageError):
            app.adopt_program('candidate',1,definition(),expected_hash=before['definitionHash'],cases=bad)
        self.assertEqual(app.program_snapshot('candidate'),before)
        result=app.adopt_program('candidate',1,definition(),expected_hash=before['definitionHash'],cases=cases())
        self.assertEqual(result['project']['version'],2)
        self.assertEqual(app.program_snapshot('candidate')['definition'],definition())
        self.client_guard.assert_not_called()

    def test_language_probe_adopt_keeps_executing_snapshot(self):
        app=self.app()
        source='''let base=current_program();let candidate=program_from(input.definition);
let report=await program_probe(candidate,input.cases);
let receipt=await program_adopt(base,candidate,input.cases,"checked actual language tool");
[report.passed,report.casesHash,receipt.version,program_metadata(current_program()).origin.version]'''
        app.create_project('self',source=source,capabilities=['program.adopt'])
        run=app.start('self',1,{'definition':definition(),'cases':cases()},mode='source')
        self.assertEqual(run.result(8),[True,canonical_hash(cases()),2,1])
        self.assertEqual(run.get()['snapshot']['source'],source)
        self.assertEqual(app.get_project('self')['source'],SOURCE)
        self.client_guard.assert_not_called()

    def test_fixture_actions_participate_in_cases_hash(self):
        app=self.app();app.create_project('hash',source='await program_probe(program_from(input.definition),input.cases)',capabilities=[])
        first=cases();second=cases();second[0]['fixtures']['agents'][0]['actions'][-1]['final']='43';second[0]['expect'][0]='43'
        reports=[app.start('hash',1,{'definition':definition(),'cases':c},mode='source').result(8) for c in (first,second)]
        self.assertTrue(all(r['passed']for r in reports))
        self.assertEqual([r['casesHash']for r in reports],[canonical_hash(first),canonical_hash(second)])
        self.assertNotEqual(reports[0]['casesHash'],reports[1]['casesHash'])
        self.assertEqual(reports[0]['definitionHash'],reports[1]['definitionHash'])

    def test_host_receipt_is_data_and_caught_unexpected_effect_blocks_adoption(self):
        app=self.app();app.create_project('host',source='input',capabilities=['program.adopt','text.normalize'])
        before=app.program_snapshot('host')
        test=[{'input':' A ','expect':'fixture-only','fixtures':{'tools':[{'name':'text.normalize','input':' A ','output':'fixture-only'}]}}]
        result=app.adopt_program('host',1,definition('tool("text.normalize",input)'),expected_hash=before['definitionHash'],cases=test)
        self.assertEqual(result['project']['version'],2)
        events=app.events('host',limit=300)['items']
        self.assertFalse(any(e['type']=='tool.started' for e in events))
        current=app.program_snapshot('host')
        hidden=definition('try{tool("memory.put",{key:"unwritten",value:42})}catch e{42}')
        with self.assertRaises(LanguageError):
            app.adopt_program('host',2,hidden,expected_hash=current['definitionHash'],cases=[{'input':None,'expect':42,'fixtures':{}}])
        self.assertEqual(app.program_snapshot('host'),current)

    def test_http_sdk_and_ir_share_fixture_adoption(self):
        app=self.app()
        with tempfile.TemporaryDirectory() as folder:
            server=make_server(port=0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                client.create_project('remote',source='input',capabilities=['program.adopt'])
                before=client.program_snapshot('remote');bad=cases();bad[0]['fixtures']['agents'][0]['actions'][0]['expect']=99
                with self.assertRaises(ValidationError):
                    client.adopt_program('remote',1,definition(),expected_hash=before['definitionHash'],cases=bad)
                self.assertEqual(client.program_snapshot('remote'),before)
                result=client.adopt_program('remote',1,definition(),expected_hash=before['definitionHash'],cases=cases())
                self.assertEqual(result['project']['version'],2)
                self.assertEqual(client.program_snapshot('remote'),app.program_snapshot('remote'))
                self.assertEqual(client.program('remote')['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
                formatted=client.format_source(SOURCE)
                self.assertEqual(formatted,Framework.format_source(SOURCE))
                self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                spec=client._request('GET','/openapi.json')
                self.assertIn('fixtures',spec['components']['schemas']['ProgramCase']['properties'])
                self.client_guard.assert_not_called()
            finally:
                server.shutdown();thread.join(3);server.server_close()
