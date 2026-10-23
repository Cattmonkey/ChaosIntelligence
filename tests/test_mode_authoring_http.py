"""One definition contract across SDK, HTTP, source and the normal AST."""
from pathlib import Path
import tempfile
import threading
import unittest
from chaos import Client, Framework, Settings, Registry, ProviderSpec, Final
from chaos.client import ConflictError, ValidationError
from chaos.server import make_server
from chaos.program import parse_program, format_program

PLAN={'kind':'plan','name':'initial','steps':[{'id':'answer','call':'worker'}]}
SKILL={'kind':'skill','actions':[{'tool':'text.normalize','input':'  old  '}],'parameters':[]}
GOAL={'kind':'goal','objective':'answer','planner':'planner','verify':'verify','max_iterations':2}

class ModeAuthoringHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port))
        self.client.create_project('authoring',source='input')

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_validation_is_pure_and_source_uses_normal_parser(self):
        before=self.client.events('authoring')['lastEventId']
        for definition in (PLAN,GOAL,SKILL):
            with self.subTest(kind=definition['kind']):
                checked=self.client.validate_mode(definition,name='example_mode')
                source=checked['source'];formatted=format_program(parse_program(source))
                result=Framework.evaluate('import "mode.chaos" as M;mode_info(M.example_mode)',files={'mode.chaos':formatted})
                self.assertEqual(result,checked['definition'])
                self.assertEqual(checked,self.app.validate_mode(definition,name='example_mode'))
        self.assertEqual(self.client.events('authoring')['lastEventId'],before)
        self.assertEqual(self.client.modes('authoring')['items'],[])
        for body in ({'definition':PLAN,'origin':{}},{'definition':{**PLAN,'origin':{}}},{'definition':PLAN,'name':'x);tool("bad",null)'}):
            with self.assertRaises(ValidationError):self.client._request('POST','/modes/validate',body)

    def test_pinned_load_cas_and_evidence_across_http(self):
        row=self.client.save_mode('authoring','pipeline',PLAN)
        newer=self.client.save_mode('authoring','pipeline',{**PLAN,'name':'new'},revision=row['revision'])
        with self.assertRaises(ConflictError):self.client.save_mode('authoring','pipeline',PLAN,revision=row['revision'])
        source='''let pinned=await load_mode("pipeline", revision=1);
let latest=await load_mode("pipeline");
fn worker(ctx){ctx.input+1}
[mode_info(pinned).name,mode_info(latest).name,mode_metadata(pinned),await pinned(41,{worker:worker})]'''
        p=self.client.get_project('authoring');p=self.client.edit_program('authoring',p['version'],source=source)
        run=self.client.start('authoring',p['version'],None,mode='source');out=run.result()
        self.assertEqual(out[:2],['initial','new']);self.assertEqual(out[2]['origin']['revision'],1);self.assertEqual(out[3],{'answer':42})
        events=[e for e in self.client.events('authoring',run_id=run.id)['items']if e['type']=='mode.loaded']
        self.assertEqual([e['payload']['revision']for e in events],[1,newer['revision']])
        for event in events:
            self.assertEqual(event['run_id'],run.id);self.assertEqual(event['payload']['key'],'pipeline');self.assertEqual(len(event['payload']['definitionHash']),64)
        self.assertEqual(self.client.mode('authoring','pipeline',revision=1)['definition']['name'],'initial')

    def test_export_defaults_and_exact_history(self):
        for key,definition in [('plan',PLAN),('goal',GOAL),('skill',SKILL)]:
            self.client.save_mode('authoring',key,definition)
            exported=self.client.mode_source('authoring',key)
            self.assertEqual(exported['format'],'function'if key=='skill'else'value')
            self.assertEqual(exported['revision'],1)
            value=self.client.mode_source('authoring',key,revision=1,format='value',name='snapshot')
            self.assertEqual(Framework.evaluate('import "value.chaos" as M;mode_info(M.snapshot)',files={'value.chaos':value['source']}),self.app.mode('authoring',key)['definition'])
        self.client.save_mode('authoring','plan',{**PLAN,'name':'updated'},revision=1)
        old=self.client.mode_source('authoring','plan',revision=1,format='value')
        self.assertIn('initial',old['source']);self.assertNotIn('updated',old['source'])
        for args in ({'format':'function'},{'format':'unknown'},{'revision':0},{'name':'bad-name'}):
            with self.subTest(args=args),self.assertRaises(ValidationError):self.client.mode_source('authoring','plan',**args)

    def test_source_export_does_not_grant_tool_authority(self):
        checked=self.client.validate_mode(SKILL)
        p=self.client.create_project('denied',source='import "skill.chaos" as M;await M.saved_mode()',files={'skill.chaos':checked['source']},capabilities=[])
        self.assertEqual(self.client.start('denied',p['version'],None,mode='source').wait()['status'],'failed')

    def test_openapi_keeps_operation_and_documents_new_contract(self):
        spec=self.client._request('GET','/openapi.json')
        self.assertEqual(spec['paths']['/modes/validate']['post']['operationId'],'validateMode')
        source=spec['paths']['/projects/{projectId}/modes/{key}/source']['get']
        self.assertEqual(source['operationId'],'exportSkillSource')
        self.assertTrue({'revision','format','name'} <= {p['name']for p in source['parameters']})
        self.assertEqual(spec['components']['schemas']['ModeValidation']['additionalProperties'],False)

    def test_agent_proposal_becomes_verified_language_self_revision(self):
        proposal={'kind':'plan','name':'reviewed','steps':[
            {'id':'answer','call':'worker'},
            {'id':'review','call':'reviewer','needs':['answer']}]}
        observed=[];registry=Registry()
        def designer(request,context):
            observed.append(request.input)
            return Final(proposal)
        registry.register_provider(ProviderSpec('designer',designer))
        source='''agent architect {provider: "designer"};
let previous = await load_mode("pipeline");
fn worker(ctx) { ctx.input + 1 }
fn reviewer(ctx) { ctx.results.answer * 2 }
let bindings = {worker: worker, reviewer: reviewer};
let candidate = mode_from(architect({current: mode_info(previous), target: "review result"}));
let evidence = await candidate(input, bindings);
if evidence.review != 84 { throw "verification failed" } else { null };
let saved = await save_mode(candidate, "pipeline", revision=mode_metadata(previous).origin.revision);
let next = await load_mode("pipeline", revision=saved.revision);
[await previous(input, bindings), await next(input, bindings), mode_metadata(next).origin.revision]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('self_revision',source=source)
            app.save_mode('self_revision','pipeline',PLAN)
            result=app.start('self_revision',1,41,mode='source').result()
            self.assertEqual(result,[{'answer':42},{'answer':42,'review':84},2])
            self.assertEqual(observed[0]['current']['name'],'initial')
            history=app.mode_history('self_revision','pipeline')['items']
            self.assertEqual([row['definition']['name']for row in history],['initial','reviewed'])
            # A valid proposal whose execution fails verification cannot update the library.
            app.create_project('rejected',source=source)
            app.save_mode('rejected','pipeline',PLAN)
            self.assertEqual(app.start('rejected',1,42,mode='source').wait()['status'],'failed')
            self.assertEqual(app.mode('rejected','pipeline')['revision'],1)
            self.assertEqual(app.mode('rejected','pipeline')['definition']['name'],'initial')
