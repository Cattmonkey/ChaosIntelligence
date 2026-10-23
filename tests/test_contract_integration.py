"""Composite data contracts stay inside native agent and program calls."""
import copy
import json
from pathlib import Path
import unittest
from chaos import Framework, Registry, ProviderSpec, ToolCall, Final, Settings
from tests.test_team_functions import definition


class ContractIntegration(unittest.TestCase):
    def test_annotated_agent_tool_enforces_payload_and_result_after_await(self):
        requests=[]
        def choose(request, context):
            requests.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('choose',choose))
        source='''let Request=contract_record({values:contract_list("Int")},{note:"String"});
        let Result=contract_record({total:"Int"});var calls=0;
        async fn work(request:Request)->Result{await sleep(0);calls=calls+1;if request.values[0]==0{{total:"bad"}}else{{total:request.values[0]+request.values[1]}}}
        agent a{provider:"choose"};let b=agent_bind(a,[agent_tool("work",work,input_type="Record",output_type="Record")]);
        try{[await b(input),calls]}catch error{["rejected",calls]}'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('contracts',source=source,capabilities=[])
            self.assertEqual(app.start('contracts',1,{'values':[40,2],'note':'sum'},mode='source').result(10),[{'total':42},1])
            self.assertEqual(app.start('contracts',1,{'values':[True,2]},mode='source').result(10),['rejected',0])
            self.assertEqual(app.start('contracts',1,{'values':[0,2]},mode='source').result(10),['rejected',1])
        self.assertEqual(len(requests),4)

    def test_team_role_uses_exported_alias_and_original_call_path(self):
        source='''import "types.chaos" as T;
        fn worker(request:T.Request)->Int{request.values[0]+request.values[1]}
        let crew=team_bind(team_from(input),{worker:worker});
        await team_role(crew,"worker")({values:[40,2]})'''
        files={'main.chaos':source,'types.chaos':'let Request=contract_record({values:contract_list("Int")});export {Request};'}
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('contracts',source=source,files=files,capabilities=[])
            run=app.start('contracts',1,definition(),mode='source')
            self.assertEqual(run.result(10),42)
            roles=[e['type'] for e in app.events('contracts',run_id=run.id)['items'] if e['type'].startswith('team.role.')]
            self.assertEqual(roles,['team.role.started','team.role.completed'])

    def test_runtime_code_captures_actual_contract_alias(self):
        source='''class Item{let value=42};let Payload=contract_record({item:Item});
        let f=function_from(code_from("fn(request:Payload)->Int{request.item.value}"),{Payload:Payload});
        [f({item:Item()}),try{f({item:42})}catch error{"rejected"}]'''
        self.assertEqual(Framework.evaluate(source),[42,'rejected'])

    def test_self_programming_rebuilds_contracts_from_portable_module(self):
        folder=Path(__file__).resolve().parents[1]/'examples/self-programming-dot'
        files={p.name:p.read_text() for p in folder.glob('*.chaos')}
        def behavior(n):
            return 'let Payload=contract_record({value:"Int"});fn work(request:Payload)->Int{request.value+'+str(n)+'};fn answer(value:Int)->Int{work({value:value})};export {answer};'
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
