"""Native resource stacks retain inert AST-derived resource and task metadata."""
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chaos import Framework
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


class ResourceStackIDE(unittest.TestCase):
    def result(self, source, **kwargs):
        result=analyze(source,**kwargs)
        self.assertTrue(result['complete'],result['diagnostics'])
        self.assertEqual(result['diagnostics'],[])
        return result

    def symbols(self, source, **kwargs):
        return {item['name']:item for item in self.result(source,**kwargs)['symbols']}

    def test_native_factory_contract_and_literal_mode(self):
        native=Evaluator().env.get('exit_stack');spec=spec_for('exit_stack')
        actual=list(inspect.signature(native.call).parameters.values())
        self.assertEqual([item.name for item in actual],[item.name for item in spec.parameters])
        self.assertEqual([item.default for item in actual],[False,1024])
        self.assertEqual(builtin_symbols()['exit_stack'].parameters,[('asynchronous','Bool','positional',True),('max_entries','Int','positional',True)])
        cases=[('exit_stack()','ExitStack'),('exit_stack(false,8)','ExitStack'),('exit_stack(max_entries=8,asynchronous=true)','AsyncExitStack'),('exit_stack(input)','Any'),('exit_stack(*input)','Any'),('exit_stack(if input{true}else{false})','Any')]
        for expression,expected in cases:
            with self.subTest(expression=expression):self.assertEqual(self.symbols('let value='+expression)['value']['type'],expected)

    def test_standard_wrappers_annotations_aliases_and_reexports(self):
        source=PREFIX+'let create=Context.ExitStack;let sync=create(max_entries=4);let async=Context.AsyncExitStack();with sync as first{first};async with async as second{second}'
        symbols=self.symbols(source)
        self.assertEqual([symbols[name]['type'] for name in ('sync','async','first','second')],['ExitStack','AsyncExitStack','ExitStack','AsyncExitStack'])
        source='import "factory.chaos" as Library;let create=Library.create;let stack=create();let moved=stack.pop_all();moved.close()'
        library=PREFIX+'let create=Context.ExitStack;export {create}'
        self.assertEqual(self.symbols(source,files={'factory.chaos':library})['moved']['type'],'ExitStack')
        source='async fn use(stack:AsyncExitStack){let moved=stack.pop_all();let task=moved.aclose();let result=await task;result}'
        symbols=self.symbols(source)
        self.assertEqual([symbols[name]['type'] for name in ('moved','task','result')],['AsyncExitStack','Task','Null'])

    def test_members_and_signature_help_use_real_receiver_nodes(self):
        common={'enter_context','callback','push','pop_all'}
        for expression,extra in [('exit_stack()',{'close'}),('exit_stack(true)',{'enter_async_context','push_async_callback','push_async_exit','aclose'})]:
            source=expression+'.'
            self.assertEqual({item['label'] for item in analyze(source)['completions']},common|extra)
        source='exit_stack(true).enter_async_context(manager=input)'
        result=self.result(source,position=source.index('manager=')+len('manager='))
        self.assertEqual(result['signatureHelp']['parameters'][0]['name'],'manager')
        self.assertIn('-> Task',result['signatureHelp']['label'])
        source='fn done(value:Int,*,suffix:String="!")->String{str(value)+suffix};exit_stack().callback(done,42,suffix="!")'
        result=self.result(source,position=source.rindex('suffix=')+len('suffix='))
        self.assertEqual(result['signatureHelp']['parameters'][0]['mode'],'positional_only')
        self.assertEqual(result['signatureHelp']['parameters'][2]['mode'],'varkw')

    def test_sync_enter_preserves_class_resource_shape_and_definition(self):
        source='class Resource{let answer=42}class Scope{fn __enter__(self)->Resource{Resource()}fn __exit__(self,error)->Bool{false}}let stack=exit_stack();let resource=stack.enter_context(Scope());resource.answer'
        result=self.result(source,position=len(source))
        self.assertEqual(next(item['type'] for item in result['symbols'] if item['name']=='resource'),'Resource')
        self.assertIn('Int',result['hover']['contents'])
        self.assertEqual(result['definition'],{'file':'main.chaos','from':source.index('answer=42'),'to':source.index('answer=42')+6})
        reference=source.index('resource.answer')+3
        self.assertEqual(self.result(source,position=reference)['definition']['from'],source.index('resource=stack'))
        method=self.result(source,position=source.index('enter_context')+5)
        self.assertIsNone(method['definition'])

    def test_async_enter_returns_cold_task_with_resource_completion_metadata(self):
        source='class Resource{let answer=42}class Scope{async fn __aenter__(self)->Resource{Resource()}async fn __aexit__(self,error)->Bool{false}}let stack=exit_stack(true);let enter=stack.enter_async_context;let cold=enter(manager=Scope());let resource=await cold;resource.answer'
        symbols=self.symbols(source)
        self.assertEqual(symbols['cold']['type'],'Task');self.assertEqual(symbols['resource']['type'],'Resource')
        self.assertIn('Int',self.result(source,position=len(source))['hover']['contents'])
        source='async fn use(computer:Computer){let stack=exit_stack(true);let cold=stack.enter_async_context(computer);let resource=await cold;resource}'
        self.assertEqual(self.symbols(source)['resource']['type'],'Computer')

    def test_generator_yield_metadata_survives_both_entry_methods(self):
        for decorator,kind,stack,method,prefix in [('contextmanager','fn*','exit_stack()','enter_context',''),('asynccontextmanager','async fn*','exit_stack(true)','enter_async_context','await ')]:
            source=PREFIX+'@Context.'+decorator+'\n'+kind+' resource(seed:Int)->String{yield {answer:seed};"done"}\nlet stack='+stack+';let value='+prefix+'stack.'+method+'(resource(42));value.answer'
            with self.subTest(decorator=decorator):
                self.assertEqual(self.symbols(source)['value']['type'],'Record')
                self.assertIn('Int',self.result(source,position=len(source))['hover']['contents'])
        source='fn* stream(){yield {answer:42}}let stack=exit_stack(true);let value=stack.enter_context(generator_context(stream()));value.answer'
        self.assertIn('Int',self.result(source,position=len(source))['hover']['contents'])

    def test_callbacks_preserve_original_callable_and_async_result(self):
        source='fn done(value:Int,*,suffix:String="!")->String{str(value)+suffix};let stack=exit_stack();let same=stack.callback(done,42);let result=same(1,suffix=".");result'
        symbols=self.symbols(source);self.assertEqual(symbols['same']['type'],'Function');self.assertEqual(symbols['result']['type'],'String')
        result=self.result(source,position=source.index('same(1,')+len('same('))
        self.assertEqual(result['signatureHelp']['parameters'][0]['name'],'value')
        source='async fn done(value:Int)->String{str(value)}let stack=exit_stack(true);let same=stack.push_async_callback(done,42);let cold=same(1);let result=await cold;result'
        symbols=self.symbols(source);self.assertEqual(symbols['cold']['type'],'Task');self.assertEqual(symbols['result']['type'],'String')
        source='class Scope{fn __exit__(self,error)->Bool{false}}let stack=exit_stack();let scope=stack.push(exit_function=Scope());scope'
        self.assertEqual(self.symbols(source)['scope']['type'],'Scope')
        # A forwarded keyword named function belongs to the original callback.
        source='fn done(*,function:String)->String{function}let same=exit_stack().callback(done,function="ok");let value=same(function="yes");value'
        self.assertEqual(self.symbols(source)['value']['type'],'String')

    def test_dynamic_protocols_expansion_and_taskgroups_remain_conservative(self):
        cases=[
            'class C{var __enter__=fn()->Int{42}}let value=exit_stack().enter_context(C())',
            'class C{fn __enter__(self)->Int{42}}fn inspect(c:C){let value=exit_stack().enter_context(c);value}',
            'class C{fn __aenter__(self)->Int{42}}let value=await exit_stack(true).enter_async_context(C())',
            'let value=await exit_stack(true).enter_async_context(task_group())',
            'let value=exit_stack().enter_context(*input)',
            'let value=await exit_stack(true).enter_async_context(*input)',
            'with exit_stack(true) as value{value}',
            'async with exit_stack() as value{value}',
        ]
        for source in cases:
            with self.subTest(source=source):self.assertEqual(self.symbols(source)['value']['type'],'Any')

    def test_local_spellings_and_mutable_aliases_never_gain_native_identity(self):
        cases=[
            'fn exit_stack(){input}let value=exit_stack().pop_all()',
            'let Context={ExitStack:fn(){input}};let value=Context.ExitStack().pop_all()',
            'var create=exit_stack;let value=create().pop_all()',
            'var stack=exit_stack();let value=stack.pop_all()',
            'let stack={enter_context:fn(manager){manager}};class C{fn __enter__(self)->Int{42}}let value=stack.enter_context(C())',
        ]
        for source in cases:
            with self.subTest(source=source):self.assertEqual(self.symbols(source)['value']['type'],'Any')

    def test_native_parameter_errors_and_callback_positional_only_binding(self):
        for source in ('exit_stack(asynchronous=42)','exit_stack(max_entries=true)','exit_stack().close(1)','exit_stack().callback(function=input)'):
            with self.subTest(source=source):self.assertTrue(analyze(source)['diagnostics'])
        source='exit_stack().callback(fn(){null},function=42)'
        self.result(source)

    def test_shared_sdk_lsp_highlight_format_and_analysis_are_execution_free(self):
        source=PREFIX+'@Context.asynccontextmanager\nasync fn* resource(seed:Int)->String{yield {answer:seed};"done"}\nasync with Context.AsyncExitStack(max_entries=8) as stack{let value=await stack.enter_async_context(resource(42));value.answer}'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace=Workspace([root]);uri=(root/'main.chaos').as_uri()
            workspace.open({'uri':uri,'languageId':'chaos','version':1,'text':source})
            snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            with patch.object(Evaluator,'run',side_effect=AssertionError('must not run')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('must not evaluate')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('must not advance')):
                position=source.index('enter_async_context(')+len('enter_async_context(')
                result=self.result(source,position=position,files=files,entry=entry)
                self.assertEqual(Framework.analyze(source,position=position,files=files,entry=entry),result)
                signature=convert('textDocument/signatureHelp',result,snapshot,files)
                self.assertIn('-> Task',signature['signatures'][0]['label'])
                tokens=convert('textDocument/semanticTokens/full',result,snapshot,files)['data']
                self.assertEqual(len(tokens)%5,0);self.assertIn(TOKEN_TYPES.index('function'),tokens)
                formatted=Server(None,None)._calculate(Job(1,'textDocument/formatting',{'options':{'tabSize':2,'insertSpaces':True}},snapshot))[0]['newText']
                self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
                self.assertEqual(format_source(formatted),formatted)
                html=render_code(source,'chaos');self.assertIn('chaos-token-function',html);self.assertIn('AsyncExitStack',html)
            self.assertFalse((root/'main.chaos').exists())


if __name__=='__main__':unittest.main()
