"""Error causality uses the shared callable/member metadata and existing AST."""
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chaos import Framework
from chaos.code_presentation import render_code
from chaos.compiler import compile_program
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp_adapters import convert
from chaos.lsp_workspace import Workspace
from chaos.native_specs import SHAPES, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.source_format import format_source
from chaos.symbols import references, rename

SOURCE='''// cause is a language value, not a source-text instruction
let original=error(20);
let translated=error(22,cause=original);
let hidden=error(0,cause=null);
let batch=error_group("workers",[translated],cause=original);
[translated.cause.value,translated.suppress_context,hidden.cause,
 hidden.suppress_context,batch.cause==original]'''
EXPECTED=[20,True,None,True,True]


class ErrorChainSurfaces(unittest.TestCase):
    def test_metadata_matches_runtime_names_modes_and_optional_parameters(self):
        env=Evaluator().env
        for name,expected in [('error',['value','cause']),('error_group',['message','errors','cause'])]:
            with self.subTest(name=name):
                spec=spec_for(name);actual=list(inspect.signature(env.get(name).call).parameters.values())
                self.assertEqual([p.name for p in spec.parameters],expected)
                self.assertEqual([p.name for p in actual],expected)
                self.assertEqual([p.default is not p.empty for p in actual],[p.optional for p in spec.parameters])
                self.assertEqual(actual[-1].kind,inspect.Parameter.KEYWORD_ONLY)
                self.assertEqual(spec.parameters[-1].mode,'keyword_only')
                self.assertEqual(spec.parameters[-1].type,'Error | Null')
                self.assertEqual(spec.parameters[-1].default_display,'<implicit>')
                self.assertEqual(spec.result_type,'Error')
        self.assertEqual(spec_for('current_error').result_type,'Any')

    def test_readonly_member_completion_nullable_links_and_type_inference(self):
        expected={'code':'String','message':'String','value':'Any','location':'Record','exceptions':'Tuple',
                  'cause':'Any','context':'Any','suppress_context':'Bool','instance':'Instance'}
        info=builtin_symbols()['error'].result_info
        self.assertEqual({name:item.type for name,item in info.members.items()},expected)
        self.assertEqual(set(expected),{field.name for field in SHAPES['ErrorFields']})
        for field in SHAPES['ErrorFields']:
            self.assertEqual(field.nullable,field.name in ('cause','context'))
        for prefix in ('let e=error(0);e.','fn inspect(e:Error){e.','try{0}catch* _ as e{e.'):
            rows={item['label']:item for item in analyze(prefix)['completions']}
            self.assertEqual(set(rows),set(expected))
            for name in ('cause','context'):
                self.assertIn('Error | Null',rows[name]['detail']);self.assertIn('只读',rows[name]['detail'])
        result=analyze('let e=error(0);let c=e.cause;let x=e.context;let hidden=e.suppress_context;let active=current_error()')
        self.assertEqual(result['diagnostics'],[])
        names={item['name']:item['type'] for item in result['symbols']}
        self.assertEqual([names[name]for name in ('c','x','hidden','active')],['Any','Any','Bool','Any'])
        for name in ('cause','context','suppress_context'):
            self.assertIn('immutable_binding',[item['code']for item in analyze('let e=error(0);e.'+name+'=null')['diagnostics']])

    def test_keyword_signature_known_arguments_aliases_and_local_shadowing(self):
        for source,index in [('error(0,cause=',1),('error_group("x",[error(0)],cause=',2),('let make=error;make(0,cause=',1)]:
            help=analyze(source)['signatureHelp'];self.assertEqual(help['activeParameter'],index)
            self.assertEqual(help['parameters'][index]['name'],'cause')
            self.assertEqual(help['parameters'][index]['type'],'Error | Null')
            self.assertIn('*, cause: Error | Null = <implicit>',help['label'])
        for source in ('error(0)','error(0,cause=null)','error(0,cause=error(1))','error(0,cause=input)',
                       'error_group("x",[error(0)],cause=null)'):
            self.assertEqual(analyze(source)['diagnostics'],[],source)
        for source in ('error(0,null)','error_group("x",[error(0)],null)','error(0,cause=1)',
                       'error(0,cause="bad")','error_group("x",[error(0)],cause=false)'):
            self.assertTrue(analyze(source)['diagnostics'],source)
        source='fn error(value: Int)->Int{value};let answer=error(42)'
        names={item['name']:item['type']for item in analyze(source)['symbols']}
        self.assertEqual(names['answer'],'Int')
        self.assertEqual([item['name']for item in analyze(source+';error(')['signatureHelp']['parameters']],['value'])

    def test_source_ir_and_comments_roundtrip_without_new_syntax(self):
        for width in (40,88):
            formatted=format_source(SOURCE,line_width=width)
            self.assertIn('// cause is a language value',formatted)
            self.assertEqual(format_source(formatted,line_width=width),formatted)
            self.assertEqual(plain(Parser(SOURCE).program()),plain(Parser(formatted).program()))
            self.assertEqual(Framework.evaluate(formatted),EXPECTED)
        program=parse_program(SOURCE);printed=format_program(program)
        self.assertEqual(plain(decode_program(program)),plain(Parser(printed).program()))
        self.assertEqual(Framework.evaluate(printed),EXPECTED)

    def test_macro_hygiene_and_rename_keep_cause_as_existing_keyword_argument(self):
        source='''macro wrap(factory,reason){quote{unquote(factory)(22,cause=unquote(reason))}};
let reason=error(20);let result=wrap!(error,reason);[result.cause==reason,result.cause.value]'''
        for text in (source,format_source(source),format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text),[True,20])
        source='let reason=error(20);let result=error(22,cause=reason);result.cause.value'
        position=source.index('reason')
        report=references(source,position=position)
        self.assertTrue(report['complete']);self.assertEqual(len(report['references']),2)
        changed=rename(source,position=position,new_name='upstream')['files']['main.chaos']
        self.assertIn('cause=upstream',changed);self.assertIn('result.cause.value',changed)
        self.assertEqual(Framework.evaluate(changed),20)

    def test_analyzer_compiler_and_rendered_docs_are_inert(self):
        from chaos import error_values
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran')),\
             patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated')),\
             patch.object(error_values,'create',side_effect=AssertionError('constructed')):
            result=analyze(SOURCE);self.assertEqual(result['diagnostics'],[])
            compile_program(SOURCE)
            html=render_code('let e=error(0,cause=null);e.suppress_context','chaos')
        self.assertIn('cause: Error | Null',html)
        self.assertIn('suppress_context: Bool',html)
        self.assertIn('chaos-token-function',html)

    def test_lsp_signature_and_completion_use_identical_shared_metadata(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder).resolve();uri=(path/'main.chaos').as_uri();workspace=Workspace([path])
            source='let origin=error(20);error(22,cause=origin)'
            workspace.open({'uri':uri,'languageId':'chaos','version':1,'text':source})
            snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            result=analyze(source,position=source.index('cause=')+6)
            signature=convert('textDocument/signatureHelp',result,snapshot,files)
            self.assertEqual(signature['activeParameter'],1)
            self.assertEqual(signature['signatures'][0]['parameters'][1]['label'],'cause')
            source='let e=error(0);e.'
            workspace.change({'uri':uri,'version':2},[{'text':source}])
            snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            result=analyze(source)
            response=convert('textDocument/completion',result,snapshot,files)
            rows={item['label']:item for item in response['items']}
            self.assertIn('Error | Null',rows['context']['detail'])
            self.assertEqual(rows['suppress_context']['kind'],10)


if __name__=='__main__':unittest.main()
