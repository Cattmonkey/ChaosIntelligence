"""Task introspection preserves shared source, IR, SDK, HTTP and CLI semantics."""
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
from chaos.ide_agent import TOOLS
from chaos.language import Evaluator, LanguageError
from chaos.server import make_server

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''macro invoke(f,x) {quote {unquote(f)(unquote(x))}}
async fn twice(x:Int)->Int {x*2}
let joined=gather([invoke!(twice,input),twice(21)]);
let before=task_info(joined);
let values=await joined;
[values,before.kind,task_info(joined).state,before.memberCount]'''

class TaskJoinSurfaces(unittest.TestCase):
    def test_shared_http_ir_and_task_events_preserve_old_run(self):
        with tempfile.TemporaryDirectory() as directory,Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('task-joins',source=SOURCE,capabilities=[])
                old=client.start(project['id'],1,20)
                self.assertEqual(old.result(5),[[40,42],'gather','completed',2])
                events=client.events(project['id'],run_id=old.id)['items']
                gathers=[r['payload']for r in events if r['type']=='program.task.created'and r['payload'].get('kind')=='gather']
                self.assertEqual(len(gathers),1);self.assertEqual(gathers[0]['memberCount'],2)
                self.assertEqual(len(gathers[0]['memberTaskIds']),2)
                self.assertTrue(any(r['type']=='program.task.completed'and r['payload']['taskId']==gathers[0]['taskId']for r in events))
                ir=client.program(project['id'])['program']
                def literals(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal'and value.get('value')==2:yield value['id']
                        for child in value.values():yield from literals(child)
                    elif isinstance(value,list):
                        for child in value:yield from literals(child)
                targets=list(literals(ir['root']));self.assertEqual(len(targets),1)
                saved=client.edit_program(project['id'],1,operations=[{'op':'set','id':targets[0],'field':'value','value':3}])
                self.assertEqual(saved['version'],2)
                self.assertEqual(client.start(project['id'],2,20).result(5),[[60,63],'gather','completed',2])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.get_project(project['id'])['source'],saved['source'])
            finally:
                server.shutdown();server.server_close();thread.join(3)

    def test_cli_formatter_repl_and_language_reference(self):
        source='async fn answer(){42};let job=answer();let before=task_info(job).state;[before,await job,task_info(job).state]'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),['created',42,'completed'])
                if command=='fmt':
                    self.assertEqual(result.stdout,Framework.format_source(source))
                    self.assertEqual(Framework.evaluate(result.stdout),['created',42,'completed'])
            result=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()),['created',42,'completed'])
        reference=next(schema for name,_,schema in TOOLS if name=='language.reference')
        self.assertIn('tasks',reference['properties']['topic']['enum'])
        page=Documentation().page('tasks')
        self.assertIn('task_info',page['markdown']);self.assertIn('chaos-token-',page['html'])

    def test_task_info_does_not_observe_failure_or_transfer_live_handles(self):
        with self.assertRaises(LanguageError) as caught:
            Framework.evaluate('async fn bad(){throw "unobserved"};let t=spawn(bad());while task_info(t).state!="failed"{null};task_info(t)')
        self.assertEqual(caught.exception.code,'user_throw')
        left,right=Evaluator(),Evaluator();left.suspension.begin();right.suspension.begin()
        try:
            def cold():
                if False: yield None
            task=left.suspension.task(cold());before=left.suspension.info(task)
            before['state']='forged';before['memberTaskIds'].append('forged')
            fresh=left.suspension.info(task)
            self.assertEqual(fresh['state'],'created');self.assertEqual(fresh['memberTaskIds'],[])
            self.assertFalse(fresh['observed'])
            with self.assertRaises(LanguageError):right.suspension.info(task)
            left.suspension.begin()
            with self.assertRaises(LanguageError):left.suspension.info(task)
        finally:
            left.suspension.finish();right.suspension.finish()

if __name__=='__main__':unittest.main()
