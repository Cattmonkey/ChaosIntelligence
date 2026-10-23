"""Identity control remains one native language across tooling and public interfaces."""
import inspect
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
from chaos.ide_language import analyze
from chaos.language import Evaluator, LanguageError, Native, Parser
from chaos.modules import ProgramBundle, STDLIB
from chaos.native_specs import SHAPES, all_specs, call_spec_for, member_specs_for, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.suspension import Scheduler

ROOT=Path(__file__).resolve().parents[1]
NAMES=('prompt_tag','call_prompt','prompt_scope','abort_prompt','prompt_available','call_escape','escape_info','control_barrier','dynamic_wind')
SOURCE='''// 🙂 identity control and cleanup share real functions
import "std/control" as C;
var events=[];
let tag=C.prompt_tag("answer");
fn report(value:Int)->Int{C.abort_prompt(tag,value+1,{stage:"report"})}
let captured=multiple_value_list(fn(){C.call_prompt(tag,fn(){
 C.dynamic_wind(fn(){events=append(events,"before")},fn(){report(41)},fn(){events=append(events,"after")})
})});
[captured,events,C.prompt_available(tag)]'''
EXPECTED=[[42,{'stage':'report'}],['before','after'],False]
TEAM={'kind':'team','name':'control crew','entry':'work','roles':[{'id':'work','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':[]}}}]}

def nodes(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from nodes(child)
    elif isinstance(value,list):
        for child in value:yield from nodes(child)


class ControlSurfaces(unittest.TestCase):
    def symbols(self,source):
        result=analyze(source);self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return {item['name']:item for item in result['symbols']}

    def test_native_callable_metadata_accepts_escape_callbacks_without_execution(self):
        source='multiple_value_list(fn(){call_escape(fn(exit:EscapeContinuation){call_prompt(prompt_tag(),exit)})})'
        receivers={spec.receiver_type for spec in all_specs().values()if spec.inference_rule=='receiver_call'}
        self.assertIn('Device',receivers);self.assertIn('EscapeContinuation',receivers)
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed callable analysis')),patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('invoked callable analysis')):
            self.assertEqual(analyze(source)['diagnostics'],[])
            for typ in sorted(receivers|{'Class'}):
                with self.subTest(receiver=typ):
                    self.assertEqual(analyze('fn accept(value:Function){null};fn forward(value:'+typ+'){accept(value)}')['diagnostics'],[])
            diagnostics=analyze('fn accept(value:Function){null};fn forward(value:PromptScope){accept(value)}')['diagnostics']
            self.assertTrue(any(d.get('code')=='type_mismatch'for d in diagnostics),diagnostics)
        self.assertEqual(Framework.evaluate(source),[])

    def test_exact_native_signatures_defaults_receiver_and_catalog_counts(self):
        names={'prompt_tag':['name'],'call_prompt':['tag','body','handler'],'prompt_scope':['tag','handler'],
               'abort_prompt':['tag','items'],'prompt_available':['tag'],'call_escape':['body'],
               'escape_info':['continuation'],'control_barrier':['body'],'dynamic_wind':['before','body','after']}
        env=Evaluator().env.values
        for name in NAMES:
            with self.subTest(name=name):
                actual=list(inspect.signature(env[name].call).parameters.values());declared=spec_for(name).parameters
                self.assertEqual(env[name].spec_id,name)
                self.assertEqual([p.name for p in actual],names[name]);self.assertEqual([p.name for p in declared],names[name])
                self.assertEqual([p.default is not p.empty for p in actual],[p.optional for p in declared])
                self.assertEqual([p['name'] for p in analyze(name+'(')['signatureHelp']['parameters']],names[name])
        self.assertEqual(spec_for('prompt_tag').parameters[0].default_display,'""')
        self.assertEqual(spec_for('call_prompt').parameters[-1].default_display,'null')
        self.assertEqual(spec_for('abort_prompt').parameters[-1].mode,'varargs')
        self.assertEqual(len([v for v in env.values()if type(v)is Native]),335);self.assertEqual(len(all_specs()),381)
        escape=call_spec_for('EscapeContinuation');self.assertEqual([p.name for p in escape.parameters],['items'])
        self.assertFalse(escape.compatibility_export);self.assertNotIn('__call__',member_specs_for('EscapeContinuation'))

    def test_exact_readonly_fields_types_and_escape_callable_metadata(self):
        expected={'PromptTag':{'id','name'},'PromptScope':{'id','tag','state','active'},'EscapeContinuation':{'id','scopeId','state','active','available'}}
        for typ,fields in expected.items():
            with self.subTest(type=typ):
                self.assertEqual({f.name for f in SHAPES[typ+'Fields']},fields)
                self.assertEqual({v['label'] for v in analyze('fn read(value:'+typ+'){value.')['completions']},fields)
        self.assertEqual({v['label'] for v in analyze('let scope=prompt_scope(prompt_tag());scope.tag.')['completions']},{'id','name'})
        self.assertEqual({v['label'] for v in analyze('call_escape(fn(exit:EscapeContinuation){escape_info(exit).')['completions']},expected['EscapeContinuation'])
        help=analyze('fn use(exit:EscapeContinuation){exit(')['signatureHelp'];self.assertIsNotNone(help)
        self.assertEqual([p['name'] for p in help['parameters']],['items'])

    def test_control_results_are_conservative_and_with_binding_is_same_scope(self):
        source='let tag=prompt_tag();let scope=prompt_scope(tag);let normal=call_prompt(tag,fn()->Int{42});let escape=call_escape(fn(exit:EscapeContinuation)->String{exit(42);"unreached"});let barrier=control_barrier(fn()->Int{42});let wind=dynamic_wind(fn(){null},fn()->Int{42},fn(){null});let held=with scope as bound{bound.tag.name};'
        symbols=self.symbols(source)
        self.assertEqual([symbols[n]['type'] for n in ('tag','scope','normal','escape','barrier','wind','held')],['PromptTag','PromptScope','Any','Any','Any','Any','String'])
        self.assertEqual(symbols['bound']['type'],'PromptScope')

    def test_aliases_shadowing_and_mutable_factories_follow_real_identity(self):
        self.assertEqual(self.symbols('let make=prompt_tag;let tag=make("x")')['tag']['type'],'PromptTag')
        self.assertEqual(self.symbols('fn prompt_tag(name)->String{name};let tag=prompt_tag("local")')['tag']['type'],'String')
        self.assertEqual(self.symbols('var make=prompt_tag;let tag=make("unknown")')['tag']['type'],'Any')
        self.assertEqual(Framework.evaluate('let make=prompt_tag;let a=make("same");let b=make("same");[a==b,a==a]'),[False,True])
        for source in ['prompt_tag(42)','call_prompt(42,fn(){0})','prompt_scope(prompt_tag(),42)','escape_info(42)','dynamic_wind(42,fn(){0},fn(){0})']:
            self.assertTrue(analyze(source)['diagnostics'],source)

    def test_inert_analysis_compilation_format_and_highlight(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran control')),patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('invoked control')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('advanced task')):
            self.assertEqual(analyze(SOURCE)['diagnostics'],[])
            self.assertIn('call',compile_program(SOURCE)['summary']['effects'])
            formatted=format_source(SOURCE,line_width=48)
            self.assertIn('chaos-token-function',render_code(SOURCE,'chaos'))
        self.assertEqual(Framework.evaluate(formatted),EXPECTED)

    def test_original_call_ir_dialects_and_readable_formatter(self):
        for version in (1,2):
            ir=parse_program(SOURCE,language_version=version);formatted=format_program(ir)
            self.assertEqual(plain(decode_program(ir)),plain(Parser(formatted,legacy_items=version==1).program()))
            self.assertEqual(parse_program(formatted,language_version=version)['semanticHash'],ir['semanticHash'])
            self.assertEqual(Evaluator().run(ProgramBundle(formatted,language_versions={'main.chaos':version})),EXPECTED)
        self.assertEqual(format_source(format_source(SOURCE)),format_source(SOURCE))

    def test_generated_code_program_module_and_native_flow_control(self):
        function='fn(value:Int)->Int{call_escape(fn(exit:EscapeContinuation){exit(value+1,"code")})}'
        source='let f=function_from(code_from('+json.dumps(function)+'));multiple_value_list(fn(){f(41)})'
        self.assertEqual(Framework.evaluate(source),[42,'code'])
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':function.replace('fn(value','fn report(value')+';export{report};'},'languageVersions':{'main.chaos':2}}
        source='let M=module_from(program_from('+json.dumps(definition)+'));multiple_value_list(fn(){M.report(41)})'
        self.assertEqual(Framework.evaluate(source),[42,'code'])
        for target in ('interpreter','auto','auto-flow'):
            source='var events=[];fn report(value:Int)->Int{call_escape(fn(exit:EscapeContinuation){dynamic_wind(fn(){events=append(events,"in")},fn(){exit(value+1,"flow")},fn(){events=append(events,"out")})})};let fast=compile_function(report,target="'+target+'");[multiple_value_list(fn(){fast(41)}),events]'
            with self.subTest(target=target):self.assertEqual(Framework.evaluate(source),[[42,'flow'],['in','out']])

    def test_pure_phase_control_unavailable_but_explicit_runtime_ast_and_shadowing_work(self):
        for name,call in [('prompt_tag','prompt_tag()'),('call_escape','call_escape(fn(exit){exit(42)})'),('control_barrier','control_barrier(fn(){42})')]:
            with self.subTest(name=name),self.assertRaises(LanguageError)as caught:
                Framework.evaluate('macro bad(){syntax_literal('+call+')};bad!()')
            self.assertEqual(caught.exception.code,'macro_error')
        source='macro emit(run,body){quote{unquote(run)(unquote(body))}};import "std/control" as C;let call_escape=fn(body){0};emit!(C.call_escape,fn(exit:EscapeContinuation){exit(42)})'
        self.assertEqual(Framework.evaluate(source),42)
        self.assertEqual(Framework.evaluate('macro local(){let prompt_tag=fn(name){name};syntax_literal(prompt_tag("local"))};local!()'),'local')
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('macro runtime control')):
            self.assertEqual(analyze(source)['diagnostics'],[])

    def test_std_control_exact_aliases_and_documented_registered_examples(self):
        self.assertEqual(len(STDLIB),21)
        source='import "std/control" as C;['+','.join('C.'+n+'=='+n for n in NAMES)+']'
        self.assertEqual(Framework.evaluate(source),[True]*len(NAMES))
        from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
        self.assertIn('control-prompts',LANGUAGE_REFERENCE_TOPICS)
        page=Documentation().page('control-prompts');self.assertIn('call/cc',page['markdown']);self.assertIn('chaos-token-function',page['html'])

    def test_real_stdio_lsp_cli_signature_hover_and_rename(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);path=root/'main.chaos';path.write_text(SOURCE)
            for action in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-B','-m','chaos',action,str(path)],cwd=root,env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                if action=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
            client=StdioClient(root)
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':SOURCE}})
                at=SOURCE.index('C.call_prompt(tag,')+len('C.call_prompt(tag,')
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(len(SOURCE[:at].encode('utf-16-le'))//2)}
                self.assertIn('body',client.request('textDocument/signatureHelp',params)['signatures'][0]['label'])
                at=SOURCE.index('tag=C.prompt_tag')
                params['position']=TextMap(SOURCE).position(len(SOURCE[:at].encode('utf-16-le'))//2)
                self.assertIn('PromptTag',str(client.request('textDocument/hover',params)))
                change=client.request('textDocument/rename',{**params,'newName':'target'})
                self.assertEqual(len(change['documentChanges'][0]['edits']),4)
                self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data'])
                self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()

    def test_http_sdk_graph_ir_edit_snapshots_and_exact_docs(self):
        with tempfile.TemporaryDirectory()as directory,Framework(settings=Settings(':memory:'))as app:
            root=Path(directory);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('control-http',source=SOURCE,capabilities=[])
                old=client.start(p['id'],p['version'],None);self.assertEqual(old.result(5),EXPECTED)
                self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(5),EXPECTED)
                ir=client.program(p['id'])['program'];target=next(n for n in nodes(ir['root'])if n.get('kind')=='literal' and n.get('value')==41)
                saved=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':42}])
                self.assertEqual(client.start(p['id'],saved['version'],None,mode='graph').result(5),[[43,{'stage':'report'}],EXPECTED[1],False])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(saved['source']),Framework.analyze(saved['source']))
                self.assertEqual(client.format_source(SOURCE),Framework.format_source(SOURCE))
                remote=client._request('GET','/docs/control-prompts');self.assertEqual(remote,Documentation().page('control-prompts'))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_actual_agent_tool_and_async_team_control_share_primary_and_full_results(self):
        registry=Registry();outputs=[]
        @registry.provider('choose')
        def choose(request,context):
            context.checkpoint()
            if request.step==1:return ToolCall('work',request.input)
            value=request.history[-1]['output'];outputs.append(value);return Final(value)
        source='fn work(x:Int)->Int{call_escape(fn(exit:EscapeContinuation){exit(x+1,"tool")})};agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("work",work,input_type="Int",output_type="Int")]);await bound(41)'
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            p=app.create_project('control-tool',source=source,capabilities=[]);self.assertEqual(app.start(p['id'],p['version'],None).result(6),42)
        self.assertEqual(outputs,[42])
        source='var calls=0;async fn work(x:Int)->Int{calls+=1;call_escape(async fn(exit:EscapeContinuation){await sleep(0);exit(x+1,"team")})};let crew=team_bind(team_from('+json.dumps(TEAM)+'),{work:work});let cold=crew(41);let before=calls;let first=multiple_value_list(async fn(){await cold});let again=multiple_value_list(cold);[before,first,again,calls]'
        with Framework(settings=Settings(':memory:'))as app:
            p=app.create_project('control-team',source=source,capabilities=[]);run=app.start(p['id'],p['version'],None)
            self.assertEqual(run.result(6),[0,[42,'team'],[42,'team'],1])
            events=[e for e in app.events(p['id'],run_id=run.id)['items']if e['type'].startswith('team.role.')]
            self.assertEqual([e['type']for e in events],['team.role.started','team.role.completed'])
            self.assertEqual(len({e['payload']['invocationId']for e in events}),1)

