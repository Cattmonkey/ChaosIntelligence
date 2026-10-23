"""Compile-time arithmetic and augmented targets keep phase and binding identity."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.program import parse_program, format_program

class AugmentedMacros(unittest.TestCase):
    def test_pure_operators_share_precedence_and_integer_rules(self):
        source='''macro value(){syntax_literal([2**3**2,-2**2,2**-2,+5,~5,
          6&3,4|1,7^3,3<<2,-8>>2,0<<(2**100),-1>>(2**100)])};value!()'''
        self.assertEqual(Framework.evaluate(source),[512,-4,0.25,5,-6,2,5,4,12,-2,0,-1])
        for expression in ['true&1','1.0|2','~false','1<<-1']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):
                Framework.evaluate('macro value(){syntax_literal('+expression+')};value!()')
        for expression in ['1<<14000','2**14000']:
            with self.subTest(expression=expression),self.assertRaises(ResourceLimit):
                Framework.evaluate('macro value(){syntax_literal('+expression+')};value!()')

    def test_template_local_targets_are_hygienic(self):
        source='''macro compute(value){quote{var count=unquote(value);count<<=1;count+=2;count}};
let count=100;[compute!(20),count]'''
        self.assertEqual(Framework.evaluate(source),[42,100])
        self.assertEqual(Framework.evaluate(format_program(parse_program(source))),[42,100])
        self.assertEqual(Framework.evaluate(Framework.format_source(source,line_width=40)),[42,100])

    def test_explicit_caller_name_and_effectful_item_target_once(self):
        calls=[]
        source='''macro bump(target,value){quote{unquote(target)+=unquote(value)}};
var number=40;
class Box{var value=40;fn __getitem__(self,key){tool("read",key);self.value}
 fn __setitem__(self,key,value){tool("write",key);self.value=value;null}}
let box=Box();fn receiver(){tool("receiver",null);box};fn key(){tool("key",null);"value"};
[bump!(number,2),number,bump!(receiver()[key()],2),box.value]'''
        result=Evaluator(tool_handler=lambda name,value:calls.append(name)).run(source)
        self.assertEqual(result,[42,42,42,42]);self.assertEqual(calls,['receiver','key','read','write'])

    def test_invalid_injected_target_rejected_before_any_effect(self):
        for target in ['41','fn(){1}','tool("bad",null)','do{1}']:
            calls=[]
            with self.subTest(target=target),self.assertRaises(LanguageError) as caught:
                Evaluator(tool_handler=lambda name,value:calls.append(name)).run(
                    'macro bump(target){quote{unquote(target)+=1}};tool("before",null);bump!('+target+')')
            self.assertEqual(caught.exception.code,'macro_error');self.assertEqual(calls,[])

    def test_free_target_cannot_capture_caller_and_mutation_stays_runtime(self):
        for source in [
            'macro bump(){quote{number+=1}};var number=41;bump!()',
            'macro bump(){let number=41;number+=1;syntax_literal(number)};bump!()',
        ]:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:Framework.evaluate(source)
            self.assertEqual(caught.exception.code,'macro_error')

    def test_imported_macro_preserves_public_protocol_name(self):
        files={'factory.chaos':'''macro make(value){quote{
class Counter{var value=unquote(value);fn __iadd__(self,other){self.value+=other;self}};
Counter}};export{make}'''}
        source='import "factory.chaos" as F;let C=F.make!(40);var c=C();let original=c;c+=2;[c.value,original.value,c==original]'
        self.assertEqual(Framework.evaluate(source,files=files),[42,42,True])

    def test_analysis_never_dispatches_runtime_protocol_or_macro_effect(self):
        from chaos.ide_language import analyze
        source='macro m(){syntax_literal((5<<2)|2)};var mask=m!();mask^=1;mask'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis must remain inert')):
            result=analyze(source)
        self.assertFalse(result['diagnostics'])
