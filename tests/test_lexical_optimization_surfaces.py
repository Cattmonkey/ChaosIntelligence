"""Real immutable lexical specialization, observables and public entry points."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings, ToolCall, compile_program
from chaos.language import Evaluator, LanguageError, Node
from chaos.modules import ProgramBundle
from chaos.program import format_program, parse_program, plain
from chaos.server import make_server
from chaos.suspension import CHECKPOINT

ROOT=Path(__file__).resolve().parents[1]
SOURCE='let factor=7;let alias=factor;fn work(value:Int){value+(alias*6)};[work(0),work(1)]'


def stage(report):
    return next(row for row in report['passes']if row['name']=='literal-operation-specialization')


def observe(source,enabled,limit=100000,cancel=None):
    trace=[];debug=[];e=Evaluator(optimize_literals=enabled,step_limit=limit,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda n,env,frames:debug.append((n[0],dict(e.current_span),e.steps))
    try:value=('value',e.run(source))
    except LanguageError as error:value=('error',error.record())
    return value,e.steps,trace,debug


class LexicalOptimizationSurfaces(unittest.TestCase):
    def test_actual_closure_alias_eliminates_multiplication_without_eliminating_reads(self):
        import chaos.operators as operators
        original=operators.primitive;counts=[];observations=[]
        for enabled in (False,True):
            trace=[];e=Evaluator(optimize_literals=enabled,trace_handler=trace.append)
            with patch('chaos.operators.primitive',wraps=original)as primitive:
                self.assertEqual(e.run(SOURCE),[42,43]);counts.append(primitive.call_count)
                calls=[c.args[:3]for c in primitive.call_args_list]
            observations.append((e.steps,trace))
            self.assertEqual(e.optimization_info()['appliedOperations'],2 if enabled else 0)
            self.assertEqual(sum(c==('*',7,6)for c in calls),0 if enabled else 2)
            self.assertEqual(sum(c[0]=='+'for c in calls),2)
        self.assertEqual(counts,[4,2]);self.assertEqual(observations[0],observations[1])

    def test_every_budget_and_cancel_threshold_preserves_original_error_debug_and_trace(self):
        source='let k=7;let alias=k;fn work(){alias*6};work()'
        total=observe(source,False)[1]
        for limit in range(total+2):
            with self.subTest(limit=limit):self.assertEqual(observe(source,True,limit=limit),observe(source,False,limit=limit))
        for cancel in range(total+2):
            with self.subTest(cancel=cancel):self.assertEqual(observe(source,True,cancel=cancel),observe(source,False,cancel=cancel))

    def test_mutable_capture_snapshot_shadow_defaults_and_errors_match_reference(self):
        cases=[
            ('let k=7;fn make(){let g=fn(){k*6};let k=8;g()};make()',42),
            ('var k=7;fn work(){k*6};let first=work();k=8;[first,work()]',[42,48]),
            ('let k=7;fn work(k=k*6){k*2};[work(),work(3)]',[84,6]),
            ('let k=7;fn work(k){k*6};[work(8),work(7)]',[48,42]),
            ('let k=7;fn work(){try{if false{1/0}else{k*6}}finally{null}};work()',42)]
        for source,expected in cases:
            with self.subTest(source=source):
                self.assertEqual(observe(source,True),observe(source,False))
                self.assertEqual(observe(source,True)[0],('value',expected))

    def test_changed_initializer_keeps_operator_identity_and_guard_miss_falls_back(self):
        source='let k=7;fn work(){k*6};[work(),work()]'
        bundle=ProgramBundle(source);tree=bundle.expanded_trees['main.chaos']
        original=tree[1][0]
        tree[1][0]=Node(('let',original[1],original[2],Node(('literal',8))))
        e=Evaluator();self.assertEqual(e.run(bundle),[48,48])
        self.assertGreaterEqual(e.optimization_info()['guardMisses'],2)
        self.assertEqual(e.optimization_info()['appliedOperations'],0)
        self.assertEqual(Evaluator(optimize_literals=False).run(bundle),[48,48])

    def test_loop_checkpoint_sequence_and_cold_async_generator_results_are_unchanged(self):
        snapshots=[]
        for enabled in (False,True):
            e=Evaluator(optimize_literals=enabled)
            fn=e.run('let k=7;fn(n){var i=0;var total=0;while i<n{total=total+(k*6);i=i+1};total}')
            e.steps=0;requests=[];iterator=e._s_invoke(fn,[3])
            while True:
                try:self.assertIs(next(iterator),CHECKPOINT);requests.append((e.steps,dict(e.current_span)))
                except StopIteration as done:snapshots.append((done.value,e.steps,requests));break
        self.assertEqual(snapshots[0],snapshots[1]);self.assertEqual(snapshots[0][0],126)
        source='let k=7;async fn task(){await sleep(0);k*6};fn* stream(){yield k*6};[await task(),collect(stream())]'
        self.assertEqual(observe(source,True),observe(source,False))

    def test_actual_agent_tool_runs_effects_once_and_uses_original_callable(self):
        import chaos.operators as operators
        registry=Registry();seen=[]
        @registry.tool('fixture.audit')
        def audit(value,ctx):seen.append(value);return value
        @registry.provider('fixture.lexical')
        def choose(request,ctx):
            return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        source='''let k=7;let alias=k;
fn work(value:Int){tool("fixture.audit","before");let out=value+(alias*6);tool("fixture.audit","after");out};
agent worker{provider:"fixture.lexical",tools:["fixture.audit"]};
await agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.audit"])])(0)'''
        with patch('chaos.operators.primitive',wraps=operators.primitive)as primitive,Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('p',source=source,capabilities=['fixture.audit'])
            self.assertEqual(app.start('p',1,None,mode='source').result(8),42)
        self.assertEqual(seen,['before','after'])
        self.assertFalse(any(c.args[:3]==('*',7,6)for c in primitive.call_args_list))

    def test_module_macro_program_ir_and_http_sdk_have_one_static_algorithm(self):
        source='import "lib.chaos" as L;[L.work(),L.work()]'
        files={'lib.chaos':'macro times(x){quote{unquote(x)*6}};let k=7;fn work(){times!(k)};export {work}'}
        e=Evaluator();self.assertEqual(e.run(source,files=files),[42,42])
        self.assertGreaterEqual(e.optimization_info()['appliedOperations'],2)
        self.assertEqual(stage(compile_program(source,files))['algorithm'],'guarded-total-int/2')
        bundle=ProgramBundle(SOURCE);before=plain(bundle.expanded_trees)
        self.assertEqual(Evaluator().run(bundle),[42,43]);self.assertEqual(plain(bundle.expanded_trees),before)
        ir=parse_program(SOURCE);self.assertEqual(parse_program(format_program(ir))['semanticHash'],ir['semanticHash'])
        with tempfile.TemporaryDirectory()as directory,Framework(settings=Settings(':memory:'))as app:
            root=Path(directory);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                report=client.compile(SOURCE);self.assertEqual(stage(report),stage(Framework.compile(SOURCE)))
                self.assertEqual(stage(report)['algorithm'],'guarded-total-int/2')
                self.assertNotIn('appliedOperations',stage(report))
                client.create_project('p',source=SOURCE,capabilities=[])
                self.assertEqual(client.start('p',1,None,mode='source').result(8),[42,43])
                self.assertEqual(client.start('p',1,None,mode='graph').result(8),[42,43])
            finally:server.shutdown();worker.join(3);server.server_close()

    def test_real_cli_compile_and_run_from_external_directory(self):
        with tempfile.TemporaryDirectory()as directory:
            file=Path(directory)/'main.chaos';file.write_text(SOURCE)
            results={}
            for command in ('compile','run'):
                p=subprocess.run([sys.executable,'-B','-m','chaos',command,str(file)],cwd=directory,
                    env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
                self.assertEqual(p.returncode,0,p.stderr);results[command]=json.loads(p.stdout)
            self.assertEqual(results['run'],[42,43]);self.assertEqual(stage(results['compile'])['algorithm'],'guarded-total-int/2')
            self.assertEqual(file.read_text(),SOURCE)
