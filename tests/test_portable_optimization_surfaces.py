"""Actual package, function/task/agent and public compiler entry points."""
import copy
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
from chaos.language import Evaluator
from chaos.modules import ProgramBundle
from chaos.program import parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source

ROOT=Path(__file__).resolve().parents[1]
SOURCE='fn answer(value=6*7){value};answer()'


def optimization(report):
    return next(row for row in report['passes'] if row['name']=='literal-operation-specialization')


class PortableOptimizationSurfaces(unittest.TestCase):
    def test_cross_module_macro_defaults_and_function_bodies_share_one_plan(self):
        source='import "math.chaos" as M;[M.work(),M.work(1)]'
        files={'math.chaos':'macro base(){quote{6*7}};fn work(x=base!()){x+(2*3)};export{work};'}
        for enabled in (False,True):
            e=Evaluator(optimize_literals=enabled)
            self.assertEqual(e.run(source,files=files),[48,7])
            info=e.optimization_info()
            if enabled:self.assertGreater(info['candidateSites'],0)
            self.assertEqual(info['appliedOperations'],3 if enabled else 0)
        report=compile_program(source,files)
        self.assertEqual(len(report['modules']),2)
        self.assertGreaterEqual(optimization(report)['candidateSites'],2)

    def test_async_generator_finally_and_multiple_values_keep_original_semantics(self):
        source='var events=[];async fn work(){try{await sleep(0);return_values(6*7,"task")}finally{events=append(events,"closed")}};fn* inputs(){yield 2*3;yield 4*5};let task=work();let before=events;let answer=multiple_value_list(async fn(){await task});[before,answer,multiple_value_list(task),collect(inputs()),events]'
        expected=[[],[42,'task'],[42,'task'],[6,20],['closed']]
        self.assertEqual(Framework.evaluate(source,optimize_literals=True),expected)
        self.assertEqual(Framework.evaluate(source,optimize_literals=False),expected)

    def test_agent_tool_actual_provider_invokes_optimized_language_callback_once(self):
        import chaos.operators as operators
        registry=Registry();seen=[]
        @registry.provider('fixture.portable')
        def provider(request,context):
            context.checkpoint()
            if request.step==1:return ToolCall('work',request.input)
            output=request.history[-1]['output'];seen.append(output);return Final(output)
        source='fn work(value:Int)->Int{value+(6*7)};agent worker{provider:"fixture.portable",tools:[]};await agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int")])(0)'
        original=operators.primitive
        with patch('chaos.operators.primitive',wraps=original) as primitive,Framework(registry=registry,settings=Settings(':memory:')) as app:
            project=app.create_project('portable-agent-tool',source=source,capabilities=[])
            self.assertEqual(app.start(project['id'],project['version'],None).result(6),42)
        self.assertEqual(seen,[42])
        self.assertFalse(any(call.args[:3]==('*',6,7) for call in primitive.call_args_list))
        self.assertTrue(any(call.args[:3]==('+',0,42) for call in primitive.call_args_list))

    def test_original_source_ir_semantic_hash_and_ast_are_not_rewritten(self):
        bundle=ProgramBundle(SOURCE);before=copy.deepcopy(bundle.expanded_trees)
        ir=parse_program(SOURCE);definition=copy.deepcopy(ir)
        self.assertEqual(Evaluator().run(bundle),42)
        self.assertEqual(plain(bundle.expanded_trees),plain(before))
        self.assertEqual(parse_program(SOURCE),definition)
        self.assertEqual(parse_program(format_source(SOURCE))['semanticHash'],ir['semanticHash'])
        self.assertEqual(bundle.files['main.chaos'],SOURCE)

    def test_static_compiler_reports_candidates_without_claiming_runtime_or_reordering(self):
        report=compile_program(SOURCE);row=optimization(report)
        self.assertEqual(row['status'],'enabled')
        self.assertGreater(row['candidateSites'],0)
        self.assertGreater(row['scannedNodes'],0)
        self.assertNotIn('appliedOperations',row)
        self.assertEqual(next(p for p in report['passes'] if p['name']=='instruction-reordering')['status'],'not-implemented')
        self.assertEqual(report['target'],'portable-ast')
        for field in ('truncated','limits','checkpointPolicy'):self.assertIn(field,row)

    def test_actual_http_sdk_source_graph_and_frozen_snapshot_share_public_report(self):
        with tempfile.TemporaryDirectory() as directory,Framework(settings=Settings(':memory:')) as app:
            root=Path(directory);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                report=client.compile(SOURCE)
                self.assertEqual(optimization(report),optimization(Framework.compile(SOURCE)))
                project=client.create_project('portable-http',source=SOURCE,capabilities=[])
                original=client.start(project['id'],project['version'],None,mode='source')
                self.assertEqual(original.result(6),42)
                self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(6),42)
                self.assertEqual(original.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.program(project['id'])['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:
                server.shutdown();thread.join(3);server.server_close()

    def test_actual_cli_compile_and_run_use_same_packaged_language(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.chaos';path.write_text(SOURCE)
            values={}
            for command in ('compile','run'):
                result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=directory,
                    env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                values[command]=json.loads(result.stdout)
            self.assertEqual(values['run'],42)
            self.assertEqual(optimization(values['compile']),optimization(compile_program(SOURCE)))
            self.assertEqual(path.read_text(),SOURCE)

    def test_native_program_compile_reports_the_same_real_static_pass(self):
        source='let n=6*7;let report=program_compile(current_program());[n,report.passes]';
        with Framework(settings=Settings(':memory:')) as app:
            project=app.create_project('portable-native-report',source=source,capabilities=[])
            result=app.start(project['id'],project['version'],None).result(6)
        self.assertEqual(result[0],42)
        row=next(p for p in result[1] if p['name']=='literal-operation-specialization')
        self.assertEqual(row['status'],'enabled');self.assertGreater(row['candidateSites'],0)
        self.assertNotIn('appliedOperations',row)
