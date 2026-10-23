"""User descriptor protocol: inert lookup, cooperative calls and object boundaries."""
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, serializable
from chaos.objects import descriptor_protocols, is_descriptor, Property
from chaos.session import Session
from chaos.suspension import TaskCancelled


class DescriptorProtocolTests(unittest.TestCase):
    def code(self, source, **kwargs): return Evaluator(**kwargs).run(source)

    def test_get_class_instance_and_nominal_capability(self):
        self.assertEqual(self.code('''class D{fn __get__(self,obj,owner){[obj==null,owner]}}
let d:Descriptor=D();class A{let x=d}class B(A){}let b=B();
[B.x[0],B.x[1]==B,b.x[0],b.x[1]==B,type_of(d),isinstance(d,D)]'''),[True,True,False,True,'Instance',True])
        self.assertEqual(self.code('class D{fn __set_name__(self,owner,name){null}};let d=D();try{let x:Descriptor=d;false}catch e{true}'),True)

    def test_data_precedence_and_raw_instance_storage(self):
        self.assertEqual(self.code('''class D{fn __get__(self,obj,owner){42}fn __set__(self,obj,x){obj.saved=x;null}}
class C{var x=0}let c=C();c.x=9;C.x=D();c.x=41;
let own=C();own.y=D();[c.x,c.saved,vars(c).x,type_of(own.y)]'''),[42,41,9,'Instance'])

    def test_nondata_let_shadow_any_and_delete_reveal(self):
        self.assertEqual(self.code('''class D{fn __get__(self,obj,owner){42}}
class C{let x:Descriptor=D()}let c=C();c.x="shadow";let first=c.x;
let deleted=delattr(c,"x");[first,deleted,c.x,vars(c),type_of(vars(C).x)]'''),['shadow',None,42,{},'Instance'])

    def test_set_only_delete_only_read_fallback_and_missing_hook(self):
        self.assertEqual(self.code('''class S{fn __set__(self,obj,x){obj.saved=x;null}}
class R{fn __delete__(self,obj){obj.deleted=true;null}}
class C{var x=0}let c=C();c.x=9;C.x=S();c.x=4;let first=c.x;
let noDelete=try{delattr(c,"x");false}catch e{e.code=="attribute_error"};
C.x=R();let noSet=try{c.x=1;false}catch e{e.code=="attribute_error"};
delattr(c,"x");[first,noDelete,noSet,c.x,c.deleted,c.saved,type_of(C().x)]'''),[9,True,True,9,True,4,'Instance'])

    def test_class_and_ordinary_instance_deletion(self):
        self.assertEqual(self.code('''class A{var x=1;let fixed=2}class B(A){var x=3}let b=B();b.x=4;
delattr(b,"x");let inherited=b.x;delattr(B,"x");[inherited,b.x,B.x]'''),[3,1,1])
        for source in ['class A{var x=1}delattr(A(),"x")','class A{var x=1}class B(A){}delattr(B,"x")','class A{let x=1}delattr(A,"x")','class A{let x=1}delattr(A(),"x")','class A{fn x(self){1}}delattr(A,"x")','class A{}let a=A();delattr(super(A,a),"x")']:
            with self.subTest(source=source),self.assertRaises(LanguageError): self.code(source)
        self.assertEqual(self.code('class C{}let c=C();c.x=1;delattr(c,"x");hasattr(c,"x")'),False)

    def test_class_write_delete_never_calls_instance_protocol(self):
        self.assertEqual(self.code('''var calls=0;class D{fn __set__(self,obj,x){calls=calls+1;null}fn __delete__(self,obj){calls=calls+1;null}}
class C{var x=D();var y=D()}C.x=42;delattr(C,"y");[C.x,calls,hasattr(C,"y")]'''),[42,0,False])

    def test_super_c3_uses_derived_owner_and_ignores_shadow(self):
        self.assertEqual(self.code('''class D{fn __get__(self,obj,owner){[obj,owner]}}
class A{let x=D()}class B(A){}class C(A){}class E(B,C){}let e=E();e.x=7;
let got=super(B,e).x;let cls=super(B,E).x;[got[0]==e,got[1]==E,cls[0]==null,cls[1]==E,e.x]'''),[True,True,True,True,7])

    def test_protocol_slots_ignore_dynamic_fields(self):
        self.assertEqual(self.code('''class D{fn __get__(self,obj,owner){42}}let d=D();d.extra=1;
class C{let x=d}class Empty{}let plain=Empty();plain.__get__=fn(obj,owner){99};
class P{let x=plain}[C().x,type_of(P().x)]'''),[42,'Instance'])
        # A mutable class hook can have an own override; protocol lookup still uses the class.
        self.assertEqual(self.code('class D{var __get__=fn(obj,owner){42}}let d=D();d.__get__=fn(obj,owner){99};class C{let x=d}C().x'),42)

    def test_noncallable_presence_errors_only_on_operation(self):
        self.assertEqual(self.code('class D{let __get__=42;let __set__=1}let d:Descriptor=D();[type_of(d),isinstance(d,D)]'),['Instance',True])
        for expression in ['c.x','c.x=1','delattr(c,"x")']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):
                self.code('class D{let __get__=42;let __set__=1;let __delete__=false}class C{let x=D()}let c=C();'+expression)

    def test_nested_descriptor_protocol_and_static_class_binding(self):
        self.assertEqual(self.code('''class Provider{fn __get__(self,descriptor,owner){fn(obj,accessClass){42}}}
class D{let __get__=Provider()}class C{let x=D()}C().x'''),42)
        self.assertEqual(self.code('''class D{
@staticmethod
fn __get__(obj,owner){42}
@classmethod
fn __set__(cls,obj,value){obj.saved=[cls,value];null}
}class C{let x=D()}let c=C();c.x=41;[c.x,c.saved[0]==D,c.saved[1]]'''),[42,True,41])

    def test_protocol_methods_can_be_properties_and_callable_replacements(self):
        self.assertEqual(self.code('''class Getter{fn __call__(self,obj,owner){42}}
class D{let __get__=property(fn(self){Getter()});let __set__=property(fn(self){fn(obj,value){obj.saved=value;null}});
let __delete__=property(fn(self){fn(obj){obj.saved=0;null}})}class C{let x=D()}let c=C();
c.x=41;let saved=c.saved;delattr(c,"x");[c.x,saved,c.saved]'''),[42,41,0])

    def test_first_mro_member_wins_even_before_later_data_descriptor(self):
        self.assertEqual(self.code('''class Read{fn __get__(self,obj,owner){1}}
class Data{fn __get__(self,obj,owner){2}fn __set__(self,obj,value){throw "wrong"}}
class A{let x=Read()}class B{let x=Data()}class C(A,B){}let c=C();c.x=42;[c.x,super(A,c).x]'''),[42,2])

    def test_synchronous_body_cold_handle_result_is_null_error(self):
        for body in ['(async fn(){null})()', '(fn*(){yield 1})()', '(async fn*(){yield 1})()']:
            with self.subTest(body=body),self.assertRaises(LanguageError) as caught:
                self.code('class D{fn __set__(self,obj,value){'+body+'}}class C{let x=D()}C().x=1')
            self.assertIn('Null',str(caught.exception))

    def test_protocol_resolution_cycle_is_budgeted_and_cleans_frames(self):
        ev=Evaluator(step_limit=10000)
        with self.assertRaises(ResourceLimit):ev.run('class D{var __get__=null}let d=D();D.__get__=d;class C{let x=d}C().x')
        self.assertLess(ev.steps,10000);self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)

    def test_naming_order_complete_owner_instantiation_snapshot_and_decorator(self):
        self.assertEqual(self.code('''var seen=[];class D{fn __set_name__(self,owner,name){seen=append(seen,[name,owner().ready]);if name=="a"{owner.c=D();delattr(owner,"b")}else{null};null}}
let d=D();fn decorate(owner){seen=append(seen,["decorated",owner().ready]);owner}
@decorate
class C{let ready=42;var a=d;var b=d}
class Child(C){}C.later=d;[seen,hasattr(C,"b"),hasattr(C,"c"),hasattr(C,"later")]'''),[[['a',42],['b',42],['decorated',42]],False,True,True])

    def test_naming_failure_not_published_but_escaped_owner_survives(self):
        session=Session()
        session.evaluate('var escaped=null;var labels=[];class D{fn __set_name__(self,owner,name){escaped=owner;labels=append(labels,name);throw "stop"}}')
        with self.assertRaises(LanguageError):session.evaluate('class Absent{let a=D();let b=D();let ready=42}')
        self.assertNotIn('Absent',session.env.values)
        self.assertEqual(session.evaluate('[escaped().ready,labels]'),[42,['a']])

    def test_naming_only_values_and_final_property_update_snapshot(self):
        self.assertEqual(self.code('''var labels=[];class D{fn __set_name__(self,owner,name){labels=append(labels,name);null}}let d=D();
class C{let a=d;let b=d}labels'''),['a','b'])

    def test_getter_handles_are_cold_and_explicit(self):
        self.assertEqual(self.code('''var called=0;class D{async fn __get__(self,obj,owner){called=called+1;42}}
class G{fn* __get__(self,obj,owner){yield 41}}class AG{async fn* __get__(self,obj,owner){yield 40}}
class C{let x=D();let y=G();let z=AG()}let c=C();let t=c.x;let cold=called;
[cold,await t,called,collect(c.y),await acollect(c.z)]'''),[0,42,1,[41],[40]])

    def test_write_delete_name_sync_and_null_matrix(self):
        for hook,params,operation in [('__set__','self,obj,value','c.x=1'),('__delete__','self,obj','delattr(c,"x")'),('__set_name__','self,owner,name','null')]:
            for flavor,body in [('async fn','null'),('fn*','yield 1'),('async fn*','yield 1'),('fn','42'),('fn','(async fn(){null})()')]:
                source='class D{'+flavor+' '+hook+'('+params+'){'+body+'}}class C{let x=D()}let c=C();'+operation
                with self.subTest(hook=hook,flavor=flavor,body=body),self.assertRaises(LanguageError):self.code(source)
        self.assertEqual(self.code('class D{fn __set__(self,obj,value){null}fn __delete__(self,obj){null}}class C{let x=D()}let c=C();[setattr(c,"x",42),delattr(c,"x")]'),[42,None])

    def test_callable_hook_async_target_rejected_before_body(self):
        session=Session();session.evaluate('var called=0;class Callable{async fn __call__(self,*args){called=called+1;null}}class D{let __set__=Callable()}class C{let x=D()}let c=C()')
        with self.assertRaises(LanguageError):session.evaluate('c.x=42')
        self.assertEqual(session.evaluate('called'),0)

    def test_property_deleter_and_lineage_preserve_all_accessors(self):
        self.assertEqual(self.code('''class C{var _x=1
@property
fn x(self){self._x}
@x.setter
fn x(self,value){self._x=value;null}
@x.deleter
fn x(self){self._x=0;null}
@x.getter
fn x(self){self._x+1}
}let c=C();c.x=41;let first=c.x;delattr(c,"x");[first,c.x,type_of(C.x.fdel),C.x.fset!=null]'''),[42,1,'Function',True])
        self.assertEqual(self.code('class C{var value=1;let x=property(fdel=fn(self){self.value=42;null})}let c=C();[delattr(c,"x"),c.value]'),[None,42])
        for source in ['property(fdel=async fn(self){null})','property(fdel=fn*(self){yield 1})','class C{let x=property(fn(self){1})}delattr(C(),"x")','class C{let x=property(fdel=fn(self){42})}delattr(C(),"x")']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_capability_introspection_and_payload_are_inert(self):
        calls=[]
        result=self.code('''class D{fn __get__(self,obj,owner){tool("bad",1)}fn __set__(self,obj,x){null}}
let d:Descriptor=D();class C{let x=d;let __call__=d}let c:Function=C();
[vars(C),vars(c),dir(c),type_of(d),isinstance(d,D)];tool("transport",[d,c])''',tool_handler=lambda n,v:calls.append((n,v)))
        self.assertEqual(calls,[('transport',[{'$instance':'D'},{'$instance':'C'}])]);self.assertIsNone(result)
        ev=Evaluator();d=ev.run('class D{fn __get__(self,obj,owner){42}}D()')
        self.assertEqual(descriptor_protocols(d),frozenset({'__get__'}));self.assertTrue(is_descriptor(d))
        self.assertEqual(serializable(d),{'$instance':'D'})
        for source in ['foreign.x','delattr(foreign,"x")','class C{let x=foreign}C().x','type_of(foreign)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source,{'foreign':d})

    def test_callability_from_descriptor_getter_does_not_execute_on_contract(self):
        self.assertEqual(self.code('''var count=0;class D{fn __get__(self,obj,owner){count=count+1;fn(x){x+1}}}
class C{let __call__=D()}let c:Function=C();let before=count;[before,c(41),count]'''),[0,42,1])

    def test_normal_hook_effects_suspend_in_same_task_for_all_operations(self):
        main=threading.get_ident();asts=set();workers=set();seen=[]
        barrier=threading.Barrier(2)
        def handler(name,config,value):
            workers.add(threading.get_ident());seen.append(value)
            if value in [41,42]:barrier.wait(timeout=3)
            return value
        source='''agent a{};class D{fn __set_name__(self,owner,name){a(name);null}
async fn __get__(self,obj,owner){a(42)}fn __set__(self,obj,value){obj.saved=a(value);null}
fn __delete__(self,obj){a("delete");obj.saved=0;null}}
async fn main(){class C{let x=D()}let c=C();async fn put(){c.x=41;c.saved}
let values=await gather([c.x,put()]);delattr(c,"x");[values,c.saved]}
await main()'''
        self.assertEqual(self.code(source,agent_handler=handler,debug_hook=lambda *args:asts.add(threading.get_ident())),[[42,41],0])
        self.assertEqual(asts,{main});self.assertEqual(len(workers),2);self.assertEqual(seen[0],'x');self.assertEqual(seen[-1],'delete')

    def test_hard_controls_and_hasattr_error_boundary(self):
        def cancelled(*args):raise TaskCancelled('fixture')
        for hook,params,operation in [('__get__','self,obj,owner','hasattr(c,"x")'),('__set__','self,obj,value','c.x=42'),('__delete__','self,obj','delattr(c,"x")'),('__set_name__','self,owner,name','null')]:
            source='class D{fn '+hook+'('+params+'){tool("cancel",1);42}}async fn main(){class C{let x=D()}let c=C();'+operation+'}await main()'
            ev=Evaluator(tool_handler=lambda *args:None,effect_handler=cancelled)
            with self.subTest(hook=hook),self.assertRaises(ExecutionControl):ev.run(source)
            self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0);self.assertIsNone(ev.suspension.pool)
        with self.assertRaises(ResourceLimit):self.code('class D{fn __get__(self,obj,owner){while true{1}}}class C{let x=D()}hasattr(C(),"x")',step_limit=150)
        with self.assertRaises(LanguageError):self.code('class D{fn __get__(self,obj,owner){throw "failure"}}class C{let x=D()}hasattr(C(),"x")')


if __name__ == '__main__': unittest.main()
