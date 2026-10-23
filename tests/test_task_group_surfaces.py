"""Native task groups retain one source/IR/SDK/HTTP/CLI execution model."""
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
from chaos.language import Evaluator, LanguageError, serializable
from chaos.server import make_server
from chaos.program import parse_program, format_program

ROOT=Path(__file__).resolve().parent.parent
SOURCE='''macro invoke(f,x) {quote {unquote(f)(unquote(x))}}
async fn twice(x:Int)->Int {x*2}
let group:TaskGroup=task_group();
let jobs=async with group as workers {
    [workers.spawn(invoke!(twice,input)),workers.spawn(twice(21))]
};
[await jobs[0],await jobs[1],task_group_info(group).state,
 task_group_info(group).memberCount]'''

class TaskGroupSurfaces(unittest.TestCase):
    def test_sdk_source_ir_macro_format_and_input_share_semantics(self):
        expected=[40,42,'closed',2]
        self.assertEqual(Framework.evaluate(SOURCE,{"input":20}),expected)
        ir=parse_program(SOURCE)
        self.assertEqual(Framework.evaluate(format_program(ir),{"input":20}),expected)
        formatted=Framework.format_source(SOURCE,line_width=40,indent_size=4)
        self.assertEqual(Framework.format_source(formatted,line_width=40,indent_size=4),formatted)
        self.assertEqual(Framework.evaluate(formatted,{"input":20}),expected)

    def test_http_structural_edit_and_immutable_run(self):
        with tempfile.TemporaryDirectory() as directory,Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('task-groups',source=SOURCE,capabilities=[])
                old=client.start(project['id'],1,20)
                self.assertEqual(old.result(5),[40,42,'closed',2])
                events=client.events(project['id'],run_id=old.id)['items']
                scopes=[r['payload']for r in events if r['type']=='program.task.created'and r['payload'].get('kind')=='scope']
                self.assertEqual(len(scopes),1)
                self.assertTrue(any(r['type'].startswith('program.group.') for r in events))
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
                self.assertEqual(client.start(project['id'],2,20).result(5),[60,63,'closed',2])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.get_project(project['id'])['source'],saved['source'])
            finally:
                server.shutdown();server.server_close();thread.join(3)

    def test_cli_repl_documentation_and_group_reference(self):
        source='async fn value(){42};let g=task_group();let job=async with g as workers{workers.spawn(value())};[await job,task_group_info(g).state]'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(source)
            env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),[42,'closed'])
                if command=='fmt':self.assertEqual(result.stdout,Framework.format_source(source))
            result=subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()),[42,'closed'])
        page=Documentation().page('tasks')
        self.assertIn('task_group',page['markdown']);self.assertIn('chaos-token-',page['html'])

    def test_member_alias_read_only_and_json_host_boundary(self):
        source='async fn value(){42};let g=task_group();let job=async with g as workers{let start=getattr(workers,"spawn");start(value())};[type_of(g),hasattr(g,"spawn"),await job]'
        self.assertEqual(Framework.evaluate(source),['TaskGroup',True,42])
        for expression in ('g.spawn=42','setattr(g,"spawn",42)','delattr(g,"spawn")'):
            with self.subTest(expression=expression):
                with self.assertRaises(LanguageError) as caught:Framework.evaluate('let g=task_group();'+expression)
                self.assertEqual(caught.exception.code,'attribute_error')
        seen=[]
        value=Evaluator(tool_handler=lambda name,value:seen.append(value)).run('let g=task_group();tool("inspect",{group:g});type_of(g)')
        self.assertEqual(value,'TaskGroup')
        self.assertEqual(seen[0]['group']['$handle'],'TaskGroup')
        self.assertIsInstance(seen[0]['group'],dict)
        from chaos.task_groups import create
        owner=Evaluator();other=Evaluator();owner.suspension.begin();other.suspension.begin()
        try:
            group=create(owner)
            self.assertEqual(serializable(group)['$handle'],'TaskGroup')
            with self.assertRaises(LanguageError):other.host_value({'group':group})
        finally:owner.suspension.finish();other.suspension.finish()

if __name__=='__main__':unittest.main()
