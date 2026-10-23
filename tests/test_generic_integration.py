"""Generic functions reuse actual agent effects and persisted program adoption."""
import copy
import json
from pathlib import Path
import unittest
from chaos import Framework, Registry, ProviderSpec, ToolCall, Final, Settings
from tests.test_team_functions import definition


class GenericIntegration(unittest.TestCase):
    def test_generic_agent_tool_dispatches_actual_input_and_preserves_async(self):
        requests=[]
        def choose(request, context):
            requests.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('choose',choose))
        source='''let base=generic_function("work");
        let strings=generic_method(base,["String"],fn(x){x+"!"});
        let work=generic_method(strings,["Int"],async fn(x){await sleep(0);x+2});
        agent a{provider:"choose"};let b=agent_bind(a,[agent_tool("work",work)]);
        [await b(40),await b("ready"),generic_info(strings).methodCount]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('generic',source=source,capabilities=[])
            run=app.start('generic',1,None,mode='source')
            self.assertEqual(run.result(10),[42,'ready!',1])
        self.assertEqual(requests,[40,40,'ready','ready'])

    def test_team_role_uses_same_generic_and_keeps_invocation_evidence(self):
        source='''let t=team_from(input);let g=generic_method(generic_function("answer"),["Int"],fn(x,*,offset=2){x+offset});
        let crew=team_bind(t,{worker:g});let result=await team_role(crew,"worker")(40,offset=2);
        [result,team_binding_info(crew).bindings.worker.kind]'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('generic',source=source,capabilities=[])
            run=app.start('generic',1,definition(),mode='source')
            self.assertEqual(run.result(10),[42,'Function'])
            events=app.events('generic',run_id=run.id)['items']
            roles=[e for e in events if e['type'].startswith('team.role.')]
            self.assertEqual([e['type'] for e in roles],['team.role.started','team.role.completed'])
            self.assertEqual(roles[0]['payload']['roleId'],'worker')

    def test_self_programming_rebuilds_generic_table_from_portable_source(self):
        folder=Path(__file__).resolve().parents[1]/'examples/self-programming-dot'
        files={p.name:p.read_text() for p in folder.glob('*.chaos')}
        def behavior(n):
            return 'import "std/generic" as G;let base=G.generic_function("answer");let answer=G.generic_method(base,["Int"],fn(value){value+'+str(n)+'});export {answer};'
        files['behavior.chaos']=behavior(1)
        def programmer(request,context):
            context.checkpoint();candidate=copy.deepcopy(request.input['definition'])
            candidate['files']['behavior.chaos']=behavior(request.input['request']['increment'])
            return Final(json.dumps(candidate))
        registry=Registry();registry.register_provider(ProviderSpec('app.programmer',programmer))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('dot',source=files['main.chaos'],files=files,capabilities=['program.adopt'])
            for version,increment in ((1,2),(2,3)):
                request={'mode':'evolve','provider':'app.programmer','request':{'increment':increment},'value':40,
                         'cases':[{'entry':'tests.chaos','input':40,'expect':40+increment}]}
                result=app.start('dot',version,request,mode='source').result(10)
                self.assertEqual(result['before'],39+increment)
                self.assertEqual(result['candidateResult'],40+increment)
                self.assertEqual(app.get_project('dot')['version'],version+1)
            self.assertEqual(app.start('dot',3,{'mode':'run','value':40},mode='source').result(10),43)
            self.assertEqual(sum(e['type']=='program.adopted' for e in app.events('dot')['items']),2)
