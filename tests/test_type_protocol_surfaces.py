"""Type protocols remain source/IR/IDE/headless-native language features."""
import copy, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from chaos.language import Evaluator, Parser
from chaos.ide_language import analyze, format_source
from chaos.native_specs import spec_for
from chaos.program import parse_program, format_program, decode_program, plain
from chaos.symbols import rename

SOURCE='''class First { let answer = 41 }
class Second { let answer = 42 }
class Adapter { fn __mro_entries__(self, original) { (First,) } }
class Predicate(Type) { fn __instancecheck__(cls, value) { value > 40 } }
class Virtual(metaclass=Predicate) {}
let adapter = Adapter();
class Product(adapter) {}
[Product.answer, isinstance(41, Virtual), issubclass(Product, First),
 Product.__orig_bases__ == (adapter,)]'''


class TypeProtocolSurfaceTests(unittest.TestCase):
    def test_native_metadata_and_bound_signature_parameters(self):
        for name,parameters in [('isinstance',['value','klass']),('issubclass',['child','base']),('Type.__instancecheck__',['cls','value']),('Type.__subclasscheck__',['cls','child'])]:
            with self.subTest(name=name):
                spec=spec_for(name);self.assertEqual([p.name for p in spec.parameters],parameters);self.assertEqual(spec.result_type,'Bool')
        for source,expected in [('Type.__instancecheck__(',['cls','value']),('Type.__subclasscheck__(',['cls','child']),('isinstance(',['value','klass']),('issubclass(',['child','base'])]:
            self.assertEqual([p['name'] for p in analyze(source)['signatureHelp']['parameters']],expected)
        self.assertTrue({'__instancecheck__','__subclasscheck__'} <= {c['label']for c in analyze('Type.')['completions']})

    def test_source_ir_format_and_structure_edit_execute_same_native_program(self):
        ir=parse_program(SOURCE);rendered=format_program(ir);decode_program(ir)
        self.assertEqual(Evaluator().run(rendered),[41,True,True,True])
        edited=copy.deepcopy(ir)
        def visit(node):
            if isinstance(node,dict):
                if node.get('kind')=='tuple':
                    for item in node['items']:
                        if item.get('kind')=='name' and item.get('name')=='First':item['name']='Second'
                for value in node.values():visit(value)
            elif isinstance(node,list):
                for value in node:visit(value)
        visit(edited)
        self.assertEqual(Evaluator().run(format_program(edited)),[42,True,False,True])
        self.assertEqual(plain(Parser(format_source(SOURCE)).program()),plain(Parser(SOURCE).program()))

    def test_analysis_highlight_and_refactoring_do_not_execute_protocols(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('executed user hook')):
            result=analyze(SOURCE);formatted=format_source(SOURCE);ir=parse_program(formatted)
            changed=rename(SOURCE,position=SOURCE.index('Adapter')+1,new_name='Choice')
        self.assertFalse(result['diagnostics'])
        self.assertIn('Choice',changed['files']['main.chaos']);self.assertEqual(Evaluator().run(changed['files']['main.chaos']),[41,True,True,True])
        self.assertTrue(result['tokens']);self.assertEqual(ir['root']['kind'],'block')
        product=next(s for s in result['symbols']if s['name']=='Product');self.assertEqual(product['type'],'Any')

    def test_module_alias_is_native_and_static_adapter_is_conservative(self):
        files={'base.chaos':'class Base{let answer=42}class Adapter{fn __mro_entries__(s,b){(Base,)}}export{Base,Adapter}'}
        source='import "base.chaos" as API;class C(API.Adapter()){}[C.answer,issubclass(C,API.Base)]'
        self.assertEqual(Evaluator().run(source,files=files),[42,True])
        result=analyze(source,files=files);self.assertFalse(result['diagnostics']);self.assertEqual(next(s for s in result['symbols']if s['name']=='C')['type'],'Any')
        self.assertTrue(any(d['code']=='type_mismatch'for d in analyze('class C(42){}')['diagnostics']))

    def test_headless_cli_runs_original_protocol_source(self):
        with tempfile.TemporaryDirectory()as directory:
            source=Path(directory)/'main.chaos';source.write_text(SOURCE)
            environment=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
            result=subprocess.run([sys.executable,'-m','chaos','run',str(source)],cwd=Path(__file__).resolve().parent.parent,env=environment,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[41,True,True,True])

    def test_installed_example_is_real_native_source(self):
        source=(Path(__file__).resolve().parent.parent/'examples'/'type_protocols.chaos').read_text()
        self.assertEqual(Evaluator().run(source),[42,42,True,False,True,True])
        self.assertEqual(analyze(source)['diagnostics'],[])
