"""Native function signature boundaries across lexical and higher-order calls."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser
from chaos.parameters import validate_parameters


class PositionalOnlyTests(unittest.TestCase):
    def run_code(self, source, **options):
        files=options.pop('files',None)
        return Evaluator(**options).run(source,files=files)

    def test_legacy_nodes_and_extended_prefix_rows(self):
        old=Parser('fn f(a,b:Int=2,*rest,key,**options){a}').program()[1][0][2]
        self.assertEqual(old[1][0],('a','Any'))
        tree=Parser('fn f(a:Int,b as [left,right]=[20,22],/,c=3,*rest,key,**options){a}').program()[1][0][2]
        self.assertEqual(tree[1][0],('a','Int','positional_only',None))
        self.assertEqual(tree[1][1][2],'positional_only');self.assertEqual(len(tree[1][1]),5)
        self.assertEqual([p[2]for p in tree[1][2:]],['positional','varargs','keyword_only','varkw'])
        self.assertEqual(validate_parameters(tree[1]),('a','b','left','right','c','rest','key','options'))
        for suffix in ('/', '/,'):
            self.assertEqual(self.run_code('fn f(a,'+suffix+'){a};f(42)'),42)

    def test_every_parameter_mode_and_same_name_keyword_passthrough(self):
        source='''fn f(a:Int,b=2,/,c=3,*rest:List,required,option=4,**extras:Record){
          [a,b,c,rest,required,option,extras]};f(1,5,6,7,8,required=9,a=10,b=11)'''
        self.assertEqual(self.run_code(source),[1,5,6,[7,8],9,4,{'a':10,'b':11}])
        self.assertEqual(self.run_code('fn f(x=1,/,**kw){[x,kw]};f(x=42)'),[1,{'x':42}])
        self.assertEqual(self.run_code('fn f(x,/,**kw){[x,kw]};f(1,**{x:42})'),[1,{'x':42}])
        self.assertEqual(self.run_code('fn f(x,/,*,y){x+y};f(20,y=22)'),42)

    def test_keyword_cannot_fill_required_positional_only(self):
        for signature,call in [('x,/','f(x=42)'),('x,/,**rest','f(x=42)'),('x=1,/','f(x=42)'),('x,/,y','f(1,x=2,y=3)'),('x,/,y','f(1,2,y=3)')]:
            calls=[]
            with self.subTest(signature=signature,call=call),self.assertRaises(LanguageError) as caught:
                self.run_code('fn f('+signature+'){tool("body",null)};'+call,tool_handler=lambda *a:calls.append(a))
            self.assertEqual(caught.exception.code,'argument_error');self.assertEqual(calls,[])

    def test_defaults_are_once_in_outer_scope_and_keep_identity(self):
        source='''var count=0;let outer=40;fn value(){count+=1;count};
          fn f(a=value(),/,b=value(),*,c=outer+2){[a,b,c]};[f(),f(9,b=8),count]'''
        self.assertEqual(self.run_code(source),[[1,2,42],[9,8,42],2])
        self.assertTrue(self.run_code('fn f(action=fn(){42},/){action};f()==f()'))
        self.assertEqual(self.run_code('fn f(value:Null=null,/){value};f()'),None)
        calls=[]
        with self.assertRaises(LanguageError):self.run_code('fn f(x:Int=tool("default",null),/){42}',tool_handler=lambda *a:calls.append(a)or 'bad')
        self.assertEqual(calls,[('default',None)])

    def test_pattern_slots_contracts_and_keyword_rest_patterns(self):
        source='''fn f(pair as [a,b]:List,/,**options as {pair:external}:Record){[a+b,external]};f([20,22],pair=99)'''
        self.assertEqual(self.run_code(source),[42,99])
        self.assertEqual(self.run_code('data D{Value(x)};fn f(slot as Value(value):D,/){value};f(Value(42))'),42)
        for call in ('f([1])','f(42)','f(pair=[20,22])'):
            calls=[]
            with self.subTest(call=call),self.assertRaises(LanguageError):self.run_code('fn f(pair as [a,b]:List,/){tool("body",null)};'+call,tool_handler=lambda *a:calls.append(a))
            self.assertEqual(calls,[])

    def test_invalid_boundaries_and_duplicates_precede_all_module_effects(self):
        invalid=['/','/,x','x,/,/','x,/,y,/','x,/*rest','x,/,*','x,/,*,','x,/,*,**kw','*rest,/','*,key,/','**kw,/','x=1,/,y','x=1,y,/','x,/,x','x as [y],/,y','x,/,y as [x]','x /','x,/,y=1,z','x,/,*rest=[]','x,/,**kw={}']
        for params in invalid:
            effects=[]
            with self.subTest(params=params),self.assertRaises(LanguageError) as caught:
                self.run_code('tool("before",null);fn f('+params+'){42}',tool_handler=lambda *a:effects.append(a))
            self.assertEqual(caught.exception.code,'syntax_error');self.assertEqual(effects,[])

    def test_shared_validator_rejects_unrepresentable_mode_orders(self):
        p=lambda name,mode,default=None:(name,'Any',mode,default)
        bad=[[("x","Any","bad",None)], [('x','Any','positional')], [('x','Any','positional',None,None,0)],
             [p('x','positional'),p('y','positional_only')],[p('x','keyword_only'),p('y','positional')],
             [p('x','keyword_only'),p('y','varargs')],[p('x','varargs'),p('y','varargs')],
             [p('x','varkw'),p('y','keyword_only')],[p('x','varkw'),p('y','varkw')],
             [p('x','positional_only',('literal',1)),p('y','positional')],
             [p('x','positional_only',('literal',1)),p('y','positional_only')],
             [p('x','varargs',('list',[]))],[p('x','varkw',('record',[]))],
             [('if','Any')],[('x','bad type')],[('x','Any'),('x','Any')]]
        for params in bad:
            with self.subTest(params=params),self.assertRaises(LanguageError):validate_parameters(params)
        self.assertEqual(validate_parameters([('__chaos_macro_1_x','__chaos_macro_2_Module.Type','positional_only',None)]),('__chaos_macro_1_x',))

    def test_division_defaults_comments_and_contextual_names_unchanged(self):
        self.assertEqual(self.run_code('fn f(value=84/2,/,other=2){value/other};f()'),21)
        self.assertEqual(self.run_code('fn f(value, // ordinary comment\n){value};f(value=42)'),42)
        self.assertEqual(self.run_code('fn f(value, /, // boundary comment\n){value};f(42)'),42)
        self.assertEqual(self.run_code('fn f(await,with,async,/,**kw){[await,with,async,kw]};f(1,2,3,await=4)'),[1,2,3,{'await':4}])
        self.assertEqual(self.run_code('fn f(输入:Int,/,**额外){[输入,额外]};f(42,输入=9)'),[42,{'输入':9}])

    def test_argument_expressions_and_spreads_keep_evaluation_order(self):
        source='''var calls=[];fn mark(x){calls=append(calls,x);x};
          fn f(a,b,/,**kw){[a,b,kw]};let value=f(mark(1),*do{mark(2);[20]},a=mark(3),**do{mark(4);{b:40}});[value,calls]'''
        self.assertEqual(self.run_code(source),[[1,20,{'a':3,'b':40}],[1,2,3,4]])
        source='''var calls=[];fn mark(x){calls=append(calls,x);x};fn f(a,/){tool("body",null)};
          let error=try{f(mark(1),a=mark(2))}catch e{e.code};[error,calls]'''
        self.assertEqual(self.run_code(source),['argument_error',[1,2]])

    def test_partial_apply_compose_and_tail_calls_bind_the_same_signature(self):
        source='''fn f(a,/,b=2,**kw){[a,b,kw]};let p=partial(f,1,a=9,b=3);
          [p(a=10),apply(f,[20],{b:22,a:42}),compose(fn(x){x[0]},f)(42,a=99)]'''
        self.assertEqual(self.run_code(source),[[1,3,{'a':10}],[20,22,{'a':42}],42])
        for call in ('partial(f,x=1)(42)','apply(f,[42],{x:1})','compose(fn(x){x},f)(42,x=1)'):
            with self.subTest(call=call),self.assertRaises(LanguageError):self.run_code('fn f(x,/){x};'+call)
        for call in ('count(n-1)','partial(count)(n-1)','apply(count,[n-1])','compose(fn(x){x},count)(n-1)'):
            ev=Evaluator(step_limit=200000)
            with self.subTest(call=call):self.assertEqual(ev.run('fn count(n:Int,/)->Int{if n==0{42}else{'+call+'}};count(1400)'),42)
            self.assertEqual(ev.depth,0)

    def test_methods_static_class_super_and_keyword_collision(self):
        source='''class Base{var n=0;fn __init__(self,/,value=40){self.n=value;null}
          fn answer(self,/,extra=2,**kw){[self.n+extra,kw]}
          @classmethod
          fn make(cls,/,n=40){cls(n)}
          @staticmethod
          fn add(x,/,y=2){x+y}}
          class Child(Base){fn answer(self,/,extra=2,**kw){super(Child,self).answer(extra,**kw)}}
          let c=Child.make(40);[c.answer(self=99),Base.answer(c,2,self=9),Child.add(40),class_of(c)==Child]'''
        self.assertEqual(self.run_code(source),[[42,{'self':99}],[42,{'self':9}],42,True])

    def test_property_operator_context_and_callable_protocols(self):
        source='''class C{var n=1;
          @property
          fn value(self,/){self.n}
          @value.setter
          fn value(self,v,/){self.n=v;null}
          fn __call__(self,x,/){self.n+x}
          fn __add__(self,x,/){self.n+x}
          fn __enter__(self,/){[self.n]}
          fn __exit__(self,error,/){null}}
          let c=C();c.value=40;[c.value,c(2),c+2,with c as [n]{n+2}]'''
        self.assertEqual(self.run_code(source),[40,42,42,42])

    def test_decorators_and_generator_context_factory_keep_contracts(self):
        source='''import "std/context" as C;
          @C.contextmanager
          fn* resource(value,/,**extra){yield ([value,extra])}
          with resource(42,value=9) as [answer,extra]{[answer,extra]}'''
        self.assertEqual(self.run_code(source),[42,{'value':9}])
        source='''fn wrap(action,/){fn(*args,**kw){action(*args,**kw)}}
          @wrap
          fn answer(x,/,**kw){[x,kw]}
          answer(42,x=9)'''
        self.assertEqual(self.run_code(source),[42,{'x':9}])

    def test_every_function_flavor_and_cold_handle_prebinding(self):
        sources=[('fn','f(42,x=9)','[x,kw]'),('fn*','collect(f(42,x=9))','yield ([x,kw])'),
                 ('async fn','await f(42,x=9)','[x,kw]'),('async fn*','await acollect(f(42,x=9))','yield ([x,kw])')]
        for flavor,call,body in sources:
            with self.subTest(flavor=flavor):
                expected=[[42,{'x':9}]] if '*'in flavor else [42,{'x':9}]
                self.assertEqual(self.run_code(flavor+' f(x,/,**kw){'+body+'};'+call),expected)
        for flavor in ('fn*','async fn','async fn*'):
            ev=Evaluator();effects=[];ev.tool_handler=lambda *a:effects.append(a)
            with self.subTest(flavor=flavor),self.assertRaises(LanguageError):ev.run(flavor+' f(x,/){tool("body",null)};f(x=42)')
            self.assertEqual(ev.suspension.tasks,[]);self.assertEqual(ev.suspension.generators,[]);self.assertEqual(effects,[])

    def test_module_function_parameter_errors_keep_real_callsite(self):
        files={'lib.chaos':'fn f(x:Int,/){x};export{f}'}
        self.assertEqual(self.run_code('import "lib.chaos" as L;L.f(42)',files=files),42)
        for call in ('L.f(x=42)','L.f(1,x=42)','L.f("bad")'):
            with self.subTest(call=call),self.assertRaises(LanguageError) as caught:self.run_code('import "lib.chaos" as L;\n'+call,files=files)
            self.assertEqual((caught.exception.file,caught.exception.line,caught.exception.column),('main.chaos',2,1))


if __name__=='__main__':unittest.main()
