"""Native error selection composes real local agent/framework resources."""
import json
import time
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec


class ExceptionIntegration(unittest.TestCase):
    def registry(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        registry.register_provider(ProviderSpec('double',lambda request,context:Final(request.input*2)))
        return registry

    def run_source(self,registry,source,capabilities=()):
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('exceptions',source=source,capabilities=list(capabilities))
            return app.start('exceptions',1,None,mode='source').result(8)

    def test_provider_failure_is_selected_by_error_record_not_message(self):
        registry=self.registry()
        def unavailable(request,context):raise ValueError('arbitrary provider detail')
        registry.register_provider(ProviderSpec('unavailable',unavailable))
        source='''agent broken{provider:"unavailable",tools:[]};agent fallback{provider:"double",tools:[]};
try{broken(21)}catch {code:"tool_error",.._}{0}
catch {code:"provider_error",.._}{fallback(21)}catch other{throw}'''
        self.assertEqual(self.run_source(registry,source),42)

    def test_recorded_agent_tool_recovers_then_calls_agent_and_replays_tool(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''agent inner{provider:"double",tools:[]};
fn work(value){try{throw {kind:"retry",value:value}}
catch {value:{kind:"retry",value:number},.._} if number>0 {tool("audit",inner(number))}
catch error{throw}}
agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("work",work,capabilities=["audit"])]);
let recorded=await record_skill(bound,21);
[recorded.output,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        self.assertEqual(self.run_source(registry,source,['audit']),[42,[42],[42]])
        self.assertEqual(seen,[42,42])

    def test_denied_tool_handler_rethrow_never_increases_authority(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('secret',lambda value,context:seen.append(value) or 42))
        source='''fn relay(){throw};fn work(value){try{tool("secret",value)}
catch error if error.code!="user_throw"{relay()}catch other{0}}
agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("work",work)]);
try{await bound(21)}catch error{"denied"}'''
        self.assertEqual(self.run_source(registry,source,['secret']),'denied')
        self.assertEqual(seen,[])

    def test_team_role_can_recover_asynchronously_from_channel_closure(self):
        definition={'kind':'team','name':'recovery','entry':'worker','roles':[
            {'id':'worker','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='''let policy=team_from('''+json.dumps(definition)+''');
async fn run(value){let messages=channel(0);channel_close(messages);
try{await channel_send(messages,value)}catch error if error.code!="user_throw"
{await sleep(0);value*2}catch other{throw}}
let roles=team_bindings(team_bind(policy,{worker:run}));await roles.worker(21)'''
        self.assertEqual(self.run_source(self.registry(),source),42)

    def test_two_agent_tasks_keep_distinct_error_contexts_after_yield(self):
        source='''agent twice{provider:"double",tools:[]};fn relay(){throw};
async fn run(value){try{try{throw value}catch error{
await sleep(0);twice(value);relay()}}catch {value:answer,.._}{answer}}
await gather([run(20),run(22)])'''
        self.assertEqual(self.run_source(self.registry(),source),[20,22])

    def test_program_edits_recovery_policy_and_preserves_historical_runs(self):
        library='fn work(value){try{throw value}catch {value:answer,.._}{answer*1}catch other{throw}};export{work}'
        source='''import "behavior.chaos" as B;
if input.kind=="adopt"{let base=current_program();let tree=program_ir(base,"behavior.chaos");
let literal=tree.root.statements[0].function.body.statements[0].cases[0].body.statements[0].right;
let candidate=program_edit(base,"behavior.chaos",[{op:"set",id:literal.id,field:"value",value:2}]);
let checked=await program_probe(candidate,[{entry:"tests.chaos",input:21,expect:42}]);
let accepted=await program_adopt(base,candidate,[{entry:"tests.chaos",input:21,expect:42}],reason="recovery policy");
[checked.passed,accepted.version]}else{agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("work",B.work)]);await bound(input.value)}'''
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('exception-evolve',source=source,files={'behavior.chaos':library,
                'tests.chaos':'import "behavior.chaos" as B;B.work(input)'},capabilities=['program.adopt'])
            old=app.start('exception-evolve',1,{'kind':'run','value':21},mode='source')
            self.assertEqual(old.result(8),21)
            self.assertEqual(app.start('exception-evolve',1,{'kind':'adopt'},mode='source').result(8),[True,2])
            self.assertEqual(app.start('exception-evolve',2,{'kind':'run','value':21},mode='source').result(8),42)
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)

    def test_session_tool_persists_output_from_guarded_error_handler(self):
        from tests.test_session_tools import SessionToolTests, response
        from tests.test_harness_runtime import message
        helper=SessionToolTests();self.addCleanup(helper.doCleanups)
        app,client=helper.app([response(name='work',value=21),message('done')])
        source='''agent worker{provider:"model",tools:[]};let session=await session_open("chat",worker);var seen=0;
async fn work(value){try{throw value}catch {value:answer,.._} if answer>0
{await sleep(0);seen=answer*2;seen}catch other{throw}}
let bound=session_bind(session,[agent_tool("work",work,input_type="Int",output_type="Int")]);
let receipt=await bound("go",message_id="exceptions");[receipt.output,seen]'''
        app.create_project('exception-session',source=source,capabilities=['session.manage'])
        self.assertEqual(app.start('exception-session',1,None,mode='source').result(8),['done',42])
        self.assertEqual(app.agent_session_history('exception-session','chat')['items'][2]['message']['content'],42)

    def test_debugger_handler_locals_do_not_include_sibling_bindings(self):
        source='\n'.join(['fn work(value){','try{throw value}',
            'catch {value:ignored,.._} if false {0}',
            'catch {value:number,.._}{','let answer=number*2;','answer','}','}',
            'agent worker{provider:"choose",tools:[]};',
            'let bound=agent_bind(worker,[agent_tool("work",work)]);','await bound(21)'])
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('exception-debug',source=source,capabilities=[])
            run=app.start_debug('exception-debug',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':6}])
            try:
                deadline=time.monotonic()+6
                while True:
                    state=run.debugger()
                    if state['status']=='paused':break
                    self.assertEqual(state['status'],'running',str(run.get()))
                    self.assertLess(time.monotonic(),deadline);time.sleep(.005)
                values={v['name']:v for frame in state['frames'] for v in frame['locals']}
                self.assertEqual(values['answer']['value'],42);self.assertEqual(values['number']['value'],21)
                self.assertNotIn('ignored',values)
                run.debug_command('breakpoints',breakpoints=[])
                run.debug_command('continue',pause_id=state['pauseId'])
                self.assertEqual(run.result(5),42)
            finally:run.cancel()


if __name__=='__main__':unittest.main()
