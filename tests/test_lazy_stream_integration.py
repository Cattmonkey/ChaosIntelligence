"""Bundled Chaos streams compose existing agent, role and program boundaries."""
import unittest
from chaos import Framework,Registry,ProviderSpec,ToolSpec,ToolCall,Final,Settings
from tests.test_team_functions import definition

IMPORT='import "std/iter" as I;'

def program(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class LazyStreamIntegration(unittest.TestCase):
    def test_agent_tool_runs_async_pipeline_in_original_evaluator(self):
        seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input)
            return Final(request.history[-1]['output'])if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('app.choose',provider))
        source=IMPORT+'''async fn work(value:Int)->Int {
let stream=I.atake(I.amap(fn(item){item+2},I.from_sync([value])),1);
(await acollect(stream))[0]};agent planner{provider:"app.choose"};
await agent_bind(planner,[agent_tool("work",work)])(40)'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('lazy-agent',source=source,capabilities=[])
            self.assertEqual(app.start('lazy-agent',1,None,mode='source').result(10),42)
        self.assertEqual(seen,[40,40])

    def test_team_callbacks_and_channel_share_task_and_message_runtime(self):
        source=IMPORT+'''let bound=team_bind(team_from(input),{worker:fn(value:Int)->Int{value+2}});
let messages=channel(2);await channel_send(messages,40);await channel_send(messages,41);channel_close(messages);
let stream=I.amap(bound,messages);let before=channel_info(messages).buffered;
[before,await acollect(stream),channel_info(messages).buffered]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('lazy-team',source=source,capabilities=[])
            run=app.start('lazy-team',1,definition(),mode='source')
            self.assertEqual(run.result(10),[2,[42,43],0])
            self.assertEqual(sum(e['type']=='team.role.completed'for e in app.events('lazy-team',run_id=run.id)['items']),2)

    def test_team_callback_cannot_gain_tool_authority_through_lazy_stream(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('app.secret',lambda value,context:calls.append(value)or 42))
        source=IMPORT+'''let bound=team_bind(team_from(input),{worker:fn(value){tool("app.secret",value)}});
try{await acollect(I.amap(bound,I.from_sync([40])))}catch e{e.code}'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('lazy-caps',source=source,capabilities=['app.secret'])
            self.assertEqual(app.start('lazy-caps',1,definition(),mode='source').result(10),'capability_error')
        self.assertEqual(calls,[])

    def test_swarm_frames_remain_borrowed_and_can_resume_after_take(self):
        source=IMPORT+'''import "std/swarm" as S;import "std/context" as C;
let frames=S.frames(S.create({a:40}),{a:fn(context){context.state+1}},3);
async with C.aclosing(frames) as owned {
let selected=I.atake(I.amap(fn(frame){frame.snapshot.states.a},owned),2);
let first=await acollect(selected);await aclose(selected);
[first,(await anext(owned)).value.snapshot.states.a]
}'''
        self.assertEqual(Framework.evaluate(source),[[41,42],43])

    def test_generated_code_module_and_program_reuse_the_bundled_functions(self):
        library=IMPORT+'''fn run(value){external(value)};export{run};'''
        source=IMPORT+'''let external=function_from(code_from("fn(value){collect(I.take(I.map(fn(item){item+2},[value]),1))[0]}"),{I:I});
let component=module_from(program_from(input),{external:external});component.run(40)'''
        self.assertEqual(Framework.evaluate(source,{'input':program(library)}),42)

    def test_program_probe_adopt_and_old_snapshot_keep_stream_source(self):
        candidate=IMPORT+'collect(I.take(I.map(fn(value){value+2},[input]),1))[0]'
        source='''let base=current_program();let candidate=program_from(input);
let checks=[{input:40,expect:42}];let report=await program_probe(candidate,checks);
let receipt=await program_adopt(base,candidate,checks,"lazy stream consumer");
[report.passed,receipt.version,program_metadata(current_program()).origin.version]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('lazy-adopt',source=source,capabilities=['program.adopt'])
            old=app.start('lazy-adopt',1,program(candidate),mode='source')
            self.assertEqual(old.result(10),[True,2,1]);self.assertEqual(app.start('lazy-adopt',2,40,mode='source').result(10),42)
            self.assertEqual(old.get()['snapshot']['source'],source)

    def test_explicit_upstream_progress_and_close_are_not_forwarded(self):
        source=IMPORT+'''var closed=false;fn* inputs(){try{yield 1;yield 2;yield 3;yield 4}finally{closed=true}};
let cursor=inputs();let mapped=I.map(fn(value){if value==1{next(cursor)}else{null};value},cursor);
let first=next(mapped).value;let outside=next(cursor).value;close(mapped);
let before=closed;let after=next(cursor).value;close(cursor);[first,outside,before,after,closed]'''
        self.assertEqual(Framework.evaluate(source),[1,3,False,4,True])


if __name__=='__main__':unittest.main()
