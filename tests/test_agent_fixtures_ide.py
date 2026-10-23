"""Shared fixture evaluation reaches IDE tools, HTTP adoption and public schemas."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, ProviderSpec, Registry, Settings
from chaos.client import ValidationError
from chaos.evaluation import evaluate, fixture_schema
from chaos.ide_agent import TOOLS
from chaos.openapi import specification
from chaos.program import parse_program
from chaos.runtime import Context
from chaos.server import make_server
from chaos.source_format import format_source
from tests import test_ide_agent as ide_harness


AGENT={'kind':'agent','name':'worker','config':{'provider':'app.never','tools':[]}}
SOURCE='''var seen=[];
fn make(offset:Int){async fn(value:Int)->Int{await sleep(0);seen=append(seen,value);value+offset}}
agent worker{provider:"app.never",tools:[]};
let bound=agent_bind(worker,[agent_tool("add",make(2),input_type="Int",output_type="Int")]);
let cold=bound(input);
let before=seen;
let answer=await cold;
[answer,before,seen]'''
FIXTURES={'agents':[{'definition':AGENT,'input':40,'actions':[{'tool':'add','input':40,'expect':42},{'final':42}]}]}
EXPECTED=[42,[],[40]]


def definition(source=SOURCE):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class AgentFixturesIDE(unittest.TestCase):
    setUp=ide_harness.IDEAgentTests.setUp
    tearDown=ide_harness.IDEAgentTests.tearDown
    wait=ide_harness.IDEAgentTests.wait
    start=ide_harness.IDEAgentTests.start

    def draft(self,source,arguments):
        return self.wait(self.start([[('draft.write',{'path':'main.chaos','source':source})],
            [('draft.validate',{}),('draft.evaluate',arguments)],'Evaluated explicit fixtures.']))

    def test_draft_evaluate_runs_real_async_closure_with_shared_fixture_evaluator(self):
        with patch.object(Context,'agent',side_effect=AssertionError('real Agent invoked')),patch.object(Context,'tool',side_effect=AssertionError('real tool invoked')):
            result=self.draft(SOURCE,{'input':40,'fixtures':FIXTURES})
        self.assertEqual(result['status'],'completed');self.assertTrue(result['validation']['ok'])
        check=result['checks'][0];self.assertTrue(check['ok'],check);self.assertEqual(check['output'],EXPECTED)
        shared=evaluate(SOURCE,40,fixtures=FIXTURES)
        self.assertEqual(check['fixtures'],shared['fixtures'])
        self.assertGreater(check['steps'],0)
        self.assertEqual(self.app.get_project('project')['source'],'input')
        self.assertEqual(self.app.get_project('project')['version'],1)

    def test_absent_fixtures_retains_original_result_shape_and_pure_rejection(self):
        result=self.draft('input+2',{'input':40})
        self.assertEqual(set(result['checks'][0]),{'ok','output','steps'})
        self.assertEqual(result['checks'][0]['output'],42)
        result=self.draft(SOURCE,{'input':40})
        self.assertFalse(result['checks'][0]['ok']);self.assertNotIn('fixtures',result['checks'][0])

    def test_catching_wrong_tool_output_or_unlisted_effect_cannot_make_check_pass(self):
        changed=copy.deepcopy(FIXTURES);changed['agents'][0]['actions'][0]['expect']=43
        source='try{'+SOURCE+'}catch error{'+json.dumps(EXPECTED)+'}'
        result=self.draft(source,{'input':40,'fixtures':changed})
        self.assertFalse(result['checks'][0]['ok']);self.assertEqual(result['checks'][0]['code'],'fixture_error')
        result=self.draft('try{tool("memory.put",{key:"x",value:1})}catch error{42}',{'fixtures':{}})
        self.assertFalse(result['checks'][0]['ok']);self.assertEqual(result['checks'][0]['code'],'fixture_error')

    def test_explicit_host_response_uses_project_grants_without_calling_handler(self):
        fixtures={'tools':[{'name':'memory.put','input':{'key':'x','value':1},'output':42}]}
        project=self.app.get_project('project');project['capabilities']=['memory.put'];self.app.save_project(project,from_view='source')
        with patch.object(Context,'tool',side_effect=AssertionError('real host tool invoked')):
            result=self.draft('tool("memory.put",{key:"x",value:1})',{'fixtures':fixtures})
        self.assertTrue(result['checks'][0]['ok'],result['checks'][0]);self.assertEqual(result['checks'][0]['output'],42)
        project=self.app.get_project('project');project['capabilities']=[];self.app.save_project(project,from_view='source')
        result=self.draft('tool("memory.put",{key:"x",value:1})',{'fixtures':fixtures})
        self.assertFalse(result['checks'][0]['ok'])

    def test_fixture_error_and_step_limit_are_reported_without_success(self):
        for fixtures in (None,[],{'unexpected':True}):
            with self.subTest(fixtures=fixtures):
                result=self.draft('42',{'fixtures':fixtures})
                self.assertFalse(result['checks'][0]['ok']);self.assertEqual(result['checks'][0]['code'],'fixture_error')
        result=self.draft('while true{}',{'fixtures':{},'steps':100})
        self.assertFalse(result['checks'][0]['ok']);self.assertEqual(result['checks'][0]['code'],'resource_limit')

    def test_shared_openapi_and_draft_tool_fixture_schema_are_copies(self):
        schemas=specification()['components']['schemas'];tool=next(schema for name,_,schema in TOOLS if name=='draft.evaluate')
        self.assertEqual(schemas['EvaluationFixtures'],fixture_schema())
        self.assertEqual(tool['properties']['fixtures'],fixture_schema())
        self.assertEqual(schemas['ProgramCase']['properties']['fixtures'],{'$ref':'#/components/schemas/EvaluationFixtures'})
        schemas['EvaluationFixtures'].clear();self.assertTrue(fixture_schema())
        self.assertFalse(specification()['components']['schemas']['ProgramCase']['additionalProperties'])


class AgentFixturesHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='chaos-fixture-http-');self.addCleanup(self.temp.cleanup)
        self.calls=[];registry=Registry()
        def forbidden(request,context):self.calls.append(request);raise AssertionError('fixture called real provider')
        registry.register_provider(ProviderSpec('app.never',forbidden))
        self.app=Framework(Path(self.temp.name)/'runtime.sqlite',registry=registry,settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port))
        self.client.create_project('fixture',source='0',capabilities=['program.adopt'])

    def close_server(self):self.server.shutdown();self.thread.join(3);self.server.server_close()

    def test_http_failure_preserves_version_then_exact_fixture_adoption_commits(self):
        base=self.client.program_snapshot('fixture');cases=[{'input':40,'expect':EXPECTED,'fixtures':copy.deepcopy(FIXTURES)}]
        cases[0]['fixtures']['agents'][0]['actions'][0]['expect']=43
        with self.assertRaises(ValidationError):self.client.adopt_program('fixture',1,definition(),expected_hash=base['definitionHash'],cases=cases)
        self.assertEqual(self.client.get_project('fixture')['version'],1)
        cases[0]['fixtures']=copy.deepcopy(FIXTURES)
        adopted=self.client.adopt_program('fixture',1,definition(),expected_hash=base['definitionHash'],cases=cases)
        self.assertEqual(adopted['project']['version'],2);self.assertEqual(adopted['receipt']['testCount'],1)
        self.assertEqual(self.calls,[])
        events=self.client.events('fixture')['items'];self.assertEqual(sum(row['type']=='program.adopted'for row in events),1)

    def test_http_schema_format_analysis_and_source_ir_share_current_language(self):
        schema=self.client._request('GET','/openapi.json')['components']['schemas']
        self.assertEqual(schema['EvaluationFixtures'],fixture_schema())
        formatted=self.client.format_source(SOURCE)
        self.assertEqual(parse_program(SOURCE)['semanticHash'],parse_program(formatted)['semanticHash'])
        self.assertEqual(formatted,format_source(SOURCE))
        position=formatted.index('bound(input')+len('bound(')
        self.assertEqual(self.client.analyze(formatted,position=position),Framework.analyze(formatted,position=position))
        self.assertEqual(evaluate(formatted,40,fixtures=FIXTURES)['value'],EXPECTED)
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
