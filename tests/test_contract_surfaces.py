"""Composite contracts stay ordinary values throughout the shared toolchain."""
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
from chaos.language import Evaluator, Parser, TYPES
from chaos.lsp_adapters import convert
from chaos.native_specs import SHAPES, spec_for
from chaos.program import decode_program, format_program, parse_program, plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.symbols import references, rename

NAMES = ('contract_list','contract_tuple','contract_record','contract_union',
         'contract_optional','contract_map','contract_accepts','contract_info')
SOURCE = '''// Payload contracts are ordinary aliases in the existing Program IR.
let Request=contract_record({values:contract_list("Int")});
fn solve(request:Request)->Int{request.values[0]+2};
solve({values:[40]})'''


class ContractSurfaces(unittest.TestCase):
    def test_native_metadata_signatures_descriptor_type_and_diagnostic_shape(self):
        evaluator=Evaluator();symbols=builtin_symbols()
        self.assertIn('Contract',TYPES)
        source='let descriptor:Contract=contract_list("Int")';at=source.index('Contract')
        self.assertEqual(next(t['type']for t in analyze(source)['tokens']if t['from']==at),'type')
        for name in NAMES:
            with self.subTest(name=name):
                value=evaluator.env.get(name);spec=spec_for(name)
                self.assertEqual(value.spec_id,spec.id)
                params=list(inspect.signature(value.call).parameters.values())
                self.assertEqual([p.name for p in params],[p.name for p in spec.parameters])
                self.assertEqual([p.default is not inspect.Parameter.empty for p in params],[p.optional for p in spec.parameters])
                self.assertEqual(symbols[name].returns,spec.result_type)
        fields={f.name:(f.type,f.optional)for f in SHAPES['ContractInfo']}
        self.assertEqual(fields,{'kind':('String',False),'name':('String',True),'element':('Record',True),
            'elements':('List',True),'required':('Record',True),'optional':('Record',True),
            'allowExtra':('Bool',True),'alternatives':('List',True),'key':('Record',True),'value':('Record',True),
            'root':('String',True),'definitions':('Record',True)})
        self.assertEqual(Framework.evaluate('contract_info(contract_optional("Int"))'),
                         {'kind':'union','alternatives':[{'kind':'builtin','name':'Int'},{'kind':'builtin','name':'Null'}]})

    def test_module_aliases_shadowing_and_conservative_payload_inference(self):
        source='''import "std/contracts" as C;
let make=C.contract_record;let Request=make({values:C.contract_list("Int")});
fn solve(request:Request)->Request{request};
let output=solve({values:[40]});let field=output.values;
let descriptor=C.contract_optional(Request);let accepts=C.contract_accepts(Request,{values:[40]});'''
        report=analyze(source);self.assertEqual(report['diagnostics'],[])
        types={s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('Request','request','output','field','descriptor','accepts')],
                         ['Contract','Any','Any','Any','Contract','Bool'])
        help=analyze(source+'solve(')['signatureHelp']
        self.assertEqual(help['parameters'][0]['type'],'Request')
        self.assertIn('Request',help['label'])
        shadow='fn contract_list(element)->String{"local"};let text=contract_list("Int")'
        self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='text'),'String')

    def test_all_constructor_calls_and_actual_alias_annotations(self):
        source='''let A=contract_list("Int");let B=contract_tuple(["Int"]);
let C=contract_record({value:"Int"});let D=contract_union([A,B]);
let E=contract_optional(C);let F=contract_map("String","Int");
fn check(input:F)->Bool{contract_accepts(F,input)};
[type_of(A),type_of(B),type_of(C),type_of(D),type_of(E),type_of(F),check(hash_map([("score",42)]))]'''
        report=analyze(source);self.assertEqual(report['diagnostics'],[])
        types={s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('A','B','C','D','E','F')],['Contract']*6)
        self.assertEqual(Framework.evaluate(source),['Contract']*6+[True])

    def test_completion_signatures_and_optional_diagnostic_fields(self):
        prefix='let Request=contract_record({value:"Int"});'
        labels={x['label']for x in analyze(prefix+'contract_info(Request).')['completions']}
        self.assertEqual(labels,{f.name for f in SHAPES['ContractInfo']})
        help=analyze('contract_record(required={},optional=null,allow_extra=')['signatureHelp']
        self.assertEqual([p['name']for p in help['parameters']],['required','optional','allow_extra'])
        self.assertEqual(help['activeParameter'],2)
        self.assertTrue(set(NAMES)<={x['label']for x in analyze('contract_')['completions']})
        report=analyze(prefix+'let info=contract_info(Request);let kind=info.kind;let child=info.element;')
        self.assertEqual(report['diagnostics'],[])
        types={s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('info','kind','child')],['Record','String','Record'])

    def test_source_ir_formatter_and_macro_preserve_named_annotation(self):
        for source in (SOURCE,format_source(SOURCE,line_width=44),format_program(parse_program(SOURCE))):
            self.assertEqual(Framework.evaluate(source),42)
            self.assertEqual(plain(decode_program(parse_program(source))),plain(Parser(SOURCE).program()))
        formatted=format_source(SOURCE)
        self.assertEqual(format_source(formatted),formatted)
        self.assertIn('// Payload contracts',formatted)
        macro='''macro build(make,child){quote{unquote(make)(unquote(child))}}
let Ints=build!(contract_list,"Int");fn solve(values:Ints)->Int{values[0]+2};solve([40])'''
        for source in (macro,format_source(macro),format_program(parse_program(macro))):
            self.assertEqual(Framework.evaluate(source),42)

    def test_lsp_highlighting_and_rename_never_construct_or_check_values(self):
        source='''let Request=contract_record({value:tool("never",null)});
fn solve(request:Request)->Int{request.value};solve({value:42})'''
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')), \
             patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated source')):
            report=analyze(source,position=source.rindex('42'))
            self.assertEqual(report['diagnostics'],[])
            help=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
            self.assertIn('request: Request',help['signatures'][0]['label'])
            self.assertIn('chaos-token-type',render_code(source,'chaos'))
            result=references(source,position=source.index('Request'))
            self.assertTrue(result['complete']);self.assertEqual(len(result['references']),2)
            changed=rename(source,position=source.index('Request'),new_name='Payload')['files']['main.chaos']
            self.assertIn('request:Payload',changed)

    def test_http_sdk_ir_edit_preserves_saved_history_and_analysis(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder)
            server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('contracts-surface',source=SOURCE,capabilities=[])
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
