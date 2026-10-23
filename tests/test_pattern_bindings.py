"""Atomic structural declarations and native loop/comprehension targets."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser, ResourceLimit
from chaos.patterns import match_pattern, pattern_names
from chaos.session import Session


class PatternBindings(unittest.TestCase):
    def run_code(self, source, **options):
        files=options.pop('files',None)
        return Evaluator(**options).run(source,files=files)

    def test_nested_lists_records_rest_and_wildcards(self):
        source='let [first,{key:second,..rest},..tail]=[1,{key:2,extra:3},4,5];[first,second,rest,tail]'
        self.assertEqual(self.run_code(source),[1,2,{'extra':3},[4,5]])
        self.assertEqual(self.run_code('let [_,_,.._]=[1,2,3];let {x:x,.._}={x:42,y:9};x'),42)
        self.assertEqual(self.run_code('let []=[];let {}={};42'),42)

    def test_declarations_return_whole_value(self):
        self.assertEqual(self.run_code('do{let [a,b]=[1,2]}'),[1,2])
        self.assertEqual(self.run_code('do{var {a:x}={a:42}}'),{'a':42})

    def test_rhs_once_outer_scope_and_all_names_commit_together(self):
        source='var calls=0;let a=40;fn values(){calls=calls+1;[a,a+2]}let answer=do{let [a,b]=values();[a,b]};[answer,a,calls]'
        self.assertEqual(self.run_code(source),[[40,42],40,1])
        with self.assertRaises(LanguageError): self.run_code('let [a,b]=[1,a];null')

    def test_session_mismatch_never_publishes_partial_names(self):
        session=Session();session.evaluate('var calls=0;let kept=42')
        with self.assertRaises(LanguageError) as caught:
            session.evaluate('let [fresh,0]=do{calls=calls+1;[1,2]}')
        self.assertEqual(caught.exception.code,'match_error')
        self.assertEqual(session.bindings(),{'calls':1,'kept':42})

    def test_session_collision_preflight_is_atomic_after_rhs(self):
        for kind in ('let','var'):
            with self.subTest(kind=kind):
                session=Session();session.evaluate('var calls=0;let kept=42')
                with self.assertRaises(LanguageError):
                    session.evaluate(kind+' [fresh,kept]=do{calls=calls+1;[1,2]}')
                self.assertEqual(session.bindings(),{'calls':1,'kept':42})

    def test_whole_contract_after_rhs_before_matching(self):
        for kind in ('let','var'):
            with self.subTest(kind=kind):
                session=Session();session.evaluate('var calls=0')
                with self.assertRaises(LanguageError) as caught:
                    session.evaluate(kind+' [a,b]:Record=do{calls=calls+1;[1,2]}')
                self.assertNotEqual(caught.exception.code,'match_error')
                self.assertEqual(session.bindings(),{'calls':1})
                with self.assertRaises(LanguageError):
                    session.evaluate(kind+' [a,b]:Missing=do{calls=calls+1;[1,2]}')
                self.assertEqual(session.bindings(),{'calls':2})

    def test_mutable_leaves_are_independent_any_cells(self):
        source='var [a,b]:List=[1,2];let read=fn(){[a,b]};a="changed";b=[42];read()'
        self.assertEqual(self.run_code(source),['changed',[42]])
        with self.assertRaises(LanguageError): self.run_code('let [a,b]=[1,2];a=3')
        with self.assertRaises(LanguageError): self.run_code('var a:Int=1;a="bad"')

    def test_constructor_and_literal_patterns(self):
        source='data Option{Some(value),None};let Some([x,y])=Some([20,22]);let None()=None();let true=true;let -2=-2;let "ok"="ok";[x,y]'
        self.assertEqual(self.run_code(source),[20,22])
        self.assertEqual(self.run_code('import "std/option" as O;let O.Some(x)=O.Some(42);x'),42)
        for declaration in ['let false=true','let 1=true','let "a"="b"','let Some(x)=None()']:
            with self.subTest(declaration=declaration), self.assertRaises(LanguageError) as caught:
                self.run_code('data Option{Some(value),None};'+declaration)
            self.assertEqual(caught.exception.code,'match_error')

    def test_constructor_lookup_uses_outer_environment_not_partial_bindings(self):
        source='data Option{Some(value)};let C=Some;fn f(){let [C,C(x)]=[99,Some(42)];[C,x]}f()'
        self.assertEqual(self.run_code(source),[99,42])
        source='data Option{Some(value)};[C+x for [C,Some(x)] in [[1,Some(41)]]]'
        self.assertEqual(self.run_code(source),[42])

    def test_constructor_arity_and_invalid_constructor_fail_without_binding(self):
        for source in ['data D{One(x)};let One(a,b)=One(1)','let C=fn(x){x};let C(x)=42']:
            with self.subTest(source=source), self.assertRaises(LanguageError): self.run_code(source)
        session=Session();session.evaluate('data D{One(x)}')
        with self.assertRaises(LanguageError): session.evaluate('let [a,One(x,y)]=[1,One(2)]')
        self.assertNotIn('a',session.bindings())

    def test_matching_is_strict_and_never_consumes_value_protocols(self):
        sources=['let [a,b]="ab"','let [a,b]=iter([1,2])','let {x:a}={x:1,y:2}','let [a]=[1,2]','let [a,b,..rest]=[1]']
        for source in sources:
            with self.subTest(source=source), self.assertRaises(LanguageError) as caught: self.run_code(source)
            self.assertEqual(caught.exception.code,'match_error')
        source='var seen=0;class C{fn __iter__(self){seen=seen+1;iter([1])}}let error=try{let [x]=C()}catch e{e.code};[error,seen]'
        self.assertEqual(self.run_code(source),['match_error',0])

    def test_duplicates_are_rejected_before_any_effect(self):
        for target in ('[x,x]','[x,{key:x}]','[x,..x]','{a:x,b:x}','{a:x,a:y}','Some(x,x)'):
            seen=[]
            with self.subTest(target=target), self.assertRaises(LanguageError):
                self.run_code('tool("before",null);let '+target+'=tool("rhs",null)',tool_handler=lambda *a:seen.append(a))
            self.assertEqual(seen,[])

    def test_simple_name_nodes_and_underscore_keep_legacy_identity(self):
        tree=Parser('let _=1;var x:Int=2;for _ in []{null};[x for x in []]').program()
        self.assertEqual(tree[1][0],('let','_','Any',('literal',1)))
        self.assertEqual(tree[1][1],('var','x','Int',('literal',2)))
        self.assertEqual(tree[1][2][0:2],('for','_'))
        self.assertEqual(tree[1][3][-1][0][0:2],('comp_for','x'))
        self.assertEqual(self.run_code('let _=42;[_,[x for _ in [1] for x in [_]]]'),[42,[1]])

    def test_all_six_new_ast_kinds_and_target_spans(self):
        source='let [a]=[1];var {x:b}={x:2};for [c] in []{null};async for [d] in []{null};[e for [e] in []];async (f async for [f] in xs)'
        nodes=Parser(source,ranges=True).program()[1]
        self.assertEqual([n[0] for n in nodes[:4]],['let_pattern','var_pattern','for_pattern','async_for_pattern'])
        self.assertEqual(nodes[4][-1][0][0],'comp_for_pattern')
        self.assertEqual(nodes[5][-1][0][0],'comp_async_for_pattern')
        self.assertEqual(nodes[0][1].span['column'],5)
        self.assertEqual(nodes[0][1][1][0].span['column'],6)

    def test_for_targets_fresh_closures_break_continue_and_no_leak(self):
        source='var fs=[];for [x,y] in [[1,2],[3,4]]{fs=append(fs,fn(){x+y})};map(fn(f){f()},fs)'
        self.assertEqual(self.run_code(source),[3,7])
        source='var out=[];for [x] in [[1],[2],[3]]{if x==1{continue}else{null};out=append(out,x);break};out'
        self.assertEqual(self.run_code(source),[2])
        with self.assertRaises(LanguageError): self.run_code('for [x] in [[1]]{x};x')

    def test_for_mismatch_is_error_and_commits_only_consumed_position(self):
        source='let it=iter([[1],[2,3],[4]]);var seen=[];let error=try{for [x] in it{seen=append(seen,x)}}catch e{e.code};[seen,error,next(it)]'
        self.assertEqual(self.run_code(source),[[1],'match_error',{'done':False,'value':[4]}])

    def test_nested_comprehensions_and_per_item_closures(self):
        source='let x=90;let fs=[fn(){[x,y,z]} for [x,y] in [[1,2],[3,4]] for {v:z} in [{v:x+y}] if z>3];[map(fn(f){f()},fs),x]'
        self.assertEqual(self.run_code(source),[[[3,4,7]],90])
        self.assertEqual(self.run_code('{key:value for [key,value] in [["a",1],["a",2]]}'),{'a':2})
        self.assertEqual(self.run_code('[x for [x] in [[1]] for [x] in [[x+1]]]'),[2])

    def test_generator_targets_remain_lazy_and_borrowed(self):
        source='let it=iter([[1],[2,3],[4]]);let g=(x for [x] in it);let first=next(g);let error=try{next(g)}catch e{e.code};[first,error,next(it)]'
        self.assertEqual(self.run_code(source),[{'done':False,'value':1},'match_error',{'done':False,'value':[4]}])
        self.assertEqual(self.run_code('let g=(x for [x] in [42]);close(g);42'),42)
        self.assertEqual(self.run_code('map(fn(f){f()},collect((fn(){x} for [x] in [[1],[2]])))'),[1,2])

    def test_async_loop_and_comprehension_targets(self):
        prefix='async fn* values(){yield ([1,2]);yield ([3,4])}'
        self.assertEqual(self.run_code(prefix+'var out=[];async for [x,y] in values(){out=append(out,x+y)};out'),[3,7])
        self.assertEqual(self.run_code(prefix+'[x+y async for [x,y] in values()]'),[3,7])
        self.assertEqual(self.run_code(prefix+'await acollect(async (x+y async for [x,y] in values()))'),[3,7])
        self.assertEqual(self.run_code('async fn work(x){x};let [a,b]=await work([20,22]);a+b'),42)

    def test_tasks_in_matched_leaves_stay_cold(self):
        source='var started=0;async fn work(){started=started+1;42};let [job]=[work()];let before=started;[before,task_info(job).state,await job,started]'
        self.assertEqual(self.run_code(source),[0,'created',42,1])

    def test_async_targets_respect_enclosing_default_and_function_scopes(self):
        for suffix in ('fn f(){async for [x] in source(){x}}null',
                       'fn outer(){fn inner(value=async for [x] in source(){x}){value};null}null',
                       'fn f(){[x async for [x] in source()]}null',
                       '(x async for [x] in source())'):
            seen=[]
            with self.subTest(suffix=suffix), self.assertRaises(LanguageError):
                self.run_code('tool("before",null);async fn* source(){yield ([1])};'+suffix,tool_handler=lambda *args:seen.append(args))
            self.assertEqual(seen,[])

    def test_function_defaults_remain_definition_time_and_no_pattern_defaults(self):
        source='var n=0;fn initial(){n=n+1;[n,n+1]};fn f(pair=initial()){let [a,b]=pair;[a,b]};[f(),f(),n]'
        self.assertEqual(self.run_code(source),[[1,2],[1,2],1])
        for source in ['let [a=1]=[]','let {x:a=1}={}','fn f([a]){a}','[x for [x=1] in []]']:
            with self.subTest(source=source), self.assertRaises(LanguageError): self.run_code(source)

    def test_modules_export_immutable_leaves_and_reject_mutable_leaves(self):
        self.assertEqual(self.run_code('import "m.chaos" as M;M.a+M.b',files={'m.chaos':'let [a,b]=[20,22];export{a,b}'}),42)
        for source in ['var [a,b]=[1,2];export{a}','let [a,b]=[1,2];let b=3','let a=0;let {x:a}={x:1}']:
            with self.subTest(source=source), self.assertRaises(LanguageError): self.run_code(source)

    def test_class_body_rejects_pattern_fields_but_methods_allow_locals(self):
        for source in ['class C{let [a,b]=[1,2]}','class C{var {x:a}={x:1}}']:
            with self.subTest(source=source), self.assertRaises(LanguageError): self.run_code(source)
        self.assertEqual(self.run_code('class C{fn answer(self){let [a,b]=[20,22];a+b}}C().answer()'),42)

    def test_existing_match_semantics_share_same_calculus(self):
        source='data D{Pair(a,b)};match Pair([1,2],{x:3,y:4}){Pair([a,..rest],{x:b,..extra})=>[a,rest,b,extra],_=>null}'
        self.assertEqual(self.run_code(source),[1,[2],3,{'y':4}])
        self.assertEqual(self.run_code('match {x:1,y:2}{{x:a}=>0,{x:a,.._}=>a}'),1)

    def test_shared_pure_matcher_and_budget(self):
        pattern=Parser('[a,{x:b,..rest},..tail]').pattern()
        self.assertEqual(pattern_names(pattern),('a','b','rest','tail'))
        calls=[]
        result=match_pattern(pattern,[1,{'x':2,'y':3},4],resolve_tag=lambda name:self.fail(name),equal=lambda a,b:type(a) is type(b) and a==b,tick=lambda:calls.append(1))
        self.assertEqual(result,{'a':1,'b':2,'rest':{'y':3},'tail':[4]})
        self.assertGreater(len(calls),5)
        self.assertIsNone(match_pattern(pattern,[1,42],resolve_tag=lambda name:self.fail(name),equal=lambda a,b:a==b))
        def exhausted(): raise ResourceLimit('pattern limit')
        with self.assertRaises(ResourceLimit): match_pattern(pattern,[],resolve_tag=lambda name:self.fail(name),equal=lambda a,b:a==b,tick=exhausted)
        for invalid in [('p_list',[('p_bind','x'),('p_bind','x')],None),('p_record',[('x',('p_any',)),('x',('p_any',))],None),('p_list',[],('p_literal',1)),('literal',1)]:
            with self.subTest(invalid=invalid), self.assertRaises(LanguageError): pattern_names(invalid)

    def test_existing_maximum_list_pattern_remains_valid_and_budgeted(self):
        pattern='['+','.join(['_']*10000)+']'
        for source in ('match range(10000){'+pattern+'=>42}', 'let '+pattern+'=range(10000);42'):
            with self.subTest(form=source[:5]):
                self.assertEqual(self.run_code(source,step_limit=100000),42)
                with self.assertRaises(ResourceLimit): self.run_code(source,step_limit=100)


if __name__=='__main__': unittest.main()
