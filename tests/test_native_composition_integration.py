"""Real agent/function composition crosses the compiled host-event boundary."""
import unittest
from chaos import Framework,Registry,ProviderSpec,ToolSpec,ToolCall,Final,Settings
from tests.test_team_functions import definition
from tests.test_native_jit import NATIVE

SOLVER='''// Calls remain ordinary language calls inside the compiled control flow.
fn adjust(value,/,*,offset=2){value+offset};
fn solve(values,/,*,offset=2){var total=0;for value in values{total=total+adjust(value,offset=offset)};total};
let fast=compile_function(solve,target="auto-flow");'''
SOURCE=SOLVER+'fast([40])'

def program(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


def assert_flow(test,info):
    test.assertEqual(info['status'],'native'if NATIVE else 'portable')
    test.assertEqual(info['abi'],'pc-host-events/1')
    test.assertGreater(info['nativeResumes'if NATIVE else 'portableResumes'],0)


class NativeCompositionIntegration(unittest.TestCase):
    def test_agent_tool_and_agent_call_enter_compiled_no_scalar_bodies(self):
        seen=[]
        def choose(request,context):
            context.checkpoint();seen.append(request.input)
            return Final(request.history[-1]['output'])if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('app.choose',choose))
        source='''let add=fn(value){value+2};
let work=compile_function(fn(value){add(value)},target="auto-flow");
let invoke=compile_function(fn(callback,value){callback(value)},target="auto-flow");
agent planner{provider:"app.choose"};let bound=agent_bind(planner,[agent_tool("work",work)]);
let pending=invoke(bound,40);let kind=type_of(pending);let answer=await pending;
[answer,kind,compile_flow_info(work),compile_flow_info(invoke),compile_optimization_info(invoke).enabled]'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('composition-agent',source=source,capabilities=[])
            result=app.start('composition-agent',1,None,mode='source',trace=False).result(10)
        self.assertEqual(result[:2],[42,'Task']);self.assertFalse(result[4]);assert_flow(self,result[2]);assert_flow(self,result[3])
        self.assertEqual(seen,[40,40])

    def test_team_role_generic_method_context_and_protected_cleanup(self):
        source='''let tenant=context_var("tenant","root");var released=[];
let base=generic_method(generic_function("work"),["Int"],fn(value){value+1});
let dispatch=generic_method(base,[generic_eql(40)],fn(next,value){next()+1},with_next=true);
let worker=compile_function(fn(value){try{return {answer:dispatch(value),tenant:tenant.get()}}finally{released=append(released,"released")}},target="auto-flow");
let team=team_bind(team_from(input),{worker:worker});
let invoke=compile_function(fn(callback,value){callback(value)},target="auto-flow");
let result=with context_bind([{variable:tenant,value:"request"}]) as binding {await invoke(team,40)};
[result,tenant.get(),released,compile_flow_info(worker),compile_flow_info(invoke)]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('composition-team',source=source,capabilities=[])
            run=app.start('composition-team',1,definition(),mode='source',trace=False);result=run.result(10)
            self.assertEqual(sum(e['type']=='team.role.completed'for e in app.events('composition-team',run_id=run.id)['items']),1)
        self.assertEqual(result[:3],[{'answer':42,'tenant':'request'},'root',['released']]);assert_flow(self,result[3]);assert_flow(self,result[4])

    def test_tool_call_in_compiled_body_cannot_bypass_team_authority(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('app.secret',lambda value,context:calls.append(value)or 42))
        source='''let worker=compile_function(fn(value){tool("app.secret",value)},target="auto-flow");
let bound=team_bind(team_from(input),{worker:worker});
let code=try{await bound(40)}catch e{e.code};[code,compile_flow_info(worker)]'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('composition-caps',source=source,capabilities=['app.secret'])
            result=app.start('composition-caps',1,definition(),mode='source',trace=False).result(10)
        self.assertEqual(result[0],'capability_error');assert_flow(self,result[1]);self.assertEqual(calls,[])

    def test_std_swarm_workers_use_compiled_calls_without_changing_json_state(self):
        source='''import "std/swarm" as S;let plus=fn(value){value+2};
let worker=compile_function(fn(context){plus(context.state)},target="auto-flow");
let before=S.create({a:40,b:41});let after=await S.step(before,{a:worker,b:worker});
[before.states,after.states,after.tick,compile_flow_info(worker)]'''
        result=Framework.evaluate(source)
        self.assertEqual(result[:3],[{'a':40,'b':41},{'a':42,'b':43},1]);assert_flow(self,result[3])

    def test_code_module_program_preserve_calls_and_keyword_binding(self):
        source='''let add=fn(value,/,*,offset=2){value+offset};
let generated=function_from(code_from("fn(value,/,*,offset=2){add(value,offset=offset)}"),{add:add});
let external=compile_function(generated,target="auto-flow");
let component=module_from(program_from(input),{external:external});
[component.run(40),compile_flow_info(external)]'''
        library='fn run(value){external(value,offset=2)};export{run};'
        result=Framework.evaluate(source,{'input':program(library)})
        self.assertEqual(result[0],42);assert_flow(self,result[1])

    def test_program_probe_adopt_rebuilds_compiled_closure_and_keeps_old_snapshot(self):
        candidate=SOLVER+'fast([input])'
        source='''let base=current_program();let candidate=program_from(input);
let checks=[{input:40,expect:42}];let report=await program_probe(candidate,checks);
let receipt=await program_adopt(base,candidate,checks,"compiled function composition");
[report.passed,receipt.version,program_metadata(current_program()).origin.version]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('composition-adopt',source=source,capabilities=['program.adopt'])
            old=app.start('composition-adopt',1,program(candidate),mode='source',trace=False)
            self.assertEqual(old.result(10),[True,2,1]);self.assertEqual(app.start('composition-adopt',2,40,mode='source',trace=False).result(10),42)
            self.assertEqual(old.get()['snapshot']['source'],source)

    def test_cold_task_generator_and_cleanup_keep_original_effect_order(self):
        source='''var events=[];async fn task(value){events=append(events,"task");value+2};
fn* stream(value){try{events=append(events,"stream");yield value+2}finally{events=append(events,"closed")}};
let invoke=compile_function(fn(callback,value){callback(value)},target="auto-flow");
let pending=invoke(task,40);let cursor=invoke(stream,40);let before=events;
let answer=await pending;let item=next(cursor).value;close(cursor);
[before,answer,item,events,compile_flow_info(invoke)]'''
        result=Framework.evaluate(source)
        self.assertEqual(result[:4],[[],42,42,['task','stream','closed']]);assert_flow(self,result[4])


if __name__=='__main__':unittest.main()
