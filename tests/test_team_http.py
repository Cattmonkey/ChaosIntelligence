"""Team definitions use one contract across HTTP, SDK, source and normal IR."""
from pathlib import Path
import copy
import tempfile
import threading
import unittest
from chaos import Client, Framework, Settings, Registry, ProviderSpec, Final
from chaos.client import ValidationError
from chaos.server import make_server
from chaos.program import parse_program, format_program

def definition():
    return {'kind':'team','name':'research','entry':'worker','roles':[
        {'id':'coordinator','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'coordinator','config':{'provider':'app.role','tools':[]}}},
        {'id':'worker','parent':'coordinator','capabilities':[],'agent':{'kind':'agent','name':'worker','config':{'provider':'app.role','prompt':'original','tools':[]}}}]}

class TeamHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.calls=[];registry=Registry()
        def answer(request,context):
            self.calls.append(request.input);context.checkpoint()
            return Final({'agent':request.name,'prompt':request.prompt,'input':request.input})
        registry.register_provider(ProviderSpec('app.role',answer))
        self.app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port));self.client.create_project('teams',source='null',capabilities=[])
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_validation_is_pure_and_source_uses_existing_ir_module(self):
        before=self.client.events('teams')['lastEventId'];value=definition()
        checked=self.client.validate_team(value,name='研究团队')
        self.assertEqual(checked,self.app.validate_team(value,name='研究团队'))
        source=format_program(parse_program(checked['source']))
        self.assertEqual(Framework.evaluate('import "t.chaos" as T;team_info(T.研究团队)',files={'t.chaos':source}),value)
        self.assertEqual(self.client.team_schema(),self.app.team_schema())
        self.assertEqual(self.client.events('teams')['lastEventId'],before)
        self.assertEqual(self.client.modes('teams')['items'],[]);self.assertEqual(self.calls,[])

    def test_relation_escalation_cycles_credentials_and_extra_transport_fields_rejected(self):
        bad=[];value=definition()
        broaden=copy.deepcopy(value);broaden['roles'][1]['capabilities']=['code.execute'];bad.append({'definition':broaden})
        cycle=copy.deepcopy(value);cycle['roles'][0]['parent']='worker';bad.append({'definition':cycle})
        secret=copy.deepcopy(value);secret['roles'][1]['agent']['config']['apiKey']='fictional-rejected-secret';bad.append({'definition':secret})
        bad.extend([{'definition':value,'authority':{}},{'definition':value,'name':'team_from'},{'definition':value,'name':'x);tool("bad",null)'}])
        for body in bad:
            with self.subTest(body=body),self.assertRaises(ValidationError):self.client._request('POST','/teams/validate',body)
        self.assertEqual(self.calls,[])

    def test_module_roles_feed_real_plan_and_effect_provenance(self):
        checked=self.client.validate_team(definition())
        p=self.client.create_project('plan_team',source='import "team.chaos" as T;await plan([{id:"answer",call:"worker"}])(41,team_bindings(T.saved_team))',files={'team.chaos':checked['source']},capabilities=[])
        run=self.client.start('plan_team',p['version'],None,mode='source');out=run.result()
        self.assertEqual(out['answer'],{'agent':'worker','prompt':'original','input':{'input':41,'results':{}}})
        events=self.client.events('plan_team',run_id=run.id)['items']
        completed=[e['payload'] for e in events if e['type']=='agent.completed']
        self.assertEqual(len(completed),1)
        self.assertEqual(completed[0]['team'],{'name':'research','definitionHash':checked['definitionHash'],'roleId':'worker','rolePath':['coordinator','worker']})
        self.assertNotIn('capabilities',completed[0]['team'])
        self.assertTrue(any(e['type']=='team.role.completed'for e in events))

    def test_valid_role_envelope_does_not_grant_project_tool_permission(self):
        value=definition()
        for row in value['roles']:row['capabilities']=['text.normalize']
        value['roles'][1]['agent']['config']['tools']=['text.normalize']
        checked=self.client.validate_team(value)
        p=self.client.create_project('denied',source='import "team.chaos" as T;await T.saved_team(42)',files={'team.chaos':checked['source']},capabilities=[])
        self.assertEqual(self.client.start('denied',p['version'],None,mode='source').wait()['status'],'failed')
        self.assertEqual(self.calls,[])

    def test_openapi_distinguishes_schema_document_and_definition_and_keeps_old_contracts(self):
        spec=self.client._request('GET','/openapi.json')
        self.assertEqual(spec['components']['schemas']['TeamDefinition'],self.client.team_schema())
        response=spec['paths']['/teams/schema']['get']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(response,{'$ref':'#/components/schemas/TeamDefinitionSchema'})
        self.assertEqual(spec['components']['schemas']['TeamValidation']['properties']['definition'],{'$ref':'#/components/schemas/TeamDefinition'})
        self.assertFalse(spec['components']['schemas']['TeamValidation']['additionalProperties'])
        self.assertEqual(spec['paths']['/teams/validate']['post']['operationId'],'validateTeam')
        self.assertEqual(spec['paths']['/agents/validate']['post']['operationId'],'validateAgent')
        self.assertEqual(spec['paths']['/modes/validate']['post']['operationId'],'validateMode')
