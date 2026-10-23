"""Strict primitives, cooperative user operators, fallback and resource boundaries."""
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, serializable
from chaos.operators import NOT_IMPLEMENTED
from chaos.suspension import TaskCancelled


class OperatorProtocolTests(unittest.TestCase):
    def run_code(self, source, **kwargs):return Evaluator(**kwargs).run(source)

    def test_primitive_behavior_and_builtin_names(self):
        self.assertEqual(self.run_code('[1+2,4-2,3*4,3/2,7%3,-4,abs(-4),pow(2,5),floordiv(7,2),floordiv(-7,2),floordiv(7.0,2),length("😀"),contains([1,2],2),contains({a:1},"a"),bool(true)]'),[3,2,12,1.5,1,-4,4,32,3,-4,3.0,1,True,True,True])
        self.assertEqual(self.run_code('[1==1.0,true==1,[1]==[1.0],{a:1}=={a:1.0},"a"+"b",[1]+[2]]'),[False,False,False,False,'ab',[1,2]])
        self.assertEqual(self.run_code('let x=7; x // still a comment\n+2'),9)
        for source in ['true+1','1+true','"a"<"b"','bool(1)','bool("")','bool([])','bool(null)','if 1{1}else{2}','!1','1&&true','false||1']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_all_arithmetic_and_unary_protocols(self):
        source='''class N{var value=0;fn __init__(self,value){self.value=value;null}
fn __add__(self,x){self.value+x}fn __sub__(self,x){self.value-x}fn __mul__(self,x){self.value*x}
fn __truediv__(self,x){self.value/x}fn __mod__(self,x){self.value%x}fn __pow__(self,x){pow(self.value,x)}
fn __floordiv__(self,x){floordiv(self.value,x)}fn __neg__(self){-self.value}fn __abs__(self){abs(self.value)}}
let n=N(7);[n+2,n-2,n*2,n/2,n%2,pow(n,2),floordiv(n,2),-n,abs(N(-7))]'''
        self.assertEqual(self.run_code(source),[9,5,14,3.5,1,49,3,-7,7])

    def test_all_reflected_protocols_from_primitive(self):
        source='''class N{fn __radd__(self,x){x+2}fn __rsub__(self,x){x-2}fn __rmul__(self,x){x*2}
fn __rtruediv__(self,x){x/2}fn __rmod__(self,x){x%2}fn __rpow__(self,x){pow(x,2)}fn __rfloordiv__(self,x){floordiv(x,2)}}
let n=N();[7+n,7-n,7*n,7/n,7%n,pow(7,n),floordiv(7,n)]'''
        self.assertEqual(self.run_code(source),[9,5,14,3.5,1,49,3])

    def test_arithmetic_subclass_reverse_order_only_new_member(self):
        self.assertEqual(self.run_code('''var calls=[];class A{fn __add__(self,x){calls=append(calls,"A+");NotImplemented}fn __radd__(self,x){calls=append(calls,"Ar");42}}
class B(A){fn __radd__(self,x){calls=append(calls,"Br");NotImplemented}}try{A()+B()}catch e{null};calls'''),['Br','A+'])
        self.assertEqual(self.run_code('''var calls=[];class A{fn __add__(self,x){calls=append(calls,"A+");NotImplemented}fn __radd__(self,x){calls=append(calls,"Ar");42}}
class B(A){}let value=A()+B();[value,calls]'''),[42,['A+','Ar']])
        self.assertEqual(self.run_code('''var calls=[];class A{fn __add__(self,x){calls=append(calls,"A+");NotImplemented}fn __radd__(self,x){calls=append(calls,"Ar");42}}
try{A()+A()}catch e{null};calls'''),['A+'])

    def test_unrelated_fallback_and_operand_evaluation_order(self):
        self.assertEqual(self.run_code('''var calls=[];fn mark(n,x){calls=append(calls,n);x}
class L{fn __add__(self,x){calls=append(calls,"left");NotImplemented}}
class R{fn __radd__(self,x){calls=append(calls,"right");42}}
let value=mark("a",L())+mark("b",R());[value,calls]'''),[42,['a','b','left','right']])

    def test_comparison_pairs_and_subclass_priority(self):
        self.assertEqual(self.run_code('''var calls=[];class A{fn __lt__(self,x){calls=append(calls,"lt");true}fn __gt__(self,x){calls=append(calls,"gt");false}}
class B(A){}let value=A()<B();[value,calls]'''),[False,['gt']])
        source='''class C{fn __lt__(self,x){true}fn __le__(self,x){true}fn __gt__(self,x){false}fn __ge__(self,x){false}}
let c=C();[c<0,c<=0,c>0,c>=0,0<c,0<=c,0>c,0>=c]'''
        self.assertEqual(self.run_code(source),[True,True,False,False,False,False,True,True])

    def test_equality_identity_fallback_and_ne_then_eq(self):
        self.assertEqual(self.run_code('''var calls=[];class C{fn __eq__(self,x){calls=append(calls,"eq");NotImplemented}fn __ne__(self,x){calls=append(calls,"ne");NotImplemented}}
let c=C();let same=c==c;let different=c!=C();[same,different,calls]'''),[True,True,['eq','eq','ne','ne','eq','eq']])
        self.assertEqual(self.run_code('class C{fn __eq__(self,x){false}}let c=C();[c==c,c!=c]'),[False,True])
        self.assertEqual(self.run_code('class C{fn __eq__(self,x){throw "must not call"}fn __ne__(self,x){false}}C()!=C()'),False)
        self.assertEqual(self.run_code('class C{}let c=C();[c==c,c==C(),c!=C(),c==1]'),[True,False,True,False])

    def test_eq_subclass_priority_and_reflection_from_primitive(self):
        self.assertEqual(self.run_code('''var calls=[];class A{fn __eq__(self,x){calls=append(calls,class_of(self));true}}class B(A){}
let same=A()==B();[same,calls[0]==B,length(calls)]'''),[True,True,1])
        self.assertEqual(self.run_code('class C{fn __eq__(self,x){x==42}}[42==C(),C()==42,1!=C()]'),[True,True,True])

    def test_containers_equal_in_left_order_and_short_circuit(self):
        source='''var seen=[];class C{var n=0;fn __init__(self,n){self.n=n;null}fn __eq__(self,x){seen=append(seen,self.n);self.n==x}}
let result=[C(1),{b:C(2),a:C(3)}]==[1,{a:3,b:2}];let first=seen;seen=[];
let different=[C(4),C(5)]==[0,5];[result,first,different,seen]'''
        self.assertEqual(self.run_code(source),[True,[1,2,3],False,[4]])
        self.assertEqual(self.run_code('class C{fn __eq__(self,x){true}}[C()]!=[42]'),False)

    def test_primitive_concat_does_not_expand_shared_container_graph(self):
        source='fn nest(n){if n==0{[1]}else{let a=nest(n-1);[a,a]}}let a=nest(35);let joined=[a]+[a];joined==[a,a]'
        self.assertIs(self.run_code(source,step_limit=2000),True)

    def test_repeated_container_alias_does_not_memoize_user_equality(self):
        self.assertEqual(self.run_code('var calls=0;class C{fn __eq__(self,x){calls=calls+1;calls==1}}let a=[C()];let b=[42];let result=[a,a]==[b,b];[result,calls]'),[False,2])

    def test_pattern_matching_remains_pure_structure(self):
        self.assertEqual(self.run_code('class C{fn __eq__(self,x){throw "effect forbidden"}}match [C()]{[42]=>false,_=>true}'),True)
        evaluator=Evaluator();c=evaluator.run('class C{fn __eq__(self,x){throw "no"}}C()')
        self.assertFalse(evaluator.equal(c,42));self.assertTrue(evaluator.equal(c,c))

    def test_descriptor_methods_class_mro_and_instance_override_ignored(self):
        self.assertEqual(self.run_code('''class Get{fn __get__(self,obj,owner){fn(other){42}}}class C{let __add__=Get();var __sub__=fn(other){41}}
let c=C();c.__sub__=fn(x){0};[c+1,c-1]'''),[42,41])
        self.assertEqual(self.run_code('''class C{
@staticmethod
fn __add__(x){x+1}
@classmethod
fn __radd__(cls,x){[cls,x]}
}let c=C();[c+41,(41+c)[0]==C]'''),[42,True])
        for source in ['class C{let __add__=42;fn __radd__(self,x){42}}C()+C()', 'class C{let __add__=property(fn(self){throw "getter error"})}C()+1']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_notimplemented_is_identity_not_marker(self):
        value=self.run_code('let n:NotImplemented=NotImplemented;[type_of(n),n==NotImplemented,n=={"$notImplemented":true},json_stringify([n]),parse_json(json_stringify(n))==n]')
        self.assertEqual(value,['NotImplemented',True,False,'[{"$notImplemented": true}]',False])
        self.assertIs(Evaluator.copy_value(NOT_IMPLEMENTED),NOT_IMPLEMENTED)
        self.assertEqual(serializable(NOT_IMPLEMENTED),{'$notImplemented':True})
        self.assertEqual(self.run_code('class C{fn __add__(self,x){{"$notImplemented":true}}}C()+1'),{'$notImplemented':True})
        seen=[]
        self.assertFalse(self.run_code('tool("identity",NotImplemented)==NotImplemented',tool_handler=lambda n,v:seen.append(v) or v))
        self.assertEqual(seen,[{'$notImplemented':True}])
        self.assertEqual(self.run_code('let NotImplemented=42;NotImplemented'),42)
        for source in ['NotImplemented()','bool(NotImplemented)','if NotImplemented{1}else{2}','!NotImplemented']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_length_bool_contains_and_strict_conditions(self):
        self.assertEqual(self.run_code('''class C{fn __len__(self){2}fn __contains__(self,x){x==42}}class B(C){fn __bool__(self){false}}
[bool(C()),bool(B()),length(C()),contains(C(),42),contains(C(),1)]'''),[True,False,2,True,False])
        self.assertEqual(self.run_code('class C{fn __eq__(self,x){x==42}}contains([C()],42)'),True)
        for source in ['class C{fn __bool__(self){true}}if C(){1}else{2}','class C{fn __bool__(self){true}}!C()','class C{fn __bool__(self){true}}C()&&true','class C{fn __bool__(self){true}}true&&C()','class C{fn __bool__(self){true}}false||C()']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)
        self.assertEqual(self.run_code('class C{fn __bool__(self){throw "short"}}[false&&bool(C()),true||bool(C())]'),[False,True])

    def test_concrete_bool_int_result_contracts_and_no_implicit_await(self):
        for method,params,call,body in [('__eq__','self,x','C()==1','1'),('__lt__','self,x','C()<1','null'),('__bool__','self','bool(C())','NotImplemented'),('__contains__','self,x','contains(C(),1)','NotImplemented'),('__len__','self','length(C())','true'),('__len__','self','length(C())','-1')]:
            with self.subTest(method=method,body=body),self.assertRaises(LanguageError):self.run_code('class C{fn '+method+'('+params+'){'+body+'}}'+call)
        for method,params,call in [('__eq__','self,x','C()==1'),('__bool__','self','bool(C())'),('__len__','self','length(C())'),('__contains__','self,x','contains(C(),1)')]:
            with self.subTest(method=method),self.assertRaises(LanguageError):self.run_code('class C{async fn '+method+'('+params+'){true}}'+call)
        with self.assertRaises(LanguageError):self.run_code('class C{fn __bool__(self){NotImplemented}fn __len__(self){1}}bool(C())')

    def test_arithmetic_cold_tasks_and_generators_require_explicit_consumption(self):
        self.assertEqual(self.run_code('''var calls=0;class C{async fn __add__(self,x){calls=calls+1;x+1}fn* __neg__(self){yield 42}async fn* __abs__(self){yield 41}}
let c=C();let task=c+41;let before=calls;[before,await task,calls,collect(-c),await acollect(abs(c))]'''),[0,42,1,[42],[41]])

    def test_limits_and_numeric_failures_remain_bounded(self):
        for source in ['pow(2,14001)','class C{fn __add__(self,x){self+x}}C()+1']:
            with self.subTest(source=source),self.assertRaises(ResourceLimit):self.run_code(source,step_limit=2000)
        for source in ['pow(-1,0.5)','1e308*1e308','floordiv(1,0)','pow(0,-1)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)
        with self.assertRaises(ResourceLimit):self.run_code('class C{fn __len__(self){pow(2,14001)}}length(C())')

    def test_agent_hooks_share_task_and_single_ast_thread(self):
        main=threading.get_ident();asts=set();workers=set();barrier=threading.Barrier(2)
        def handler(name,config,value):workers.add(threading.get_ident());barrier.wait(timeout=3);return value
        source='agent a{};class C{fn __add__(self,x){a(x+1)}}async fn f(x){C()+x}await gather([f(40),f(41)])'
        self.assertEqual(self.run_code(source,agent_handler=handler,debug_hook=lambda *args:asts.add(threading.get_ident())),[41,42])
        self.assertEqual(asts,{main});self.assertEqual(len(workers),2)

    def test_agent_comparison_length_contains_and_bool(self):
        source='''agent a{};class C{fn __eq__(self,x){a(x==42)}fn __len__(self){a(2)}fn __contains__(self,x){a(x==42)}fn __bool__(self){a(true)}}
async fn main(){[C()==42,length(C()),contains(C(),42),bool(C())]}await main()'''
        self.assertEqual(self.run_code(source,agent_handler=lambda n,c,v:v),[True,2,True,True])

    def test_hard_cancel_error_no_fallback_and_worker_drain(self):
        def cancelled(*args):raise TaskCancelled('fixture')
        for expression in ['C()+1','C()==1','length(C())','contains(C(),1)','bool(C())']:
            source='''class C{fn __add__(self,x){tool("stop",1)}fn __eq__(self,x){tool("stop",1)}fn __len__(self){tool("stop",1)}fn __contains__(self,x){tool("stop",1)}fn __bool__(self){tool("stop",1)}}async fn main(){'''+expression+'}await main()'
            evaluator=Evaluator(tool_handler=lambda *args:None,effect_handler=cancelled)
            with self.subTest(expression=expression),self.assertRaises(ExecutionControl):evaluator.run(source)
            self.assertEqual(evaluator.frames,[]);self.assertEqual(evaluator.depth,0);self.assertIsNone(evaluator.suspension.pool)
        self.assertEqual(self.run_code('var called=false;class A{fn __add__(self,x){throw "stop"}}class B{fn __radd__(self,x){called=true;42}}try{A()+B()}catch e{null};called'),False)

    def test_cross_owner_is_rejected(self):
        foreign=Evaluator().run('class C{fn __add__(self,x){42}fn __eq__(self,x){true}fn __len__(self){1}}C()')
        for expression in ['foreign+1','1+foreign','foreign==1','1==foreign','length(foreign)','contains([foreign],1)']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):Evaluator().run(expression,{'foreign':foreign})


if __name__=='__main__':unittest.main()
