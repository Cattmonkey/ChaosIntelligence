"""Default execution frames drive the existing language and every shared entry."""
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
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native, Parser
from chaos.modules import ProgramBundle, STDLIB
from chaos.native_specs import all_specs
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'examples/execution-frames.chaos').read_text()
EXPECTED={'ordinary':[42,'ordinary'],'events':['left','right'],'code':[42,'code'],
          'task':[42,'task'],'again':[42,'task'],'before':0,'calls':1}
TEAM={'kind':'team','name':'frame crew','entry':'work','roles':[{'id':'work','parent':None,
      'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}


@contextmanager
def observe_cursors():
    """Observe the real iterator without changing results, scheduling or effects."""
    seen={'entries':0,'cursors':set(),'owners':set(),'frames':set()}
    def record(cursor):
        seen['entries']+=1;seen['cursors'].add(id(cursor));seen['owners'].add(type(cursor.owner).__name__)
        seen['frames'].update(type(frame).__name__ for frame in cursor.frames)
    original_send=ExecutionCursor.send;original_next=ExecutionCursor.__next__
    def send(cursor,*args,**kwargs):
        record(cursor)
        try:return original_send(cursor,*args,**kwargs)
        finally:record(cursor)
    def advance(cursor):
        record(cursor)
        try:return original_next(cursor)
        finally:record(cursor)
    with patch.object(ExecutionCursor,'send',new=send),patch.object(ExecutionCursor,'__next__',new=advance):
        yield seen


def nodes(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from nodes(child)
    elif isinstance(value,list):
        for child in value:yield from nodes(child)


class ExecutionFrameSurfaces(unittest.TestCase):
    def assert_cursor(self,seen):
        self.assertGreater(seen['entries'],0)
        self.assertEqual(seen['owners'],{'Evaluator'})
        self.assertTrue({'OperandFrame','InvocationFrame'}.issubset(seen['frames']),seen)

    def test_actual_default_source_and_graph_execute_same_cursor_without_replay(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_cursors()as seen,Framework(settings=Settings(':memory:'))as app:
                project=app.create_project('frame-'+mode,source=SOURCE,capabilities=[])
                run=app.start(project['id'],project['version'],None,mode=mode)
                self.assertEqual(run.result(6),EXPECTED)
            self.assert_cursor(seen)
            self.assertGreaterEqual(len(seen['cursors']),2)  # main holder and cold Task holder

    def test_source_ir_dialects_and_parser_keep_the_same_real_program(self):
        for version in (1,2):
            with self.subTest(version=version),observe_cursors()as seen:
                ir=parse_program(SOURCE,language_version=version);formatted=format_program(ir)
                self.assertEqual(plain(decode_program(ir)),plain(Parser(formatted,legacy_items=version==1).program()))
                self.assertEqual(parse_program(formatted,language_version=version)['semanticHash'],ir['semanticHash'])
                self.assertEqual(Evaluator().run(ProgramBundle(formatted,language_versions={'main.chaos':version})),EXPECTED)
            self.assert_cursor(seen)

    def test_inert_ide_lsp_compiler_formatter_and_highlight_do_not_drive_cursor(self):
        with patch.object(ExecutionCursor,'send',side_effect=AssertionError('analysis drove execution')), \
             patch.object(ExecutionCursor,'__next__',side_effect=AssertionError('analysis advanced execution')):
            self.assertEqual(analyze(SOURCE)['diagnostics'],[])
            self.assertIn('call',compile_program(SOURCE)['summary']['effects'])
            formatted=format_source(SOURCE,line_width=52)
            self.assertIn('chaos-token-function',render_code(SOURCE,'chaos'))
            self.assertEqual(format_source(formatted,line_width=52),formatted)
        with observe_cursors()as seen:self.assertEqual(Framework.evaluate(formatted),EXPECTED)
        self.assert_cursor(seen)

    def test_generated_code_macro_program_module_and_compiler_use_default_cursor(self):
        function='fn(value:Int)->Int{return_values(value+1,"generated")}'
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':function.replace('fn(value','fn report(value')+';export{report};'},'languageVersions':{'main.chaos':2}}
        sources=['let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){f(41)})',
                 'let M=module_from(program_from('+json.dumps(definition)+'));multiple_value_list(fn(){M.report(41)})',
                 'macro invoke(f,x){quote{unquote(f)(unquote(x))}};let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){invoke!(f,41)})']
        sources.extend('fn report(value:Int)->Int{return_values(value+1,"generated")};let f=compile_function(report,target="'+target+'");multiple_value_list(fn(){f(41)})'for target in ('interpreter','auto','auto-flow'))
        for source in sources:
            with self.subTest(source=source),observe_cursors()as seen:self.assertEqual(Framework.evaluate(source),[42,'generated'])
            self.assert_cursor(seen)

    def test_cli_and_real_stdio_lsp_keep_original_language_entry(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'main.chaos';path.write_text(SOURCE)
            for action in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-B','-m','chaos',action,str(path)],cwd=directory,
                                      env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                if action=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
            client=StdioClient(Path(directory))
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':SOURCE}})
                at=SOURCE.index('report(mark("left",4),')+len('report(mark("left",4),')
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(len(SOURCE[:at].encode('utf-16-le'))//2)}
                self.assertIn('right',client.request('textDocument/signatureHelp',params)['signatures'][0]['label'])
                self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data'])
            finally:client.close()
            self.assertEqual(path.read_text(),SOURCE)

    def test_sdk_http_ir_edit_and_snapshot_share_default_execution(self):
        with tempfile.TemporaryDirectory()as directory,observe_cursors()as seen,Framework(settings=Settings(':memory:'))as app:
            root=Path(directory);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('frames-http',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(6),EXPECTED)
                self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(6),EXPECTED)
                ir=client.program(project['id'])['program']
                target=next(node for node in nodes(ir['root'])if node.get('kind')=='literal'and node.get('value')==4)
                saved=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':5}])
                changed=dict(EXPECTED,ordinary=[52,'ordinary']);self.assertEqual(client.start(project['id'],saved['version'],None,mode='graph').result(6),changed)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(saved['source']),Framework.analyze(saved['source']))
                self.assertEqual(client._request('GET','/docs/execution-frames'),Documentation().page('execution-frames'))
            finally:server.shutdown();thread.join(3);server.server_close()
        self.assert_cursor(seen)

    def test_agent_tool_actual_language_function_uses_default_invocation_frames(self):
        registry=Registry();outputs=[]
        @registry.provider('fixture.frames')
        def choose(request,context):
            context.checkpoint()
            if request.step==1:return ToolCall('work',request.input)
            output=request.history[-1]['output'];outputs.append(output);return Final(output)
        source='fn add(value:Int)->Int{value+1};fn work(value:Int)->Int{return_values(add(value),"tool")};agent worker{provider:"fixture.frames",tools:[]};await agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int")])(41)'
        with observe_cursors()as seen,Framework(registry=registry,settings=Settings(':memory:'))as app:
            project=app.create_project('frame-tool',source=source,capabilities=[])
            self.assertEqual(app.start(project['id'],project['version'],None).result(6),42)
        self.assertEqual(outputs,[42]);self.assert_cursor(seen)

    def test_async_team_full_results_and_completed_task_do_not_replay_role(self):
        source='var calls=0;fn add(value:Int)->Int{value+1};async fn work(value:Int)->Int{calls=calls+1;await sleep(0);return_values(add(value),"team")};let crew=team_bind(team_from('+json.dumps(TEAM)+'),{work:work});let pending=crew(41);let before=calls;let result=multiple_value_list(async fn(){await pending});[before,result,multiple_value_list(pending),calls]'
        with observe_cursors()as seen,Framework(settings=Settings(':memory:'))as app:
            project=app.create_project('frame-team',source=source,capabilities=[]);run=app.start(project['id'],project['version'],None)
            self.assertEqual(run.result(6),[0,[42,'team'],[42,'team'],1])
            events=[e for e in app.events(project['id'],run_id=run.id)['items']if e['type'].startswith('team.role.')]
        self.assertEqual([e['type']for e in events],['team.role.started','team.role.completed']);self.assert_cursor(seen)

    def test_documentation_registers_real_examples_and_no_capture_api(self):
        docs=Documentation();page=docs.page('execution-frames');self.assertIn('不透明',page['markdown'])
        self.assertIn('call/cc',page['markdown']);self.assertIn('chaos-token-function',page['html'])
        self.assertEqual(len(docs.entries()),101);self.assertEqual(len(json.loads((ROOT/'docs/examples.json').read_text())),408)
        natives=[value for value in Evaluator().env.values.values()if type(value)is Native]
        self.assertEqual(len(natives),335);self.assertEqual(len(all_specs()),381);self.assertEqual(len(STDLIB),21)
        self.assertNotIn('Continuation',Evaluator().env.values)
        rows=json.loads((ROOT/'docs/examples.json').read_text());ids={row['id']for row in rows}
        self.assertTrue({'execution-frame-order','execution-frame-cold-results','execution-frame-code-macro','execution-frame-flow'}.issubset(ids))
