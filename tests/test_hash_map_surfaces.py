"""General-key maps keep one native metadata and program/toolchain surface."""
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

NAMES = ('hash', 'hash_map', 'hash_get', 'hash_set', 'hash_remove', 'hash_has',
         'hash_items', 'hash_keys', 'hash_values', 'hash_info')
SOURCE = '''// Native keys stay ordinary expressions in Program IR.
let before: HashMap = hash_map([(("score", 1), 40)]);
let after = hash_set(before, ("score", 1), 42);
[before[("score", 1)], after[("score", 1)], hash_info(after).size]'''
EXPECTED = [40, 42, 1]


class HashMapSurfaces(unittest.TestCase):
    def test_native_metadata_exact_signatures_types_and_info_shape(self):
        evaluator = Evaluator()
        symbols = builtin_symbols()
        self.assertIn('HashMap', TYPES)
        source = 'let mapping:HashMap=hash_map()'
        at = source.index('HashMap')
        token = next(t for t in analyze(source)['tokens'] if t['from'] == at)
        self.assertEqual(token['type'], 'type')
        for name in NAMES:
            with self.subTest(name=name):
                value = evaluator.env.get(name)
                spec = spec_for(name)
                self.assertEqual(value.spec_id, spec.id)
                params = list(inspect.signature(value.call).parameters.values())
                self.assertEqual([p.name for p in params], [p.name for p in spec.parameters])
                self.assertEqual([p.default is not inspect.Parameter.empty for p in params],
                                 [p.optional for p in spec.parameters])
                self.assertEqual(symbols[name].returns, spec.result_type)
        fields = {f.name:f.type for f in SHAPES['HashMapInfo']}
        self.assertEqual(fields, {'size':'Int','ordered':'Bool','persistent':'Bool','maxEntries':'Int','keyPolicy':'String'})
        self.assertEqual(Framework.evaluate('hash_info(hash_map())'),
                         {'size':0,'ordered':True,'persistent':True,'maxEntries':10000,'keyPolicy':'native-hash-equality'})

    def test_module_aliases_shadowing_and_conservative_lookup(self):
        source = '''import "std/hash" as H;
let create=H.hash_map;let original=create([("a",40)]);
let updated=H.hash_set(original,"a",42);let removed=H.hash_remove(updated,"a");
let result=H.hash_get(updated,"a");let item=updated["a"];let present=H.hash_has(updated,"a");'''
        report = analyze(source)
        self.assertEqual(report['diagnostics'], [])
        types = {s['name']:s['type'] for s in report['symbols']}
        self.assertEqual([types[n]for n in ('original','updated','removed','result','item','present')],
                         ['HashMap','HashMap','HashMap','Any','Any','Bool'])
        shadow = 'fn hash_map(entries)->String{"local"};let text=hash_map([])'
        self.assertEqual(next(s['type']for s in analyze(shadow)['symbols']if s['name']=='text'), 'String')

    def test_hash_map_is_an_inert_synchronous_source(self):
        source = '''let mapping:HashMap=hash_map([("a",40),("b",2)]);
let iterator=iter(mapping);let keys=collect(mapping);
let copied=[key for key in mapping];let pairs=[(key,mapping[key]) for key in mapping];
fn count(*keys){length(keys)};let size=count(*mapping);
for key in mapping{hash_get(mapping,key)}'''
        report = analyze(source)
        self.assertEqual(report['diagnostics'], [])
        types = {s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('iterator','keys','copied','pairs')], ['Iterator','List','List','List'])
        self.assertEqual(types['key'], 'Any')
        self.assertEqual(Framework.evaluate(source+';[keys,copied,size]'), [['a','b'],['a','b'],2])

    def test_completions_signature_and_readonly_info_types(self):
        prefix = 'let mapping=hash_map();'
        labels = {x['label']for x in analyze(prefix+'hash_info(mapping).')['completions']}
        self.assertEqual(labels, {f.name for f in SHAPES['HashMapInfo']})
        report = analyze(prefix+'let info=hash_info(mapping);let size=info.size;let ordered=info.ordered;let policy=info.keyPolicy;')
        types = {s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[n]for n in ('info','size','ordered','policy')], ['Record','Int','Bool','String'])
        help = analyze(prefix+'hash_get(mapping=mapping,key="missing",default=')['signatureHelp']
        self.assertEqual([p['name']for p in help['parameters']], ['mapping','key','default'])
        self.assertEqual(help['activeParameter'], 2)
        self.assertTrue(set(NAMES)-{'hash'} <= {x['label']for x in analyze('hash_')['completions']})

    def test_source_ir_formatter_and_macro_keep_call_and_subscript_semantics(self):
        for source in (SOURCE,format_source(SOURCE,line_width=44),format_program(parse_program(SOURCE))):
            self.assertEqual(Framework.evaluate(source),EXPECTED)
            self.assertEqual(plain(decode_program(parse_program(source))),plain(Parser(SOURCE).program()))
        formatted = format_source(SOURCE)
        self.assertEqual(format_source(formatted),formatted)
        self.assertIn('// Native keys',formatted)
        macro = '''macro entry(make,key,value){quote{unquote(make)([(unquote(key),unquote(value))])}}
let mapping=entry!(hash_map,(20,22),42);mapping[(20,22)]'''
        for source in (macro,format_source(macro),format_program(parse_program(macro))):
            self.assertEqual(Framework.evaluate(source),42)

    def test_lsp_highlighting_and_rename_never_run_hash_protocol(self):
        source = '''class Key{
fn __hash__(self)->Int{tool("never",null)}
fn __eq__(self,other)->Bool{tool("never",other)}
}
let mapping:HashMap=hash_map([(Key(),42)]);hash_get(mapping,Key())'''
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')), \
             patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated source')):
            at = source.rindex('Key()')
            report = analyze(source,position=at)
            self.assertEqual(report['diagnostics'],[])
            help = convert('textDocument/signatureHelp',report,SimpleNamespace(name='main.chaos'),{'main.chaos':source})
            self.assertIn('mapping: HashMap',help['signatures'][0]['label'])
            self.assertIn('chaos-token-type',render_code(source,'chaos'))
            self.assertIn('HashMap',render_code('hash_info(hash_map()).size','chaos'))
            result = references(source,position=source.index('Key'))
            self.assertTrue(result['complete'])
            self.assertEqual(len(result['references']),3)
            changed = rename(source,position=source.index('Key'),new_name='JobKey')['files']['main.chaos']
            self.assertIn('JobKey()',changed)

    def test_http_sdk_ir_edit_preserves_saved_history_and_analysis(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            root=Path(folder)
            server=make_server('127.0.0.1',0,framework=app,state_dir=root/'control',update_home=root/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('hash-map-surface',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None)
                self.assertEqual(old.result(),EXPECTED)
                program=client.program(project['id'])['program']
                def literal(value):
                    if isinstance(value,dict):
                        if value.get('value')==42 and 'id'in value:return value
                        for child in value.values():
                            found=literal(child)
                            if found:return found
                    elif isinstance(value,list):
                        for child in value:
                            found=literal(child)
                            if found:return found
                target=literal(program['root']);self.assertIsNotNone(target)
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':target['id'],'field':'value','value':43}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),[40,43,1])
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source']),Framework.analyze(updated['source']))
            finally:
                server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
