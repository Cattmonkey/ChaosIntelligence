"""Language operators and captured lvalues, not textual assignment rewriting."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser, ResourceLimit, tokenize
from chaos.operators import AUGMENTED


class AugmentedOperatorTests(unittest.TestCase):
    def run_code(self, source, **kwargs):
        return Evaluator(**kwargs).run(source)

    def test_token_boundaries_comments_and_existing_spread(self):
        source='x**=2;y<<=1;z>>=2;a|=3;b&=2;c^=1; // /= remains comment\nfn f(**kw){kw};f(**{x:42})'
        tokens=[t.value for t in tokenize(source)]
        for token in ('**=', '<<=', '>>=', '|=', '&=', '^='): self.assertIn(token,tokens)
        self.assertNotIn('/=',tokens)
        self.assertEqual(self.run_code('fn f(**kw){kw.x};[f(**{x:42}),2 //= still comment\n+3]'),[42,5])

    def test_precedence_associativity_and_explicit_await(self):
        self.assertEqual(self.run_code('[2**3**2,(2**3)**2,-2**2,(-2)**2,2**-2,+2**3,~2**3,1+2<<3,32>>1+2,1|2^3&4,3&2==2]'),[512,64,-4,4,.25,8,-9,24,4,3,True])
        self.assertEqual(self.run_code('async fn f(){3};[await f()**2,-await f()**2,2**await f()]'),[9,-9,8])
        self.assertEqual(Parser('var n=1;n+=2').program()[1][1][:3],('aug_assign',('name','n'),'+'))
        for source in ('1+=2','f()+=1','(a+b)*=2','[a,b]+=2','a+=b+=1','a&&=true'):
            with self.subTest(source=source), self.assertRaises(LanguageError): Parser(source).program()

    def test_contextual_names_accept_every_augmented_operator(self):
        for token in AUGMENTED:
            for name in ('await','yield','async','with','finally'):
                with self.subTest(token=token,name=name):
                    source='var '+name+'=4;'+name+token+'2;'+name
                    self.run_code(source)
                    self.run_code('fn* gen(){'+source+';yield 42};collect(gen())')

    def test_all_primitive_augmented_forms_return_computed_value(self):
        operands={'+':(7,2,9),'-':(7,2,5),'*':(7,2,14),'/':(7,2,3.5),'%':(7,2,1),'**':(7,2,49),
                  '&':(7,2,2),'|':(7,8,15),'^':(7,2,5),'<<':(7,2,28),'>>':(7,2,1)}
        self.assertEqual(set(AUGMENTED),{op+'=' for op in operands})
        for op,(a,b,expected) in operands.items():
            with self.subTest(op=op):
                self.assertEqual(self.run_code('var x='+str(a)+';let result=do{x'+op+'='+str(b)+'};[x,result]'),[expected,expected])

    def test_primitive_containers_remain_values(self):
        self.assertEqual(self.run_code('var xs=[1];let alias=xs;xs+=[2];var s="a";let original=s;s+="b";[alias,xs,original,s]'),[[1],[1,2],'a','ab'])
        for source in ('var xs=[1];xs[0]+=2','var r={x:1};r.x+=2','var s="a";s[0]+="b"'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_bits_are_strict_int_and_negative_shifts_reject(self):
        self.assertEqual(self.run_code('[-1&3,-1|2,-1^2,-8>>2,~0,+1.5]'),[3,-1,-3,-2,-1,1.5])
        for source in ('true&1','1|false','1.0^2','1<<2.0','true>>1','~true','~1.0','+true','1<<-1','0<<-1','1>>-1'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_shift_preflight_and_saturation_before_allocation(self):
        result=self.run_code('let huge=pow(2,1000);[0<<huge,1>>huge,-1>>huge,-9>>huge,1<<13999]')
        self.assertEqual(result[:4],[0,0,-1,-1]);self.assertEqual(result[4].bit_length(),14000)
        for source in ('1<<14000','-1<<14000','1<<pow(2,1000)','2**14001','var n=1;n<<=14000'):
            with self.subTest(source=source),self.assertRaises(ResourceLimit):self.run_code(source)

    def test_all_direct_and_reflected_bit_protocols(self):
        self.assertEqual(self.run_code('''class C{fn __and__(s,x){1}fn __or__(s,x){2}fn __xor__(s,x){3}
fn __lshift__(s,x){4}fn __rshift__(s,x){5}fn __pow__(s,x){6}fn __pos__(s){7}fn __invert__(s){8}
fn __rand__(s,x){11}fn __ror__(s,x){12}fn __rxor__(s,x){13}fn __rlshift__(s,x){14}fn __rrshift__(s,x){15}fn __rpow__(s,x){16}}
let c=C();[c&0,c|0,c^0,c<<0,c>>0,c**0,+c,~c,0&c,0|c,0^c,0<<c,0>>c,0**c]'''),[1,2,3,4,5,6,7,8,11,12,13,14,15,16])

    def test_all_inplace_protocols_and_computed_identity(self):
        names=['__iadd__','__isub__','__imul__','__itruediv__','__imod__','__ipow__','__iand__','__ior__','__ixor__','__ilshift__','__irshift__']
        for token,name in zip(AUGMENTED,names):
            with self.subTest(token=token):
                self.assertEqual(self.run_code('class C{var n=1;fn '+name+'(s,x){s.n=x;s}}var c=C();let old=c;let result=do{c'+token+'42};[c==old,result==old,c.n]'),[True,True,42])

    def test_inplace_first_then_binary_subclass_and_singleton_fallback(self):
        source='''var calls=[];class A{fn __iadd__(s,x){calls=append(calls,"i");NotImplemented}fn __add__(s,x){calls=append(calls,"add");42}}
class B(A){fn __radd__(s,x){calls=append(calls,"reverse");NotImplemented}}
var a=A();a+=B();[a,calls]'''
        self.assertEqual(self.run_code(source),[42,['i','reverse','add']])
        self.assertEqual(self.run_code('class A{fn __add__(s,x){40+x}}var a=A();a+=2;a'),42)
        self.assertEqual(self.run_code('class A{fn __iadd__(s,x){{"$notImplemented":true}}fn __add__(s,x){99}}var a=A();a+=2;a'),{'$notImplemented':True})

    def test_fallback_does_not_catch_errors_or_start_declining_tasks(self):
        self.assertEqual(self.run_code('var calls=[];class C{fn __iadd__(s,x){calls=append(calls,"i");throw "stop"}fn __add__(s,x){calls=append(calls,"add");42}}var c=C();try{c+=1}catch e{null};calls'),['i'])
        self.assertEqual(self.run_code('var calls=[];class C{async fn __iadd__(s,x){calls=append(calls,"i");NotImplemented}fn __add__(s,x){calls=append(calls,"add");42}}var c=C();c+=1;let before=calls;let value=await c;[before,calls,value==NotImplemented]'),[[],['i'],True])

    def test_cold_inplace_generators_and_unary_tasks(self):
        self.assertEqual(self.run_code('''var calls=0;class C{fn* __iadd__(s,x){calls+=1;yield x}async fn __pos__(s){calls+=1;42}async fn* __invert__(s){calls+=1;yield 41}}
var c=C();c+=40;let task=+C();let stream=~C();let before=calls;[before,collect(c),await task,await acollect(stream),calls]'''),[0,[40],42,[41],3])

    def test_contract_failure_preserves_cell_and_cold_task(self):
        self.assertEqual(self.run_code('var x:Int=1;let reason=try{x/=2;null}catch e{e.code};[x,reason]'),[1,'runtime_error'])
        self.assertEqual(self.run_code('var calls=0;class C{async fn __iadd__(s,x){calls+=1;s}}var c:C=C();try{c+=1}catch e{null};[type_of(c),calls]'),['Instance',0])

    def test_immutable_or_missing_name_rejected_before_rhs_and_hooks(self):
        for declaration,target in [('let x=1;','x'),('','unknown')]:
            source='var calls=0;fn rhs(){calls+=1;2};'+declaration+'try{'+target+'+=rhs()}catch e{null};calls'
            self.assertEqual(self.run_code(source),0)

    def test_read_old_cell_before_effectful_rhs(self):
        self.assertEqual(self.run_code('var x=1;fn rhs(){x=100;2};x+=rhs();x'),3)
        self.assertEqual(self.run_code('var x=1;async fn rhs(){x=100;await sleep(0);2};x+=await rhs();x'),3)

    def test_receiver_key_getter_rhs_operation_setter_once(self):
        source='''var events=[];fn mark(name,value){events=append(events,name);value}
class N{fn __iadd__(s,x){events=append(events,"op");40+x}}
class C{var n=N();fn __getitem__(s,k){events=append(events,"get");s.n}fn __setitem__(s,k,v){events=append(events,["set",k,v]);s.n=v;null}}
let c=C();let value=do{mark("receiver",c)[mark("key",7)]+=mark("rhs",2)};[value,c.n,events]'''
        self.assertEqual(self.run_code(source),[42,42,['receiver','key','get','rhs','op',['set',7,42]]])

    def test_slice_bounds_and_key_identity_captured_once(self):
        source='''var events=[];var captured=null;fn mark(name,value){events=append(events,name);value}
class C{fn __getitem__(s,k){captured=k;events=append(events,"get");40}fn __setitem__(s,k,v){events=append(events,[k==captured,k.start,k.stop,k.step,v]);null}}
let result=do{mark("receiver",C())[mark("start",1):mark("stop",5):mark("step",2)]+=mark("rhs",2)};[result,events]'''
        self.assertEqual(self.run_code(source),[42,['receiver','start','stop','step','get','rhs',[True,1,5,2,42]]])

    def test_member_receiver_is_stable_when_rhs_rebinds_original_name(self):
        source='class C{var x=1}var c=C();let old=c;fn rhs(){c=C();c.x=99;2};c.x+=rhs();[old.x,c.x]'
        self.assertEqual(self.run_code(source),[3,99])

    def test_property_get_once_set_once_and_legacy_member(self):
        source='''var events=[];class C{var value=40;let x=property(fn(s){events=append(events,"get");s.value},fn(s,v){events=append(events,"set");s.value=v;null})}
let c=C();let result=do{c.["x"]+=2};[result,c.value,events]'''
        self.assertEqual(self.run_code(source),[42,42,['get','set']])
        self.assertEqual(self.run_code('class C{var x=40}let c=C();let result=do{c["x"]+=2};[result,c.x]'),[42,42])

    def test_setter_resolves_current_class_slot_after_rhs(self):
        source='''var events=[];class C{var value=40;fn __getitem__(s,k){s.value};var __setitem__=fn(k,v){events=append(events,"old");null}}
fn rhs(){C.__setitem__=fn(k,v){events=append(events,["new",k,v]);null};2};let c=C();c["x"]+=rhs();events'''
        self.assertEqual(self.run_code(source),[['new','x',42]])

    def test_descriptor_bound_inplace_uses_class_mro_not_instance_override(self):
        self.assertEqual(self.run_code('''var events=[];class D{fn __get__(s,obj,owner){events=append(events,"bind");fn(x){events=append(events,"call");40+x}}}
class C{var __iadd__=D()}var c=C();c.__iadd__=fn(x){99};c+=2;[c,events]'''),[42,['bind','call']])

    def test_write_result_must_be_null_but_augmented_result_is_computed(self):
        for method in ['fn __setitem__(s,k,v){42}','async fn __setitem__(s,k,v){null}']:
            source='class C{fn __getitem__(s,k){1}'+method+'};let c=C();c[0]+=1'
            with self.subTest(method=method),self.assertRaises(LanguageError):self.run_code(source)
        self.assertEqual(self.run_code('var called=0;async fn cold(){called+=1;null};class C{fn __getitem__(s,k){1}fn __setitem__(s,k,v){cold()}}let c=C();try{c[0]+=1}catch e{null};called'),0)

    def test_read_failure_skips_rhs_and_operator_failure_skips_setter(self):
        self.assertEqual(self.run_code('var calls=[];fn rhs(){calls=append(calls,"rhs");1};class C{fn __getitem__(s,k){throw "get"}fn __setitem__(s,k,v){calls=append(calls,"set");null}}try{C()[0]+=rhs()}catch e{null};calls'),[])
        self.assertEqual(self.run_code('var calls=[];class N{fn __iadd__(s,x){throw "op"}}class C{fn __getitem__(s,k){N()}fn __setitem__(s,k,v){calls=append(calls,"set");null}}try{C()[0]+=1}catch e{null};calls'),[])


if __name__=='__main__':unittest.main()
