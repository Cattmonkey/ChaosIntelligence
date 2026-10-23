"""Typed loop state is shared by source, graph and every ordinary callable entry."""
from contextlib import contextmanager
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
from chaos.code_presentation import render_code
from chaos.documentation import Documentation
from chaos.execution_frames import ExecutionCursor
from chaos.execution_loops import LoopFrame, LoopCompletionFrame
from chaos.ide_language import analyze
from chaos.language import Evaluator, LanguageError, Native
from chaos.modules import STDLIB
from chaos.native_specs import all_specs
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.source_format import format_source

ROOT=Path(__file__).resolve().parents[1]
SOURCE='''async fn* rows(){yield (20,);yield (22,)};
fn sum(values){var total=0;for (value,) in values{total+=value}else{total}};
let ordinary=sum([(20,),(22,)]);
var n=0;let counted=while n<2{n+=1}else{n};
var total=0;let asynchronous=async for (value,) in rows(){total+=value}else{total};
{ordinary:ordinary,counted:counted,asynchronous:asynchronous}'''
EXPECTED={'ordinary':42,'counted':2,'asynchronous':42}


@contextmanager
def observe_cursors():
    seen={'entries':0,'owners':set(),'frames':set()}
    original_send=ExecutionCursor.send;original_next=ExecutionCursor.__next__
    def record(cursor):
        seen['entries']+=1;seen['owners'].add(type(cursor.owner).__name__)
        seen['frames'].update(type(frame).__name__ for frame in cursor.frames)
    def send(cursor,*args,**kwargs):
        record(cursor)
        try:return original_send(cursor,*args,**kwargs)
        finally:record(cursor)
    def advance(cursor):
        record(cursor)
        try:return original_next(cursor)
        finally:record(cursor)
    with patch.object(ExecutionCursor,'send',new=send),patch.object(ExecutionCursor,'__next__',new=advance):yield seen


@contextmanager
def reference_loops(enabled):
    # Disable both routing sets for the whole execution. Child loops reached
    # through the old opaque bridge must also use the original implementation.
    if enabled:yield
    else:
        with patch.object(LoopFrame,'KINDS',set()),patch.object(LoopCompletionFrame,'KINDS',set()):yield


def observation(source,enabled,*,limit=100000,cancel=None):
    trace=[];debug=[];effects=[]
    e=Evaluator(step_limit=limit,trace_handler=trace.append,tool_handler=lambda n,v:effects.append((n,v)))
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda n,env,frames:debug.append((n[0],dict(e.current_span),e.steps))
    with reference_loops(enabled):
        try:value=('value',e.run(source))
        except LanguageError as error:value=('error',error.record())
    return value,e.steps,trace,debug,effects


def nodes(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from nodes(child)
    elif isinstance(value,list):
        for child in value:yield from nodes(child)


class LoopFrameSurfaces(unittest.TestCase):
    def assert_cursor(self,seen):
        self.assertGreater(seen['entries'],0);self.assertEqual(seen['owners'],{'Evaluator'})
        self.assertTrue({'LoopFrame','LoopCompletionFrame'}.issubset(seen['frames']),seen)

    def test_actual_default_source_graph_and_ir_roundtrip_share_loop_frames(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_cursors()as seen,Framework(settings=Settings(':memory:'))as app:
                project=app.create_project('loops-'+mode,source=SOURCE,capabilities=[])
                self.assertEqual(app.start(project['id'],project['version'],None,mode=mode).result(8),EXPECTED)
            self.assert_cursor(seen)
        ir=parse_program(SOURCE);formatted=format_program(ir)
        self.assertEqual(parse_program(formatted)['semanticHash'],ir['semanticHash'])
        self.assertEqual(Framework.evaluate(formatted),EXPECTED)

    def test_every_budget_and_cancel_threshold_preserves_error_trace_debug_and_steps(self):
        sources=['var n=0;while n<2{n+=1;continue}else{n}',
                 'for x in [1,2]{if x==1{continue}else{break}}else{99}',
                 'var n=0;for (x,) in [(20,),(22,)]{n+=x}else{n}',
                 'async fn* g(){yield 42};var n=0;async for x in g(){n+=x}else{n}',
                 'async fn* g(){yield (42,)};var n=0;async for (x,) in g(){n+=x}else{n}']
        for source in sources:
            total=observation(source,False)[1]
            self.assertEqual(observation(source,True),observation(source,False))
            for limit in range(total+2):
                with self.subTest(source=source,limit=limit):self.assertEqual(observation(source,True,limit=limit),observation(source,False,limit=limit))
            for cancel in range(total+2):
                with self.subTest(source=source,cancel=cancel):self.assertEqual(observation(source,True,cancel=cancel),observation(source,False,cancel=cancel))

    def test_iterator_acquire_next_body_errors_and_effect_order_keep_distinct_boundaries(self):
        prefix='''class C{var n=0;fn __iter__(s){tool("audit","acquire");s}
fn __next__(s){tool("audit","next");s.n+=1;NEXT}}
'''
        cases=[('if s.n==1{{done:false,value:42}}else{{done:true,value:null}}','tool("audit","body")',
                ['acquire','next','body','next','else'],42),
               ('throw "from-next"','tool("audit","body")',['acquire','next'],'from-next'),
               ('{done:false,value:42}','tool("audit","body");throw "from-body"',['acquire','next','body'],'from-body')]
        for next_body,body,expected_events,expected in cases:
            source=prefix.replace('NEXT',next_body)+'try{for x in C(){'+body+'}else{tool("audit","else");42}}catch e{e.value}'
            with self.subTest(next_body=next_body,body=body):
                current=observation(source,True);self.assertEqual(current,observation(source,False))
                self.assertEqual(current[0],('value',expected));self.assertEqual([v for n,v in current[-1]],expected_events)
        for source in ['class C{fn __iter__(s){throw "factory"}};for x in C(){}else{tool("wrong",null)}',
                       'for (x,) in [42]{}else{tool("wrong",null)}']:
            current=observation(source,True);self.assertEqual(current,observation(source,False));self.assertEqual(current[0][0],'error');self.assertEqual(current[-1],[])

    def test_borrowed_iterator_break_cold_stream_close_and_full_else_results(self):
        sources=[('class C{var n=0;var closes=0;fn __iter__(s){s}fn __next__(s){s.n+=1;{done:s.n>2,value:s.n}}fn __close__(s){s.closes+=1;null}};let raw=C();let c=iter(raw);let result=for x in c{break}else{99};[result,next(c),raw.closes]',[None,{'done':False,'value':2},0]),
                 ('var events=[];async fn* g(){try{for x in [1,2]{yield x}else{events=append(events,"else")}}finally{events=append(events,"cleanup")}};let s=g();let cold=anext(s);let before=events;let first=await cold;await aclose(s);[before,first,events]',[[],{'done':False,'value':1},['cleanup']]),
                 ('fn work(){for x in []{}else{return_values(20,22)}};multiple_value_list(work)',[20,22]),
                 ('fn* g(){var n=0;while n<2{n+=1;yield n}else{yield 42}};collect(g())',[1,2,42])]
        for source,expected in sources:
            with self.subTest(source=source):
                current=observation(source,True);self.assertEqual(current,observation(source,False));self.assertEqual(current[0],('value',expected))

    def test_generated_function_module_macro_and_existing_compile_entry_use_loop_frames(self):
        function='fn(value:Int){var total=0;for x in [20,22]{total+=x}else{return_values(total+value,"loop")}}'
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':function.replace('fn(value','fn work(value')+';export{work};'},'languageVersions':{'main.chaos':2}}
        sources=['let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){f(0)})',
                 'let M=module_from(program_from('+json.dumps(definition)+'));multiple_value_list(fn(){M.work(0)})',
                 'macro invoke(f,x){quote{unquote(f)(unquote(x))}};let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){invoke!(f,0)})',
                 function.replace('fn(value','fn work(value')+';let f=compile_function(work,target="interpreter");multiple_value_list(fn(){f(0)})']
        for source in sources:
            with self.subTest(source=source),observe_cursors()as seen:self.assertEqual(Framework.evaluate(source),[42,'loop'])
            self.assert_cursor(seen)

    def test_real_agent_tool_and_bot_callbacks_use_same_loop_state_without_replay(self):
        registry=Registry();outputs=[];effects=[]
        @registry.tool('fixture.loop.audit')
        def audit(value,ctx):effects.append(value);return value
        @registry.provider('fixture.loop')
        def choose(request,ctx):
            ctx.checkpoint()
            if request.step==1:return ToolCall('work',request.input)
            output=request.history[-1]['output'];outputs.append(output);return Final(output)
        source='''fn work(value:Int){var total=value;for x in [20,22]{tool("fixture.loop.audit",x);total+=x}else{total}};
agent worker{provider:"fixture.loop",tools:["fixture.loop.audit"]};
let agent_result=await agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.loop.audit"])])(0);
fn plan(s){for x in [s.input]{return x}else{0}};
fn execute(p,s){var n=0;while n<1{n+=1}else{p}};
fn verify(s){{done:true,context:s.context,output:s.evidence[-1]}};
let b=bot("loop goal",plan,execute,verify);let result=await b(agent_result);[agent_result,result.output]'''
        with observe_cursors()as seen,Framework(registry=registry,settings=Settings(':memory:'))as app:
            project=app.create_project('loop-callables',source=source,capabilities=['fixture.loop.audit'])
            self.assertEqual(app.start(project['id'],project['version'],None).result(8),[42,42])
        self.assertEqual(outputs,[42]);self.assertEqual(effects,[20,22]);self.assert_cursor(seen)

    def test_real_http_sdk_graph_edit_and_old_source_snapshot_keep_same_runtime(self):
        with tempfile.TemporaryDirectory()as directory,observe_cursors()as seen,Framework(settings=Settings(':memory:'))as app:
            root=Path(directory);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('loop-http',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(8),EXPECTED)
                self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(8),EXPECTED)
                ir=client.program(project['id'])['program'];target=next(n for n in nodes(ir['root'])if n.get('kind')=='literal'and n.get('value')==2)
                saved=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':3}])
                self.assertEqual(client.start(project['id'],saved['version'],None,mode='graph').result(8),dict(EXPECTED,counted=3))
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                remote=client.compile(SOURCE);local=Framework.compile(SOURCE)
                self.assertEqual(remote['artifactHash'],local['artifactHash']);self.assertEqual(remote['passes'],local['passes'])
                self.assertEqual(client._request('GET','/docs/execution-frames'),Documentation().page('execution-frames'))
            finally:server.shutdown();worker.join(3);server.server_close()
        self.assert_cursor(seen)

    def test_external_cli_and_inert_editor_tools_preserve_public_contracts(self):
        with patch.object(ExecutionCursor,'send',side_effect=AssertionError('analysis executed source')),patch.object(ExecutionCursor,'__next__',side_effect=AssertionError('analysis advanced source')):
            self.assertEqual(analyze(SOURCE)['diagnostics'],[]);compile_program(SOURCE)
            formatted=format_source(SOURCE,line_width=54);self.assertEqual(format_source(formatted,line_width=54),formatted)
            self.assertIn('chaos-token-keyword',render_code(SOURCE,'chaos'))
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'main.chaos';path.write_text(SOURCE)
            for action in ('check','fmt','ir','compile','run'):
                p=subprocess.run([sys.executable,'-B','-m','chaos',action,str(path)],cwd=directory,
                    env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
                self.assertEqual(p.returncode,0,p.stderr)
                if action=='run':self.assertEqual(json.loads(p.stdout),EXPECTED)
            self.assertEqual(path.read_text(),SOURCE)
        self.assertEqual(len(Documentation().entries()),101);self.assertEqual(len(json.loads((ROOT/'docs/examples.json').read_text())),408)
        self.assertEqual(len([n for n in Evaluator().env.values.values()if type(n)is Native]),335)
        self.assertEqual(len(all_specs()),381);self.assertEqual(len(STDLIB),21)
