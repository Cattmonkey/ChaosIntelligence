"""Runtime decorator order, explicit descriptors and scheduler ownership."""
import threading
import unittest

from chaos.language import Evaluator, Parser, LanguageError, ResourceLimit, ExecutionControl, serializable
from chaos.session import Session
from chaos.suspension import TaskCancelled


class DecoratorTests(unittest.TestCase):
    def run_code(self, source, **kwargs): return Evaluator(**kwargs).run(source)

    def test_definition_and_application_order_once(self):
        source='''var seen=[];
fn mark(x){seen=append(seen,x);x}
fn deco(x){mark(x);fn(f){mark(x+" apply");fn(*args,**kwargs){apply(f,args,kwargs)+1}}}
@deco("outer")
@deco("inner")
fn f(x=mark(40)){x}
[f(),seen]'''
        self.assertEqual(self.run_code(source),[42,['outer','inner',40,'inner apply','outer apply']])

    def test_multiline_expression_span_and_comments(self):
        source='''fn make(n){fn(f){fn(x){f(x)+n}}}
@make( // retain logical continuation
  2
)
fn f(x){x}
f(40)'''
        tree=Parser(source,'decorator.chaos',ranges=True).program()
        wrapper=tree[1][1]
        self.assertEqual(wrapper[0],'decorated');self.assertEqual(wrapper.span['line'],2)
        self.assertEqual(wrapper[1][0].span['line'],2)
        self.assertEqual(self.run_code(source),42)

    def test_reject_invalid_decorator_positions_and_control(self):
        for source in ['@identity','@\nfn f(){}','@identity fn f(){}','@identity\nlet x=1','@identity\nrec{fn f(){1}}','class C{\n@identity\nagent x{}}','fn f(){\n@do{return 1}\nfn g(){}\n}','while true{\n@do{break}\nfn f(){}\n}','fn* f(){\n@yield 1\nfn g(){}\n}','fn f(){\n@await task\nfn g(){}\n}','@x\n'*65+'fn f(){}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()

    def test_raw_recursion_identity_arbitrary_replacement_and_no_publish_failure(self):
        self.assertEqual(self.run_code('''fn plus(f){fn(n){f(n)+1}}
@plus
fn f(n){if n==0{0}else{f(n-1)}}
f(20)'''),1)
        self.assertEqual(self.run_code('''fn answer(x){42}
@answer
fn f(){0}
@answer
class C{}
@answer
agent a{}
[f,C,a]'''),[42,42,42])
        session=Session()
        with self.assertRaises(LanguageError):session.evaluate('fn bad(x){throw "bad"}\n@bad\nfn absent(){1}')
        self.assertNotIn('absent',session.env.values)
        with self.assertRaises(LanguageError):session.evaluate('@bad\nclass Absent{}')
        self.assertNotIn('Absent',session.env.values)

    def test_raw_class_self_identity_survives_replacement(self):
        result=self.run_code('''fn replace(c){c.make()}
@replace
class C{let make=fn(){C};fn same(self,x:C){x}}
let x=C();x.same(x)==x''')
        self.assertTrue(result)

    def test_static_class_and_plain_decorated_methods(self):
        self.assertEqual(self.run_code('''fn wrap(f){fn(self,x){f(self,x)+1}}
class A{
@staticmethod
fn zero(){42}
@classmethod
fn make(cls){cls()}
@wrap
fn plus(self,x){x+1}
}
class B(A){}let b=B.make();let detached=b.make;
[A.zero(),b.zero(),b.plus(40),class_of(b)==B,class_of(detached())==B]'''),[42,42,42,True,True])

    def test_classmethod_c3_and_class_super(self):
        self.assertEqual(self.run_code('''class A{
@classmethod
fn chain(cls){[cls]}
}class B(A){
@classmethod
fn chain(cls){[B]+super(B,cls).chain()}
}class C(A){
@classmethod
fn chain(cls){[C]+super(C,cls).chain()}
}class D(B,C){}
let result=D.chain();[result[0]==B,result[1]==C,result[2]==D,dir(super(D,D))]'''),[True,True,True,['chain']])

    def test_property_accessors_and_class_side_descriptor(self):
        source='''class C{var _x:Int=1
@property
fn x(self){self._x}
@x.setter
fn x(self,value:Int){self._x=value;null}
@x.getter
fn x(self){self._x+1}
}let c=C();let assigned=setattr(c,"x",41);[assigned,c.x,getattr(c,"x"),type_of(C.x),hasattr(c,"x"),hasattr(c,"missing")]'''
        self.assertEqual(self.run_code(source),[41,42,42,'Descriptor',True,False])

    def test_property_precedence_and_inherited_update(self):
        self.assertEqual(self.run_code('''class A{var _x=40;let x=property(fn(self){self._x},fn(self,v){self._x=v;null})}class B(A){
@A.x.getter
fn x(self){super(B,self).x+2}
}let b=B();b.x=41;[b.x,A().x,keys(vars(b))]'''),[43,40,['_x']])
        # A property installed on a mutable class member outranks prior instance storage.
        self.assertEqual(self.run_code('class C{var x=1}let c=C();c.x=9;C.x=property(fn(self){42});c.x'),42)

    def test_duplicate_member_exception_is_only_property_lineage(self):
        self.assertEqual(self.run_code('var n=0;try{class C{let x=do{n=n+1;0};let x=1}}catch e{null};n'),0)
        for source in ['class C{fn x(self){1}fn x(self){2}}','class C{let x=property(fn(self){1})\n@fn(f){property(f)}\nfn x(self){2}\n}','class C{let x=property(fn(self){1});let x=2}','class C{let x=property(fn(self){1})\n@fn(f){x}\nfn x(self){2}\n}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_setter_restrictions_and_missing_accessors(self):
        for source in ['property(null,async fn(self,x){null})','property(null,fn*(self,x){yield 1})','property(null,async fn*(self,x){yield 1})','class C{let x=property(fn(self){1})}C().x=2','class C{let x=property(null,fn(self,x){null})}C().x','class C{let x=property(null,fn(self,x){1})}C().x=2','class C{let x=property(null,fn(self,x){(async fn(){null})()})}C().x=2']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)
        self.assertEqual(self.run_code('class C{let x=property(null,fn(self,x){null})}hasattr(C(),"x")'),False)

    def test_async_and_generator_getters_are_explicit_cold_handles(self):
        self.assertEqual(self.run_code('''var calls=0;class C{
@property
async fn x(self){calls=calls+1;42}
@property
fn* stream(self){yield 42}
}let c=C();let t=c.x;let before=calls;[before,await t,calls,collect(c.stream)]'''),[0,42,1,[42]])

    def test_descriptor_fields_and_direct_static_callable(self):
        self.assertEqual(self.run_code('class C{let f=staticmethod(fn(x){x+1});var make=classmethod(fn(cls){cls()})}let f:Function=staticmethod(fn(x){x+1});[f(41),C().f(41),class_of(C.make())==C,type_of(vars(C).make)]'),[42,42,True,'Descriptor'])
        for source in ['classmethod(fn(cls){1})()','property(fn(x){1})()','staticmethod(42)','classmethod(property(fn(x){1}))']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_method_replacement_agent_is_not_bound_and_decorated_agent(self):
        source='''agent raw{};fn actor(f){raw}fn twice(a){fn(x){a(x)+1}}
@twice
agent a{}
class C{
@actor
fn f(self,x){x}
} [a(41),C().f(42)]'''
        self.assertEqual(self.run_code(source,agent_handler=lambda name,config,value:value),[42,42])

    def test_descriptor_call_protocol_accepts_all_callable_replacements(self):
        self.assertEqual(self.run_code('''class C{
@classmethod
fn __call__(cls,x){x+1}
}fn use(f:Function){f(41)}use(C())'''),42)
        self.assertEqual(self.run_code('class C{let __call__=property(fn(self){fn(x){x+1}})}let c:Function=C();c(41)'),42)
        with self.assertRaises(LanguageError):self.run_code('class C{let __call__=property(fn(self){42})}let c:Function=C();c()')
        source='''class F{fn __call__(self,cls,x){x}}class C{let f=classmethod(F());let size=staticmethod(length)}let f=C.f;[f(42),C.size(range(42)),f]'''
        result=self.run_code(source)
        self.assertEqual(result[:2],[42,42]);self.assertEqual(serializable(result[2]),{'$boundMethod':'<callable>','$class':'C'})
        self.assertEqual(self.run_code('agent a{};class C{let f=staticmethod(a)}C().f(42)',agent_handler=lambda n,c,v:v),42)
        seen=[]
        self.assertEqual(self.run_code('agent a{};class C{let f=classmethod(a);let p=property(a)}[C.f(),C().p]',agent_handler=lambda n,c,v:seen.append(v) or 42),[42,42])
        self.assertEqual(seen,[{'$class':'C'},{'$instance':'C'}])
        self.assertFalse(self.run_code('hasattr(staticmethod(fn(){1}),"owner")'))

    def test_decorated_flavors_and_bool_return_contracts(self):
        source='''fn identity(f){f}
@identity
async fn a(){42}
@identity
fn* g(){yield 42}
@identity
async fn* ag(){yield 42}
[await a(),collect(g()),await acollect(ag())]'''
        self.assertEqual(self.run_code(source),[42,[42],[42]])
        with self.assertRaises(LanguageError):self.run_code('fn identity(f){f}\n@identity\nfn f()->Bool{true && 1}\nf()')

    def test_await_decorator_and_method_definition_environment(self):
        self.assertEqual(self.run_code('''async fn factory(){fn(f){fn(){f()+1}}}
@await factory()
fn f(){41}
f()'''),42)
        self.assertEqual(self.run_code('''let n=40;class C{let n=1;let decorate=fn(f){f}
@decorate
fn f(self,x=n){n+x}
}C().f()'''),41)

    def test_owner_transport_and_introspection_are_inert(self):
        owner=Evaluator();descriptor=owner.run('property(fn(self){42})')
        for source in ['foreign.getter(fn(self){1})','staticmethod(foreign)','type_of(foreign)','let x:Descriptor=foreign;getattr(x,"fget")']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source,{'foreign':descriptor})
        events=[]
        source='class C{let x=property(fn(self){tool("bad",1)})}let c=C();[vars(C),vars(c),dir(c),C.x];tool("transport",[C.x,staticmethod(fn(){1}),classmethod(fn(cls){1})])'
        self.run_code(source,tool_handler=lambda name,value:events.append((name,value)))
        self.assertEqual(events,[('transport',[{'$descriptor':'Property'},{'$descriptor':'StaticMethod'},{'$descriptor':'ClassMethod'}])])

    def test_property_effects_share_task_and_ast_thread(self):
        barrier=threading.Barrier(2);asts=set();workers=set();main=threading.get_ident()
        def handler(name,config,value):workers.add(threading.get_ident());barrier.wait(timeout=2);return value
        source='''agent a{};class C{var _x=0
@property
async fn value(self){a(42)}
@value.setter
fn value(self,x){self._x=a(x);null}
}let c=C();async fn put(){c.value=41;c._x}await gather([c.value,put()])'''
        self.assertEqual(self.run_code(source,agent_handler=handler,debug_hook=lambda *args:asts.add(threading.get_ident())),[42,41])
        self.assertEqual(asts,{main});self.assertEqual(len(workers),2)

    def test_hard_cancel_and_budget_do_not_become_hasattr_false(self):
        def cancelled(*args):raise TaskCancelled('fixture')
        source='class C{let x=property(fn(self){tool("cancel",1)})}hasattr(C(),"x")'
        with self.assertRaises(ExecutionControl):self.run_code(source,tool_handler=cancelled)
        with self.assertRaises(ResourceLimit):self.run_code('class C{let x=property(fn(self){while true{1}})}hasattr(C(),"x")',step_limit=150)
        ev=Evaluator(tool_handler=lambda *args:None,effect_handler=lambda *args:cancelled())
        with self.assertRaises(ExecutionControl):ev.run('class C{let x=property(fn(self){tool("cancel",1)})}async fn f(){getattr(C(),"x")}await f()')
        self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0);self.assertIsNone(ev.suspension.pool)


if __name__=='__main__':unittest.main()
