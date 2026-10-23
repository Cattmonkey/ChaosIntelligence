"""Generator-defined resources share runtime, editing, and real effect boundaries."""
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
from chaos.program import parse_program, format_program
from chaos.server import make_server
from test_computer_integration import ComputerPage

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''import "std/context" as C;var closed=false;
@C.contextmanager
fn* lease(value:Int=40){try{yield value}finally{closed=true}}
let answer=with lease() as value{value+2};[answer,closed]'''

class GeneratorContextIntegration(unittest.TestCase):
    def test_agent_resource_factory_and_capability_boundary(self):
        registry=Registry();calls=[]
        def provider(request,context):
            context.checkpoint();calls.append(['agent',request.input]);return Final(request.input)
        registry.register_provider(ProviderSpec('resource',provider))
        registry.register_tool(ToolSpec('audit',lambda value,context:calls.append(['tool',value]) or value))
        source='''import "std/context" as C;agent worker{provider:"resource",tools:[],maxSteps:1};
@C.contextmanager
fn* lease(){let value=worker("acquire");try{yield value}finally{worker("release")}}
@C.asynccontextmanager
async fn* asyncLease(){let value=worker("async acquire");try{yield value}finally{worker("async release")}}
[with lease() as value{tool("audit",value)},async with asyncLease() as value{tool("audit",value)}]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('resource',source=source,capabilities=['audit'])
            self.assertEqual(app.start('resource',1,None,mode='source').result(8),['acquire','async acquire'])
            self.assertEqual(calls,[['agent','acquire'],['tool','acquire'],['agent','release'],['agent','async acquire'],['tool','async acquire'],['agent','async release']])
            calls.clear()
            app.create_project('denied',source='import "std/context" as C;fn* raw(){try{yield 42}finally{tool("audit",42)}}let lease=C.contextmanager(raw);with lease() as x{x}',capabilities=[])
            self.assertEqual(app.start('denied',1,None,mode='source').wait(5)['status'],'failed')
            self.assertEqual(calls,[])

    def test_http_sdk_ir_and_source_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                client.create_project('resource',source=SOURCE)
                run=client.start('resource',1,None,mode='source',trace=True)
                self.assertEqual(run.result(5),[42,True])
                program=client.program('resource')['program']
                canonical=format_program(program)
                self.assertEqual(Framework.evaluate(canonical),[42,True])
                self.assertEqual(parse_program(canonical)['semanticHash'],program['semanticHash'])
                self.assertEqual(client.format_source(SOURCE),Framework.format_source(SOURCE))
                self.assertEqual(client.analyze(SOURCE),Framework.analyze(SOURCE))
                self.assertEqual(run.get()['snapshot']['source'],SOURCE)
                events=client.events('resource',run_id=run.id)['items']
                self.assertTrue(any(row['type']=='program.step' and row['payload']['kind']=='with' for row in events))
            finally:server.shutdown();server.server_close();thread.join(3)

    def test_debugger_pauses_in_real_resource_body(self):
        source='\n'.join(['import "std/context" as C;','var closed=false;','@C.contextmanager',
            'fn* lease(){try{yield 40}finally{closed=true}}','let manager=lease();',
            'let answer=with manager as resource{','resource+2','};[answer,closed]'])
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('debug-resource',source=source)
            run=app.start_debug('debug-resource',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':7}])
            deadline=time.monotonic()+4
            while True:
                state=run.debugger()
                if state['status']=='paused':break
                self.assertEqual(state['status'],'running',str(run.get()))
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.005)
            values={item['name']:item['value']for frame in state['frames']for item in frame['locals']}
            self.assertEqual(values['resource'],40)
            self.assertEqual(values['manager']['type'],'ContextManager')
            self.assertNotIn('generator',values['manager'])
            self.assertIs(values['closed'],False)
            run.debug_command('breakpoints',breakpoints=[])
            run.debug_command('continue',pause_id=state['pauseId'])
            self.assertEqual(run.result(4),[42,True])

    def test_cli_format_ir_run_and_registered_documentation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'resource.chaos';path.write_text(SOURCE)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for action in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',action,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                if action=='run':self.assertEqual(json.loads(result.stdout),[42,True])
                if action=='fmt':
                    self.assertEqual(result.stdout,Framework.format_source(SOURCE))
                    self.assertEqual(Framework.evaluate(result.stdout),[42,True])
        page=Documentation().page('contexts')
        self.assertIn('asynccontextmanager',page['markdown'])
        self.assertIn('chaos-token-',page['html'])

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class GeneratorContextComputer(unittest.TestCase):
    def test_real_computer_nested_in_generator_factory_closes_before_return(self):
        page=ComputerPage();self.addCleanup(page.close)
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        settings=Settings(':memory:');settings.update({'runtime':{'workspace':temp.name,'timeout':15}},1)
        source='''import "std/context" as C;var held=null;
@C.asynccontextmanager
async fn* browser(url){async with await computer_open(url) as computer{held=computer;yield computer}}
let title=async with browser(input) as computer{observation_info(await computer_observe(computer,false)).title};
[title,computer_info(held).status]'''
        with Framework(Path(temp.name)/'state.sqlite3',settings=settings) as app:
            app.create_project('browser',source=source,capabilities=['browser.control'])
            run=app.start('browser',1,page.url,mode='source')
            self.assertEqual(run.result(15),['Computer test','closed'])
            lifecycle=[r for r in app.computer_history('browser',run_id=run.id)['items'] if r['kind']=='lifecycle']
            self.assertEqual(lifecycle[-1]['data']['action'],'close')
            self.assertFalse(lifecycle[-1]['data'].get('automatic',False))
