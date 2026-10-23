"""Recursive contracts have one source/IR/SDK/CLI/HTTP and inert IDE surface."""
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
from chaos.language import Evaluator,Parser
from chaos.lsp_adapters import convert
from chaos.native_specs import SHAPES,spec_for
from chaos.program import decode_program,format_program,parse_program,plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.symbols import references,rename
from tests.test_recursive_contract_integration import TREE,GOOD

SOURCE='// A closed recursive boundary uses ordinary source nodes.\n'+TREE+'\ntotal({value:40,children:[{value:2,children:[]}]})'
NAMES=('contract_ref','contract_recursive','contract_list','contract_tuple','contract_record','contract_union','contract_optional','contract_map','contract_accepts','contract_info')


class RecursiveContractSurfaces(unittest.TestCase):
    def test_native_signatures_aliases_std_exports_and_finite_info_shape(self):
        ev=Evaluator();symbols=builtin_symbols()
        for name,parameters in (('contract_ref',['name']),('contract_recursive',['definitions','root'])):
            spec=spec_for(name);native=ev.env.get(name)
            self.assertEqual(native.spec_id,spec.id);self.assertEqual([p.name for p in spec.parameters],parameters)
            self.assertEqual(list(inspect.signature(native.call).parameters),parameters)
            self.assertEqual(spec.result_type,'Contract');self.assertEqual(symbols[name].returns,'Contract')
            help=analyze('let construct='+name+';construct(')['signatureHelp']
            self.assertEqual([p['name']for p in help['parameters']],parameters)
        self.assertEqual(set(Framework.evaluate('import "std/contracts" as C;keys(C)')),set(NAMES))
        fields={f.name:(f.type,f.optional)for f in SHAPES['ContractInfo']}
        self.assertEqual(fields['root'],('String',True));self.assertEqual(fields['definitions'],('Record',True))
        info=Framework.evaluate(TREE+'contract_info(Tree)')
        self.assertEqual(set(info),{'kind','definitions','root'});self.assertEqual(info['root'],'Node')
        self.assertEqual(info['definitions']['Node']['required']['children']['element'],{'kind':'ref','name':'Node'})

    def test_conservative_types_completion_signature_and_shadowing(self):
        source='''import "std/contracts" as C;let reference=C.contract_ref("Node");
let Tree=C.contract_recursive({Node:C.contract_list(reference)},"Node");
fn keep(value:Tree)->Tree{value};let output=keep([]);let info=C.contract_info(Tree);
let root=info.root;let definitions=info.definitions;'''
        report=analyze(source);self.assertEqual(report['diagnostics'],[])
        types={s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('reference','Tree','value','output','info','root','definitions')],['Contract','Contract','Any','Any','Record','String','Record'])
        self.assertIn('value: Tree',analyze(source+'keep(')['signatureHelp']['label'])
        self.assertTrue(set(NAMES)<={c['label']for c in analyze('contract_')['completions']})
        help=analyze('contract_recursive(definitions={},root=')['signatureHelp']
        self.assertEqual(help['activeParameter'],1);self.assertEqual([p['name']for p in help['parameters']],['definitions','root'])
        shadow='fn contract_recursive(definitions,root)->String{"local"};let text=contract_recursive({},"x")'
        self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='text'),'String')

    def test_source_ir_format_and_macro_keep_original_call_and_annotation_nodes(self):
        macro='''macro build(bind,record,list,ref){quote{unquote(bind)({Node:unquote(record)({value:"Int",children:unquote(list)(unquote(ref)("Node"))})},"Node")}}
let Tree=build!(contract_recursive,contract_record,contract_list,contract_ref);
fn solve(node:Tree)->Int{node.value+2};solve({value:40,children:[]})'''
        for source in (SOURCE,macro):
            for version in (1,2):
                program=parse_program(source,language_version=version)
                for text in (format_program(program),format_source(source,language_version=version,line_width=48)):
                    self.assertEqual(Framework.evaluate(text),42)
                    self.assertEqual(plain(Parser(text).program()),plain(decode_program(program)))
        formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
        self.assertIn('// A closed recursive boundary',formatted)

    def test_analysis_lsp_highlight_and_refactor_never_execute_descriptors(self):
        source='let Tree=contract_recursive({Node:tool("never",null)},"Node");fn solve(node:Tree)->Int{42};solve({})'
        with patch.object(Evaluator,'run',side_effect=AssertionError('executed source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('executed source')):
            report=analyze(source,position=source.rindex('{}')+1)
            self.assertEqual(report['diagnostics'],[])
            help=convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
            self.assertIn('node: Tree',help['signatures'][0]['label'])
            self.assertIn('chaos-token-type',render_code(source,'chaos'))
            result=references(source,position=source.index('Tree'))
            self.assertTrue(result['complete']);self.assertEqual(len(result['references']),2)
            changed=rename(source,position=source.index('Tree'),new_name='Payload')['files']['main.chaos']
            self.assertIn('node:Payload',changed)

    def test_cli_sdk_http_ir_edits_and_old_snapshot_agree(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder);path=root/'recursive.chaos';path.write_text(SOURCE)
            output=io.StringIO();errors=io.StringIO()
            with patch.object(sys,'argv',['chaos','run',str(path)]),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):self.assertEqual(main(),0)
            self.assertEqual(json.loads(output.getvalue()),42);self.assertEqual(errors.getvalue(),'')
            self.assertEqual(Framework.evaluate(path.read_text()),42)
            server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('recursive-surface',source=SOURCE,capabilities=[])
                old=client.start(p['id'],p['version'],None);self.assertEqual(old.result(),42)
                program=client.program(p['id'])['program']
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
                updated=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':41}])
                self.assertEqual(client.start(p['id'],updated['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_open_descriptors_are_values_and_wire_diagnostics_do_not_rebind(self):
        source=TREE+'''let ref:Contract=contract_ref("Node");fn identity(value:Contract)->Contract{value};
let open=identity(ref);let wire=parse_json(json_stringify(Tree));
[type_of(open),contract_info(open),wire,try{contract_accepts(wire,input)}catch e{e.code},
 try{contract_accepts(contract_union(["Any",open]),input)}catch e{e.code}]'''
        result=Framework.evaluate(source,{'input':GOOD})
        self.assertEqual(result[:2],['Contract',{'kind':'ref','name':'Node'}])
        self.assertEqual(result[2],{'$contract':Framework.evaluate(TREE+'contract_info(Tree)')})
        self.assertEqual(result[3:],['argument_error','argument_error'])


if __name__=='__main__':unittest.main()
