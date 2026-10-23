"""Execution-free editor contracts for language-owned Agent tools."""
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


SOURCE='''let offset=2;
fn add(value:Int)->Int{value+offset}
agent worker{provider:"app.tools",tools:[]};
let tool=agent_tool("add",add,description="Add captured offset",input_type="Int",output_type="Int");
let bound=agent_bind(worker,[tool]);
let cold=bound(input=40);
let result=await cold;
let contract=agent_tool_info(tool);
let binding=agent_binding_info(bound);
[result,contract.inputType,binding["agent"].name]'''


class AgentFunctionToolsIDE(unittest.TestCase):
    def result(self, source=SOURCE, **kwargs):
        result=analyze(source,**kwargs)
        self.assertTrue(result['complete'],result['diagnostics'])
        self.assertEqual(result['diagnostics'],[])
        return result

    def symbols(self, source=SOURCE, **kwargs):
        return {item['name']:item for item in self.result(source,**kwargs)['symbols']}

    def test_native_specs_match_runtime_parameter_names_modes_and_defaults(self):
        symbols=builtin_symbols();runtime=Evaluator().env
        for name in ('agent_tool','agent_bind','agent_tool_info','agent_binding_info'):
            with self.subTest(name=name):
                native=runtime.get(name);spec=spec_for(name)
                self.assertEqual(native.spec_id,name)
                parameters=list(inspect.signature(native.call).parameters.values())
                self.assertEqual([item.name for item in parameters],[item.name for item in spec.parameters])
                self.assertEqual([item.default is not item.empty for item in parameters],[item.optional for item in spec.parameters])
                self.assertEqual([item[0] for item in symbols[name].parameters],[item.name for item in parameters])
                self.assertEqual(symbols[name].builtin,name)
        self.assertEqual(spec_for('agent_bind').result_type,'Function')
        self.assertEqual(spec_for('agent_tool').result_type,'AgentTool')

    def test_descriptor_bound_function_cold_task_and_unknown_final_result(self):
        symbols=self.symbols()
        self.assertEqual([symbols[name]['type'] for name in ('tool','bound','cold','result','contract','binding')],['AgentTool','Function','Task','Any','Record','Record'])
        self.assertIn('-> Task',symbols['bound']['detail'])
        # The provider's final value is independent of an individual tool contract.
        self.assertNotEqual(symbols['result']['type'],'Int')
        source=SOURCE+';let wrapped=apply(bound,[40]);let waiting=spawn(wrapped);let final=await waiting'
        symbols=self.symbols(source)
        self.assertEqual([symbols[name]['type'] for name in ('wrapped','waiting','final')],['Task','Task','Any'])

    def test_native_aliases_records_and_module_exports_retain_call_contract(self):
        source='let create=agent_bind;agent worker{};let bound=create(worker,[]);let alias=bound;let object={call:alias};let cold=object.call(40)'
        self.assertEqual(self.symbols(source)['cold']['type'],'Task')
        module='agent worker{};let bind=agent_bind;let bound=bind(worker,[]);export {bind,bound}'
        source='import "worker.chaos" as Worker;let call=Worker.bound;let cold=call(40);let value=await cold;value'
        symbols=self.symbols(source,files={'worker.chaos':module})
        self.assertEqual(symbols['cold']['type'],'Task');self.assertEqual(symbols['value']['type'],'Any')
        result=self.result(source,files={'worker.chaos':module},position=source.index('Worker.bound')+len('Worker.bo'))
        self.assertEqual(result['definition']['file'],'worker.chaos')
        self.assertEqual(result['definition']['from'],module.index('bound=bind'))

    def test_contract_record_completion_shapes_and_no_fabricated_handler(self):
        prefix=SOURCE+';'
        expected={'name','description','inputType','outputType','capabilities'}
        self.assertEqual({item['label'] for item in analyze(prefix+'contract.')['completions']},expected)
        self.assertEqual({item['label'] for item in analyze(prefix+'binding.')['completions']},{'agent','tools'})
        self.assertEqual({item['label'] for item in analyze(prefix+'binding["agent"].')['completions']},{'kind','name','config'})
        self.assertFalse(analyze(prefix+'tool.')['completions'])
        result=self.result(SOURCE,position=SOURCE.index('contract.inputType')+len('contract.inputT'))
        self.assertIn('String',result['hover']['contents']);self.assertIsNone(result['definition'])

    def test_signatures_use_native_and_bound_identity(self):
        position=SOURCE.index('output_type=')+len('output_type=')
        result=self.result(position=position)
        self.assertEqual(result['signatureHelp']['activeParameter'],4)
        self.assertEqual([item['name'] for item in result['signatureHelp']['parameters']],['name','handler','description','input_type','output_type','capabilities'])
        result=self.result(position=SOURCE.index('bound(input=')+len('bound(input='))
        self.assertIn('-> Task',result['signatureHelp']['label'])
        self.assertEqual(result['signatureHelp']['parameters'][0]['name'],'input')
        prefix=SOURCE+';tool('
        self.assertIsNone(analyze(prefix)['signatureHelp'])

    def test_shadowed_spellings_and_mutable_values_do_not_gain_task_metadata(self):
        sources=[
            'fn agent_bind(value,tools){input}let bound=agent_bind(input,[]);let cold=bound(1)',
            'let object={agent_bind:fn(value,tools){input}};let bound=object.agent_bind(input,[]);let cold=bound(1)',
            'var create=agent_bind;let bound=create(input,[]);let cold=bound(1)',
            'agent worker{};var bound=agent_bind(worker,[]);let cold=bound(1)',
            'fn inspect(bound:Function){let cold=bound(1);cold}',
        ]
        for source in sources:
            with self.subTest(source=source):self.assertEqual(self.symbols(source)['cold']['type'],'Any')

    def test_known_native_argument_errors_are_shared_diagnostics(self):
        for source in ('agent_tool(42,input)','agent_tool("x",input,input_type=42)','agent_tool("x",input,capabilities=true)','agent_bind(42,[])','agent_bind(input)','agent_binding_info(42)','agent_tool_info(42)','agent worker{};let bound=agent_bind(worker,[]);bound()','agent worker{};let bound=agent_bind(worker,[]);bound(1,2)'):
            with self.subTest(source=source):self.assertTrue(analyze(source)['diagnostics'])
        self.result('agent worker{};let bound=agent_bind(worker,[]);fn use(call:Function){null};use(bound)')
        self.assertTrue(analyze('let tool=agent_tool("x",fn(x){x});fn use(call:Function){null};use(tool)')['diagnostics'])

    def test_sdk_lsp_format_highlight_and_definitions_are_inert_and_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace=Workspace([root]);uri=(root/'main.chaos').as_uri()
            workspace.open({'uri':uri,'languageId':'chaos','version':1,'text':SOURCE})
            snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            with patch.object(Evaluator,'run',side_effect=AssertionError('analysis ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('analysis evaluated source')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('analysis advanced task')):
                position=SOURCE.index('bound(input=')+len('bound(input=')
                result=self.result(position=position,files=files,entry=entry)
                self.assertEqual(Framework.analyze(SOURCE,position=position,files=files,entry=entry),result)
                signature=convert('textDocument/signatureHelp',result,snapshot,files)
                self.assertIn('-> Task',signature['signatures'][0]['label'])
                completion_source=SOURCE+';contract.'
                completion=Framework.analyze(completion_source)
                self.assertIn('inputType',{item['label'] for item in completion['completions']})
                data=convert('textDocument/semanticTokens/full',result,snapshot,files)['data']
                self.assertEqual(len(data)%5,0);self.assertIn(TOKEN_TYPES.index('function'),data[3::5])
                formatted=Server(None,None)._calculate(Job(1,'textDocument/formatting',{'options':{'tabSize':2,'insertSpaces':True}},snapshot))[0]['newText']
                self.assertEqual(plain(Parser(SOURCE).program()),plain(Parser(formatted).program()))
                self.assertEqual(format_source(formatted),formatted)
                self.assertIn('chaos-token-function',render_code(SOURCE,'chaos'))
                reference=SOURCE.index('bound(input=')+2
                definition=self.result(position=reference)['definition']
                self.assertEqual(definition,{'file':'main.chaos','from':SOURCE.index('bound=agent_bind'),'to':SOURCE.index('bound=agent_bind')+5})
            self.assertFalse((root/'main.chaos').exists())


if __name__=='__main__':unittest.main()
