"""Resource scopes retain one execution contract through SDK, HTTP, CLI and debug."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from chaos import Client, Framework, Registry, Settings
from chaos.documentation import Documentation
from chaos.ide_agent import TOOLS
from chaos.server import make_server

ROOT = Path(__file__).resolve().parent.parent


class ContextSurfaces(unittest.TestCase):
    def test_http_source_ir_edit_events_and_immutable_run_snapshot(self):
        calls=[]
        registry=Registry()
        @registry.tool('test.scope', effect='write')
        def log(value, context):
            context.checkpoint();calls.append(value);return None
        source='''class Scope {
  fn __enter__(self) {tool("test.scope","enter");input}
  fn __exit__(self,error) {tool("test.scope","exit");null}
}
with Scope() as resource {resource+1}'''
        with tempfile.TemporaryDirectory() as directory:
            with Framework(settings=Settings(':memory:'),registry=registry) as app:
                server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
                worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
                try:
                    client=Client('http://127.0.0.1:'+str(server.server_port))
                    project=client.create_project('contexts',source=source,capabilities=['test.scope'])
                    run=client.start(project['id'],project['version'],41,trace=True)
                    self.assertEqual(run.result(5),42)
                    self.assertEqual(calls,['enter','exit'])
                    events=client.events(project['id'],run_id=run.id)['items']
                    self.assertTrue(any(row['type']=='program.step' and row['payload']['kind']=='with' for row in events))
                    ir=client.program(project['id'])['program']
                    node=ir['root']['statements'][-1]
                    self.assertEqual(node['kind'],'with')
                    target=node['body']['statements'][0]['right']['id']
                    saved=client.edit_program(project['id'],1,operations=[{'op':'set','id':target,'field':'value','value':2}])
                    self.assertEqual(saved['version'],2)
                    self.assertEqual(client.get_project(project['id'])['source'],saved['source'])
                    self.assertEqual(client.program(project['id'])['program']['semanticHash'],saved['program']['semanticHash'])
                    self.assertEqual(client.start(project['id'],2,41).result(5),43)
                    self.assertEqual(calls,['enter','exit','enter','exit'])
                    self.assertEqual(run.get()['snapshot']['source'],source)
                    versions=[row['payload'].get('version') for row in client.events(project['id'])['items']]
                    self.assertIn(2,versions)
                finally:
                    server.shutdown();server.server_close();worker.join(3)

    def test_debugger_observes_actual_scope_binding_before_exit(self):
        source='''var released=false;
class Scope {
  fn __enter__(self) {40}
  fn __exit__(self,error) {released=true;null}
}
let answer=with Scope() as resource {
  resource+2
};
[answer,released]'''
        with Framework(settings=Settings(':memory:')) as app:
            project=app.create_project('scope-debug',source=source)
            run=app.start_debug(project['id'],1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':7}])
            deadline=time.monotonic()+4
            while True:
                state=run.debugger()
                if state['status']=='paused':break
                self.assertEqual(state['status'],'running',str(run.get()))
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.005)
            self.assertEqual(state['location']['line'],7)
            locals_={item['name']:item['value']for frame in state['frames']for item in frame['locals']}
            self.assertEqual(locals_['resource'],40)
            self.assertIs(locals_['released'],False)
            run.debug_command('breakpoints',breakpoints=[])
            run.debug_command('continue',pause_id=state['pauseId'])
            self.assertEqual(run.result(4),[42,True])

    def test_cli_repl_formatter_and_installed_reference_contract(self):
        source='import "std/context" as C;var closed=false;fn* values(){try{yield 42;yield 43}finally{closed=true}};let x=with C.closing(values()) as stream{next(stream).value};[x,closed]'
        with tempfile.TemporaryDirectory() as directory:
            module=Path(directory)/'main.chaos';module.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for action in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',action,str(module)],cwd=directory,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if action=='run':self.assertEqual(json.loads(result.stdout),[42,True])
                if action=='fmt':
                    self.assertEqual(result.stdout,Framework.format_source(source))
                    self.assertEqual(Framework.evaluate(result.stdout),[42,True])
            repl=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(repl.returncode,0,repl.stderr)
            self.assertEqual(json.loads(repl.stdout.strip()),[42,True])
        reference=next(schema for name,_,schema in TOOLS if name=='language.reference')
        self.assertIn('contexts',reference['properties']['topic']['enum'])
        page=Documentation().page('contexts')
        self.assertIn('async with',page['markdown'])
        self.assertIn('chaos-token-',page['html'])


if __name__=='__main__':unittest.main()
