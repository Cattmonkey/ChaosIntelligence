"""Multiple completions use the existing source, IR and inert language services."""
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chaos import Framework, compile_program
from chaos.code_presentation import render_code
from chaos.documentation import Documentation
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native, Parser
from chaos.modules import ProgramBundle
from chaos.native_specs import all_specs, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.source_format import format_source
from chaos.suspension import Scheduler

ROOT=Path(__file__).resolve().parents[1]
SOURCE='''// 🙂 real functions return answer and evidence
import "std/values" as V;
fn report(value:Int)->Int{V.values(value+1,{status:"ready"})}
let primary=report(41);
let captured=V.multiple_value_list(fn(){report(41)});
[primary,captured]'''
EXPECTED=[42,[42,{'status':'ready'}]]
NAMES=('return_values','values_list','multiple_value_list','nth_value','multiple_value_call','multiple_value_bind','multiple_value_prog1')


class MultipleValueSurfaces(unittest.TestCase):
    def symbols(self,source):
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return {item['name']:item for item in result['symbols']}

    def test_exact_native_signatures_and_legacy_record_values(self):
        expected={'return_values':['items'],'values_list':['items'],'multiple_value_list':['producer'],
                  'nth_value':['index','producer'],'multiple_value_call':['function','producers'],
                  'multiple_value_bind':['count','producer','consumer'],'multiple_value_prog1':['first','afters']}
        env=Evaluator().env.values
        for name in NAMES:
            with self.subTest(name=name):
                native=env[name];self.assertEqual(native.spec_id,name)
                self.assertEqual(list(inspect.signature(native.call).parameters),expected[name])
                self.assertEqual([p.name for p in spec_for(name).parameters],expected[name])
                self.assertEqual([p['name'] for p in analyze(name+'(')['signatureHelp']['parameters']],expected[name])
        self.assertEqual(len([v for v in env.values() if type(v)is Native]),335)
        self.assertEqual(len(all_specs()),381)
        self.assertEqual([p.name for p in spec_for('values').parameters],['obj'])
        self.assertEqual(Framework.evaluate('values({answer:42})'),[42])

    def test_primary_inference_preserves_record_and_callable_identity(self):
        source='let zero=return_values();let empty=values_list([]);let number=return_values(42,"meta");let first=values_list([42,"meta"]);let record=return_values({answer:42},0);let call=return_values(values,0);let output=call({answer:42});'
        symbols=self.symbols(source)
        self.assertEqual([symbols[n]['type'] for n in ('zero','empty','number','first','record','call','output')],['Null','Null','Int','Int','Record','Function','List'])
        self.assertEqual({v['label'] for v in analyze(source+'record.')['completions']},{'answer'})
        self.assertEqual([p['name'] for p in analyze(source+'call(')['signatureHelp']['parameters']],['obj'])
        source='fn add(x:Int)->Int{x};let call=values_list([add,0]);call('
        self.assertIn('x: Int',analyze(source)['signatureHelp']['label'])

    def test_declared_callbacks_and_explicit_async_metadata(self):
        source='fn first()->Int{return_values(42,"proof")};async fn async_first()->String{return_values("ok",42)};fn cold()->Task{async_first()};let list=multiple_value_list(first);let selected=nth_value(1,first);let kept=multiple_value_prog1(first);let async_kept=multiple_value_prog1(async_first);let task_kept=multiple_value_prog1(cold);let joined=multiple_value_call(fn(x:Int)->Int{x},first);let bound=multiple_value_bind(1,first,fn(x:Int)->Int{x});'
        symbols=self.symbols(source)
        self.assertEqual([symbols[n]['type'] for n in ('list','selected','kept','async_kept','task_kept','joined','bound')],['List','Any','Int','String','Task','Int','Int'])
        self.assertEqual(analyze('let capture=multiple_value_list;let result=capture(fn(){return_values()})')['diagnostics'],[])

    def test_shadowed_mutable_and_expanded_calls_stay_conservative(self):
        for source in ['fn return_values(x)->String{"own"};let item=return_values(42)',
                       'var create=return_values;let item=create(42)',
                       'var items=[42];let item=values_list(items)',
                       'let item=return_values(*input)']:
            with self.subTest(source=source):
                typ=self.symbols(source)['item']['type']
                self.assertEqual(typ,'String' if source.startswith('fn ') else 'Any')
        for source in ['values_list(42)','nth_value("1",fn(){0})','multiple_value_bind(true,fn(){0},fn(x){x})']:
            self.assertTrue(analyze(source)['diagnostics'],source)

    def test_completed_handle_receivers_keep_terminal_metadata_conservative(self):
        source='async fn task()->Int{return_values(42,"done")};fn* stream()->Int{return_values(42,"done")};async fn* async_stream()->Int{return_values(42,"done")};let t=task();let g=stream();let a=async_stream();let captured=multiple_value_list(g);let selected=nth_value(1,a);let kept=multiple_value_prog1(t);'
        symbols=self.symbols(source)
        self.assertEqual([symbols[n]['type'] for n in ('captured','selected','kept')],['List','Any','Any'])
        # Analysis cannot prove completion and never starts a handle to find out.
        with patch.object(Scheduler,'_s_resume',side_effect=AssertionError('started producer')):
            self.assertEqual(analyze(source)['diagnostics'],[])
        for name,param in [('multiple_value_list','producer'),('nth_value','producer'),('multiple_value_call','producers'),('multiple_value_bind','producer'),('multiple_value_prog1','first'),('multiple_value_prog1','afters')]:
            self.assertEqual(next(p.type for p in spec_for(name).parameters if p.name==param),'Any')

    def test_async_multiple_call_target_stays_cold_task_in_inert_analysis(self):
        source='let t=multiple_value_call(async fn(x:Int)->Int{x},fn(){42});let answer=await t;t'
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed call target')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('started async target')):
            symbols=self.symbols(source)
            self.assertEqual(symbols['t']['type'],'Task')
            self.assertEqual(symbols['answer']['type'],'Int')
        self.assertEqual(Framework.evaluate('var calls=0;let t=multiple_value_call(async fn(x:Int)->Int{calls+=1;x},fn(){42});let before=calls;let answer=await t;[type_of(t),before,answer,calls]'),['Task',0,42,1])

    def test_analysis_compile_format_and_highlight_are_execution_free(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')),patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('called producer')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('resumed task')):
            self.assertEqual(analyze(SOURCE)['diagnostics'],[])
            report=compile_program(SOURCE)
            self.assertIn('call',report['summary']['effects'])
            formatted=format_source(SOURCE,line_width=48)
            self.assertIn('chaos-token-function',render_code(SOURCE,'chaos'))
        self.assertEqual(Framework.evaluate(formatted),EXPECTED)

    def test_original_ir_and_both_dialects_keep_same_completion(self):
        for version in (1,2):
            ir=parse_program(SOURCE,language_version=version)
            formatted=format_program(ir)
            self.assertEqual(plain(decode_program(ir)),plain(Parser(formatted,legacy_items=version==1).program()))
            self.assertEqual(Evaluator().run(ProgramBundle(formatted,language_versions={'main.chaos':version})),EXPECTED)
            self.assertEqual(parse_program(formatted,language_version=version)['semanticHash'],ir['semanticHash'])
        self.assertEqual(format_source(format_source(SOURCE)),format_source(SOURCE))

    def test_code_and_program_modules_return_full_results_on_same_evaluator(self):
        code='let f=function_from(code_from("fn()->Int{return_values(42,\\\"code\\\")}"));multiple_value_list(fn(){f()})'
        self.assertEqual(Framework.evaluate(code),[42,'code'])
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':'fn report()->Int{return_values(42,"module")};export{report};'},'languageVersions':{'main.chaos':2}}
        source='let module=module_from(program_from('+json.dumps(definition)+'));multiple_value_list(fn(){module.report()})'
        self.assertEqual(Framework.evaluate(source),[42,'module'])

    def test_standard_module_aliases_exact_native_and_primary_projection(self):
        source='import "std/values" as V;[V.values==V.return_values,V.multiple_value_list(fn(){V.values()}),V.multiple_value_list(fn(){V.values(null)}),V.values(42,99),values({answer:42})]'
        self.assertEqual(Framework.evaluate(source),[True,[],[None],42,[42]])
        self.assertEqual(self.symbols('import "std/values" as V;let x=V.values(42,99)')['x']['type'],'Int')

    def test_pure_macro_results_and_runtime_quote_keep_native_hygiene(self):
        pure='macro answer(){syntax_literal(multiple_value_call(fn(a:Int,b:Int)->Int{a+b},fn(){return_values(20,22)}))};answer!()'
        quoted='macro pair(capture,emit){quote{unquote(capture)(fn(){unquote(emit)(20,22)})}};let saved_capture=multiple_value_list;let saved_emit=return_values;let return_values=fn(*items){0};pair!(saved_capture,saved_emit)'
        self.assertEqual(Framework.evaluate(pure),42)
        self.assertEqual(Framework.evaluate(quoted),[20,22])
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('runtime producer')):
            self.assertEqual(analyze(pure)['diagnostics'],[])
            self.assertEqual(analyze(quoted)['diagnostics'],[])

    def test_cli_and_real_stdio_lsp_use_shared_metadata(self):
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
                at=SOURCE.index('V.multiple_value_list(')+len('V.multiple_value_list(')
                offset=len(SOURCE[:at].encode('utf-16-le'))//2
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(offset)}
                help=client.request('textDocument/signatureHelp',params)
                self.assertIn('producer',help['signatures'][0]['label'])
                self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data'])
                at=SOURCE.index('primary=')
                params['position']=TextMap(SOURCE).position(len(SOURCE[:at].encode('utf-16-le'))//2)
                self.assertIn('Int',str(client.request('textDocument/hover',params)))
                changed=client.request('textDocument/rename',{**params,'newName':'answer'})
                self.assertEqual(len(changed['documentChanges'][0]['edits']),2)
                self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()

    def test_registered_documentation_and_reference_topic(self):
        from chaos.ide_agent import LANGUAGE_REFERENCE_TOPICS
        self.assertIn('multiple-values',LANGUAGE_REFERENCE_TOPICS)
        page=Documentation().page('multiple-values')
        self.assertIn('return_values',page['markdown'])
        self.assertIn('chaos-token-function',page['html'])

