"""Tuple values compose actual Agent, Team, Session, Channel and Program paths."""
import json
import time
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec


class TupleIntegration(unittest.TestCase):
    def registry(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        registry.register_provider(ProviderSpec('double',lambda r,c:Final(r.input*2)))
        return registry

    def run_source(self,registry,source,capabilities=(),value=None):
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('tuples',source=source,capabilities=list(capabilities))
            return app.start('tuples',1,value,mode='source').result(8)

    def test_tool_callback_and_channel_tuple_remain_live_in_recorded_scope(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''agent inner{provider:"double",tools:[]};
async fn work(value){let messages=channel(1);await channel_send(messages,(inner,value));
let (action,number)=(await channel_receive(messages)).value;channel_close(messages);
let result=(action(number),"tuple");tool("audit",parse_json(json_stringify(result)));parse_json(json_stringify(result))}
agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("work",work,capabilities=["audit"])]);
let recorded=await record_skill(bound,21);
[recorded.output,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        wire={'$tuple':[42,'tuple']}
        self.assertEqual(self.run_source(registry,source,['audit']),[wire,[wire],[wire]])
        self.assertEqual(seen,[wire,wire])

    def test_tool_result_stays_json_only_and_projection_is_explicit(self):
        source='''agent worker{provider:"choose",tools:[]};
let bad=agent_bind(worker,[agent_tool("work",fn(value){(value,42)})]);
let rejected=try{await bad(21);false}catch error{true};
let good=agent_bind(worker,[agent_tool("work",fn(value){parse_json(json_stringify((value,42)))})]);
[rejected,await good(21),type_of(parse_json(json_stringify((20,22))))]'''
        self.assertEqual(self.run_source(self.registry(),source),[True,{'$tuple':[21,42]},'Record'])

    def test_tuple_wrapped_callable_does_not_enlarge_tool_authority(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('secret',lambda value,context:seen.append(value) or 42))
        source='''fn secret(value){tool("secret",value)};let container=(secret,);
fn work(value){container[0](value)};agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("work",work)]);
try{await bound(21)}catch error{"denied"}'''
        self.assertEqual(self.run_source(registry,source,['secret']),'denied');self.assertEqual(seen,[])

    def test_team_member_can_receive_tuple_function_product(self):
        definition={'kind':'team','name':'tuple team','entry':'worker','roles':[
            {'id':'worker','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='''let policy=team_from('''+json.dumps(definition)+''');
fn run(pair as (action,value):Tuple,/){action(value)};
let roles=team_bindings(team_bind(policy,{worker:run}));await roles.worker((fn(x){x*2},21))'''
        self.assertEqual(self.run_source(self.registry(),source),42)

    def test_composite_key_inside_tool_uses_native_tuple_and_agent_effect(self):
        source='''agent inner{provider:"double",tools:[]};
class Matrix{fn __getitem__(self,key as (row,col):Tuple){inner(row)+col}}
fn work(value){Matrix()[value,2]};agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("work",work)]);await bound(20)'''
        self.assertEqual(self.run_source(self.registry(),source),42)

    def test_program_renames_tuple_leaf_then_adopts_without_changing_previous_snapshot(self):
        library='fn work(pair as (left,right):Tuple,/){left+right};export{work}'
        source='''import "behavior.chaos" as B;
if input.kind=="adopt"{let base=current_program();
let candidate=program_rename(base,"behavior.chaos",17,"amount");
await program_adopt(base,candidate,[{entry:"tests.chaos",input:21,expect:42}],reason="tuple leaf rename")
}else{agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("work",fn(value){B.work((value,value))})]);await bound(input.value)}'''
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('tuple-evolve',source=source,files={'behavior.chaos':library,'tests.chaos':'import "behavior.chaos" as B;B.work((input,input))'},capabilities=['program.adopt'])
            old=app.start('tuple-evolve',1,{'kind':'run','value':21},mode='source');self.assertEqual(old.result(8),42)
            app.start('tuple-evolve',1,{'kind':'adopt'},mode='source').result(8)
            project=app.get_project('tuple-evolve');self.assertEqual(project['version'],2)
            self.assertIn('(amount,right)',project['files']['behavior.chaos'])
            self.assertEqual(app.start('tuple-evolve',2,{'kind':'run','value':21},mode='source').result(8),42)
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)

    def test_persistent_session_tool_explicit_projection_keeps_original_schema(self):
        from tests.test_session_tools import SessionToolTests, response
        from tests.test_harness_runtime import message
        helper=SessionToolTests();self.addCleanup(helper.doCleanups)
        app,client=helper.app([response(name='work',value=21),message('done')])
        source='''agent worker{provider:"model",tools:[]};let session=await session_open("chat",worker);var seen=null;
async fn work(value){await sleep(0);seen=(value*2,"tuple");parse_json(json_stringify(seen))}
let bound=session_bind(session,[agent_tool("work",work,input_type="Int",output_type="Record")]);
let receipt=await bound("go",message_id="tuple");[receipt.output,seen]'''
        app.create_project('tuple-session',source=source,capabilities=['session.manage'])
        self.assertEqual(app.start('tuple-session',1,None,mode='source').result(8),['done',{'$tuple':[42,'tuple']}])
        history=app.agent_session_history('tuple-session','chat')['items']
        self.assertEqual(history[2]['message']['content'],{'$tuple':[42,'tuple']})

    def test_actual_debugger_tuple_preview_has_bounded_safe_elements(self):
        source='\n'.join(['fn work(value){','let pair=(value,fn(x){x*2});','pair[1](pair[0])','}',
            'agent worker{provider:"choose",tools:[]};','let bound=agent_bind(worker,[agent_tool("work",work)]);','await bound(21)'])
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('tuple-debug',source=source,capabilities=[])
            run=app.start_debug('tuple-debug',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
            try:
                deadline=time.monotonic()+6
                while True:
                    state=run.debugger()
                    if state['status']=='paused':break
                    self.assertEqual(state['status'],'running',str(run.get()));self.assertLess(time.monotonic(),deadline);time.sleep(.005)
                values={v['name']:v for frame in state['frames'] for v in frame['locals']}
                self.assertEqual(values['pair']['type'],'Tuple');preview=values['pair']['value']
                self.assertEqual(preview['type'],'Tuple');self.assertEqual(preview['items'][0],21)
                self.assertEqual(preview['items'][1]['type'],'Function')
                run.debug_command('breakpoints',breakpoints=[]);run.debug_command('continue',pause_id=state['pauseId'])
                self.assertEqual(run.result(5),42)
            finally:run.cancel()


if __name__=='__main__':unittest.main()
