"""Native Set protocols share the unchanged AST, metadata and authoring surfaces."""
import contextlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.cli import main
from chaos.code_presentation import render_code
from chaos.ide_language import analyze,builtin_symbols
from chaos.language import Evaluator,LanguageError,Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import spec_for
from chaos.program import decode_program,format_program,parse_program,plain
from chaos.server import make_server
from chaos.set_values import SetValue,set_equal
from chaos.source_format import format_source
from chaos.symbols import references,rename
from tests.test_set_protocol_integration import KEY

SOURCE='// User keys stay actual class values in the original Set syntax.\n'+KEY+'''
let start=20;let choices:Set={Key(start),Key(22),Key(start)};
fold(fn(total,key){total+key.number},0,choices)'''


class SetProtocolSurfaces(unittest.TestCase):
    def test_unchanged_native_signatures_and_set_types(self):
        expected={'set_of':['items'],'set_add':['value','item'],'set_discard':['value','item'],'set_is_disjoint':['left','right']}
        evaluator=Evaluator();symbols=builtin_symbols()
        for name,parameters in expected.items():
            native=evaluator.env.get(name);spec=spec_for(name)
            self.assertEqual(native.spec_id,spec.id)
            self.assertEqual(list(inspect.signature(native.call).parameters),parameters)
            self.assertEqual([p.name for p in spec.parameters],parameters)
            self.assertEqual(spec.result_type,'Bool'if name=='set_is_disjoint'else'Set')
            self.assertEqual(symbols[name].returns,spec.result_type)
        self.assertEqual(spec_for('set_of').parameters[0].default_display,'[]')
        self.assertEqual(Framework.evaluate('[type_of({}),type_of({*[]}),set({a:1},"a",42).a]'),['Record','Set',42])

    def test_literal_comprehension_ir_and_formatter_in_both_dialects(self):
        cases=[SOURCE,KEY+'let values={*[Key(20),Key(22)],Key(20)};length(values)',
               KEY+'length({Key(x) for x in [20,22,20]})',
               KEY+'async fn* rows(){yield 20;await sleep(0);yield 20};length({Key(x) async for x in rows()})']
        for source in cases:
            expected=Framework.evaluate(source)
            for version in (1,2):
                program=parse_program(source,language_version=version)
                for text in (format_program(program),format_source(source,language_version=version,line_width=44)):
                    self.assertEqual(Framework.evaluate(text),expected)
                    self.assertEqual(plain(Parser(text).program()),plain(decode_program(program)))
        formatted=format_source(SOURCE)
        self.assertEqual(format_source(formatted),formatted);self.assertIn('// User keys',formatted)

    def test_pure_macros_and_runtime_hygienic_object_set_share_nodes(self):
        pure='macro values(){syntax_literal({(1,2),{3,4},{4,3}})};values!()'
        self.assertEqual(Framework.evaluate(pure),{'$set':[{'$tuple':[1,2]},{'$set':[3,4]}]})
        source='macro distinct(value){quote{{unquote(value),unquote(value)}}};'+KEY+'length(distinct!(Key(42)))'
        for text in (source,format_source(source),format_program(parse_program(source))):self.assertEqual(Framework.evaluate(text),1)
        with self.assertRaises(LanguageError):Framework.evaluate('macro bad(){syntax_literal({fn(){42}})};bad!()')

    def test_analysis_lsp_completion_and_rename_do_not_call_protocols(self):
        source=KEY+'let values:Set={Key(42)};set_add(values,'
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated source')):
            report=analyze(source)
            help=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
            self.assertIn('value: Set',help['signatures'][0]['label'])
            self.assertEqual(report['signatureHelp']['activeParameter'],1)
            self.assertTrue({'set_of','set_add','set_discard','set_is_disjoint'}<={c['label']for c in analyze('set_')['completions']})
            analyzed=analyze(SOURCE);self.assertEqual(analyzed['diagnostics'],[])
            self.assertEqual(next(s['type']for s in analyzed['symbols']if s['name']=='choices'),'Set')
            self.assertIn('Set',render_code(SOURCE,'chaos'))
            at=SOURCE.index('start=');refs=references(SOURCE,position=at);self.assertTrue(refs['complete']);self.assertEqual(len(refs['references']),3)
            changed=rename(SOURCE,position=at,new_name='initial')['files']['main.chaos']
        self.assertEqual(Framework.evaluate(changed),42)

    def test_cli_sdk_http_ir_and_old_snapshot_use_same_set_protocol(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);sourcefile=root/'consumer.chaos';sourcefile.write_text(SOURCE)
            output=io.StringIO();errors=io.StringIO()
            with patch.object(sys,'argv',['chaos','run',str(sourcefile)]),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue()),42);self.assertEqual(errors.getvalue(),'')
            self.assertEqual(Framework.evaluate(sourcefile.read_text()),42)
            server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('set-protocol',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program']
                def find(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal'and value.get('value')==20:return value
                        for child in value.values():
                            found=find(child)
                            if found:return found
                    elif isinstance(value,list):
                        for child in value:
                            found=find(child)
                            if found:return found
                target=find(program['root']);self.assertIsNotNone(target)
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':21}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_pure_host_values_remain_portable_and_live_sets_remain_owned(self):
        pure=SetValue([1,2]);first=Evaluator();second=Evaluator()
        self.assertIs(Evaluator.copy_value(pure),pure)
        self.assertTrue(set_equal(pure,SetValue([2,1])))
        self.assertEqual(second.run('collect(input)',{'input':pure}),[1,2])
        live=first.run(KEY+'{Key(42)}')
        with self.assertRaises(LanguageError)as caught:second.run('collect(input)',{'input':live})
        self.assertEqual(caught.exception.code,'ownership_error')
        with self.assertRaises(LanguageError):SetValue([first.run('class Item{};Item()')])

    def test_json_old_shape_is_inert_and_does_not_run_user_callbacks(self):
        source='''var calls=0;class Item{fn __hash__(self){calls+=1;42}fn __eq__(self,other){calls+=1;true}};
let value={Item()};let before=calls;let description=parse_json(json_stringify(value));
[description,type_of(description),calls==before,parse_json(json_stringify({20,22})),type_of(value)]'''
        self.assertEqual(Framework.evaluate(source),[{'$set':[{'$instance':'Item'}]},'Record',True,{'$set':[20,22]},'Set'])


if __name__=='__main__':unittest.main()
