"""Public Agent definitions share a pure contract across HTTP, SDK and modules."""
from pathlib import Path
import tempfile
import threading
import unittest
from chaos import Client, Framework, Settings, Registry, ProviderSpec, Final
from chaos.client import ValidationError
from chaos.server import make_server
from chaos.program import parse_program, format_program

DEFINITION={'kind':'agent','name':'审阅者','config':{'provider':'app.answer','prompt':'inspect','tools':[]}}

class AgentValuesHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.calls=[];registry=Registry()
        def answer(request,context):
            self.calls.append(request.input);context.checkpoint()
            return Final(request.input['input']+1)
        registry.register_provider(ProviderSpec('app.answer',answer))
        self.app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port))
        self.client.create_project('agent_values',source='input')
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_pure_validation_schema_normal_ast_and_module_roundtrip(self):
        before=self.client.events('agent_values')['lastEventId']
        checked=self.client.validate_agent(DEFINITION,name='角色')
        self.assertEqual(checked,self.app.validate_agent(DEFINITION,name='角色'))
        formatted=format_program(parse_program(checked['source']))
        self.assertEqual(Framework.evaluate('import "roles.chaos" as R;agent_info(R.角色)',files={'roles.chaos':formatted}),DEFINITION)
        self.assertEqual(self.client.agent_schema(),self.app.agent_schema())
        self.assertEqual(self.client.agent_schema()['properties']['maxSteps']['maximum'],12)
        self.assertNotIn('maxSteps',checked['definition']['config'])
        self.assertEqual(self.client.events('agent_values')['lastEventId'],before)
        self.assertEqual(self.client.modes('agent_values')['items'],[])
        self.assertEqual(self.calls,[])

    def test_rejects_credential_fields_wrong_types_and_injection_without_effects(self):
        invalid=[{'definition':DEFINITION,'origin':{}},
                 {'definition':{**DEFINITION,'origin':{}}},
                 {'definition':{**DEFINITION,'config':{'apiKey':'fixture-secret'}}},
                 {'definition':{**DEFINITION,'config':{'maxSteps':True}}},
                 {'definition':{**DEFINITION,'config':{'tools':[1]}}},
                 {'definition':DEFINITION,'name':'agent_from'},
                 {'definition':DEFINITION,'name':'x);tool("bad",null)'}]
        for body in invalid:
            with self.subTest(body=body),self.assertRaises(ValidationError):
                self.client._request('POST','/agents/validate',body)
        self.assertEqual(self.calls,[])

    def test_source_import_plan_executes_provider_with_matching_event_identity(self):
        checked=self.client.validate_agent(DEFINITION)
        p=self.client.create_project('run_roles',source='import "roles.chaos" as R;await plan([{id:"answer",call:"worker"}])(input,{worker:R.saved_agent})',files={'roles.chaos':checked['source']},capabilities=[])
        run=self.client.start('run_roles',p['version'],41,mode='source')
        self.assertEqual(run.result(),{'answer':42})
        self.assertEqual(len(self.calls),1)
        events=self.client.events('run_roles',run_id=run.id)['items']
        matching=[e['payload'] for e in events if e['type'] in ('agent.observing','agent.completed')]
        self.assertEqual(len(matching),2)
        for payload in matching:
            self.assertEqual(payload['agentDefinitionHash'],checked['definitionHash'])
            self.assertNotIn('config',payload)

    def test_reconstruction_and_level_do_not_grant_tool_authority(self):
        checked=self.client.validate_agent({**DEFINITION,'config':{'provider':'app.answer','tools':['text.normalize'],'level':'platform'}})
        p=self.client.create_project('denied_role',source='import "role.chaos" as R;R.saved_agent(input)',files={'role.chaos':checked['source']},capabilities=[])
        self.assertEqual(self.client.start('denied_role',p['version'],41,mode='source').wait()['status'],'failed')
        self.assertEqual(self.calls,[])

    def test_openapi_and_online_docs_match_installed_contract(self):
        spec=self.client._request('GET','/openapi.json')
        self.assertEqual(spec['paths']['/agents/validate']['post']['operationId'],'validateAgent')
        self.assertEqual(spec['paths']['/agents/schema']['get']['operationId'],'agentConfigurationSchema')
        self.assertEqual(spec['components']['schemas']['AgentConfig'],self.client.agent_schema())
        self.assertFalse(spec['components']['schemas']['AgentValidation']['additionalProperties'])
        response=spec['paths']['/agents/schema']['get']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(response,{'$ref':'#/components/schemas/AgentConfigurationSchema'})
        self.assertEqual(spec['components']['schemas']['AgentDefinition']['properties']['config'],{'$ref':'#/components/schemas/AgentConfig'})
        # Existing operations are retained with the same names.
        self.assertEqual(spec['paths']['/modes/validate']['post']['operationId'],'validateMode')
