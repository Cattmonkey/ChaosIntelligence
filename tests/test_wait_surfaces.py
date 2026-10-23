"""Native waits keep source/IR, headless APIs and documentation coherent."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Client, Framework, Settings
from chaos.documentation import Documentation
from chaos.language import Evaluator, Native
from chaos.server import make_server
from chaos.program import parse_program, format_program

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''macro call(action,value){quote{unquote(action)(unquote(value))}}
async fn work(value){await sleep(0);call!(fn(x){x*2},value)}
async with task_group() as workers{
 let jobs=map(fn(value){workers.spawn(work(value))},input);
 let ready=await task_wait(jobs,until="all");
 var result=[];
 for job in ready.done{result=append(result,await job)};
 [result,ready.timedOut,length(ready.pending)]
}'''

class WaitSurfaces(unittest.TestCase):
    def test_macro_source_ir_formatter_and_module_calls_are_same_language(self):
        expected=[[40,42],False,0]
        self.assertEqual(Framework.evaluate(SOURCE,{'input':[20,21]}),expected)
        self.assertEqual(Framework.evaluate(format_program(parse_program(SOURCE)),{'input':[20,21]}),expected)
        formatted=Framework.format_source(SOURCE,line_width=40,indent_size=4)
        self.assertEqual(Framework.format_source(formatted,line_width=40,indent_size=4),formatted)
        self.assertEqual(Framework.evaluate(formatted,{'input':[20,21]}),expected)
        files={'worker.chaos':'async fn work(x){await sleep(0);x+1};export{work}'}
        self.assertEqual(Framework.evaluate('import "worker.chaos" as W;let done=await task_wait([W.work(41)],until="all");await done.done[0]',files=files),42)

    def test_http_structure_edits_do_not_rewrite_previous_run_or_old_interfaces(self):
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('wait-surfaces',source=SOURCE,capabilities=[])
                old=client.start(project['id'],1,[20,21]);self.assertEqual(old.result(5),[[40,42],False,0])
                events=client.events(project['id'],run_id=old.id)['items']
                kinds={event['payload'].get('kind')for event in events if event['type']=='program.task.created'}
                self.assertTrue({'sleep','wait'}<=kinds)
                ir=client.program(project['id'])['program']
                def targets(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal' and value.get('value')==2:yield value['id']
                        for child in value.values():yield from targets(child)
                    elif isinstance(value,list):
                        for child in value:yield from targets(child)
                ids=list(targets(ir['root']));self.assertEqual(len(ids),1)
                saved=client.edit_program(project['id'],1,operations=[{'op':'set','id':ids[0],'field':'value','value':3}])
                self.assertEqual(client.start(project['id'],2,[20,21]).result(5),[[60,63],False,0])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.get_project(project['id'])['source'],saved['source'])
            finally:server.shutdown();server.server_close();thread.join(3)

    def test_cli_repl_and_installed_documentation_share_wait_semantics(self):
        source='async fn work(){await sleep(0);42};let ready=await task_wait([work()],until="all");await ready.done[0]'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)
                if command=='fmt':self.assertEqual(result.stdout,Framework.format_source(source))
            result=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout.strip()),42)
        page=Documentation().page('waits');self.assertIn('task_wait',page['markdown']);self.assertIn('chaos-token-function',page['html'])
        self.assertEqual(Framework.evaluate((ROOT/'examples/reactive-workers.chaos').read_text()),[40,42])

    def test_native_host_constructor_compatibility_and_identity_metadata(self):
        owner=Evaluator();legacy=Native('legacy',lambda x:x+1,owner)
        self.assertIsNone(legacy.constructor);self.assertIsNone(legacy.spec_id)
        self.assertEqual(owner.run('legacy(41)',{'legacy':legacy}),42)
        sentinel=object();old_fourth=Native('legacy',lambda:None,owner,sentinel)
        self.assertIs(old_fourth.constructor,sentinel);self.assertIsNone(old_fourth.spec_id)
        self.assertEqual(owner.env.get('sleep').spec_id,'sleep')
        self.assertEqual(owner.env.get('task_wait').spec_id,'task_wait')

if __name__=='__main__':unittest.main()
