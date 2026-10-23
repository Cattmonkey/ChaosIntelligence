"""Recursive contracts remain native boundaries for actual agents and modules."""
import unittest
from chaos import Framework,Registry,ProviderSpec,ToolSpec,ToolCall,Final,Settings
from tests.test_team_functions import definition

TREE='''let Tree=contract_recursive({Node:contract_record({value:"Int",children:contract_list(contract_ref("Node"))})},"Node");
fn total(node:Tree)->Int{node.value+fold(fn(sum,child){sum+total(child)},0,node.children)};'''
GOOD={'value':40,'children':[{'value':2,'children':[]}]}
BAD={'value':40,'children':[{'value':'bad','children':[]}]}

def program(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class RecursiveContractIntegration(unittest.TestCase):
    def test_agent_tool_checks_recursive_input_in_same_evaluator(self):
        seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input)
            return Final(request.history[-1]['output'])if request.history else ToolCall('total',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('app.choose',provider))
        source=TREE+'''async fn work(value:Tree)->Int{total(value)};
agent planner{provider:"app.choose"};await agent_bind(planner,[agent_tool("total",work)])(input)'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('recursive-agent',source=source,capabilities=[])
            self.assertEqual(app.start('recursive-agent',1,GOOD,mode='source').result(10),42)
        self.assertEqual(seen,[GOOD,GOOD])

    def test_team_and_swarm_use_recursive_json_state_and_reject_nested_mismatch(self):
        source='import "std/swarm" as S;'+TREE+'''
fn work(node:Tree)->Tree{{value:node.value+2,children:node.children}};
let bound=team_bind(team_from(input.team),{worker:work});
async fn worker(state){await bound(state)}
let before=S.create({a:input.tree});let after=await S.step(before,{a:worker},prepare=fn(context){context.state});
let bad=try{await bound(input.bad);false}catch e{e.code};
[before.states,after.states,after.tick,bad]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('recursive-team',source=source,capabilities=[])
            run=app.start('recursive-team',1,{'team':definition(),'tree':GOOD,'bad':BAD},mode='source')
            self.assertEqual(run.result(10),[{'a':GOOD},{'a':{**GOOD,'value':42}},1,'type_mismatch'])
            self.assertTrue(any(e['type']=='team.role.completed'for e in app.events('recursive-team',run_id=run.id)['items']))

    def test_valid_contract_does_not_grant_restricted_role_tool_authority(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('app.secret',lambda value,context:calls.append(value)or 42))
        source=TREE+'''fn work(node:Tree)->Int{tool("app.secret",total(node))};
let bound=team_bind(team_from(input.team),{worker:work});try{await bound(input.tree)}catch e{e.code}'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('recursive-caps',source=source,capabilities=['app.secret'])
            self.assertEqual(app.start('recursive-caps',1,{'team':definition(),'tree':GOOD},mode='source').result(10),'capability_error')
        self.assertEqual(calls,[])

    def test_code_module_recreates_closed_descriptors_and_preserves_named_bindings(self):
        library=TREE+'fn run(value:Tree)->Int{external(value)};export{run,Tree};'
        source=TREE+'''let external=function_from(code_from("fn(value:Tree)->Int{total(value)}"),{Tree:Tree,total:total});
let component=module_from(program_from(input.program),{external:external});
[component.run(input.tree),contract_info(component.Tree).root,
 try{component.run(input.bad)}catch e{e.code}]'''
        self.assertEqual(Framework.evaluate(source,{'input':{'program':program(library),'tree':GOOD,'bad':BAD}}),[42,'Node','type_mismatch'])

    def test_program_probe_and_adopt_keep_original_snapshot_and_recursive_source(self):
        candidate=TREE+'total(input)'
        source='''let base=current_program();let candidate=program_from(input.program);
let checks=[{input:input.tree,expect:42}];let report=await program_probe(candidate,checks);
let receipt=await program_adopt(base,candidate,checks,"recursive input boundary");
[report.passed,receipt.version,program_metadata(current_program()).origin.version]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('recursive-adopt',source=source,capabilities=['program.adopt'])
            old=app.start('recursive-adopt',1,{'program':program(candidate),'tree':GOOD},mode='source')
            self.assertEqual(old.result(10),[True,2,1]);self.assertEqual(app.start('recursive-adopt',2,GOOD,mode='source').result(10),42)
            self.assertEqual(old.get()['snapshot']['source'],source)

    def test_async_and_generator_contracts_check_arguments_and_final_returns(self):
        source=TREE+'''var entered=0;async fn later(node:Tree)->Tree{entered+=1;node};
fn* values(node:Tree)->Tree{yield "unconstrained yield";return node};
let cold=later(input.tree);let failed=try{later(input.bad);false}catch e{e.code};
let before=entered;let done=await cold;let stream=values(done);
[before,failed,entered,next(stream),next(stream)]'''
        self.assertEqual(Framework.evaluate(source,{'input':{'tree':GOOD,'bad':BAD}}),
                         [0,'type_mismatch',1,{'done':False,'value':'unconstrained yield'},{'done':True,'value':GOOD}])


if __name__=='__main__':unittest.main()
