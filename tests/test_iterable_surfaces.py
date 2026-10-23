"""One iterable call protocol across source, macros, IR, CLI and HTTP."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Framework, Settings, __version__
from chaos.documentation import Documentation
from chaos.program import parse_program, format_program
from chaos.server import make_server

ROOT = Path(__file__).resolve().parent.parent
SOURCE = '''macro launch(worker,value){quote{unquote(worker)(unquote(value))}}
fn* inputs(values) { for value in values { yield value } }
async fn work(value) { value * 2 }
let jobs = map(fn(value){launch!(work,value)}, inputs(input));
await gather(jobs)'''


class IterableSurfaces(unittest.TestCase):
    def test_macro_module_source_ir_and_formatter_share_execution(self):
        expected = [40, 42]
        self.assertEqual(Framework.evaluate(SOURCE, {'input': [20,21]}), expected)
        self.assertEqual(Framework.evaluate(format_program(parse_program(SOURCE)), {'input': [20,21]}), expected)
        formatted = Framework.format_source(SOURCE, line_width=40, indent_size=4)
        self.assertEqual(Framework.format_source(formatted, line_width=40, indent_size=4), formatted)
        self.assertEqual(Framework.evaluate(formatted, {'input': [20,21]}), expected)
        files = {'arguments.chaos': 'fn* arguments(){yield 20;yield 22};export{arguments}'}
        source = 'import "arguments.chaos" as A;fn add(a,b){a+b};[add(*A.arguments()),apply(add,A.arguments())]'
        self.assertEqual(Framework.evaluate(source, files=files), [42,42])

    def test_http_structure_change_keeps_original_source_and_old_run(self):
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server = make_server('127.0.0.1',0,framework=app,state_dir=Path(directory)/'control',update_home=Path(directory)/'updates')
            thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                client = Client('http://127.0.0.1:'+str(server.server_port))
                project = client.create_project('iterable-surfaces',source=SOURCE,capabilities=[])
                old = client.start(project['id'],1,[20,21]); self.assertEqual(old.result(5),[40,42])
                ir = client.program(project['id'])['program']
                def targets(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal' and value.get('value')==2: yield value['id']
                        for child in value.values(): yield from targets(child)
                    elif isinstance(value,list):
                        for child in value: yield from targets(child)
                ids = list(targets(ir['root'])); self.assertEqual(len(ids),1)
                saved = client.edit_program(project['id'],1,operations=[{'op':'set','id':ids[0],'field':'value','value':3}])
                self.assertEqual(client.start(project['id'],2,[20,21]).result(5),[60,63])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.get_project(project['id'])['source'],saved['source'])
                changes = client.events(project['id'])['items']
                self.assertTrue(any(event['type']=='graph.saved' and event['payload']['version']==2 for event in changes))
            finally:
                server.shutdown(); server.server_close(); thread.join(3)

    def test_cli_repl_and_packaged_example(self):
        source = 'fn* args(){yield 20;yield 22};fn add(a,b){a+b};[add(*args()),apply(add,args())]'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.chaos'; path.write_text(source)
            env = dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result = subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=directory,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run': self.assertEqual(json.loads(result.stdout),[42,42])
                if command=='fmt': self.assertEqual(result.stdout,Framework.format_source(source))
            result = subprocess.run([sys.executable,'-m','chaos','repl'],cwd=directory,env=env,input=source+'\n.quit\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(json.loads(result.stdout.strip()),[42,42])
        self.assertEqual(Framework.evaluate((ROOT/'examples/iterable-workers.chaos').read_text()),[40,42])

    def test_docs_and_headless_analysis_do_not_consume_user_source(self):
        source = 'class Items { fn __iter__(self){throw "analysis must not call this"} };map(fn(x){x},Items())'
        report = Framework.analyze(source)
        self.assertEqual(report['diagnostics'],[])
        page = Documentation().page('iterable-composition')
        self.assertIn('同步',page['markdown']); self.assertIn('chaos-token-function',page['html'])
        self.assertIn('map',page['html']); self.assertEqual(page['version'],__version__)


if __name__=='__main__': unittest.main()
