"""Streams preserve agent effects and one source model through host-facing APIs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry
from chaos.ide_language import format_source
from chaos.modules import ProgramBundle
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.settings import Settings

LIBRARY = '''macro invoke(f,x){quote{unquote(f)(unquote(x))}}
macro stream(f,values){quote{async fn*(){for item in unquote(values){yield invoke!(unquote(f),item)}}}}
export{stream}'''
SOURCE = '''import "lib/streams.chaos" as S;
macro invoke(f,x){quote{0}}
agent worker{provider:"increment",tools:[],maxSteps:1};
await acollect(S.stream!(worker,[40,41])())'''

class StreamsIntegration(unittest.TestCase):
    def test_agent_streams_resume_from_feedback_and_effects_overlap(self):
        registry=Registry();barrier=threading.Barrier(2);seen=[]
        def provider(request,context):
            context.checkpoint();barrier.wait(timeout=3)
            seen.append(request.input)
            return Final(request.input+1)
        registry.register_provider(ProviderSpec('increment',provider))
        source='''agent worker{provider:"increment",tools:[],maxSteps:1};
async fn* conversation(initial){let nextInput=yield worker(initial);yield worker(nextInput);return 0}
let left=conversation(20);let right=conversation(30);
let first=await gather([anext(left),anext(right)]);
let second=await gather([asend(left,40),asend(right,41)]);
let finish=await gather([anext(left),anext(right)]);
[first,second,finish]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('feedback',source=source,capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            result=run.result(timeout=10)
            self.assertEqual(result,[[{'done':False,'value':21},{'done':False,'value':31}],
                [{'done':False,'value':41},{'done':False,'value':42}],
                [{'done':True,'value':0},{'done':True,'value':0}]])
            self.assertCountEqual(seen,[20,30,40,41])
            events=app.events(p['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),4)
            self.assertTrue(any(e['type']=='program.task.completed' for e in events))

    def test_cancelling_async_stream_drains_actual_agent_effect(self):
        registry=Registry();entered=threading.Event();exited=threading.Event()
        def provider(request,context):
            entered.set()
            try:
                for _ in range(500):
                    context.checkpoint()
                    threading.Event().wait(.01)
                raise AssertionError('Agent was never cancelled')
            finally:exited.set()
        registry.register_provider(ProviderSpec('cooperative',provider))
        source='agent worker{provider:"cooperative",tools:[],maxSteps:1};async fn* stream(){yield worker(1)};await anext(stream())'
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('cancel_stream',source=source,capabilities=[])
            run=app.start(p['id'],p['version'],None,mode='source')
            try:
                self.assertTrue(entered.wait(3),'Agent effect did not start')
                run.cancel()
                self.assertEqual(run.wait(timeout=5)['status'],'cancelled')
                self.assertTrue(exited.is_set(),'Run became terminal before agent effect exited')
            finally:
                run.cancel()

    def test_exported_macro_builds_agent_stream_without_caller_capture(self):
        registry=Registry();seen=[]
        def provider(request,context):
            context.checkpoint();seen.append(request.input);return Final(request.input+1)
        registry.register_provider(ProviderSpec('increment',provider))
        files={'lib/streams.chaos':LIBRARY}
        bundle=ProgramBundle(SOURCE,files)
        self.assertEqual(bundle.exports['lib/streams.chaos'],[])
        self.assertEqual(bundle.summary()['macroExports']['lib/streams.chaos'],['stream'])
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            p=app.create_project('macro_agents',source=SOURCE,files=files,capabilities=[])
            self.assertEqual(app.start(p['id'],p['version'],None,mode='graph').result(),[41,42])
        self.assertEqual(seen,[40,41])

    def test_public_http_ir_changes_module_without_rewriting_old_run(self):
        main='import "forms.chaos" as F;async fn* values(){yield F.value!()};await acollect(values())'
        library='macro value(){quote{42}};export{value}'
        with tempfile.TemporaryDirectory() as temp,Framework(settings=Settings(':memory:')) as app:
            server=make_server(port=0,framework=app,state_dir=Path(temp)/'control',update_home=Path(temp)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                p=client.create_project('module_edit',source=main,files={'forms.chaos':library},capabilities=[])
                old=client.start(p['id'],p['version'],None,mode='source')
                self.assertEqual(old.result(),[42])
                ir=client.program(p['id'],file='forms.chaos')['program']
                pending=[ir];literal=None
                while pending:
                    value=pending.pop()
                    if isinstance(value,dict):
                        if value.get('kind')=='literal' and value.get('value')==42:literal=value
                        pending.extend(value.values())
                    elif isinstance(value,list):pending.extend(value)
                self.assertIsNotNone(literal)
                p=client.edit_program(p['id'],p['version'],file='forms.chaos',operations=[{'op':'set','id':literal['id'],'field':'value','value':43}])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(),[43])
                self.assertEqual(old.get()['snapshot']['files']['forms.chaos'],library)
                self.assertEqual(old.get()['output'],[42])
                saved=client.snapshot(p['id'])
                self.assertEqual(saved['project']['version'],p['version'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_cli_multifile_streams_have_same_result_as_public_sdk(self):
        main='import "lib/forms.chaos" as F;async fn* values(){yield F.id!(40);yield F.id!(42)};await acollect(values())'
        files={'lib/forms.chaos':'macro id(x){quote{unquote(x)}};export{id}'}
        expected=Framework.evaluate(main,files=files)
        self.assertEqual(expected,[40,42])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'main.chaos';path.write_text(main)
            for name,source in files.items():
                target=Path(temp)/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source)
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)
            for action in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',action,str(path)],cwd=temp,env=env,text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if action=='run':self.assertEqual(json.loads(result.stdout),expected)

    def test_comment_formatter_and_ir_preserve_multifile_macro_streams(self):
        source='// before\nimport "forms.chaos" as F;async fn* g(){yield F.value!()};await acollect(g()) // after\n'
        files={'forms.chaos':'macro value(){quote{42}};export{value}'}
        formatted=format_source(source)
        self.assertIn('// before',formatted);self.assertIn('// after',formatted)
        ir=parse_program(source)
        canonical=format_program(json.loads(json.dumps(ir)))
        for text in (source,formatted,canonical):
            self.assertEqual(Framework.evaluate(text,files=files),[42])

if __name__=='__main__':unittest.main()
