"""Resource factory hints use sealed module identity and inert yield metadata."""
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp import Job, Server
from chaos.lsp_adapters import TOKEN_TYPES, convert
from chaos.lsp_workspace import Workspace
from chaos.native_specs import spec_for
from chaos.program import plain
from chaos.source_format import format_source
from chaos.suspension import Scheduler


PREFIX='import "std/context" as Context;\n'


class GeneratorContextIDE(unittest.TestCase):
    def result(self, source, **kwargs):
        result=analyze(source,**kwargs)
        self.assertTrue(result['complete'],result['diagnostics'])
        self.assertEqual(result['diagnostics'],[])
        return result

    def symbols(self, source, **kwargs):
        return {item['name']:item for item in self.result(source,**kwargs)['symbols']}

    def test_native_spec_matches_callable_and_known_flag_controls_manager_type(self):
        native=Evaluator().env.get('generator_context');spec=spec_for('generator_context')
        actual=list(inspect.signature(native.call).parameters.values())
        self.assertEqual(native.spec_id,spec.id)
        self.assertEqual([p.name for p in actual],[p.name for p in spec.parameters])
        self.assertEqual([p.default is not p.empty for p in actual],[p.optional for p in spec.parameters])
        self.assertIn('asynchronous: Bool = false',builtin_symbols()['generator_context'].detail)
        source='fn* g()->String{yield 42;"done"}let convert=generator_context;let scope=convert(g());with scope as value{value}'
        symbols=self.symbols(source)
        self.assertEqual(symbols['scope']['type'],'ContextManager');self.assertEqual(symbols['value']['type'],'Int')
        source='async fn* g(){yield "ok"}let scope=generator_context(generator=g(),asynchronous=true);async with scope as value{value}'
        symbols=self.symbols(source)
        self.assertEqual(symbols['scope']['type'],'AsyncContextManager');self.assertEqual(symbols['value']['type'],'String')
        self.assertTrue(any(item['code']=='type_mismatch' for item in analyze('generator_context(42,asynchronous="yes")')['diagnostics']))

    def test_decorated_factory_preserves_parameters_and_yield_record(self):
        source=PREFIX+'@Context.contextmanager\nfn* resource(seed:Int,*,extra:Int=2)->String{yield {answer:seed+extra};"done"}\nwith resource(40,extra=2) as value{value.answer}'
        result=self.result(source,position=source.index('extra=2)')+len('extra='))
        self.assertEqual(result['signatureHelp']['activeParameter'],1)
        self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['seed','extra'])
        self.assertIn('ContextManager',result['signatureHelp']['label'])
        self.assertEqual(self.symbols(source)['value']['type'],'Record')
        result=self.result(source,position=source.rindex('value.answer')+len('value.ans'))
        self.assertIn('answer',{item['label'] for item in result['completions']})
        self.assertIn('Int',result['hover']['contents'])

    def test_async_factory_hint_is_manager_without_implicit_await(self):
        source=PREFIX+'@Context.asynccontextmanager\nasync fn* resource(label:String)->Int{yield label;7}\nlet scope=resource("ok");async with scope as value{value}'
        symbols=self.symbols(source)
        self.assertEqual(symbols['scope']['type'],'AsyncContextManager')
        self.assertEqual(symbols['value']['type'],'String')

    def test_import_alias_direct_call_and_reexport_retain_real_identity(self):
        library=PREFIX+'let wrap=Context.contextmanager;export {wrap}'
        source='import "factory.chaos" as Library;let decorate=Library.wrap;fn* raw(n:Int){yield n};let resource=decorate(raw);with resource(42) as value{value}'
        self.assertEqual(self.symbols(source,files={'factory.chaos':library})['value']['type'],'Int')
        result=self.result(source,files={'factory.chaos':library},position=source.index('resource(42)')+len('resource('))
        self.assertEqual(result['signatureHelp']['parameters'][0]['name'],'n')

    def test_shadowed_names_and_mutable_aliases_are_not_trusted(self):
        cases=[
            'fn contextmanager(factory){factory}\n@contextmanager\nfn* resource(){yield 42}\nwith resource() as value{value}',
            'let Context={contextmanager:fn(factory){factory}};\n@Context.contextmanager\nfn* resource(){yield 42}\nwith resource() as value{value}',
            PREFIX+'var decorate=Context.contextmanager;\n@decorate\nfn* resource(){yield 42}\nwith resource() as value{value}',
        ]
        for source in cases:
            with self.subTest(source=source):self.assertEqual(self.symbols(source)['value']['type'],'Any')

    def test_yield_hints_ignore_completion_nested_functions_and_unknown_delegation(self):
        for body,expected in [
            ('fn* nested(){yield "unrelated"};yield 42;return "done"','Int'),
            ('if input{yield 42}else{yield "other"};return "done"','Any'),
            ('yield from [42];return "done"','Any'),
            ('yield input;return "done"','Any'),
        ]:
            source=PREFIX+'@Context.contextmanager\nfn* resource()->String{'+body+'}\nwith resource() as value{value}'
            with self.subTest(body=body):self.assertEqual(self.symbols(source)['value']['type'],expected)

    def test_methods_static_and_class_methods_keep_receiver_and_yield_contracts(self):
        source=PREFIX+'''class Resources {
@Context.contextmanager
fn* resource(self,seed:Int,*,extra:Int=2){yield seed+extra}
@staticmethod
@Context.contextmanager
fn* static(seed:Int){yield seed}
@classmethod
@Context.contextmanager
fn* shared(cls,seed:Int){yield seed}
}
with Resources().resource(40,extra=2) as first{first};
with Resources.static(42) as second{second};
with Resources.shared(42) as third{third}'''
        symbols=self.symbols(source)
        self.assertEqual([symbols[name]['type'] for name in ('first','second','third')],['Int']*3)
        for expression,parameter in [('resource(40,extra=2)','seed'),('static(42)','seed'),('shared(42)','seed')]:
            result=self.result(source,position=source.index(expression)+expression.index('(')+1)
            self.assertEqual(result['signatureHelp']['parameters'][0]['name'],parameter)

    def test_pattern_parameter_and_builtin_alias_shadowing(self):
        source=PREFIX+'@Context.contextmanager\nfn* resource(pair as [left,right]:List){yield left+right}\nwith resource([20,22]) as value{value}'
        result=self.result(source,position=source.index('resource([20')+len('resource('))
        self.assertIn('pair as [left, right]',result['signatureHelp']['label'])
        source='fn generator_context(generator,asynchronous=false){42}fn* stream(){yield 1};with generator_context(stream()) as value{value}'
        self.assertEqual(self.symbols(source)['value']['type'],'Any')

    def test_editor_lsp_formatter_and_document_highlight_do_not_execute(self):
        source=PREFIX+'@Context.contextmanager\nfn* resource(seed:Int,*,extra:Int=2)->String{yield {answer:seed+extra};"done"}\nwith resource(40,extra=2) as value{value.answer}'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace=Workspace([root]);uri=(root/'main.chaos').as_uri()
            workspace.open({'uri':uri,'languageId':'chaos','version':1,'text':source})
            snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            with patch.object(Evaluator,'run',side_effect=AssertionError('must not run')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('must not evaluate')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('must not advance')):
                result=self.result(source,position=source.index('extra=2)')+len('extra='),files=files,entry=entry)
                signature=convert('textDocument/signatureHelp',result,snapshot,files)
                self.assertEqual(signature['activeParameter'],1)
                self.assertIn('ContextManager',signature['signatures'][0]['label'])
                tokens=convert('textDocument/semanticTokens/full',result,snapshot,files)['data']
                self.assertEqual(len(tokens)%5,0);self.assertIn(TOKEN_TYPES.index('function'),tokens)
                formatted=Server(None,None)._calculate(Job(1,'textDocument/formatting',{'options':{'tabSize':2,'insertSpaces':True}},snapshot))[0]['newText']
                self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
                self.assertEqual(format_source(formatted),formatted)
                html=render_code(source,'chaos')
                self.assertIn('chaos-token-function',html);self.assertIn('ContextManager',html)
            self.assertFalse((root/'main.chaos').exists())


if __name__=='__main__':unittest.main()
