"""Completion branches execute through real agent-native composition paths."""
import json
import time
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec


class CompletionIntegration(unittest.TestCase):
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
            app.create_project('completion',source=source,capabilities=list(capabilities))
            return app.start('completion',1,None,mode='source').result(8)

    def test_recorded_tool_channel_exhaustion_calls_agent_and_replays_action(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''agent inner{provider:"double",tools:[]};
async fn work(value){let messages=channel(1);await channel_send(messages,(value,));
channel_close(messages);var total=0;
async for (number,) in messages {total=total+number}
else{let answer=inner(total);tool("audit",answer)}}
agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("work",work,capabilities=["audit"])]);
let recorded=await record_skill(bound,21);
[recorded.output,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        self.assertEqual(self.run_source(registry,source,['audit']),[42,[42],[42]])
        self.assertEqual(seen,[42,42])

    def test_agent_candidate_search_fallback_only_after_exhaustion(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('fallback',lambda value,context:seen.append(value) or -1))
        source='''agent score{provider:"double",tools:[]};
fn work(candidates){var result=null;
for candidate in candidates {let answer=score(candidate);if answer==42{result=answer;break}else{null}}
else{result=tool("fallback",candidates)};result}
agent worker{provider:"choose",tools:["fallback"]};
let bound=agent_bind(worker,[agent_tool("work",work,capabilities=["fallback"])]);
[await bound([20,21,99]),await bound([20]),await bound([])]'''
        self.assertEqual(self.run_source(registry,source,['fallback']),[42,-1,-1])
        self.assertEqual(seen,[[20],[]])

    def test_success_tool_failure_escapes_same_catch_and_runs_finally_once(self):
        seen=[];registry=self.registry()
        def fail(value,context):seen.append('fail');raise ValueError('failed commit')
        registry.register_tool(ToolSpec('fail',fail))
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''fn work(value){try{value}catch error{tool("audit","wrong handler")}
else{tool("fail",value)}finally{tool("audit","cleanup")}}
agent worker{provider:"choose",tools:["fail","audit"]};
let bound=agent_bind(worker,[agent_tool("work",work,capabilities=["fail","audit"])]);
try{await bound(21);false}catch error{true}'''
        self.assertTrue(self.run_source(registry,source,['fail','audit']))
        self.assertEqual(seen,['fail','cleanup'])

    def test_completion_branch_does_not_increase_tool_authority(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('secret',lambda value,context:seen.append(value) or 42))
        source='''fn work(value){for candidate in []{candidate}else{tool("secret",value)}}
agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("work",work)]);
try{await bound(21)}catch error{"denied"}'''
        self.assertEqual(self.run_source(registry,source,['secret']),'denied')
        self.assertEqual(seen,[])

    def test_team_role_function_produces_completion_value(self):
        definition={'kind':'team','name':'completion team','entry':'worker','roles':[
            {'id':'worker','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}
        source='''let policy=team_from('''+json.dumps(definition)+''');
async fn run(value){try{await sleep(0)}catch error{0}else{value*2}}
let roles=team_bindings(team_bind(policy,{worker:run}));await roles.worker(21)'''
        self.assertEqual(self.run_source(self.registry(),source),42)

    def test_program_edits_completion_branch_probes_adopts_and_preserves_old_run(self):
        library='fn work(value){for candidate in []{candidate}else{value*1}};export{work}'
        source='''import "behavior.chaos" as B;
if input.kind=="adopt"{let base=current_program();
let tree=program_ir(base,"behavior.chaos");
let literal=tree.root.statements[0].function.body.statements[0].otherwise.statements[0].right;
let candidate=program_edit(base,"behavior.chaos",[{op:"set",id:literal.id,field:"value",value:2}]);
let checked=await program_probe(candidate,[{entry:"tests.chaos",input:21,expect:42}]);
let accepted=await program_adopt(base,candidate,[{entry:"tests.chaos",input:21,expect:42}],reason="normal completion");
[checked.passed,accepted.version]
}else{agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("work",B.work)]);await bound(input.value)}'''
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('completion-evolve',source=source,files={'behavior.chaos':library,
                'tests.chaos':'import "behavior.chaos" as B;B.work(input)'},capabilities=['program.adopt'])
            old=app.start('completion-evolve',1,{'kind':'run','value':21},mode='source')
            self.assertEqual(old.result(8),21)
            self.assertEqual(app.start('completion-evolve',1,{'kind':'adopt'},mode='source').result(8),[True,2])
            self.assertEqual(app.start('completion-evolve',2,{'kind':'run','value':21},mode='source').result(8),42)
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)

    def test_session_bound_tool_persists_output_from_success_branch(self):
        from tests.test_session_tools import SessionToolTests, response
        from tests.test_harness_runtime import message
        helper=SessionToolTests();self.addCleanup(helper.doCleanups)
        app,client=helper.app([response(name='work',value=21),message('done')])
        source='''agent worker{provider:"model",tools:[]};let session=await session_open("chat",worker);var seen=0;
async fn work(value){try{await sleep(0)}catch error{0}else{seen=value*2;seen}}
let bound=session_bind(session,[agent_tool("work",work,input_type="Int",output_type="Int")]);
let receipt=await bound("go",message_id="completion");[receipt.output,seen]'''
        app.create_project('completion-session',source=source,capabilities=['session.manage'])
        self.assertEqual(app.start('completion-session',1,None,mode='source').result(8),['done',42])
        self.assertEqual(app.agent_session_history('completion-session','chat')['items'][2]['message']['content'],42)

    def test_debugger_pauses_in_else_without_leaking_iteration_binding(self):
        source='\n'.join(['fn work(value){','for item in [1] {item} else {',
            'let answer=value*2;','answer','}','}',
            'agent worker{provider:"choose",tools:[]};',
            'let bound=agent_bind(worker,[agent_tool("work",work)]);','await bound(21)'])
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('completion-debug',source=source,capabilities=[])
            run=app.start_debug('completion-debug',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':4}])
            try:
                deadline=time.monotonic()+6
                while True:
                    state=run.debugger()
                    if state['status']=='paused':break
                    self.assertEqual(state['status'],'running',str(run.get()))
                    self.assertLess(time.monotonic(),deadline);time.sleep(.005)
                values={v['name']:v for frame in state['frames'] for v in frame['locals']}
                self.assertEqual(values['answer']['value'],42);self.assertNotIn('item',values)
                run.debug_command('breakpoints',breakpoints=[])
                run.debug_command('continue',pause_id=state['pauseId'])
                self.assertEqual(run.result(5),42)
            finally:run.cancel()


if __name__=='__main__':unittest.main()
