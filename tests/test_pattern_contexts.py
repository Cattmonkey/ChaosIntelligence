"""Patterns bind inside the existing resource and structured-control scopes."""
import unittest

from chaos.language import Evaluator, LanguageError, Parser


SYNC='''var events=[];class C{var value=null;var label="";var suppress=false;
 fn __init__(s,value,label="resource",suppress=false){s.value=value;s.label=label;s.suppress=suppress;null}
 fn __enter__(s){events=append(events,"enter:"+s.label);s.value}
 fn __exit__(s,error){events=append(events,[s.label,if error==null{null}else{error.code}]);s.suppress}}
'''
ASYNC='''class A{var value=null;var label="";var suppress=false;
 fn __init__(s,value,label="resource",suppress=false){s.value=value;s.label=label;s.suppress=suppress;null}
 async fn __aenter__(s){await sleep(0);events=append(events,"enter:"+s.label);s.value}
 async fn __aexit__(s,error){await sleep(0);events=append(events,[s.label,if error==null{null}else{error.code}]);s.suppress}}
'''


class PatternContextTests(unittest.TestCase):
    def run_code(self, source):return Evaluator().run(source)

    def test_sync_pattern_and_mixed_resource_dependencies(self):
        source=SYNC+'''let a=99;let answer=with C([20,22],"one") as [a,b],
          C({sum:a+b,extra:9},"two") as record,C(record,"three") as {sum:n,..rest}{[n,rest]};
          [answer,a,events]'''
        self.assertEqual(self.run_code(source),[[42,{'extra':9}],99,['enter:one','enter:two','enter:three',['three',None],['two',None],['one',None]]])
        self.assertEqual(self.run_code(SYNC+'with C([]) as []{42}'),42)
        self.assertEqual(self.run_code(SYNC+'with C({}) as {}{42}'),42)

    def test_pattern_names_are_local_immutable_and_outer_constructor_lookup(self):
        self.assertEqual(self.run_code(SYNC+'''data D{Item(x)};let Ctor=Item;
          with C([99,Item(42)]) as [Ctor,Ctor(x)]{[Ctor,x]}'''),[99,42])
        self.assertEqual(self.run_code(SYNC+'with C([42]) as [x]{try{x=0}catch e{e.code}}'),'runtime_error')
        with self.assertRaises(LanguageError):self.run_code(SYNC+'with C([42]) as [hidden]{hidden};hidden')

    def test_mismatch_runs_exit_and_can_be_suppressed(self):
        self.assertEqual(self.run_code(SYNC+'''let result=with C([1],"one",true) as [a,b]{events=append(events,"body");42};[result,events]'''),[None,['enter:one',['one','match_error']]])
        self.assertEqual(self.run_code(SYNC+'''let reason=try{with C([1],"one") as [a,b]{42}}catch e{e.code};[reason,events]'''),['match_error',['enter:one',['one','match_error']]])

    def test_inner_mismatch_unwinds_outer_scope_in_reverse_order(self):
        source=SYNC+'''try{with C([1],"outer") as [a],C([2],"inner") as [b,c]{42}}catch e{null};events'''
        self.assertEqual(self.run_code(source),['enter:outer','enter:inner',['inner','match_error'],['outer','match_error']])

    def test_async_patterns_await_entry_and_exit_once(self):
        source=SYNC+ASYNC+'''let result=async with A([20,22],"one") as [a,b],A({n:a+b},"two") as {n:x}{x};[result,events]'''
        self.assertEqual(self.run_code(source),[42,['enter:one','enter:two',['two',None],['one',None]]])
        source=SYNC+ASYNC+'''let result=async with A([1],"one",true) as [a,b]{42};[result,events]'''
        self.assertEqual(self.run_code(source),[None,['enter:one',['one','match_error']]])

    def test_generator_context_cleanup_observes_pattern_failure(self):
        source='''import "std/context" as C;var events=[];
          @C.contextmanager
          fn* resource(){try{yield ([1])}catch e{events=append(events,e.code)}finally{events=append(events,"exit")}}
          let result=with resource() as [a,b]{events=append(events,"body");42};[result,events]'''
        self.assertEqual(self.run_code(source),[None,['match_error','exit']])
        source='''import "std/context" as C;var events=[];
          @C.asynccontextmanager
          async fn* resource(){try{yield ([1])}finally{await sleep(0);events=append(events,"exit")}}
          let result=try{async with resource() as [a,b]{42}}catch e{e.code};[result,events]'''
        self.assertEqual(self.run_code(source),['match_error',['exit']])

    def test_generator_closing_and_function_control_use_normal_cleanup(self):
        source=SYNC+'''fn* produce(){with C([42]) as [value]{yield value}};
          let g=produce();next(g);close(g);events'''
        self.assertEqual(self.run_code(source),['enter:resource',['resource',None]])
        source=SYNC+'''fn result(){with C([42]) as [value]{return value}};[result(),events]'''
        self.assertEqual(self.run_code(source),[42,['enter:resource',['resource',None]]])
        source=SYNC+'''for n in [1,2]{with C([n]) as [value]{if value==1{continue}else{break}}};events'''
        self.assertEqual(self.run_code(source),['enter:resource',['resource',None],'enter:resource',['resource',None]])

    def test_native_task_group_binding_failure_still_closes_group(self):
        source='''let group=task_group();var body=false;
          let error=try{async with group as []{body=true}}catch e{e.code};
          [error,body,task_group_info(group),try{async with group as g{42}}catch e{e.code}]'''
        result=self.run_code(source)
        self.assertEqual(result[:2],['match_error',False]);self.assertEqual(result[2]['state'],'closed')
        self.assertIsNotNone(result[2]['bodyTaskId']);self.assertEqual(result[3],'task_group_error')
        self.assertEqual(self.run_code('let g=task_group();try{with g as []{42}}catch e{e.code}'),'argument_error')

    def test_native_shield_failure_closes_and_does_not_leave_deferral(self):
        for prefix in ('with','async with'):
            source='''async fn run(){let shield=cancel_shield();var body=false;
              let error=try{'''+prefix+''' shield as []{body=true}}catch e{e.code};
              [error,body,cancel_shield_info(shield),try{with shield as s{42}}catch e{e.code}]};await run()'''
            result=self.run_code(source)
            with self.subTest(prefix=prefix):
                self.assertEqual(result[:2],['match_error',False]);self.assertEqual(result[2]['state'],'closed')
                self.assertEqual(result[2]['depth'],0);self.assertFalse(result[2]['cancellationDeferred'])
                self.assertEqual(result[3],'cancel_shield_error')

    def test_native_exit_stack_failure_unwinds_registered_resource(self):
        source=SYNC+'''let stack=exit_stack();stack.enter_context(C([42]));
          let error=try{with stack as []{42}}catch e{e.code};[error,events]'''
        self.assertEqual(self.run_code(source),['match_error',['enter:resource',['resource','match_error']]])

    def test_simple_underscore_context_remains_a_binding(self):
        node=Parser('with input as _{_}').program()[1][0]
        self.assertEqual(node[:2],('with','_'))
        self.assertEqual(self.run_code(SYNC+'with C(42) as _{_}'),42)
        self.assertEqual(self.run_code(SYNC+'let _=9;with C([42]) as [_]{_}'),9)

    def test_async_permission_and_invalid_targets_fail_before_effects(self):
        targets=('true','1','"x"','[a,a]','{a:x,a:y}','[x.y]')
        for target in targets:
            with self.subTest(target=target),self.assertRaises(LanguageError):Parser('with input as '+target+'{42}').program()
        for body in ('fn f(){async with input as [a]{a}}','fn f(x=async with input as [a]{a}){x}'):
            # Top-level defaults may await; the default here is in an explicitly
            # synchronous outer function for the second test.
            source=body if body.startswith('fn f(){') else 'fn outer(){'+body+'}'
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()

    def test_each_mixed_nested_ast_uses_its_own_target_kind(self):
        outer=Parser('async with first as [a],\nsecond as b,\nthird as {n:c}{c}','scope.chaos').program()[1][0]
        middle=outer[3][1][0];inner=middle[3][1][0]
        self.assertEqual([outer[0],middle[0],inner[0]],['async_with_pattern','async_with','async_with_pattern'])
        self.assertEqual([middle.span['line'],inner.span['line']],[2,3])
        self.assertEqual(inner[1][1][0][1].span['column'],13)


if __name__=='__main__':unittest.main()
