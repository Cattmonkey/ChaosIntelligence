"""Exact native numbers preserve the established AST, authority and numeric ABI."""
import json, math, random, unittest
from fractions import Fraction as Reference
from unittest.mock import patch
from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable, check_type
from chaos.numeric_values import Fraction, Complex, encode, decode, binary, compare, equal, MAX_BITS
from chaos.set_values import SetValue, set_equal
from chaos.debugger import preview


class NumericValueTests(unittest.TestCase):
    def code(self, source, **options): return Evaluator(**options).run(source)

    def test_canonical_fraction_and_retained_identity(self):
        value=self.code('fraction(-6,-8)');self.assertEqual((value.numerator,value.denominator),(3,4))
        self.assertEqual(self.code('[type_of(fraction(4,2)),type_of(fraction(0,-7)),fraction(0,-7).denominator]'),['Fraction','Fraction',1])

    def test_complex_component_normalization_and_retained_identity(self):
        self.assertEqual(self.code('[type_of(complex(fraction(2,2),0)),type_of(complex(fraction(2,2),0).real),type_of(complex(fraction(1,3),0).real)]'),['Complex','Int','Fraction'])
        self.assertEqual(self.code('[type_of(complex(fraction(1,3),0.0).real),type_of(complex(1,0.0).imag)]'),['Number','Number'])

    def test_constructor_defaults_keywords_and_strict_inputs(self):
        self.assertTrue(self.code('fraction()==fraction(denominator=1,numerator=0)&&complex()==complex(imag=0,real=0)'))
        for source in ['fraction(true)','fraction(1,1.0)','fraction(1,0)','complex(true)','complex(complex(1))','fraction(1,1,2)','complex(missing=2)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_immutable_readonly_closed_members(self):
        self.assertEqual(self.code('[getattr(fraction(2,3),"numerator"),getattr(complex(1,2),"imag"),hasattr(fraction(1),"missing")]'),[2,2,False])
        for source in ['fraction(1).numerator=42','complex(1).real=42','fraction(1).__dict__','setattr(complex(1),"imag",3)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)
        self.assertEqual(self.code('dir(fraction(1))'),['numerator','denominator'])

    def test_additive_contracts_do_not_widen_number_or_int(self):
        self.assertTrue(self.code('let a:Rational=fraction(1,3);let b:Real=a;let c:Numeric=complex(b,1);let d:Complex=c;c==complex(a,1)'))
        for source in ['let n:Number=fraction(1);n','let n:Int=fraction(1);n','let n:Real=complex(1);n','let n:Numeric=true;n','index(fraction(1))']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_legacy_int_float_division_and_typed_equality(self):
        self.assertEqual(self.code('[type_of(1/2),1/2,1==1.0,true==1,2**-1]'),['Number',.5,False,False,.5])
        self.assertEqual(self.code('[fraction(1)==1,fraction(1)==fraction(2,2),complex(1)==1,complex(1)==complex(1.0),complex(fraction(1),0)==complex(1)]'),[False,True,False,False,True])

    def test_fraction_arithmetic_exact_even_integral(self):
        self.assertTrue(self.code('[fraction(1,3)+2,2-fraction(1,3),fraction(2,3)*3,2/fraction(2,3)]==[fraction(7,3),fraction(5,3),fraction(2),fraction(3)]'))

    def test_fraction_arithmetic_independent_random_reference(self):
        rng=random.Random(709)
        for unused in range(40):
            a,b,c,d=rng.randrange(-100,100),rng.randrange(1,100),rng.randrange(1,100),rng.randrange(1,100)
            for op in ('+','-','*','/'):
                result=binary(op,Fraction(a,b),Fraction(c,d))
                x,y=Reference(a,b),Reference(c,d)
                expected={'+':lambda:x+y,'-':lambda:x-y,'*':lambda:x*y,'/':lambda:x/y}[op]()
                self.assertEqual((result.numerator,result.denominator),(expected.numerator,expected.denominator))

    def test_cross_cancel_large_fraction_before_allocation(self):
        self.assertTrue(self.code('let n=1<<10000;fraction(n,3)*fraction(3,n)==fraction(1)'))

    def test_mixed_float_is_explicit_inexact_and_finite(self):
        self.assertEqual(self.code('[type_of(fraction(1,3)+0.0),fraction(1,2)+0.5,complex(1,2)+0.5]')[:2],['Number',1.0])
        for source in ['fraction(1<<14000)','complex(1e308,1e308)*complex(1e308,1e308)','number("inf")']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_complex_exact_add_multiply_division(self):
        self.assertTrue(self.code('let z=complex(fraction(1,3),fraction(2,5));[z+conjugate(z),z*conjugate(z),z/z]==[complex(fraction(2,3)),complex(fraction(61,225)),complex(1)]'))

    def test_large_exact_complex_division_uses_bounded_scratch(self):
        self.assertTrue(self.code('let z=complex(1<<8000,1<<8000);z/z==complex(1)'))

    def test_large_finite_complex_division_avoids_square_overflow(self):
        self.assertTrue(self.code('let z=complex(1e308,1e308);z/z==complex(1.0,0.0)'))

    def test_complex_forbidden_order_floor_mod_and_bit_operations(self):
        for source in ['complex(1)<2','floordiv(complex(1),2)','complex(1)%2','complex(1)&1','~fraction(1)','min([complex(1)])','sort([complex(1)])']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_new_numeric_integer_powers_positive_negative_zero(self):
        self.assertTrue(self.code('[fraction(2,3)**-2,fraction(2,3)**0,complex(0,1)**4,complex(1,1)**-1]==[fraction(9,4),fraction(1),complex(1),complex(fraction(1,2),fraction(-1,2))]'))
        for source in ['fraction(2)**0.5','complex(2)**fraction(1)','fraction(0)**-1','complex(0)**-1']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_powers_are_bounded_and_unit_exponent_does_not_loop(self):
        self.assertTrue(self.code('fraction(-1)**(1<<1000)==fraction(1)'))
        for source in ['fraction(2)**1000000000','complex(2)**(1<<1000)']:
            with self.subTest(source=source),self.assertRaises(ResourceLimit):self.code(source)

    def test_precise_mixed_real_comparison_not_float_rounding(self):
        self.assertTrue(self.code('fraction(1,10)<0.1&&fraction((1<<100)+1)>1<<100'))
        self.assertEqual(compare(Fraction(1,10),.1),-1)

    def test_real_floor_ceil_round_are_exact_ties_to_even(self):
        self.assertEqual(self.code('[floor(fraction(-7,3)),ceil(fraction(-7,3)),round(fraction(5,2)),round(fraction(7,2)),round(fraction(-5,2)),round(fraction(-7,2))]'),[-3,-2,2,4,-2,-4])

    def test_real_floor_division_and_remainder_sign(self):
        self.assertTrue(self.code('[floordiv(fraction(-7,3),fraction(2,3)),fraction(-7,3)%fraction(2,3)]==[-4,fraction(1,3)]'))

    def test_sum_extrema_sort_and_explicit_conversion(self):
        self.assertTrue(self.code('sum([fraction(1,3),complex(1,2),fraction(2,3)])==complex(2,2)'))
        self.assertTrue(self.code('sort([fraction(1,10),0.1,0])==[0,fraction(1,10),0.1]'))
        self.assertTrue(self.code('min([fraction(1,2),1])==fraction(1,2)&&max([fraction(1,2),1])==1'))
        self.assertEqual(self.code('[number(fraction(1,2)),sqrt(fraction(1,4)),abs(complex(3,4))]'),[.5,.5,5.0])

    def test_boolean_and_complex_real_conversion_are_explicit_rejections(self):
        for source in ['bool(fraction(0))','bool(complex(0))','number(complex(1))','sqrt(complex(1))','if fraction(1){1}else{0}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_numeric_codec_lossless_roundtrip_and_json_no_promotion(self):
        self.assertTrue(self.code('let n=complex(fraction(1<<10000,3),fraction(-7,2));numeric_decode(parse_json(json_stringify(numeric_encode(n))))==n'))
        self.assertEqual(self.code('type_of(parse_json(json_stringify(fraction(1,3))))'),'Record')
        self.assertEqual(self.code('type_of(numeric_decode(numeric_encode(1)))'),'Int')

    def test_codec_rejects_bad_shapes_and_noncanonical_text(self):
        invalid=[{}, {'$numeric':{'kind':'Int','value':'01'}},{'$numeric':{'kind':'Int','value':True}}, {'$numeric':{'kind':'Number','value':'nan'}},{'$numeric':{'kind':'Number','value':'1'}},{'$numeric':{'kind':'Fraction','numerator':'1','denominator':'0'}},{'$numeric':{'kind':'Int','value':'9'*5000}}, {'$numeric':{'kind':'Int','value':'1'},'extra':0}]
        for value in invalid:
            with self.subTest(value=str(value)[:60]),self.assertRaises(LanguageError):decode(value)
        with self.assertRaises(LanguageError):decode({'$numeric':{'kind':'Complex','real':encode(Complex(1)), 'imag':encode(0)}})

    def test_pure_set_keys_are_typed_canonical_and_nested(self):
        self.assertEqual(self.code('length({fraction(1),fraction(2,2),1,1.0,complex(1),complex(fraction(1),0),(fraction(1),)})'),5)
        self.assertTrue(set_equal(SetValue([Fraction(1,2),Complex(1,2)]),SetValue([Complex(1,2),Fraction(2,4)])))

    def test_pure_pattern_equality_of_nested_numeric_values(self):
        self.assertTrue(self.code('let saved=complex(fraction(1,3),2);match [complex(fraction(2,6),2)]{[x] if x==saved=>true,_=>false}'))
        e=Evaluator();self.assertTrue(e.equal({'x':[Fraction(1,3)]},{'x':[Fraction(2,6)]}))

    def test_macro_arithmetic_and_hygienic_constructor_capture(self):
        self.assertTrue(self.code('macro n(){syntax_literal(complex(fraction(1,3)+fraction(1,6),1)**2)}let fraction=fn(){0};let complex=fn(){0};n!()==numeric_decode({"$numeric":{kind:"Complex",real:{"$numeric":{kind:"Fraction",numerator:"-3",denominator:"4"}},imag:{"$numeric":{kind:"Int",value:"1"}}}})'))

    def test_macro_set_codec_and_pure_aggregate_members(self):
        self.assertTrue(self.code('macro n(){syntax_literal(set_of([fraction(1),fraction(2,2),complex(1)]))}length(n!())==2'))
        self.assertTrue(self.code('macro n(){syntax_literal(numeric_decode(numeric_encode(-fraction(1,3))))}n!()==fraction(-1,3)'))

    def test_macro_namespace_cannot_obtain_runtime_tool(self):
        seen=[]
        with self.assertRaises(LanguageError):self.code('macro n(){tool("secret",fraction(1))}n!()',tool_handler=lambda n,v:seen.append(n))
        self.assertEqual(seen,[])

    def test_reflected_and_inplace_instance_protocols_receive_native_values(self):
        self.assertTrue(self.code('class X{fn __radd__(s,v){v+fraction(1,3)}fn __iadd__(s,v){v}}fraction(1,3)+X()==fraction(2,3)'))
        self.assertTrue(self.code('class X{fn __iadd__(s,v){v}}var x=X();x+=fraction(1,3);x==fraction(1,3)'))

    def test_class_storage_and_metadata_preserve_numeric_identity(self):
        self.assertTrue(self.code('class C{var x:Fraction=fraction(1,3);fn read(s){s.x}}let c=C();c.x=fraction(2,3);c.read()==fraction(2,3)&&class_members(C).x.value==fraction(1,3)'))

    def test_numeric_values_cross_tool_boundary_only_as_records(self):
        seen=[];result=self.code('tool("save",{exact:fraction(1,3),z:complex(1,2)})',tool_handler=lambda n,v:seen.append(v)or v)
        self.assertEqual(result,seen[0]);self.assertEqual(result['exact'],encode(Fraction(1,3)))

    def test_task_and_generator_return_contracts_retain_native_values(self):
        self.assertTrue(self.code('async fn f()->Numeric{complex(fraction(1,3),1)}let task=f();await task==complex(fraction(1,3),1)'))
        self.assertTrue(self.code('fn* f()->Numeric{yield fraction(1,3);return fraction(0)}collect(f())==[fraction(1,3)]'))

    def test_debugger_preview_uses_closed_public_fields(self):
        self.assertEqual(preview(Fraction(1,3)),{'type':'Fraction','numerator':1,'denominator':3})
        self.assertEqual(preview(Complex(Fraction(1,3),1)),{'type':'Complex','real':{'type':'Fraction','numerator':1,'denominator':3},'imag':1})

    def test_runtime_and_macro_checkpoints_cancel_euclid(self):
        calls=[]
        def cancel():
            calls.append(1)
            if len(calls)==4:raise ResourceLimit('cancel checkpoint')
        with self.assertRaises(ResourceLimit):binary('*',Fraction(832040,1346269),Fraction(2178309,3524578),cancel)
        self.assertEqual(len(calls),4)

    def test_large_fraction_floor_quotient_checks_only_final_int(self):
        self.assertEqual(self.code('let n=1<<8000;floordiv(fraction(n,n-1),fraction(n-1,n))'),1)

    def test_scaled_complex_division_large_and_small_divisor(self):
        self.assertTrue(self.code('complex(1e308,1e308)/complex(1.0,1.0)==complex(1e308,0.0)'))
        self.assertTrue(self.code('complex(1e308,0.0)/complex(0.5,0.5)==complex(1e308,-1e308)'))
        self.assertTrue(self.code('complex(1.0,2.0)**0==complex(1.0,0.0)'))

    def test_explicit_decode_keeps_euclid_checkpoints(self):
        calls=[]
        def tick():
            calls.append(1)
            if len(calls)==3:raise ResourceLimit('cancel')
        with self.assertRaises(ResourceLimit):decode({'$numeric':{'kind':'Fraction','numerator':'832040','denominator':'1346269'}},tick)
        self.assertEqual(len(calls),3)

    def test_macro_reads_closed_numeric_members_without_runtime_protocol(self):
        self.assertEqual(self.code('macro n(){syntax_literal([fraction(2,3).numerator,complex(1,2).imag])}n!()'),[2,2])
