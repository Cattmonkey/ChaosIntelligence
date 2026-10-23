"""Primary next composes real language functions, agents and program revisions."""
import copy
import json
from pathlib import Path
import unittest

from chaos import Framework, Registry, ProviderSpec, ToolCall, Final, Settings
from tests.test_team_functions import definition


class GenericNextIntegration(unittest.TestCase):
    def test_agent_tool_awaits_actual_composed_primary_implementation(self):
        requests = []

        def choose(request, context):
            requests.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('work', request.input)

        registry = Registry()
        registry.register_provider(ProviderSpec('choose', choose))
        source = '''var calls=[];
        let base=generic_method(generic_function("work"),["Any"],async fn(x){
          await sleep(0);calls=append(calls,"base");x+1
        });
        let work=generic_method(base,["Int"],async fn(next,x){
          calls=append(calls,"specific");let answer=await next();answer+1
        },with_next=true);
        agent a{provider:"choose"};let b=agent_bind(a,[agent_tool("work",work)]);
        [await b(40),calls]'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('next', source=source, capabilities=[])
            self.assertEqual(app.start('next', 1, None, mode='source').result(10),
                             [42, ['specific', 'base']])
        self.assertEqual(requests, [40, 40])

    def test_team_role_uses_exported_composed_function_with_keywords(self):
        source = '''import "worker.chaos" as W;
        let crew=team_bind(team_from(input),{worker:W.work});
        await team_role(crew,"worker")(40,offset=1)'''
        files = {'main.chaos': source, 'worker.chaos': '''import "std/generic" as G;
        let base=G.generic_method(G.generic_function("work"),["Any"],fn(x,*,offset=0){x+offset});
        let work=G.generic_method(base,["Int"],fn(next,x,*,offset=0){next()+1},with_next=true);
        export {work};'''}
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('next', source=source, files=files, capabilities=[])
            run = app.start('next', 1, definition(), mode='source')
            self.assertEqual(run.result(10), 42)
            events = app.events('next', run_id=run.id)['items']
            self.assertEqual([event['type'] for event in events if event['type'].startswith('team.role.')],
                             ['team.role.started', 'team.role.completed'])

    def test_generated_code_keeps_captured_contract_and_native_next(self):
        source = '''let Payload=contract_record({value:"Int"});
        let body=function_from(code_from("fn(next,request:Payload)->Int{next()+1}"),{Payload:Payload});
        let base=generic_method(generic_function("work"),["Any"],fn(request){request.value+1});
        let work=generic_method(base,["Record"],body,with_next=true);
        [work({value:40}),try{work({value:"bad"})}catch e{e.code}]'''
        self.assertEqual(Framework.evaluate(source), [42, 'type_mismatch'])

    def test_program_adoption_rebuilds_method_chain_across_two_revisions(self):
        folder = Path(__file__).resolve().parents[1] / 'examples/self-programming-dot'
        files = {path.name: path.read_text() for path in folder.glob('*.chaos')}

        def behavior(increment):
            return ('import "std/generic" as G;'
                    'let base=G.generic_method(G.generic_function("answer"),["Any"],fn(value){value+1});'
                    'let answer=G.generic_method(base,["Int"],fn(next,value){next()+' + str(increment - 1) +
                    '},with_next=true);export {answer};')

        files['behavior.chaos'] = behavior(1)

        def programmer(request, context):
            context.checkpoint()
            candidate = copy.deepcopy(request.input['definition'])
            candidate['files']['behavior.chaos'] = behavior(request.input['request']['increment'])
            return Final(json.dumps(candidate))

        registry = Registry()
        registry.register_provider(ProviderSpec('app.programmer', programmer))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('dot', source=files['main.chaos'], files=files, capabilities=['program.adopt'])
            for version, increment in ((1, 2), (2, 3)):
                request = {'mode': 'evolve', 'provider': 'app.programmer', 'request': {'increment': increment},
                           'value': 40, 'cases': [{'entry': 'tests.chaos', 'input': 40, 'expect': 40 + increment}]}
                result = app.start('dot', version, request, mode='source').result(10)
                self.assertEqual(result['before'], 39 + increment)
                self.assertEqual(result['candidateResult'], 40 + increment)
                self.assertEqual(app.get_project('dot')['version'], version + 1)
            self.assertEqual(app.start('dot', 3, {'mode': 'run', 'value': 40}, mode='source').result(10), 43)
            self.assertEqual(sum(event['type'] == 'program.adopted' for event in app.events('dot')['items']), 2)
