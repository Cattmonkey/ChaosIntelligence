"""Primary next remains an opt-in ordinary call across all language surfaces."""
import inspect
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp_adapters import convert
from chaos.modules import STDLIB, declarations
from chaos.native_specs import SHAPES, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.symbols import references, rename

NAMES=('generic_function','generic_method','generic_remove','generic_methods','generic_info','generic_next_available','generic_eql','generic_eql_info')
SOURCE='''// Primary next stays an ordinary call with an explicit keyword option.
let base=generic_method(generic_function("calculate"),["Number"],fn(value){value+1});
let calculate=generic_method(base,["Int"],fn(next,value){next()+1},with_next=true);
calculate(40)'''


class GenericNextSurfaces(unittest.TestCase):
    def test_native_signatures_keyword_only_option_and_bool_result(self):
        evaluator=Evaluator();symbols=builtin_symbols()
        modes={inspect.Parameter.POSITIONAL_ONLY:'positional_only',inspect.Parameter.POSITIONAL_OR_KEYWORD:'positional',
               inspect.Parameter.KEYWORD_ONLY:'keyword_only',inspect.Parameter.VAR_POSITIONAL:'varargs',inspect.Parameter.VAR_KEYWORD:'varkw'}
        for name in NAMES:
            with self.subTest(name=name):
                native=evaluator.env.get(name);spec=spec_for(name)
                self.assertEqual(native.spec_id,spec.id)
                params=list(inspect.signature(native.call).parameters.values())
                self.assertEqual([p.name for p in params],[p.name for p in spec.parameters])
                self.assertEqual([modes[p.kind]for p in params],[p.mode for p in spec.parameters])
                self.assertEqual([p.default is not inspect.Parameter.empty for p in params],[p.optional for p in spec.parameters])
                self.assertEqual(symbols[name].returns,spec.result_type)
        flag=spec_for('generic_method').parameters[-1]
        self.assertEqual((flag.name,flag.type,flag.mode,flag.optional,flag.default_display),('with_next','Bool','keyword_only',True,'false'))
        self.assertIs(inspect.signature(evaluator.env.get('generic_method').call).parameters['with_next'].default,False)
        self.assertEqual([(p.name,p.type)for p in spec_for('generic_next_available').parameters],[('next','Function')])
        self.assertEqual(spec_for('generic_next_available').result_type,'Bool')

    def test_old_record_shapes_and_info_stay_compatible(self):
        result=Framework.evaluate('''let base=generic_method(generic_function("f"),["Number"],fn(value){value});
let changed=generic_method(base,["Int"],fn(next,value){next()+2},with_next=true);
let replaced=generic_method(changed,["Int"],fn(value){42});
[keys(generic_methods(base)[0]),keys(generic_methods(changed)[1]),generic_methods(changed)[1].withNext,
has(generic_methods(replaced)[1],"withNext"),generic_info(changed)]''')
        self.assertEqual(set(result[0]),{'specializers','implementation','qualifier'})
        self.assertEqual(set(result[1]),{'specializers','implementation','qualifier','withNext'})
        self.assertEqual(result[2:4],[True,False])
        fields={f.name:f.type for f in SHAPES['GenericFunctionInfo']}
        self.assertEqual(fields,{'name':'String','arity':'Int','methodCount':'Int','qualifiers':'List','precedence':'String','nextArguments':'String'})
        self.assertEqual(set(result[4]),set(fields));self.assertEqual(result[4]['methodCount'],2)

    def test_module_exports_aliases_and_shadowed_names(self):
        self.assertEqual(set(declarations(Parser(STDLIB['std/generic']).program())),set(NAMES))
        source='''import "std/generic" as G;
let extend=G.generic_method;let available=G.generic_next_available;
let base=extend(G.generic_function("f"),["Number"],fn(value){value});
let work=extend(base,["Int"],fn(next,value){if available(next){next()+2}else{0}},with_next=true);
let output=work(40);'''
        report=analyze(source);self.assertEqual(report['diagnostics'],[])
        types={s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('extend','available','base','work','output')],['Function','Function','Function','Function','Any'])
        self.assertEqual(Framework.evaluate(source+'output'),42)
        shadow='fn generic_next_available(next)->String{"local"};let text=generic_next_available(null)'
        self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='text'),'String')

    def test_completions_and_keyword_only_signature_help(self):
        prefix='let base=generic_function("g");'
        help=analyze(prefix+'generic_method(base,["Int"],fn(next,value){next()},with_next=')['signatureHelp']
        self.assertEqual(help['activeParameter'],4)
        self.assertEqual([p['name']for p in help['parameters']],['function','specializers','implementation','qualifier','with_next'])
        self.assertEqual(help['parameters'][-1]['mode'],'keyword_only')
        self.assertIn('with_next: Bool',help['label'])
        self.assertTrue(set(NAMES)<={x['label']for x in analyze('generic_')['completions']})
        report=analyze('fn query(next:Function)->Bool{generic_next_available(next)};let answer=query(fn(){42})')
        self.assertEqual(next(s['type']for s in report['symbols']if s['name']=='answer'),'Bool')

    def test_source_ir_formatter_and_macro_retain_keyword_call(self):
        for source in (SOURCE,format_source(SOURCE,line_width=42),format_program(parse_program(SOURCE))):
            self.assertEqual(Framework.evaluate(source),42)
            self.assertEqual(plain(decode_program(parse_program(source))),plain(Parser(SOURCE).program()))
        formatted=format_source(SOURCE)
        self.assertEqual(format_source(formatted),formatted);self.assertIn('// Primary next',formatted)
        macro='''macro extend(factory,base,kind,implementation,flag){quote{
 unquote(factory)(unquote(base),[unquote(kind)],unquote(implementation),with_next=unquote(flag))}}
let base=generic_method(generic_function("f"),["Any"],fn(value){value});
let work=extend!(generic_method,base,"Int",fn(next,value){next()+2},true);work(40)'''
        for source in (macro,format_source(macro),format_program(parse_program(macro))):self.assertEqual(Framework.evaluate(source),42)

    def test_lsp_highlighting_and_nominal_rename_do_not_run_methods(self):
        source='''class Request{};let base=generic_method(generic_function("f"),["Any"],fn(value){40});
let work=generic_method(base,[Request],fn(next,value){next()+2},with_next=true);work(Request())'''
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')), \
             patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated source')):
            unfinished=source+';generic_method(base,[Request],fn(next,value){tool("never",next)},with_next='
            report=analyze(unfinished)
            help=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':unfinished})
            self.assertIn('with_next: Bool',help['signatures'][0]['label'])
            self.assertIn('generic_next_available',render_code('generic_next_available(fn(){42})','chaos'))
            self.assertIn('with_next',render_code(source,'chaos'))
            refs=references(source,position=source.index('Request'));self.assertTrue(refs['complete']);self.assertEqual(len(refs['references']),3)
            changed=rename(source,position=source.index('Request'),new_name='WorkItem')['files']['main.chaos']
            self.assertIn('[WorkItem]',changed)
        self.assertEqual(Framework.evaluate(changed),42)

    def test_http_sdk_ir_edit_keeps_old_snapshot_and_common_analysis(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('generic-next',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program']
                def literal(value):
                    if isinstance(value,dict):
                        if value.get('value')==40 and 'id'in value:return value
                        for child in value.values():
                            found=literal(child)
                            if found:return found
                    elif isinstance(value,list):
                        for child in value:
                            found=literal(child)
                            if found:return found
                target=literal(program['root']);self.assertIsNotNone(target)
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':41}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
