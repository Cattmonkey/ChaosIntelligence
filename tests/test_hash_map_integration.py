"""General-key tables participate in real agent calls and program revisions."""
import copy
import json
from pathlib import Path
import unittest
from chaos import Framework, Registry, ProviderSpec, ToolCall, Final, Settings
from tests.test_team_functions import definition


class HashMapIntegration(unittest.TestCase):
    def test_agent_tool_selects_native_async_callable_by_tuple_key(self):
        requests=[]
        def choose(request, context):
            requests.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('choose',choose))
        source='''let routes=hash_map([[tuple(["math",1]),async fn(x){await sleep(0);x+2}],
                                    [tuple(["text",1]),fn(x){x+"!"}]]);
        fn work(request){hash_get(routes,tuple([request.kind,1]))(request.value)}
        agent a{provider:"choose"};let b=agent_bind(a,[agent_tool("work",work)]);
        [await b({kind:"math",value:40}),await b({kind:"text",value:"ready"}),length(routes)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('maps',source=source,capabilities=[])
            self.assertEqual(app.start('maps',1,None,mode='source').result(10),[42,'ready!',2])
        self.assertEqual(len(requests),4)

    def test_team_role_dispatches_hash_map_contract_and_preserves_old_version(self):
        source='''let original=hash_map([[tuple(["job",1]),40]]);
        let revised=hash_set(original,tuple(["job",1]),41);
        let worker=generic_method(generic_function("worker"),["HashMap"],fn(m){m[tuple(["job",1])]+2});
        let crew=team_bind(team_from(input),{worker:worker});
        [await team_role(crew,"worker")(original),await team_role(crew,"worker")(revised),original[tuple(["job",1])]]'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('maps',source=source,capabilities=[])
            run=app.start('maps',1,definition(),mode='source')
            self.assertEqual(run.result(10),[42,43,40])
            roles=[e for e in app.events('maps',run_id=run.id)['items'] if e['type'].startswith('team.role.')]
            self.assertEqual([e['type'] for e in roles],['team.role.started','team.role.completed']*2)

    def test_self_programming_rebuilds_general_key_table_from_portable_module(self):
        folder=Path(__file__).resolve().parents[1]/'examples/self-programming-dot'
        files={p.name:p.read_text() for p in folder.glob('*.chaos')}
        def behavior(n):
            return 'import "std/hash" as H;let operations=H.hash_map([[tuple(["answer",1]),fn(value){value+'+str(n)+'}]]);fn answer(value){operations[tuple(["answer",1])](value)};export {answer};'
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
