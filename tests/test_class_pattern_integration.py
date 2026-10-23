"""Native class patterns compose agent roles, recording and program evolution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec


class ClassPatternIntegration(unittest.TestCase):
    def registry(self):
        registry=Registry()
        registry.register_provider(ProviderSpec('double',lambda request,context:Final(request.input*2)))
        registry.register_provider(ProviderSpec('choose',lambda request,context:
            ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])))
        return registry

    def execute(self,source,registry=None,capabilities=()):
        with Framework(registry=registry or self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('class-patterns',source=source,capabilities=list(capabilities))
            return app.start('class-patterns',1,None,mode='source').result(8)

    def test_agent_component_projects_callable_and_state(self):
        source='''agent worker{provider:"double",tools:[]};class Component{
let role=worker;var offset:Int=0};class Specialized(Component){let label="fast"};
fn invoke(component as Component::{role:role,offset:offset},value){role(value)+offset}
invoke(Specialized(),21)'''
        self.assertEqual(self.execute(source),42)

    def test_async_role_and_stream_validate_component_before_execution(self):
        source='''agent worker{provider:"double",tools:[]};class Component{let role=worker};
async fn execute(component as Component::{role:role},value){await sleep(0);role(value)}
async fn* stream(component as Component::{role:role}){yield role(21)}
[await gather([execute(Component(),20),execute(Component(),21)]),await acollect(stream(Component()))]'''
        self.assertEqual(self.execute(source),[[40,42],[42]])

    def test_recorded_agent_tool_uses_native_component_binding(self):
        seen=[];registry=self.registry();registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''class Request{var amount:Int=0;fn __init__(self,value){self.amount=value;null}}
fn process(request as Request::{amount:amount}){tool("audit",amount*2)}
fn work(value){process(Request(value))};agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("work",work,capabilities=["audit"])]);
let recorded=await record_skill(bound,21);
[recorded.output,map(fn(action){action.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        self.assertEqual(self.execute(source,registry,['audit']),[42,[42],[42]])
        self.assertEqual(seen,[42,42])

    def test_nested_callback_projection_does_not_expand_tool_authority(self):
        seen=[];registry=self.registry();registry.register_tool(ToolSpec('secret',lambda value,context:seen.append(value) or value))
        source='''class Component{let bundle={run:fn(value){tool("secret",value)}}};
let component=Component();fn work(value){let Component::{bundle:{run:run}}=component;run(value)}
agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("work",work)]);
try{await bound(42)}catch error{"denied"}'''
        self.assertEqual(self.execute(source,registry,['secret']),'denied');self.assertEqual(seen,[])

    def test_program_edits_class_pattern_and_keeps_old_run_snapshot(self):
        library='class Box{let left=21;let right=42};fn work(value){let Box::{left:answer}=Box();answer};export{work}'
        source='''import "behavior.chaos" as B;
if input=="adopt"{let base=current_program();let tree=program_ir(base,"behavior.chaos");
let pattern=tree.root.statements[1].function.body.statements[0].pattern;
let candidate=program_edit(base,"behavior.chaos",[{op:"set",id:pattern.id,field:"fields",value:[{key:"right",value:pattern.fields[0].value}]}]);
let checked=await program_probe(candidate,[{entry:"tests.chaos",input:null,expect:42}]);
let accepted=await program_adopt(base,candidate,[{entry:"tests.chaos",input:null,expect:42}],reason="component projection");
[checked.passed,accepted.version]}else{B.work(input)}'''
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('class-evolve',source=source,files={'behavior.chaos':library,
                'tests.chaos':'import "behavior.chaos" as B;B.work(input)'},capabilities=['program.adopt'])
            old=app.start('class-evolve',1,None,mode='source');self.assertEqual(old.result(8),21)
            self.assertEqual(app.start('class-evolve',1,'adopt',mode='source').result(8),[True,2])
            self.assertEqual(app.start('class-evolve',2,None,mode='source').result(8),42)
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)

    def test_team_function_role_matches_class_without_application_parser(self):
        definition={'kind':'team','name':'components','entry':'worker','roles':[{'id':'worker','parent':None,
            'capabilities':[],'agent':{'kind':'agent','name':'worker','config':{'provider':'local','tools':[]}}}]}
        source='''class Request{let value=21};async fn work(request as Request::{value:value}){await sleep(0);value*2}
let policy=team_from('''+json.dumps(definition)+''');let roles=team_bindings(team_bind(policy,{worker:work}));await roles.worker(Request())'''
        self.assertEqual(self.execute(source),42)

    def test_session_tool_uses_component_projection_and_persists_result(self):
        from tests.test_session_tools import SessionToolTests,response
        from tests.test_harness_runtime import message
        helper=SessionToolTests();self.addCleanup(helper.doCleanups)
        app,client=helper.app([response(name='work',value=21),message('done')])
        source='''class Component{let scale=2};fn apply(component as Component::{scale:scale},value){scale*value}
agent worker{provider:"model",tools:[]};let session=await session_open("chat",worker);var seen=0;
fn work(value){seen=apply(Component(),value);seen};
let bound=session_bind(session,[agent_tool("work",work,input_type="Int",output_type="Int")]);
let receipt=await bound("go",message_id="class-patterns");[receipt.output,seen]'''
        app.create_project('class-session',source=source,capabilities=['session.manage'])
        self.assertEqual(app.start('class-session',1,None,mode='source').result(8),['done',42])
        self.assertEqual(app.agent_session_history('class-session','chat')['items'][2]['message']['content'],42)

    def test_cli_check_fmt_ir_run_and_repl_use_native_class_node(self):
        source='class Box{let value:Int=42};let Box::{value:answer}=Box();answer'
        root=Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(root))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)
                if command=='ir':self.assertEqual(json.loads(result.stdout)['root']['statements'][1]['pattern']['kind'],'p_class')
                if command=='fmt':self.assertEqual(result.stdout,Framework.format_source(source))
            result=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout.strip()),42)

if __name__=='__main__':unittest.main()
