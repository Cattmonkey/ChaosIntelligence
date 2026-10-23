import unittest
from chaos.language import Evaluator,LanguageError,ResourceLimit,serializable
from chaos.decimal_values import Decimal,DecimalContext

class DecimalValues(unittest.TestCase):
    def value(self,source):return Evaluator().run(source)
    def test_exact_scale_signed_zero(self):
        self.assertEqual(self.value('[decimal_text(decimal("0.10")+decimal("0.20")),decimal("-0.00").sign,decimal("-0.00").exponent]'),['0.30',1,-2])
    def test_constructor_exact_under_small_precision(self):
        self.assertEqual(self.value('with decimal_context(precision=2) as c {[decimal_text(decimal("123.450")),c.flags]}'),['123.450',[]])
    def test_fraction_explicit_rounded(self):
        self.assertEqual(self.value('with decimal_context(precision=3) as c {[decimal_text(decimal(fraction(1,7))),c.flags]}'),['0.143',['Inexact','Rounded']])
    def test_float_explicit_signal(self):
        self.assertEqual(self.value('[decimal(0.5)==decimal("0.5"),get_decimal_context().flags]'),[True,['FloatOperation']])
    def test_float_trap(self):
        self.assertEqual(self.value('with decimal_context(traps=["FloatOperation"]) as c {[try {decimal(0.5)} catch e {e.code},c.flags]}'),['decimal_signal',['FloatOperation']])
    def test_eight_roundings(self):
        expected={'ceiling':'2.35','down':'2.34','floor':'2.34','half_down':'2.34','half_even':'2.34','half_up':'2.35','up':'2.35','05up':'2.34'}
        for mode,result in expected.items():
            with self.subTest(mode=mode):self.assertEqual(self.value('with decimal_context(rounding="'+mode+'") as c {decimal_text(decimal_quantize(decimal("2.345"),decimal("0.01")))}'),result)
    def test_rounded_without_inexact(self):
        self.assertEqual(self.value('with decimal_context(precision=2) as c {[decimal_text(+decimal("1.00")),c.flags]}'),['1.0',['Rounded']])
    def test_trapped_flags_committed(self):
        self.assertEqual(self.value('with decimal_context(precision=2,traps=["Inexact","Rounded"]) as c {[try {decimal(1)/decimal(3)} catch e {e.code},c.flags]}'),['decimal_signal',['Inexact','Rounded']])
    def test_finite_only_signals_when_untrapped(self):
        for expr,signal in [('decimal(0)/decimal(0)','InvalidOperation'),('decimal(1)/decimal(0)','DivisionByZero'),('decimal("1e3")*decimal("1e3")','Overflow')]:
            with self.subTest(signal=signal):
                result=self.value('with decimal_context(emax=2,emin=-2,traps=[]) as c {[try {'+expr+'} catch e {e.code},c.flags]}')
                self.assertEqual(result[0],'decimal_signal');self.assertIn(signal,result[1])
    def test_subnormal_underflow_clamp(self):
        self.assertEqual(self.value('with decimal_context(precision=2,emin=-2,emax=2,traps=[]) as c {[decimal_text(decimal("1e-2")/decimal(3)),c.flags]}'),['0.003',['Inexact','Rounded','Subnormal','Underflow']])
        self.assertIn('Clamped',self.value('with decimal_context(precision=2,emin=-2,emax=2,clamp=true) as c {+decimal("1e2");c.flags}'))
    def test_quantize_no_underflow(self):
        flags=self.value('with decimal_context(precision=3,emin=-2,emax=2) as c {decimal_quantize(decimal("0.00111"),decimal("0.001"));c.flags}')
        self.assertNotIn('Underflow',flags);self.assertIn('Subnormal',flags)
    def test_type_contracts_and_strict_bool(self):
        self.assertEqual(self.value('fn f(x:Real)->Numeric{x};[type_of(f(decimal(1))),try{bool(decimal(1))}catch e{e.code},try{fn n(x:Number){x};n(decimal(1))}catch e{e.code}]'),['Decimal','argument_error','runtime_error'])
    def test_implicit_float_fraction_rejected(self):
        for operand in ['0.5','fraction(1,2)']:
            with self.subTest(operand=operand),self.assertRaises(LanguageError):self.value('decimal(1)+'+operand)
    def test_floor_mod_and_integer_round(self):
        self.assertEqual(self.value('[floordiv(decimal(-7),decimal(3)),decimal_text(decimal(-7)%decimal(3)),floor(decimal("-2.5")),ceil(decimal("-2.5")),round(decimal("2.5")),round(decimal("3.5"))]'),[-3,'2',-3,-2,2,4])
    def test_power_sqrt_aggregate(self):
        self.assertEqual(self.value('[decimal_text(decimal(2)**-3),decimal_text(sqrt(decimal("2.25"))),decimal_text(sum([decimal("0.1"),decimal("0.2")])),decimal_text(decimal(min([decimal(2),fraction(1,2)])))]'),['0.125','1.5','0.3','0.5'])
    def test_exact_real_comparison(self):
        self.assertEqual(self.value('[decimal("0.1")<0.1,decimal("0.5")==fraction(1,2),decimal("1.00")==decimal("1"),decimal("-0")==decimal("0")]'),[True,False,True,True])
    def test_set_canonical_quantum(self):
        self.assertEqual(self.value('length(set_of([decimal("1"),decimal("1.00"),decimal("-0"),decimal("0.00"),1]))'),3)
    def test_codec_preserves_scale_and_no_auto_decode(self):
        self.assertEqual(self.value('let raw=parse_json(json_stringify(numeric_encode(decimal("-0.00"))));[type_of(raw),decimal_text(numeric_decode(raw)),numeric_decode(raw).sign,numeric_decode(raw).exponent]'),['Record','-0.00',1,-2])
    def test_codec_invalid_fields(self):
        for s in ['numeric_decode({"$numeric":{kind:"Decimal",sign:0,coefficient:"01",exponent:0}})','numeric_decode({"$numeric":{kind:"Decimal",sign:true,coefficient:"1",exponent:0}})']:
            with self.assertRaises(LanguageError):self.value(s)
    def test_complex_decimal_real_domain(self):
        self.assertEqual(self.value('let z=complex(decimal("1.20"),decimal("2.0"));[type_of(z.real),decimal_text(conjugate(z).imag),type_of((z/z).real),(z/z).real==decimal(1),(z/z).imag==decimal(0)]'),['Decimal','-2.0','Decimal',True,True])
    def test_complex_exact_quotient_before_round(self):
        self.assertEqual(self.value('with decimal_context(precision=3) as c {let z=complex(decimal("123456789"),decimal("987654321"));[decimal_text((z/z).real),decimal_text((z/z).imag),c.flags]}'),['1','0',[]])
    def test_complex_large_opposite_exponents(self):
        self.assertEqual(self.value('let z=complex(decimal("1e2048"),decimal("1e-2048"));[(z/z).real==decimal(1),(z/z).imag==decimal(0)]'),[True,True])
    def test_complex_implicit_float_rejected(self):
        with self.assertRaises(LanguageError):self.value('complex(decimal(1),0.5)')
    def test_resource_and_configuration_bounds(self):
        for source in ['decimal("1e2049")','decimal("'+('1'*1025)+'")','decimal_context(precision=1025)','decimal_context(emin=-1025)','decimal_context(rounding="unknown")','decimal_context(traps=["Inexact","Inexact"])']:
            with self.subTest(source=source[:40]),self.assertRaises(LanguageError):self.value(source)
    def test_readonly_and_foreign_host_values(self):
        with self.assertRaises(LanguageError):self.value('let x=decimal(1);x.coefficient=2')
        class Foreign(Decimal):pass
        from chaos.numeric_values import validate
        with self.assertRaises(LanguageError):validate(Foreign())
    def test_macro_sealed_decimal_constructor(self):
        self.assertEqual(self.value('macro m(){syntax_literal([decimal("-0.00"),complex(decimal("0.10"),decimal(1))])};let decimal=fn(v){42};let complex=fn(a,b){42};let xs=m!();[xs[0].sign,xs[0].exponent,xs[1].real.coefficient]'),[1,-2,10])
    def test_macro_fixed_context_and_no_runtime_helpers(self):
        self.assertEqual(self.value('macro m(){syntax_literal(decimal(1)/decimal(7))};with decimal_context(precision=3) as c {decimal_text(m!())}'),'0.1428571428571428571428571429')
        with self.assertRaises(LanguageError):self.value('macro m(){syntax_literal(get_decimal_context())};m!()')

    def test_sqrt_fixed_half_even_at_current_precision(self):
        self.assertEqual(self.value('with decimal_context(precision=3,rounding="ceiling") as c {decimal_text(sqrt(decimal(2)))}'),'1.41')
    def test_decimal_zero_divisor_signals_all_paths(self):
        for operation,signal in [('floordiv(decimal(1),decimal(0))','DivisionByZero'),('decimal(1)%decimal(0)','InvalidOperation'),('complex(decimal(1),decimal(1))/complex(decimal(0),decimal(0))','DivisionByZero')]:
            with self.subTest(operation=operation):
                result=self.value('with decimal_context(traps=[]) as c {[try{'+operation+'}catch e{e.code},c.flags]}')
                self.assertEqual(result[0],'decimal_signal');self.assertIn(signal,result[1])

    def test_text_and_macro_independent_host_capitals(self):
        import decimal
        context=decimal.getcontext();before=context.capitals;default=decimal.DefaultContext.capitals
        try:
            for mode in (0,1):
                context.capitals=mode;decimal.DefaultContext.capitals=mode
                self.assertEqual(self.value('decimal_text(decimal("1E+200"))'),'1E+200')
                self.assertEqual(self.value('macro value(){syntax_literal(decimal("1E+200"))};decimal_text(value!())'),'1E+200')
                self.assertEqual(self.value('decimal_text(decimal("-0.00"))'),'-0.00')
        finally:
            context.capitals=before;decimal.DefaultContext.capitals=default
