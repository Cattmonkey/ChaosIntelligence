"""Every native binding site consumes the same class-field matcher."""
import unittest

from chaos.language import Evaluator, LanguageError
from chaos.session import Session
from chaos.suspension import TERMINAL


class ClassPatternSiteTests(unittest.TestCase):
    def test_let_var_and_assignment_are_atomic_and_rhs_runs_once(self):
        source = '''class C{var x=20;var y=22};var calls=0;fn make(){calls+=1;C()};
          let C::{x:left}=make();var C::{y:right}=make();right+=1;
          var a:Int=0;var b:Int=0;C::{x:a,y:b}=make();[left,right,a+b,calls]'''
        self.assertEqual(Evaluator().run(source), [20, 23, 42, 3])
        session = Session()
        session.evaluate('class C{var x=42;var y="bad"};var a:Int=1;var b:Int=2')
        with self.assertRaises(LanguageError): session.evaluate('C::{x:a,y:b}=C()')
        self.assertEqual(session.evaluate('[a,b]'), [1, 2])
        with self.assertRaises(LanguageError): session.evaluate('let C::{x:fresh,missing:absent}=C()')
        self.assertNotIn('fresh', session.bindings())

    def test_all_function_flavors_validate_before_cold_handle_allocation(self):
        for flavor in ('fn', 'async fn', 'fn*', 'async fn*'):
            evaluator = Evaluator()
            source = 'class C{var x=42};'+flavor+' work(value as C::{x:x}){x};try{work(1)}catch e{e.code}'
            with self.subTest(flavor=flavor):
                self.assertEqual(evaluator.run(source), 'match_error')
                self.assertEqual(evaluator.suspension.tasks, [])
                self.assertEqual(evaluator.suspension.generators, [])

    def test_parameter_defaults_slots_and_methods_share_nominal_bindings(self):
        source = '''class C{var x=42};fn read(value as C::{x:x}=C(),/){x};
          class Reader{fn read(self,value as C::{x:x}){x}};[read(),Reader().read(C())]'''
        self.assertEqual(Evaluator().run(source), [42, 42])

    def test_sync_async_loops_and_comprehensions(self):
        source = '''class C{var x=21};var total=0;for C::{x:x} in [C(),C()]{total+=x};
          async fn* rows(){yield C();yield C()};var asyncTotal=0;
          async for C::{x:x} in rows(){asyncTotal+=x};
          [total,asyncTotal,[x*2 for C::{x:x} in [C()]],
          [x*2 async for C::{x:x} in rows()]]'''
        self.assertEqual(Evaluator().run(source), [42, 42, [42], [42, 42]])

    def test_lazy_comprehensions_keep_class_pattern_at_resume(self):
        source = 'class C{var x=42};let g=(x for C::{x:x} in [C()]);collect(g)'
        self.assertEqual(Evaluator().run(source), [42])
        source = 'class C{var x=42};async fn* rows(){yield C()};let g=async(x async for C::{x:x} in rows());await acollect(g)'
        self.assertEqual(Evaluator().run(source), [42])

    def test_catch_matches_instance_in_error_value_and_guard_sees_leaves(self):
        source = '''class Failure{var status=42};try{throw Failure()}
          catch {value:Failure::{status:code},.._} if code>40{code}'''
        self.assertEqual(Evaluator().run(source), 42)

    def test_with_and_async_with_project_acquired_value(self):
        for asynchronous in (False, True):
            start = 'async ' if asynchronous else ''
            source = '''var closed=0;class C{var x=42};class Manager{
              '''+start+'fn __'+('a' if asynchronous else '')+'''enter__(s){C()}
              '''+start+'fn __'+('a' if asynchronous else '')+'''exit__(s,e){closed+=1;null}};
              let result='''+start+'''with Manager() as C::{x:x}{x};[result,closed]'''
            with self.subTest(asynchronous=asynchronous): self.assertEqual(Evaluator().run(source), [42, 1])

    def test_resource_pattern_failure_exits_before_propagating(self):
        for asynchronous in (False, True):
            start = 'async ' if asynchronous else ''
            source = '''var closed=[];class C{};class Manager{'''+start+'fn __'+('a' if asynchronous else '')+'''enter__(s){1}
              '''+start+'fn __'+('a' if asynchronous else '')+'''exit__(s,e){closed=append(closed,e.code);null}};
              let result=try{'''+start+'''with Manager() as C::{}{0}}catch e{e.code};[result,closed]'''
            with self.subTest(asynchronous=asynchronous): self.assertEqual(Evaluator().run(source), ['match_error', ['match_error']])

    def test_suspended_generator_class_binding_uses_existing_close_cleanup(self):
        source = '''var exits=0;class C{var x=42};class M{fn __enter__(s){C()}
          fn __exit__(s,e){exits+=1;null}};fn* values(){with M() as C::{x:x}{yield x}};
          let g=values();let first=next(g);close(g);[first,exits]'''
        self.assertEqual(Evaluator().run(source), [{'done': False, 'value': 42}, 1])
        source = '''var exits=0;class C{var x=42};class M{async fn __aenter__(s){await sleep(0);C()}
          async fn __aexit__(s,e){await sleep(0);exits+=1;null}};
          async fn* values(){async with M() as C::{x:x}{yield x}};
          let g=values();let first=await anext(g);await aclose(g);[first,exits]'''
        self.assertEqual(Evaluator().run(source), [{'done': False, 'value': 42}, 1])

    def test_task_group_and_cancel_shield_pattern_failures_unwind_native_scopes(self):
        for manager, prefix in [('task_group()', 'async '), ('cancel_shield()', '')]:
            evaluator = Evaluator()
            source = 'class C{};async fn work(){try{'+prefix+'with '+manager+' as C::{}{0}}catch e{e.code}};await work()'
            with self.subTest(manager=manager): self.assertEqual(evaluator.run(source), 'match_error')
            self.assertTrue(all(task.state in TERMINAL for task in evaluator.suspension.tasks))
            self.assertTrue(all(group.state == 'closed' for group in evaluator.suspension.groups))
            self.assertTrue(all(shield.state == 'closed' for shield in evaluator.suspension.cancel_shields))


if __name__ == '__main__': unittest.main()
