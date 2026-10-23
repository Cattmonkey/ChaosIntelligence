"""Native resource composition through effects, recording and public surfaces."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry, Settings, ToolSpec
from chaos.documentation import Documentation
from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
from chaos.program import parse_program,format_program
from chaos.server import make_server
from test_computer_integration import ComputerPage

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''import "std/context" as C;var events=[];
@C.contextmanager
fn* resource(n:Int){try{yield n}finally{events=append(events,n)}}
let answer=with C.ExitStack() as stack{let a=stack.enter_context(resource(20));let b=stack.enter_context(resource(22));a+b};[answer,events]'''

class ResourceStackIntegration(unittest.TestCase):
    def test_agent_tools_and_mixed_lifo_share_capabilities(self):
        seen=[];registry=Registry()
        registry.register_provider(ProviderSpec('scope',lambda request,context:seen.append(['agent',request.input]) or Final(request.input)))
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(['tool',value]) or value))
        source='''import "std/context" as C;agent worker{provider:"scope",tools:[],maxSteps:1};
@C.contextmanager
fn* resource(n){let value=worker(n);try{yield value}finally{worker(-n)}}
async fn release(n){tool("audit",n)}
async with C.AsyncExitStack() as stack{
 let values=[stack.enter_context(resource(n))for n in [20,22]];
 stack.push_async_callback(release,42);sum(values)
}'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('agents',source=source,capabilities=['audit'])
            self.assertEqual(app.start('agents',1,None,mode='source').result(8),42)
            self.assertEqual(seen,[['agent',20],['agent',22],['tool',42],['agent',-22],['agent',-20]])
            seen.clear()
            app.create_project('denied',source='with exit_stack() as stack{stack.callback(fn(){tool("audit",42)});42}',capabilities=[])
            self.assertEqual(app.start('denied',1,None,mode='source').wait(5)['status'],'failed')
            self.assertEqual(seen,[])

    def test_recording_includes_implicit_stack_cleanup_and_replay(self):
        seen=[];registry=Registry();registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''fn action(input){let stack=exit_stack();stack.callback(fn(){tool("audit","release")});tool("audit",input)}
let recorded=await record_skill(action,42);
[recorded.output,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('record',source=source,capabilities=['audit'])
            self.assertEqual(app.start('record',1,None,mode='source').result(8),[42,[42,'release'],[42,'release']])
            self.assertEqual(seen,[42,'release',42,'release'])

    def test_failed_recording_closes_lexical_owner_before_abort_and_no_late_effect(self):
        seen=[];registry=Registry();registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''fn action(input){
 let stack=exit_stack();stack.callback(fn(){tool("audit","release")});
 fn* owner(){with stack as s{try{yield 42}finally{tool("audit","body")}}}
 let stream=owner();next(stream);throw "original"
}
let error=try{await record_skill(action)}catch e{e.value};tool("audit","outside");error'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('record',source=source,capabilities=['audit'])
            self.assertEqual(app.start('record',1,None,mode='source').result(8),'original')
            self.assertEqual(seen,['body','release','outside'])
            record=app.modes('record')['items'][0]
            self.assertEqual(record['status'],'incomplete')
            self.assertEqual(len(record['checkpoint']['receipts']),2)

    def test_http_sdk_ir_revision_and_execution_snapshot(self):
        with tempfile.TemporaryDirectory()as directory,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));client.create_project('stack',source=SOURCE)
                run=client.start('stack',1,None,mode='source',trace=True)
                self.assertEqual(run.result(5),[42,[22,20]])
                ir=client.program('stack')['program'];canonical=format_program(ir)
                self.assertEqual(Framework.evaluate(canonical),[42,[22,20]])
                self.assertEqual(parse_program(canonical)['semanticHash'],ir['semanticHash'])
                self.assertEqual(client.analyze(SOURCE),Framework.analyze(SOURCE))
                self.assertEqual(client.format_source(SOURCE),Framework.format_source(SOURCE))
                def nodes(value):
                    if isinstance(value,dict):
                        yield value
                        for child in value.values():yield from nodes(child)
                    elif isinstance(value,list):
                        for child in value:yield from nodes(child)
                target=next(n['id']for n in nodes(ir['root'])if n.get('kind')=='literal' and n.get('value')==22)
                saved=client.edit_program('stack',1,operations=[{'op':'set','id':target,'field':'value','value':23}])
                self.assertEqual(saved['version'],2)
                self.assertEqual(client.start('stack',2,None,mode='source').result(5),[43,[23,20]])
                self.assertEqual(run.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.program('stack')['program']['semanticHash'],saved['program']['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(3)

    def test_debugger_exposes_inert_stack_at_real_body_pause(self):
        source='\n'.join(['var closed=false;','let stack=exit_stack();','stack.callback(fn(){closed=true});','let answer=with stack as current{','42','};[answer,closed]'])
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('debug-stack',source=source)
            run=app.start_debug('debug-stack',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':5}])
            deadline=time.monotonic()+4
            while True:
                state=run.debugger()
                if state['status']=='paused':break
                self.assertEqual(state['status'],'running',str(run.get()));self.assertLess(time.monotonic(),deadline);time.sleep(.005)
            values={item['name']:item['value']for frame in state['frames']for item in frame['locals']}
            self.assertEqual(values['stack']['type'],'ExitStack');self.assertNotIn('entries',values['stack']);self.assertIs(values['closed'],False)
            run.debug_command('breakpoints',breakpoints=[]);run.debug_command('continue',pause_id=state['pauseId'])
            self.assertEqual(run.result(4),[42,True])

    def test_cli_formatter_execution_and_installed_reference(self):
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'stack.chaos';path.write_text(SOURCE);env=dict(os.environ,PYTHONPATH=str(ROOT))
            for action in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',action,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                if action=='run':self.assertEqual(json.loads(result.stdout),[42,[22,20]])
                if action=='fmt':self.assertEqual(Framework.evaluate(result.stdout),[42,[22,20]])
        self.assertIn('resource-stacks',LANGUAGE_REFERENCE_TOPICS)
        page=Documentation().page('resource-stacks');self.assertIn('pop_all',page['markdown']);self.assertIn('chaos-token-',page['html'])

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ResourceStackComputer(unittest.TestCase):
    def test_data_driven_computers_release_in_reverse_order(self):
        page=ComputerPage();self.addCleanup(page.close);temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        settings=Settings(':memory:');settings.update({'runtime':{'workspace':temp.name,'timeout':15}},1)
        source='''var computers=[];
let titles=async with exit_stack(true) as stack{
 for url in [input,input]{let c=await stack.enter_async_context(await computer_open(url));computers=append(computers,c)};
 [(observation_info(await computer_observe(c,false))).title for c in computers]
};[titles,map(fn(c){computer_info(c).status},computers)]'''
        with Framework(Path(temp.name)/'state.sqlite3',settings=settings)as app:
            app.create_project('browsers',source=source,capabilities=['browser.control']);run=app.start('browsers',1,page.url,mode='source')
            self.assertEqual(run.result(15),[['Computer test','Computer test'],['closed','closed']])
            lifecycle=[r for r in app.computer_history('browsers',run_id=run.id)['items']if r['kind']=='lifecycle']
            self.assertEqual([r['data']['action']for r in lifecycle],['open','open','close','close'])
            self.assertTrue(all(not r['data'].get('automatic',False)for r in lifecycle[-2:]))
