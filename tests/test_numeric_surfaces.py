"""One numeric source/AST and inert tooling across code and visual authorship."""
import copy,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator,Parser,serializable
from chaos.ide_language import analyze,format_source
from chaos.native_specs import spec_for
from chaos.program import parse_program,format_program,decode_program,plain
from chaos.numeric_values import Fraction,Complex,encode
from chaos.symbols import rename
from chaos.compiler import compile_bundle
from chaos.modules import ProgramBundle

SOURCE='''import "std/math" as M;
let ratio:Fraction = M.fraction(1,3);
let phase:Complex = M.complex(ratio,M.fraction(2,5));
let energy = phase*M.conjugate(phase);
[energy.real==fraction(61,225),energy.imag==0,
 numeric_decode(parse_json(json_stringify(energy)))==energy]'''


class NumericSurfaceTests(unittest.TestCase):
    def test_native_signature_names_contracts_and_members(self):
        expected={'fraction':['numerator','denominator'],'complex':['real','imag'],'conjugate':['value'],'numeric_encode':['value'],'numeric_decode':['value']}
        for name,names in expected.items():
            self.assertEqual([p.name for p in spec_for(name).parameters],names)
            self.assertEqual([p['name']for p in analyze(name+'(')['signatureHelp']['parameters']],names)
        self.assertEqual({c['label']for c in analyze('fraction(1,3).')['completions']},{'numerator','denominator'})
        self.assertEqual({c['label']for c in analyze('complex(1,2).')['completions']},{'real','imag'})

    def test_exact_return_inference_and_additive_contracts(self):
        source='let q=fraction(1,3);let z=complex(q,1);let a=q+1;let b=q+0.0;let c=z*q;let r:Real=q;let n:Numeric=z;let rational:Rational=1;let part=q.numerator;'
        report=analyze(source);self.assertFalse(report['diagnostics'])
        types={s['name']:s['type']for s in report['symbols']}
        self.assertEqual([types[x]for x in ('q','z','a','b','c','part')],['Fraction','Complex','Fraction','Number','Complex','Int'])
        more={s['name']:s['type']for s in analyze('let a=fraction(5,3)%1;let b=floordiv(fraction(5,3),1);let c=floordiv(fraction(5,3),1.0)')['symbols']}
        self.assertEqual([more[x]for x in ('a','b','c')],['Fraction','Int','Number'])
        for source in ['let n:Number=fraction(1);','let n:Int=fraction(1);','let n:Real=complex(1);','fraction(true)']:
            with self.subTest(source=source):self.assertTrue(analyze(source)['diagnostics'])

    def test_format_ir_and_real_graph_call_argument_edit(self):
        ir=parse_program(SOURCE);decode_program(ir)
        self.assertEqual(Evaluator().run(format_program(ir)),[True]*3)
        changed=copy.deepcopy(ir)
        def visit(value):
            if isinstance(value,dict):
                if value.get('kind')=='literal' and value.get('value')==3:value['value']=6
                for child in value.values():visit(child)
            elif isinstance(value,list):
                for child in value:visit(child)
        visit(changed)
        self.assertEqual(Evaluator().run(format_program(changed)),[False,True,True])
        self.assertEqual(plain(Parser(format_source(SOURCE)).program()),plain(Parser(SOURCE).program()))

    def test_inert_analysis_format_and_rename_never_run_numeric_or_ast(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('ran user AST')),patch('chaos.numeric_values.make_fraction',side_effect=AssertionError('constructed numeric')):
            report=analyze(SOURCE);formatted=format_source(SOURCE);renamed=rename(SOURCE,position=SOURCE.index('ratio:')+1,new_name='exact')
        self.assertFalse(report['diagnostics']);self.assertTrue(report['tokens'])
        self.assertEqual(Evaluator().run(renamed['files']['main.chaos']),[True]*3)
        self.assertEqual(Evaluator().run(formatted),[True]*3)

    def test_macro_hygiene_survives_module_alias_source_and_ir_roundtrip(self):
        files={'values.chaos':'macro value(){syntax_literal(complex(fraction(1,3),1))}export{value}'}
        source='import "values.chaos" as M;let expected=complex(fraction(1,3),1);let fraction=fn(){0};let complex=fn(){0};M.value!()==expected'
        self.assertTrue(Evaluator().run(source,files=files));self.assertFalse(analyze(source,files=files)['diagnostics'])
        self.assertTrue(Evaluator().run(format_program(parse_program(source)),files=files))
        self.assertFalse(analyze('macro n(){syntax_literal([fraction(1,3),complex(1,2)])}n!()')['diagnostics'])

    def test_compiler_report_is_inert_and_conservative(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('ran AST')):
            report=compile_bundle(ProgramBundle(SOURCE))
        self.assertIn('unknown',report['summary']['effects'])

    def test_headless_cli_uses_same_numeric_json_codec(self):
        with tempfile.TemporaryDirectory()as directory:
            source=Path(directory)/'main.chaos';source.write_text('complex(fraction(1,3),1)')
            result=subprocess.run([sys.executable,'-m','chaos','run',str(source)],cwd=Path(__file__).resolve().parent.parent,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),encode(Complex(Fraction(1,3),1)))

    def test_installed_example_is_real_native_language_and_analyzable(self):
        source=(Path(__file__).resolve().parents[1]/'examples/numeric_tower.chaos').read_text()
        self.assertEqual(Evaluator().run(source),[True,True,'Fraction','Complex','Record',True]);self.assertEqual(analyze(source)['diagnostics'],[])

    def test_sdk_does_not_auto_decode_tag_records(self):
        wire=encode(Fraction(1,3))
        self.assertEqual(Framework.evaluate('type_of(input)',{'input':wire}),'Record')
        self.assertEqual(Framework.evaluate('numeric_decode(input)',{'input':wire}),wire)
