"""Named argument slots with atomic structural parameter bindings."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser, ResourceLimit
from chaos.patterns import bind_parameter_patterns, parameter_names
from chaos.session import Session


class ParameterPatterns(unittest.TestCase):
    def run_code(self, source, **options):
        files = options.pop('files', None)
        return Evaluator(**options).run(source, files=files)

    def test_old_parameter_ast_shapes_and_pattern_spans(self):
        params = Parser('fn f(a,b:Int=2,*rest:List,key,**options:Record){a}').program()[1][0][2][1]
        self.assertEqual(params, [('a', 'Any'), ('b', 'Int', 'positional', ('literal', 2)),
            ('rest', 'List', 'varargs', None), ('key', 'Any', 'keyword_only', None),
            ('options', 'Record', 'varkw', None)])
        param = Parser('fn f(input as {x: value, ..rest}:Record={x:42}){value}', ranges=True).program()[1][0][2][1][0]
        self.assertEqual(param[:3], ('input', 'Record', 'positional'))
        self.assertEqual(param[3], ('record', [('x', ('literal', 42))]))
        self.assertEqual(param[4], ('p_record', [('x', ('p_bind', 'value'))], ('p_bind', 'rest')))
        self.assertEqual(param[4].span['column'], 15)
        self.assertEqual(param[4][1][0][1].span['column'], 19)

    def test_whole_slot_keywords_nested_rest_and_lambda(self):
        source = '''fn f(input as [first,{key:second,..extra},..tail]:List){
            [input,first,second,extra,tail]};f(input=[1,{key:2,other:3},4,5])'''
        self.assertEqual(self.run_code(source), [[1, {'key':2, 'other':3}, 4, 5], 1, 2, {'other':3}, [4,5]])
        self.assertEqual(self.run_code('let f=fn(input as {x:x,.._}){[input,x]};f({x:42,y:9})'), [{'x':42,'y':9},42])
        self.assertEqual(self.run_code('fn f(value as alias){[value,alias]};f(42)'), [42,42])
        self.assertEqual(self.run_code('fn f(value as _){value};f(42)'), 42)

    def test_all_parameter_modes_use_the_collected_whole_value(self):
        source = '''fn f(a as [x]:List,*items as [first,..tail]:List,
            key as {v:v}:Record={v:4},**options as {q:q,..extra}:Record){
            [a,x,items,first,tail,key,v,options,q,extra]};f([1],2,3,q=5,r=6)'''
        self.assertEqual(self.run_code(source), [[1],1,[2,3],2,[3],{'v':4},4,{'q':5,'r':6},5,{'r':6}])
        self.assertEqual(self.run_code('fn f(*args as [],**kwargs as {}){[args,kwargs]};f()'), [[],{}])
        self.assertEqual(self.run_code('fn f(*,arg as [x]){x};f(arg=[42])'), 42)

    def test_literals_are_strict_and_shape_matching_does_not_coerce(self):
        self.assertEqual(self.run_code('fn f(a as true,b as -2,c as "ok",d as null){[a,b,c,d]};f(true,-2,"ok",null)'), [True,-2,'ok',None])
        for signature, argument in [('x as 1','true'), ('x as [a]','"a"'),
                                    ('x as []','[1]'), ('x as {a:a}','{a:1,b:2}'),
                                    ('x as [a,..rest]','[]')]:
            with self.subTest(signature=signature), self.assertRaises(LanguageError) as error:
                self.run_code('fn f('+signature+'){42};f('+argument+')')
            self.assertEqual(error.exception.code, 'match_error')

    def test_defaults_are_once_in_outer_scope_and_match_at_call_time(self):
        source = '''var count=0;let x=40;fn initial(){count=count+1;[count]};
            fn f(value as [x]=initial(),other as [y]=[x+2]){[x,y]};
            [count,f(),f(),f(value=[9]),count]'''
        self.assertEqual(self.run_code(source), [1,[1,42],[1,42],[9,42],1])
        self.assertEqual(self.run_code('fn f(value as [x,y]:List=[1]){x+y};42'), 42)
        self.assertEqual(self.run_code('fn f(value as [x,y]:List=[1]){x+y};f([20,22])'), 42)
        with self.assertRaises(LanguageError) as error:
            self.run_code('fn f(value as [x,y]:List=[1]){x+y};f()')
        self.assertEqual(error.exception.code, 'match_error')
        with self.assertRaises(LanguageError) as error:
            self.run_code('fn f(value as [x]:Record=[1]){x};42')
        self.assertNotEqual(error.exception.code, 'match_error')

    def test_argument_order_and_whole_contracts_precede_pattern_matching(self):
        source = '''var log=[];fn mark(n,value){log=append(log,n);value};
            fn f(a as [x],b as [y]){log=append(log,99)};
            let error=try{f(mark(1,[]),mark(2,[42]))}catch e{e.code};[error,log]'''
        self.assertEqual(self.run_code(source), ['match_error',[1,2]])
        with self.assertRaises(LanguageError) as error:
            self.run_code('fn f(a as [x],b as [y]:Record){42};f([],[])')
        self.assertNotEqual(error.exception.code, 'match_error')

    def test_slot_call_errors_and_patterns_never_enter_body(self):
        for call in ('f()', 'f([1],input=[2])', 'f(x=1)', 'f([1],other=2)', 'f([],[])'):
            effects = []
            with self.subTest(call=call), self.assertRaises(LanguageError) as error:
                self.run_code('fn f(input as [x]){tool("body",x)};'+call,
                              tool_handler=lambda *args:effects.append(args))
            self.assertEqual(error.exception.code, 'argument_error')
            self.assertEqual(effects, [])
        effects = []
        with self.assertRaises(LanguageError) as error:
            self.run_code('fn f(a as [x],b as [y,0]){tool("body",x)};f([42],[1,2])',
                          tool_handler=lambda *args:effects.append(args))
        self.assertEqual(error.exception.code, 'match_error')
        self.assertEqual(effects, [])

    def test_every_slot_leaf_collision_rejects_before_any_effect(self):
        signatures = ['input as [input]', 'a as [x],x', 'x,a as [x]',
                      'a as [x],b as {y:x}', '*a as [x],**b as {y:x}',
                      'a as [x,x]', 'a as [x,..x]', 'a as {x:y,x:z}',
                      'a,a as _', '[a]', 'a as [x=1]', 'a:Int as [x]',
                      '*a as [x]=[1]', '**a as {}={}', 'a as [x]=[1],b']
        for signature in signatures:
            effects=[]
            with self.subTest(signature=signature), self.assertRaises(LanguageError) as error:
                self.run_code('tool("before",null);fn f('+signature+'){null}',
                              tool_handler=lambda *args:effects.append(args))
            self.assertEqual(error.exception.code, 'syntax_error')
            self.assertEqual(effects, [])

    def test_leaves_and_whole_slot_are_readonly_and_lexical(self):
        for body in ('x=2', 'input=[2]'):
            with self.subTest(body=body), self.assertRaises(LanguageError):
                self.run_code('fn f(input as [x]){'+body+'};f([1])')
        source = '''let x=90;fn f(input as [x]){fn(){[input,x]}};
            let a=f([1]);let b=f([2]);[a(),b(),x]'''
        self.assertEqual(self.run_code(source), [[[1],1],[[2],2],90])
        session=Session();session.evaluate('let x=90;fn f(input as [fresh,0]){fresh}')
        with self.assertRaises(LanguageError):session.evaluate('f([1,2])')
        self.assertNotIn('fresh',session.bindings())
        self.assertEqual(session.evaluate('x'),90)

    def test_constructor_patterns_use_one_captured_lookup_environment(self):
        source = '''data Box{Boxed(value)};let C=Boxed;
            fn f(left as [C],right as C(x)){[C,x]};f([9],Boxed(42))'''
        self.assertEqual(self.run_code(source), [9,42])
        source = '''data Box{Boxed(value)};let C=Boxed;
            fn f(C,input as C(x)){[C,x]};f(9,Boxed(42))'''
        self.assertEqual(self.run_code(source), [9,42])
        self.assertEqual(self.run_code('import "std/option" as O;fn f(input as O.Some(x):O.Option){x};f(O.Some(42))'), 42)
        for signature in ('input as C(x,y)', 'input as Missing(x)'):
            with self.subTest(signature=signature),self.assertRaises(LanguageError):
                self.run_code('data Box{C(value)};fn f('+signature+'){42};f(C(1))')

    def test_constructor_alias_follows_closure_cells_on_new_calls(self):
        source = '''data D{A(value),B(value)};var C=A;fn f(input as C(x)){x};
            let one=f(A(1));C=B;let two=f(B(2));
            let error=try{f(A(3))}catch e{e.code};[one,two,error]'''
        self.assertEqual(self.run_code(source), [1,2,'match_error'])

    def test_async_and_generator_match_before_creating_cold_handle(self):
        for flavor, body in [('async fn','x'), ('fn*','yield x'), ('async fn*','yield x')]:
            source = 'var seen=0;'+flavor+' f(a as [x],b as [y]){seen=seen+1;'+body+'};let error=try{f([1],[])}catch e{e.code};[error,seen]'
            with self.subTest(flavor=flavor):self.assertEqual(self.run_code(source), ['match_error',0])
        source = '''var seen=0;async fn f(input as [x]){seen=seen+1;x};
            let task=f([42]);let before=seen;[before,task_info(task).state,await task,seen]'''
        self.assertEqual(self.run_code(source), [0,'created',42,1])

    def test_cold_handle_reuses_first_complete_binding_without_rematching(self):
        for flavor, body, consume in [('async fn','x','await handle'),
                                     ('fn*','yield x','next(handle).value'),
                                     ('async fn*','yield x','(await anext(handle)).value')]:
            source = 'data D{A(value),B(value)};var C=A;'+flavor+' f(input as C(x)){'+body+'};let handle=f(A(42));C=B;'+consume
            with self.subTest(flavor=flavor):self.assertEqual(self.run_code(source),42)
        source = '''async fn f(input as [x]){[input,x]};var value=[1];
            let task=f(value);value=[2];await task'''
        self.assertEqual(self.run_code(source), [[1],1])

    def test_methods_static_class_methods_and_constructor_arguments(self):
        source = '''class C{var value=0;
            fn __init__(self,input as {x:x}){self.value=x;null}
            fn add(self,input as [x,y]){self.value+x+y}
            @staticmethod
            fn total(input as [x,y]){x+y}
            @classmethod
            fn make(cls,input as [x]){cls({x:x})}
            async fn later(self,input as [x]){self.value+x}
            fn* rows(self,input as [x]){yield (self.value+x)}}
            let c=C({x:20});[c.add([1,21]),C.total(input=[20,22]),C.make([42]).value,
            await c.later([22]),collect(c.rows([22]))]'''
        self.assertEqual(self.run_code(source), [42,42,42,42,[42]])
        self.assertEqual(self.run_code('class C{fn f(self as _){42}}C().f()'),42)

    def test_map_partial_apply_compose_and_tail_calls(self):
        source = '''fn f(input as [x,y],*,extra as [z]=[0]){x+y+z};
            let p=partial(f,extra=[2]);[map(f,[[1,2],[3,4]]),
            p(input=[20,20]),apply(f,[],{input:[20,20],extra:[2]}),
            compose(str,f)([20,22])];'''
        self.assertEqual(self.run_code(source), [[3,7],42,42,'42'])
        source = 'fn count(input as [n,total]){if n==0{total}else{count([n-1,total+1])}}count([500,0])'
        self.assertEqual(self.run_code(source,step_limit=100000),500)

    def test_patterns_do_not_dispatch_iteration_or_object_protocols(self):
        source = '''var seen=0;class C{fn __iter__(self){seen=seen+1;iter([1])}
            fn __eq__(self,other){seen=seen+1;true}}
            fn list_value(input as [x]){x};fn literal(input as 1){input};
            let a=try{list_value(C())}catch e{e.code};
            let b=try{literal(C())}catch e{e.code};[a,b,seen]'''
        self.assertEqual(self.run_code(source), ['match_error','match_error',0])
        source = 'let it=iter([42]);fn f(input as [x]){x};let error=try{f(it)}catch e{e.code};[error,next(it)]'
        self.assertEqual(self.run_code(source), ['match_error',{'done':False,'value':42}])

    def test_shared_parameter_namespace_and_matcher_are_atomic(self):
        params=Parser('fn f(a as [x],b as {k:y,..rest}){42}').program()[1][0][2][1]
        self.assertEqual(parameter_names(params),('a','x','b','y','rest'))
        values={'a':[1],'b':{'k':2,'extra':3}}
        matched=bind_parameter_patterns(params,values,resolve_tag=lambda name:self.fail(name),equal=lambda a,b:type(a)is type(b)and a==b)
        self.assertEqual(matched,{'a':[1],'b':{'k':2,'extra':3},'x':1,'y':2,'rest':{'extra':3}})
        self.assertEqual(values,{'a':[1],'b':{'k':2,'extra':3}})
        invalid=[params[0],('x','Any')]
        with self.assertRaises(LanguageError):parameter_names(invalid)
        values={'a':[1],'b':{}}
        with self.assertRaises(LanguageError):bind_parameter_patterns(params,values,resolve_tag=lambda name:self.fail(name),equal=lambda a,b:a==b)
        self.assertEqual(values,{'a':[1],'b':{}})

    def test_pattern_budget_uses_shared_execution_limit(self):
        source='fn f(input as ['+','.join(['_']*500)+']){42};f(range(500))'
        self.assertEqual(self.run_code(source,step_limit=10000),42)
        with self.assertRaises(ResourceLimit):self.run_code(source,step_limit=500)


class ParameterPatternMacros(unittest.TestCase):
    def run_code(self, source, **options):
        return Evaluator(**options).run(source)

    def test_compile_time_pure_functions_support_all_modes_and_defaults(self):
        source='''macro m(){fn f(a as [x]:List=[20],*rest as [y]:List,
            option as {value:z}={value:1},**extras as {more:w}){x+y+z+w};
            syntax_literal(f([20],20,more=1))};m!()'''
        self.assertEqual(self.run_code(source),42)
        self.assertEqual(self.run_code('macro m(){let f=fn(input as [x,y]=[20,22]){x+y};syntax_literal(f())};m!()'),42)

    def test_compile_time_mismatch_is_before_all_runtime_effects(self):
        for body in ('fn f(input as [x,y]){quote{42}};f([1])',
                     'fn f(input as [x]:Record){quote{42}};f([1])',
                     'fn f(input as C(x)){quote{42}};f(42)'):
            seen=[]
            with self.subTest(body=body),self.assertRaises(LanguageError):
                self.run_code('tool("before",null);macro m(){'+body+'};m!()',
                              tool_handler=lambda *args:seen.append(args))
            self.assertEqual(seen,[])

    def test_quoted_slots_and_leaves_rename_without_changing_keywords(self):
        source='''macro m(value){quote{fn(input as [x,y]=[20,22]){
            [input,x+y,unquote(value)]}}};let x=90;let input=91;
            let f=m!(x);[f(input=[1,2]),f(),x,input]'''
        self.assertEqual(self.run_code(source), [[[1,2],3,90],[[20,22],42,90],90,91])
        source='''macro m(){quote{fn(*items as [x,..tail],option as {v:y}={v:2},
            **extra as {z:z}){[items,x,tail,option,y,extra,z]}}};let f=m!();f(1,3,z=4)'''
        self.assertEqual(self.run_code(source), [[1,3],1,[3],{'v':2},2,{'z':4},4])

    def test_quoted_constructors_use_captured_scope_across_all_parameters(self):
        source='''data D{Box(value)};macro m(ctor){quote{do{let C=unquote(ctor);
            fn f(left as [C],right as C(x)){[C,x]};f}}};let f=m!(Box);f([9],Box(42))'''
        self.assertEqual(self.run_code(source),[9,42])
        source='''data D{Box(value)};macro m(ctor){quote{do{let C=unquote(ctor);
            fn f(C,input as C(x)){[C,x]};f}}};let f=m!(Box);f(9,Box(42))'''
        self.assertEqual(self.run_code(source),[9,42])
        with self.assertRaises(LanguageError):
            self.run_code('data D{Box(value)};macro m(){quote{fn(input as Box(x)){x}}};m!()')

    def test_quoted_methods_and_cold_functions_preserve_parameter_patterns(self):
        source='''macro m(){quote{do{class C{
            fn f(self,input as [x,y]){x+y}
            async fn later(self,input as [x,y]){x+y}
            fn* rows(self,input as [x,y]){yield (x+y)} };C}}};
            let C=m!();let c=C();[c.f(input=[20,22]),await c.later([20,22]),collect(c.rows([20,22]))]'''
        self.assertEqual(self.run_code(source),[42,42,[42]])

    def test_macro_defaults_remain_in_outer_scope_and_obey_control_boundaries(self):
        self.assertEqual(self.run_code('macro m(){quote{do{let x=40;fn f(input as [x]=[x+2]){x};f}}};let f=m!();f()'),42)
        for source in ('macro m(){quote{fn(input as [x]=do{return [1]}){x}}};m!()',
                       'macro m(){quote{fn(input as [x]=do{break}){x}}};while true{m!();break}',
                       'macro m(){quote{fn(input as [x]=await source()){x}}};fn f(){m!()}'):
            seen=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):
                self.run_code('tool("before",null);'+source,tool_handler=lambda *args:seen.append(args))
            self.assertEqual(seen,[])


if __name__ == '__main__':
    unittest.main()
