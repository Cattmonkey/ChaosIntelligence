"""Actual language tool calls across native provider, project and editor surfaces."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec
from chaos.documentation import Documentation
from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
from chaos.program import format_program, parse_program
from chaos.server import make_server
from test_harness_runtime import FakeClient, call, message

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''var seen=[];
fn twice(x:Int)->Int{seen=append(seen,x);x*2}
agent worker{provider:"choose",tools:[]}
let bound=agent_bind(worker,[agent_tool("twice",twice,input_type="Int",output_type="Int")]);
[await bound(input),seen]'''
PURE='''agent worker{provider:"local",tools:[]};
let item=agent_tool("twice",fn(x:Int){x*2},input_type="Int",output_type="Int");
let bound=agent_bind(worker,[item]);[type_of(item),type_of(bound),type_of(bound(21))]'''

def registry():
    result=Registry()
    def choose(request,context):
        context.checkpoint()
        return ToolCall('twice',request.input) if request.step==1 else Final(request.history[-1]['output'])
    result.register_provider(ProviderSpec('choose',choose))
    return result


class AgentFunctionToolsIntegration(unittest.TestCase):
    def test_closure_state_and_contracts_through_real_provider(self):
        with Framework(registry=registry(),settings=Settings(':memory:')) as app:
            app.create_project('closure',source=SOURCE,capabilities=[])
            run=app.start('closure',1,21,mode='source')
            self.assertEqual(run.result(6),[42,[21]])
            self.assertEqual(app.start('closure',1,22,mode='source').result(6),[44,[22]])
            self.assertEqual(run.get()['snapshot']['source'],SOURCE)

    def test_native_protocol_executes_language_closure_and_closes_client(self):
        client=FakeClient([message(calls=[call(args='{"input":21}')]),message('done')])
        settings=Settings(':memory:')
        settings.update({'providers':{'text':{'apiKey':'test-key'}},'roles':{'executor':{'model':'test-model'}}},1)
        with patch.object(settings,'client',return_value=client), Framework(registry=registry(),settings=settings) as app:
            app.create_project('native',source=SOURCE.replace('provider:"choose"','provider:"model"'),capabilities=[])
            self.assertEqual(app.start('native',1,21,mode='source').result(8),['done',[21]])
        self.assertTrue(client.closed)
        self.assertEqual(len(client.requests),2)
        self.assertEqual(json.loads(client.requests[1]['messages'][-1]['content']),42)
        declaration=client.requests[0]['tools'][0]['function']
        self.assertIn('twice',declaration['description'])

    def test_recorded_language_tool_contains_actual_host_cleanup_receipts(self):
        seen=[];registered=registry()
        registered.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''fn work(x){with exit_stack() as stack{
 stack.callback(fn(){tool("audit","release")});tool("audit",x)}}
agent worker{provider:"choose",tools:["audit"]}
let bound=agent_bind(worker,[agent_tool("twice",work,capabilities=["audit"])]);
let recorded=await record_skill(bound,21);
[recorded.output,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        with Framework(registry=registered,settings=Settings(':memory:')) as app:
            app.create_project('record',source=source,capabilities=['audit'])
            self.assertEqual(app.start('record',1,None,mode='source').result(8),[21,[21,'release'],[21,'release']])
        self.assertEqual(seen,[21,'release',21,'release'])

    def test_http_sdk_ir_share_versions_and_formatting(self):
        with tempfile.TemporaryDirectory() as folder, Framework(registry=registry(),settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                client.create_project('binding',source=SOURCE,capabilities=[])
                run=client.start('binding',1,21,mode='source')
                self.assertEqual(run.result(8),[42,[21]])
                ir=client.program('binding')['program'];formatted=format_program(ir)
                self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                self.assertEqual(client.analyze(SOURCE),Framework.analyze(SOURCE))
                self.assertEqual(client.format_source(SOURCE),Framework.format_source(SOURCE))
                def walk(value):
                    if isinstance(value,dict):
                        yield value
                        for item in value.values():yield from walk(item)
                    elif isinstance(value,list):
                        for item in value:yield from walk(item)
                target=next(node['id'] for node in walk(ir['root']) if node.get('kind')=='literal' and node.get('value')==2)
                saved=client.edit_program('binding',1,operations=[{'op':'set','id':target,'field':'value','value':3}])
                self.assertEqual(saved['version'],2)
                self.assertEqual(client.start('binding',2,21,mode='source').result(8),[63,[21]])
                self.assertEqual(run.get()['snapshot']['source'],SOURCE)
            finally:
                server.shutdown();thread.join(3);server.server_close()

    def test_debugger_pauses_in_actual_tool_closure(self):
        source='\n'.join(['var calls=0;','fn twice(x:Int)->Int{','calls=calls+1;','x*2','}',
            'agent worker{provider:"choose",tools:[]}',
            'let bound=agent_bind(worker,[agent_tool("twice",twice)]);','[await bound(21),calls]'])
        with Framework(registry=registry(),settings=Settings(':memory:')) as app:
            app.create_project('debug-binding',source=source,capabilities=[])
            run=app.start_debug('debug-binding',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':4}])
            deadline=time.monotonic()+5
            while True:
                state=run.debugger()
                if state['status']=='paused':break
                self.assertEqual(state['status'],'running',str(run.get()))
                self.assertLess(time.monotonic(),deadline);time.sleep(.005)
            values={v['name']:v['value'] for frame in state['frames'] for v in frame['locals']}
            self.assertEqual(values['x'],21)
            self.assertEqual(values['calls'],1)
            run.debug_command('breakpoints',breakpoints=[])
            run.debug_command('continue',pause_id=state['pauseId'])
            self.assertEqual(run.result(5),[42,1])

    def test_cli_and_installed_reference_preserve_cold_construction(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'binding.chaos';path.write_text(PURE)
            for action in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',action,str(path)],cwd=folder,
                    env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                if action=='run':self.assertEqual(json.loads(result.stdout),['AgentTool','Function','Task'])
                if action=='fmt':self.assertEqual(parse_program(result.stdout)['semanticHash'],parse_program(PURE)['semanticHash'])
        self.assertIn('function-tools',LANGUAGE_REFERENCE_TOPICS)
        page=Documentation().page('function-tools')
        self.assertIn('agent_bind',page['markdown']);self.assertIn('chaos-token-',page['html'])

    def test_language_adoption_updates_next_run_function_tool(self):
        main='''import "behavior.chaos" as B;
if input.kind=="adopt"{
 let base=current_program();let candidate=program_from(input.definition);
 await program_adopt(base,candidate,[{entry:"tests.chaos",input:21,expect:63}],reason="new tool implementation")
}else{
 agent worker{provider:"choose",tools:[]};
 let bound=agent_bind(worker,[agent_tool("twice",B.work,input_type="Int",output_type="Int")]);
 await bound(input.value)
}'''
        files={'main.chaos':main,'behavior.chaos':'fn work(x:Int)->Int{x*2};export{work}',
               'tests.chaos':'import "behavior.chaos" as B;B.work(input)'}
        candidate={'kind':'program','entry':'main.chaos','files':{**files,'behavior.chaos':'fn work(x:Int)->Int{x*3};export{work}'},'languageVersions':{name:2 for name in files}}
        with Framework(registry=registry(),settings=Settings(':memory:')) as app:
            app.create_project('evolve-tool',source=main,files=files,capabilities=['program.adopt'])
            old=app.start('evolve-tool',1,{'kind':'run','value':21},mode='source')
            self.assertEqual(old.result(8),42)
            app.start('evolve-tool',1,{'kind':'adopt','definition':candidate},mode='source').result(8)
            self.assertEqual(app.get_project('evolve-tool')['version'],2)
            self.assertEqual(app.start('evolve-tool',2,{'kind':'run','value':21},mode='source').result(8),63)
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],files['behavior.chaos'])


if __name__=='__main__':unittest.main()
