"""Private function parameters through real Agent, Team, Session and Program paths."""
import json
import time
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec


class PositionalOnlyIntegration(unittest.TestCase):
    def registry(self):
        registry=Registry()
        def choose(request, context):
            context.checkpoint()
            return ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        registry.register_provider(ProviderSpec('double',lambda r,c:Final(r.input*2)))
        return registry

    def run_source(self, registry, source, capabilities=(), value=None):
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('parameters',source=source,capabilities=list(capabilities))
            return app.start('parameters',1,value,mode='source').result(8)

    def test_actual_async_tool_handler_patterns_partial_and_recording(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''agent inner{provider:"double",tools:[]};
class Resource{fn __enter__(self,/){tool("audit","enter");null}
fn __exit__(self,error,/){tool("audit","exit");null}}
async fn work(request as {value:n}:Record,/,*,offset:Int=2,**options){
with Resource() as resource{[inner(n)+offset,options]}}
agent outer{provider:"choose",tools:["audit"]};
let handler=partial(work,request="metadata");
let bound=agent_bind(outer,[agent_tool("work",handler,input_type="Record",output_type="List",capabilities=["audit"])]);
let recorded=await record_skill(bound,{value:20});
[recorded.output,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        self.assertEqual(self.run_source(registry,source,['audit']),
                         [[42,{'request':'metadata'}],['enter','exit'],['enter','exit']])
        self.assertEqual(seen,['enter','exit','enter','exit'])

    def test_bad_keyword_call_never_enters_nested_agent_or_user_body(self):
        registry=self.registry();seen=[]
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value)))
        source='''agent inner{provider:"double",tools:[]};
async fn target(value,/){tool("audit","body");inner(value)}
fn work(value,/){try{target(value=value)}catch error{error.code}}
agent outer{provider:"choose",tools:["audit"]};
let bound=agent_bind(outer,[agent_tool("work",work,capabilities=["audit"])]);await bound(21)'''
        self.assertEqual(self.run_source(registry,source,['audit']),'argument_error')
        self.assertEqual(seen,[])

    def test_actual_tool_authority_remains_restricted(self):
        registry=self.registry();seen=[]
        registry.register_tool(ToolSpec('secret',lambda value,context:seen.append(value) or 42))
        source='''fn work(value,/){tool("secret",value)}
agent outer{provider:"choose",tools:[]};let bound=agent_bind(outer,[agent_tool("work",work)]);
try{await bound(21)}catch error{"denied"}'''
        self.assertEqual(self.run_source(registry,source,['secret']),'denied')
        self.assertEqual(seen,[])

    def test_bound_team_forwards_private_slot_and_same_spelling_metadata(self):
        registry=self.registry();seen=[]
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        definition={'kind':'team','name':'parameter team','entry':'worker','roles':[
            {'id':'worker','parent':None,'capabilities':['audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['audit']}}}]}
        source='''let policy=team_from('''+json.dumps(definition)+''');
fn work(value:Int,/,**options){tool("audit",[value+2,options])}
let bound=team_bind(policy,{worker:work});let roles=team_bindings(bound);
await roles.worker(40,value="data")'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('team-params',source=source,capabilities=['audit'])
            run=app.start('team-params',1,None,mode='source')
            self.assertEqual(run.result(8),[42,{'value':'data'}])
            events=app.events('team-params',run_id=run.id,limit=300)['items']
            self.assertEqual([e['type'] for e in events if e['type'].startswith('team.role.')],
                             ['team.role.started','team.role.completed'])
            self.assertTrue(any(e['payload'].get('team',{}).get('roleId')=='worker' for e in events))
        self.assertEqual(seen,[[42,{'value':'data'}]])

    def test_program_self_rename_adopt_keeps_agent_tool_and_metadata_contract(self):
        library='fn work(value:Int,/,**options){[value*2,options]};export{work}'
        main='''import "behavior.chaos" as B;
if input.kind=="adopt"{
 let base=current_program();
 let candidate=program_rename(base,"behavior.chaos",8,"amount");
 await program_adopt(base,candidate,[{entry:"tests.chaos",input:21,expect:[42,{value:"metadata"}]}],reason="private parameter rename")
}else{
 agent worker{provider:"choose",tools:[]};
 let handler=partial(B.work,value="metadata");
 let bound=agent_bind(worker,[agent_tool("work",handler)]);await bound(input.value)
}'''
        tests='import "behavior.chaos" as B;B.work(input,value="metadata")'
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('rename-params',source=main,files={'behavior.chaos':library,'tests.chaos':tests},capabilities=['program.adopt'])
            old=app.start('rename-params',1,{'kind':'run','value':21},mode='source')
            self.assertEqual(old.result(8),[42,{'value':'metadata'}])
            app.start('rename-params',1,{'kind':'adopt'},mode='source').result(8)
            new=app.get_project('rename-params');self.assertEqual(new['version'],2)
            self.assertIn('fn work(amount:Int,/',new['files']['behavior.chaos'])
            self.assertEqual(new['files']['tests.chaos'],tests)
            self.assertEqual(app.start('rename-params',2,{'kind':'run','value':21},mode='source').result(8),[42,{'value':'metadata'}])
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)

    def test_persistent_native_session_uses_same_handler_and_original_contract(self):
        from tests.test_session_tools import SessionToolTests, response
        from tests.test_harness_runtime import message
        fixture=SessionToolTests();self.addCleanup(fixture.doCleanups)
        app,client=fixture.app([response(name='work',value=21),message('done')])
        source='''agent worker{provider:"model",tools:[]};
let session=await session_open("chat",worker);var seen=null;
async fn work(value:Int,/,**options){await sleep(0);seen=[value*2,options];seen}
let bound=session_bind(session,[agent_tool("work",partial(work,value="metadata"),input_type="Int",output_type="List")]);
let receipt=await bound("go",message_id="one");[receipt.output,seen]'''
        app.create_project('session-params',source=source,capabilities=['session.manage'])
        self.assertEqual(app.start('session-params',1,None,mode='source').result(8),['done',[42,{'value':'metadata'}]])
        binding=app.agent_session('session-params','chat')['lastToolBinding']
        self.assertEqual(set(binding['tools'][0]),{'name','description','inputType','outputType','capabilities'})
        history=app.agent_session_history('session-params','chat')['items']
        self.assertEqual(history[2]['message']['content'],[42,{'value':'metadata'}])

    def test_debugger_sees_private_argument_and_separate_keyword_record(self):
        source='\n'.join(['fn work(value:Int,/,**options){',
                         'let answer=value*2;', '[answer,options]', '}',
                         'agent worker{provider:"choose",tools:[]};',
                         'let bound=agent_bind(worker,[agent_tool("work",partial(work,value="metadata"))]);',
                         'await bound(21)'])
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('debug-params',source=source,capabilities=[])
            run=app.start_debug('debug-params',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':3}])
            try:
                deadline=time.monotonic()+6
                while True:
                    state=run.debugger()
                    if state['status']=='paused':break
                    self.assertEqual(state['status'],'running',str(run.get()))
                    self.assertLess(time.monotonic(),deadline);time.sleep(.005)
                values={v['name']:v['value'] for f in state['frames'] for v in f['locals']}
                self.assertEqual(values['value'],21);self.assertEqual(values['options'],{'value':'metadata'})
                run.debug_command('breakpoints',breakpoints=[])
                run.debug_command('continue',pause_id=state['pauseId'])
                self.assertEqual(run.result(5),[42,{'value':'metadata'}])
            finally:run.cancel()


if __name__=='__main__':unittest.main()
