"""Function signatures, ordered argument binding and the callable trampoline."""
import unittest

from chaos.language import (Evaluator, ExecutionControl, LanguageError, Native,
                            Parser, ResourceLimit)


class CallSyntax(unittest.TestCase):
    def test_legacy_ast_and_extended_parameter_shapes(self):
        fn = Parser('fn f(a,b:Int=2,*rest:List,key,option=3,**options:Record){a}').program()[1][0][2]
        self.assertEqual(fn[1][0], ('a', 'Any'))
        self.assertEqual(fn[1][1][:3], ('b', 'Int', 'positional'))
        self.assertEqual(fn[1][1][3], ('literal', 2))
        self.assertEqual(fn[1][2], ('rest', 'List', 'varargs', None))
        self.assertEqual(fn[1][3], ('key', 'Any', 'keyword_only', None))
        self.assertEqual(fn[1][-1], ('options', 'Record', 'varkw', None))
        call = Parser('f(1,*xs,key=2,**options)').program()[1][0]
        self.assertEqual(call[0], 'call')
        self.assertEqual([arg[0] for arg in call[2]], ['literal','spread','keyword','kwspread'])

    def test_invalid_signatures_and_calls_reject_before_effect(self):
        invalid = [
            'fn f(a=1,b){b}', 'fn f(a,a=1){a}', 'fn f(*a,*b){a}',
            'fn f(*a,**a){a}', 'fn f(**kw,x){x}', 'fn f(**kw,*xs){xs}',
            'fn f(*xs=[]){xs}', 'fn f(**kw={}){kw}', 'fn f(*){1}',
            'fn f(*,){1}', 'fn f(*,**kw){kw}',
            'f(a=1,a=2)', 'f(a=1,2)', 'f(**{},2)', 'f(a=1,*[])',
            'f(**{},*[])', 'let x=*[]', 'let x=**{}',
            'fn outer(){fn inner(x=do{return 3}){x}}',
            'while true{fn f(x=do{break}){x};break}',
            'while true{fn f(x=do{continue}){x};break}',
        ]
        for source in invalid:
            effects = []
            with self.subTest(source=source), self.assertRaises(LanguageError) as error:
                Evaluator(tool_handler=lambda *args: effects.append(args)).run('tool("bad",null);'+source)
            self.assertEqual(error.exception.code, 'syntax_error')
            self.assertEqual(effects, [])

    def test_defaults_may_contain_local_functions_or_local_loops(self):
        self.assertEqual(Evaluator().run('fn f(x=fn(){return 42}){x()};f()'), 42)
        self.assertEqual(Evaluator().run('fn f(x=do{while true{break};42}){x};f()'), 42)

    def test_reserved_keyword_labels_are_contextual(self):
        self.assertEqual(Evaluator().run('map(fn=fn(x){x+1},xs=[1,2])'), [2,3])
        self.assertEqual(Evaluator().run('fn f(**kw){kw};f(if=1,return=2)'), {'if':1,'return':2})


class BindingSemantics(unittest.TestCase):
    def test_defaults_evaluated_once_left_to_right_in_outer_scope(self):
        source = '''var count=0; fn next(){count=count+1;count}; let a=40;
            fn f(a=next(),b=next(),c=a+2){[a,b,c]};[f(),f(a=8),count]'''
        self.assertEqual(Evaluator().run(source), [[1,2,42],[8,2,42],2])

    def test_default_function_identity_is_retained(self):
        self.assertTrue(Evaluator().run('fn make(f=fn(){42}){f};make()==make()'))

    def test_default_contract_checked_at_definition(self):
        effects = []
        with self.assertRaisesRegex(LanguageError, 'Int'):
            Evaluator(tool_handler=lambda *args:effects.append(args)).run('fn f(x:Int="bad"){tool("body",null)};0')
        self.assertEqual(effects, [])
        self.assertEqual(Evaluator().run('fn f(x:Null=null){x};f()'), None)

    def test_positional_rest_keyword_only_and_keyword_rest(self):
        source = '''fn f(a,b:Int=2,*rest:List,required,option=3,**options:Record){
            [a,b,rest,required,option,options]};f(1,4,5,6,required=7,other=8)'''
        self.assertEqual(Evaluator().run(source), [1,4,[5,6],7,3,{'other':8}])
        self.assertEqual(Evaluator().run('fn f(a=1,*,b=2){a+b};f(b=4)'),5)
        self.assertEqual(Evaluator().run('fn f(*xs,**kw){[xs,kw]};f()'),[[],{}])
        self.assertEqual(Evaluator().run('fn f(*xs,**kw){[xs,kw]};f(1,xs=2,kw=3)'),[[1],{'xs':2,'kw':3}])

    def test_binding_errors_never_enter_body(self):
        calls = ['f()', 'f(1,2)', 'f(1,a=2)', 'f(z=1)', 'f(1,required=2,z=3)']
        for call in calls:
            effects=[]
            with self.subTest(call=call), self.assertRaises(LanguageError) as error:
                Evaluator(tool_handler=lambda *args:effects.append(args)).run('fn f(a,*,required){tool("body",null)};'+call)
            self.assertEqual(error.exception.code,'argument_error')
            self.assertEqual(effects,[])

    def test_rest_contract_applies_to_collected_container(self):
        self.assertEqual(Evaluator().run('fn f(*xs:List,**kw:Record){[xs,kw]};f(1,a=2)'),[[1],{'a':2}])
        with self.assertRaisesRegex(LanguageError,'Int'):
            Evaluator().run('fn f(*xs:Int){0};f(1)')
        with self.assertRaisesRegex(LanguageError,'List'):
            Evaluator().run('fn f(**xs:List){0};f(a=1)')

    def test_argument_expressions_evaluate_in_source_order(self):
        source='''var log=[];fn mark(x){log=append(log,x);x};
            fn f(a,b,*,c,**kw){[a,b,c,kw]};
            let value=f(mark(1),*do{mark(2);[20]},c=mark(3),**do{mark(4);{d:40}});
            [value,log]'''
        self.assertEqual(Evaluator().run(source),[[1,20,3,{'d':40}],[1,2,3,4]])

    def test_duplicate_spread_reports_argument_error_after_its_value(self):
        source='''var log=[];fn mark(x){log=append(log,x);x};fn f(**kw){log=append(log,99)};
            let code=try{f(a=mark(1),**do{mark(2);{a:3}},b=mark(4))}catch e{e.code};[code,log]'''
        self.assertEqual(Evaluator().run(source),['argument_error',[1,2]])
        for call in ('f(**{a:1},a=2)', 'f(**{a:1},**{a:2})', 'f(*1)', 'f(**[])'):
            with self.subTest(call=call),self.assertRaises(LanguageError) as error:
                Evaluator().run('fn f(*xs,**kw){null};'+call)
            self.assertEqual(error.exception.code,'argument_error')

    def test_constructor_keywords_cannot_replace_closure_metadata(self):
        source='data Pair { PairValue(tag,arity) };PairValue(arity=2,tag=1)'
        self.assertEqual(Evaluator().run(source),{'$type':'main.chaos::Pair','$tag':'PairValue','$values':[1,2]})
        for call in ('Empty(tag="forged")','Some(1,arity=0)','Some(value=1,tag="forged")','Some(1,value=2)'):
            with self.subTest(call=call),self.assertRaises(LanguageError) as error:
                Evaluator().run('data Option { Empty,Some(value) };'+call)
            self.assertEqual(error.exception.code,'argument_error')
        self.assertEqual(Evaluator().run('data Option{Some(value)};match Some(value=42){Some(x)=>x}'),42)

    def test_native_python_signature_binding_and_body_errors(self):
        self.assertEqual(Evaluator().run('range(stop=6,start=2,step=2)'),[2,4])
        for call in ('range(foo=1)', 'range(1,start=2)', 'length()'):
            with self.subTest(call=call),self.assertRaises(LanguageError) as error:
                Evaluator().run(call)
            self.assertEqual(error.exception.code,'argument_error')
        with self.assertRaises(LanguageError) as error: Evaluator().run('length(x=1)')
        self.assertEqual(error.exception.code,'TypeError')

    def test_agent_named_input_and_binding_before_effect(self):
        calls=[]
        evaluator=Evaluator(agent_handler=lambda name,config,value:(calls.append(value),value)[1])
        self.assertEqual(evaluator.run('agent Echo {};fn f(){Echo(input=42)};f()'),42)
        self.assertEqual(calls,[42])
        for call in ('Echo()', 'Echo(1,input=2)', 'Echo(foo=3)'):
            calls=[]
            with self.subTest(call=call),self.assertRaises(LanguageError) as error:
                Evaluator(agent_handler=lambda *args:calls.append(args)).run('agent Echo {};'+call)
            self.assertEqual(error.exception.code,'argument_error'); self.assertEqual(calls,[])

    def test_aggregate_argument_budget(self):
        self.assertEqual(Evaluator(step_limit=100000).run('fn f(*xs){length(xs)};f(*range(10000))'),10000)
        for source in (
            'fn f(*xs){0};f(*range(6000),*range(6000))',
            'fn f(*xs,**kw){0};f(*range(10000),extra=1)',
            'fn f(*xs,**kw){0};apply(f,range(10000),{extra:1})',
            'fn f(*xs,**kw){0};let p=partial(f,*range(9999));p(1,2)',
        ):
            with self.subTest(source=source),self.assertRaises(ResourceLimit):
                Evaluator(step_limit=100000).run(source)


class CallableTrampoline(unittest.TestCase):
    def test_apply_partial_overrides_and_duplicate_position(self):
        source='''fn f(a,b=2,*,c=3){a+b+c};let p=partial(f,b=4,c=5);
            [apply(f,[1],{b:2,c:3}),p(1),p(a=2,b=7),apply(p,[1],{c:8})]'''
        self.assertEqual(Evaluator().run(source),[6,10,14,13])
        self.assertEqual(Evaluator().run('fn f(**kw){kw};partial(f,fn=42)()'),{'fn':42})
        with self.assertRaises(LanguageError) as error: Evaluator().run('fn f(a){a};partial(f,1)(a=2)')
        self.assertEqual(error.exception.code,'argument_error')
        with self.assertRaises(LanguageError) as error: Evaluator().run('partial(fn=fn(x){x})')
        self.assertEqual(error.exception.code,'argument_error')

    def test_compose_forwards_all_arguments_and_checks_inner_before_outer(self):
        self.assertEqual(Evaluator().run('fn add(a,b=2){a+b};compose(str,add)(a=3,b=4)'),'7')
        self.assertEqual(Evaluator().run('fn g(x)->Int{x};fn f(x)->String{str(x)};compose(f,g)(42)'),'42')
        effects=[]
        with self.assertRaisesRegex(LanguageError,'Int'):
            Evaluator(tool_handler=lambda *args:effects.append(args)).run('fn g(x)->Int{"bad"};fn f(x){tool("outer",x)};compose(f,g)(1)')
        self.assertEqual(effects,[])

    def test_tail_recursion_through_partial_apply_and_both_compose_branches(self):
        for call in ('partial(count)(n-1)', 'apply(count,[n-1])',
                     'compose(count,fn(x){x})(n-1)', 'compose(fn(x){x},count)(n-1)'):
            evaluator=Evaluator(step_limit=100000)
            with self.subTest(call=call):
                self.assertEqual(evaluator.run('fn count(n)->Int{if n==0{42}else{'+call+'}};count(1500)'),42)
                self.assertEqual(evaluator.depth,0)

    def test_tail_native_agent_and_bool_contracts(self):
        self.assertEqual(Evaluator().run('fn f()->Int{length([1,2])};f()'),2)
        self.assertEqual(Evaluator(agent_handler=lambda *a:42).run('agent A {};fn f()->Int{A(input=null)};f()'),42)
        self.assertTrue(Evaluator().run('fn f(){true&&partial(fn(){true})()};f()'))
        for source in ('fn f()->String{length([])};f()',
                       'fn f(){true&&partial(fn(){42})()};f()',
                       'fn f(){true&&compose(str,fn(){42})()};f()'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)
        self.assertEqual(Evaluator().run('fn g()->Bool{true};fn f(x)->String{str(x)};compose(f,g)()'),'true')

    def test_module_tail_binding_and_native_error_span(self):
        for call in ('typed("bad")', 'typed()', 'length(unknown=1)', 'length(1)', 'partial(typed)("bad")'):
            source='fn typed(x:Int){x};\nfn run(){\n  '+call+'\n};export{run}'
            with self.subTest(call=call),self.assertRaises(LanguageError) as error:
                Evaluator().run('import "lib.chaos" as L;\nL.run()',files={'lib.chaos':source})
            self.assertEqual((error.exception.file,error.exception.line,error.exception.column),('lib.chaos',3,3))

    def test_tail_resource_limit_keeps_imported_call_span(self):
        evaluator=Evaluator()
        def exhaust():
            evaluator.steps=evaluator.step_limit
            return None
        evaluator.env.bind('exhaust',Native('exhaust',exhaust,evaluator))
        with self.assertRaises(ResourceLimit) as error:
            evaluator.run('import "lib.chaos" as L;L.run()',files={
                'lib.chaos':'fn run(){\n  partial(exhaust)()\n};export{run}'})
        self.assertEqual((error.exception.file,error.exception.line,error.exception.column),('lib.chaos',2,3))
        self.assertEqual(evaluator.depth,0)

    def test_inner_boolean_contract_stays_before_compose_continuation(self):
        effects=[]
        source='fn g(){true&&partial(fn(){42})()};fn f(x){tool("outer",x)};compose(f,g)()'
        with self.assertRaisesRegex(LanguageError,'Bool'):
            Evaluator(tool_handler=lambda *args:effects.append(args)).run(source)
        self.assertEqual(effects,[])
        self.assertEqual(Evaluator().run('fn g(){true&&partial(fn(){true})()};compose(str,g)()'),'true')

    def test_owner_checks_through_wrappers(self):
        foreign=Evaluator().run('fn f(x){x};f')
        for source in ('foreign(1)','partial(foreign)(1)','apply(foreign,[1])','compose(foreign,fn(x){x})(1)'):
            with self.subTest(source=source),self.assertRaisesRegex(LanguageError,'跨执行上下文'):
                Evaluator().run(source,{'foreign':foreign})

    def test_non_tail_recursion_remains_bounded(self):
        evaluator=Evaluator(step_limit=100000)
        with self.assertRaises(ResourceLimit): evaluator.run('fn f(n){if n==0{0}else{1+f(n-1)}};f(1000)')
        self.assertEqual(evaluator.depth,0)

    def test_finally_resolves_wrappers_before_cleanup_and_preserves_hard_control(self):
        source='''var seen=[];fn g(){seen=append(seen,"g");42};fn f(x){seen=append(seen,"f");x};
            fn run(){try{return compose(f,g)()}finally{seen=append(seen,"cleanup")}};[run(),seen]'''
        self.assertEqual(Evaluator().run(source),[42,['g','f','cleanup']])
        for failure in (ExecutionControl('cancel'),ResourceLimit('stop')):
            effects=[]
            def tool(name,value):
                effects.append(name)
                if name=='stop':raise failure
                return value
            with self.subTest(failure=type(failure).__name__),self.assertRaises(type(failure)):
                Evaluator(tool_handler=tool).run('fn f(){try{return partial(tool,"stop")(null)}finally{tool("cleanup",null)}};f()')
            self.assertEqual(effects,['stop'])

    def test_budget_cannot_be_bypassed_by_wrapper_trampoline(self):
        with self.assertRaises(ResourceLimit):
            Evaluator(step_limit=300).run('fn f(){partial(f)()};try{f()}catch e{42}')


if __name__ == '__main__':
    unittest.main()
