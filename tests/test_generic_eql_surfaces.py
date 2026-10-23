"""Eql selectors are real values on the existing source, IR and IDE surfaces."""
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
from chaos import Client, Framework, Settings
from chaos.cli import main
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import SHAPES, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.symbols import references, rename

SOURCE='''// An actual value selects the method, not its spelling in source.
let base=generic_method(generic_function("answer"),["Int"],fn(value){value+1});
let answer=generic_method(base,[generic_eql(40)],fn(next,value){next()+1},with_next=true);
answer(40)'''


class GenericEqlSurfaces(unittest.TestCase):
    def test_native_signatures_and_inert_info_shape(self):
        evaluator=Evaluator();symbols=builtin_symbols()
        for name,parameter,kind in [('generic_eql','value','EqlSpecializer'),('generic_eql_info','specializer','Record')]:
            spec=spec_for(name);native=evaluator.env.get(name)
            self.assertEqual(native.spec_id,spec.id)
            self.assertEqual(list(inspect.signature(native.call).parameters),[parameter])
            self.assertEqual([(p.name,p.mode,p.optional)for p in spec.parameters],[(parameter,'positional',False)])
            self.assertEqual(spec.result_type,kind);self.assertEqual(symbols[name].returns,kind)
        info=spec_for('generic_eql_info')
        self.assertEqual({f.name:f.type for f in SHAPES[info.result_shape]}, {'kind':'String','comparison':'String','valueType':'String'})
        self.assertEqual(Framework.evaluate('generic_eql_info(generic_eql(40))'),{'kind':'eql','comparison':'value','valueType':'Int'})

    def test_aliases_shadowing_and_conservative_call_result(self):
        source='''import "std/generic" as G;let select=G.generic_eql;let describe=G.generic_eql_info;
let specializer=select(40);let info=describe(specializer);let policy=info.comparison;
let work=G.generic_method(G.generic_function("answer"),[specializer],fn(x){42});let result=work(40);'''
        report=analyze(source);self.assertEqual(report['diagnostics'],[])
        types={s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('specializer','info','policy','work','result')],['EqlSpecializer','Record','String','Function','Any'])
        shadow='fn generic_eql(value)->String{"local"};let result=generic_eql(40)'
        self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='result'),'String')
        self.assertEqual(Framework.evaluate(source+'result'),42)

    def test_formatter_ir_and_hygienic_macro_retain_value_calls(self):
        for source in (SOURCE,format_source(SOURCE,line_width=44),format_program(parse_program(SOURCE))):
            self.assertEqual(Framework.evaluate(source),42)
            self.assertEqual(plain(decode_program(parse_program(source))),plain(Parser(SOURCE).program()))
        formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
        self.assertIn('// An actual value',formatted)
        macro='''macro choose(factory,value){quote{unquote(factory)(unquote(value))}}
let work=generic_method(generic_function("work"),[choose!(generic_eql,"ready")],fn(value){42});work("rea"+"dy")'''
        for source in (macro,format_source(macro),format_program(parse_program(macro))):self.assertEqual(Framework.evaluate(source),42)

    def test_completion_lsp_highlighting_and_rename_are_inert(self):
        source='let specializer=generic_eql("review");generic_eql_info(specializer).'
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated source')):
            self.assertEqual({c['label']for c in analyze(source)['completions']},{'kind','comparison','valueType'})
            unfinished='generic_eql_info('
            report=analyze(unfinished)
            signature=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':unfinished})
            self.assertIn('specializer: EqlSpecializer',signature['signatures'][0]['label'])
            self.assertEqual(analyze('generic_eql(')['signatureHelp']['parameters'][0]['name'],'value')
            self.assertTrue({'generic_eql','generic_eql_info'}<={c['label']for c in analyze('generic_eql')['completions']})
            html=render_code('let choice:EqlSpecializer=generic_eql(40);choice','chaos')
            self.assertIn('EqlSpecializer',html);self.assertIn('generic_eql',html)
            named='let choice=generic_eql(40);let work=generic_method(generic_function("f"),[choice],fn(x){42});work(40)'
            refs=references(named,position=named.index('choice'));self.assertTrue(refs['complete']);self.assertEqual(len(refs['references']),2)
            changed=rename(named,position=named.index('choice'),new_name='selectedValue')['files']['main.chaos']
        self.assertEqual(Framework.evaluate(changed),42)

    def test_cli_sdk_and_portable_source_share_result(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'consumer.chaos';path.write_text(SOURCE)
            output=io.StringIO();errors=io.StringIO()
            with patch.object(sys,'argv',['chaos','run',str(path)]),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
                self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue()),42)
            self.assertEqual(errors.getvalue(),'')
            self.assertEqual(Framework.evaluate(path.read_text()),42)

    def test_http_ir_edit_keeps_value_key_and_old_snapshot(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('eql',source=SOURCE,capabilities=[])
                old=client.start('eql',project['version'],None);self.assertEqual(old.result(),42)
                program=client.program('eql')['program']
                def literals(value):
                    if isinstance(value,dict):
                        if value.get('kind')=='literal' and value.get('value')==40:yield value
                        for child in value.values():yield from literals(child)
                    elif isinstance(value,list):
                        for child in value:yield from literals(child)
                found=list(literals(program['root']));self.assertEqual(len(found),2)
                updated=client.edit_program('eql',project['version'],operations=[{'op':'set','id':found[-1]['id'],'field':'value','value':50}])
                self.assertEqual(client.start('eql',updated['version'],None,mode='graph').result(),51)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_wire_diagnostics_cannot_reconstruct_identity_payload(self):
        source='''let token={private:"payload"};let selector=generic_eql(token);
let wire=parse_json(json_stringify(selector));
[wire,try{generic_eql_info(wire)}catch e{e.code},generic_eql_info(selector),
 try{selector.value}catch e{e.code}]'''
        result=Framework.evaluate(source)
        self.assertEqual(result[0],{'$eqlSpecializer':{'kind':'eql','comparison':'identity','valueType':'Record'}})
        self.assertNotIn('payload',json.dumps(result[0]))
        self.assertEqual(result[1],'argument_error')
        self.assertEqual(result[2],{'kind':'eql','comparison':'identity','valueType':'Record'})
        self.assertEqual(result[3],'runtime_error')


if __name__=='__main__':unittest.main()
