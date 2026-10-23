"""Sets are native agent-program data with explicit JSON projections."""
import json,time,unittest
from chaos import Final,Framework,ProviderSpec,Registry,Settings,ToolCall,ToolSpec

class SetIntegration(unittest.TestCase):
    def registry(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose));return registry

    def run_source(self,registry,source,capabilities=()):
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('sets',source=source,capabilities=list(capabilities))
            return app.start('sets',1,None,mode='source').result(8)

    def test_tool_callback_channel_and_recording_use_native_sets(self):
        seen=[];registry=self.registry();registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''async fn work(value){let c=channel(1);await channel_send(c,{value,value+1,value});
let choices=(await channel_receive(c)).value;channel_close(c);let output=collect(choices|{42});tool("audit",output);output}
agent worker{provider:"choose",tools:["audit"]};let bound=agent_bind(worker,[agent_tool("work",work,capabilities=["audit"])]);
let recorded=await record_skill(bound,21);[recorded.output,await replay_skill(recorded.skill)]'''
        self.assertEqual(self.run_source(registry,source,['audit']),[[21,22,42],[[21,22,42]]]);self.assertEqual(seen,[[21,22,42],[21,22,42]])

    def test_tool_output_stays_json_only_and_projection_is_explicit(self):
        source='''agent worker{provider:"choose",tools:[]};
let bad=agent_bind(worker,[agent_tool("work",fn(value){{value,42}})]);
let rejected=try{await bad(21);false}catch error{true};
let good=agent_bind(worker,[agent_tool("work",fn(value){collect({value,42})})]);
[rejected,await good(21),type_of(parse_json(json_stringify({20,22})))]'''
        self.assertEqual(self.run_source(self.registry(),source),[True,[21,42],'Record'])

    def test_team_role_accepts_native_set_and_class_pattern_preserves_it(self):
        definition={'kind':'team','name':'set team','entry':'worker','roles':[{'id':'worker','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='''let policy=team_from('''+json.dumps(definition)+''');class State{let visited={20,22}};
fn work(value:Set){class Box{let visited=value};let Box::{visited:visited}=Box();fold(fn(a,b){a+b},0,visited)};
let roles=team_bindings(team_bind(policy,{worker:work}));await roles.worker(State().visited)'''
        self.assertEqual(self.run_source(self.registry(),source),42)

    def test_program_renames_comprehension_leaf_then_adopts(self):
        library='fn work(value){fold(fn(a,b){a+b},0,{item for item in [value,22,value]})};export{work}'
        source='''import "behavior.chaos" as B;if input.kind=="adopt"{let base=current_program();
let candidate=program_rename(base,"behavior.chaos",'''+str(library.index('item for'))+''',"amount");
await program_adopt(base,candidate,[{entry:"tests.chaos",input:20,expect:42}],reason="set leaf rename")
}else{B.work(input.value)}'''
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('set-evolve',source=source,files={'behavior.chaos':library,'tests.chaos':'import "behavior.chaos" as B;B.work(input)'},capabilities=['program.adopt'])
            old=app.start('set-evolve',1,{'kind':'run','value':20},mode='source');self.assertEqual(old.result(8),42)
            app.start('set-evolve',1,{'kind':'adopt'},mode='source').result(8);project=app.get_project('set-evolve')
            self.assertEqual(project['version'],2);self.assertIn('{amount for amount',project['files']['behavior.chaos'])
            self.assertEqual(app.start('set-evolve',2,{'kind':'run','value':20},mode='source').result(8),42)
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)

    def test_session_tool_projects_native_set_explicitly(self):
        from tests.test_session_tools import SessionToolTests,response
        from tests.test_harness_runtime import message
        helper=SessionToolTests();self.addCleanup(helper.doCleanups);app,client=helper.app([response(name='work',value=21),message('done')])
        source='''agent worker{provider:"model",tools:[]};let session=await session_open("chat",worker);var seen=null;
async fn work(value){await sleep(0);seen={value*2,value*2};collect(seen)};
let bound=session_bind(session,[agent_tool("work",work,input_type="Int",output_type="List")]);
let receipt=await bound("go",message_id="set");[receipt.output,seen]'''
        app.create_project('set-session',source=source,capabilities=['session.manage']);self.assertEqual(app.start('set-session',1,None,mode='source').result(8),['done',{'$set':[42]}])
        self.assertEqual(app.agent_session_history('set-session','chat')['items'][2]['message']['content'],[42])

    def test_debugger_shows_set_type_and_bounded_elements(self):
        source='\n'.join(['fn work(value){','let choices={value,22,value};','fold(fn(a,b){a+b},0,choices)','}','work(20)'])
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('set-debug',source=source,capabilities=[])
            run=app.start_debug('set-debug',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
            try:
                deadline=time.monotonic()+6
                while True:
                    state=run.debugger()
                    if state['status']=='paused':break
                    self.assertEqual(state['status'],'running',str(run.get()));self.assertLess(time.monotonic(),deadline);time.sleep(.005)
                values={v['name']:v for frame in state['frames'] for v in frame['locals']}
                self.assertEqual(values['choices']['type'],'Set');self.assertEqual(values['choices']['value'],{'type':'Set','items':[20,22]})
                run.debug_command('breakpoints',breakpoints=[]);run.debug_command('continue',pause_id=state['pauseId']);self.assertEqual(run.result(5),42)
            finally:run.cancel()

if __name__=='__main__':unittest.main()
