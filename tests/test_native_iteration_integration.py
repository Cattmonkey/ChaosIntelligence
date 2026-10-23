"""Original compiled loop functions cross the existing agent/program boundaries."""
import unittest
from chaos import Framework,Registry,ProviderSpec,ToolSpec,ToolCall,Final,Settings
from tests.test_team_functions import definition

SOLVER='''import "std/iter" as I;
fn solve(items) {var total=0;for value in I.take(items,2){total=total+value}else{total=total+1};total};
let fast=compile_function(solve,target="auto-flow");'''
SOURCE=SOLVER+'fast([40,1])'

def program(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class NativeIterationIntegration(unittest.TestCase):
    def test_agent_tool_calls_original_compiled_function(self):
        seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input)
            return Final(request.history[-1]['output'])if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('app.choose',provider))
        source=SOLVER+'agent planner{provider:"app.choose"};await agent_bind(planner,[agent_tool("work",fast)])([40,1])'
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('iteration-agent',source=source,capabilities=[])
            self.assertEqual(app.start('iteration-agent',1,None,mode='source',trace=False).result(10),42)
        self.assertEqual(seen,[[40,1],[40,1]])

    def test_team_role_uses_compiled_for_and_same_result_events(self):
        source=SOLVER+'let bound=team_bind(team_from(input),{worker:fast});await bound([40,1])'
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('iteration-team',source=source,capabilities=[])
            run=app.start('iteration-team',1,definition(),mode='source',trace=False)
            self.assertEqual(run.result(10),42)
            self.assertEqual(sum(e['type']=='team.role.completed'for e in app.events('iteration-team',run_id=run.id)['items']),1)

    def test_iteration_callback_preserves_restricted_team_authority(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('app.secret',lambda value,context:calls.append(value)or 42))
        source='''fn work(value){var result=0;for item in [tool("app.secret",value)]{result=result+item};result};
let fast=compile_function(work,target="auto-flow");let bound=team_bind(team_from(input),{worker:fast});
try{await bound(40)}catch e{e.code}'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('iteration-caps',source=source,capabilities=['app.secret'])
            self.assertEqual(app.start('iteration-caps',1,definition(),mode='source',trace=False).result(10),'capability_error')
        self.assertEqual(calls,[])

    def test_generated_code_module_and_program_share_original_compiler(self):
        library='fn run(items){external(items)};export{run};'
        source='''import "std/iter" as I;
let generated=function_from(code_from("fn(items){var total=0;for value in I.take(items,2){total=total+value}else{total=total+1};total}"),{I:I});
let external=compile_function(generated,target="auto-flow");
let component=module_from(program_from(input),{external:external});
[component.run([40,1]),compile_flow_info(external).abi]'''
        self.assertEqual(Framework.evaluate(source,{'input':program(library)}),[42,'pc-host-events/1'])

    def test_program_probe_adopt_preserves_old_iteration_source(self):
        candidate=SOLVER+'fast(input)'
        source='''let base=current_program();let candidate=program_from(input);
let checks=[{input:[40,1],expect:42}];let report=await program_probe(candidate,checks);
let receipt=await program_adopt(base,candidate,checks,"compiled iteration consumer");
[report.passed,receipt.version,program_metadata(current_program()).origin.version]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('iteration-adopt',source=source,capabilities=['program.adopt'])
            old=app.start('iteration-adopt',1,program(candidate),mode='source',trace=False)
            self.assertEqual(old.result(10),[True,2,1]);self.assertEqual(app.start('iteration-adopt',2,[40,1],mode='source',trace=False).result(10),42)
            self.assertEqual(old.get()['snapshot']['source'],source)

    def test_channel_collected_values_and_borrowed_generator_share_runtime(self):
        source='''import "std/iter" as I;var closed=false;
fn* values(){try{yield 40;yield 41;yield 42}finally{closed=true}};
fn first(items){for item in items{return item};0};let fast=compile_function(first,target="auto-flow");
let cursor=values();let firstValue=fast(cursor);let secondValue=next(cursor).value;
let messages=channel(1);await channel_send(messages,firstValue);channel_close(messages);
let thirdValue=fast(await acollect(messages));let before=closed;close(cursor);
[firstValue,secondValue,thirdValue,before,closed]'''
        self.assertEqual(Framework.evaluate(source),[40,41,40,False,True])


if __name__=='__main__':unittest.main()
