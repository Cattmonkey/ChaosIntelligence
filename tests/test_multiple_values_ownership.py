import unittest
from chaos.language import Evaluator,LanguageError,ResourceLimit,Native,serializable
from chaos.multiple_values import Completion,make,primary
from chaos.capture_graph import seal


class MultipleValueOwnershipTests(unittest.TestCase):
    def test_secondary_foreign_callable_rejects_before_discard(self):
        other=Evaluator();e=Evaluator();e.env.bind('foreign',Native('foreign',lambda:42,other))
        for source in ('return_values(1,foreign)','nth_value(0,fn(){return_values(1,foreign)})',
                       'multiple_value_bind(0,fn(){return_values(1,foreign)},fn(){42})'):
            with self.subTest(source=source),self.assertRaises(LanguageError) as error:e.run(source)
            self.assertEqual(error.exception.code,'ownership_error')

    def test_packet_is_not_json_data_or_a_cross_owner_result(self):
        e=Evaluator();packet=Completion(e,(None,))
        with self.assertRaises(LanguageError):serializable(packet)
        with self.assertRaises(LanguageError):primary(packet,Evaluator())
        with self.assertRaises(LanguageError):make(e,[packet])

    def test_capture_walk_checks_secondary_values(self):
        e=Evaluator();other=Evaluator();packet=Completion(e,(1,Native('foreign',lambda:42,other)))
        with self.assertRaises(LanguageError) as error:seal(e,(packet,),frozenset())
        self.assertEqual(error.exception.code,'ownership_error')

    def test_secondary_callable_authority_is_projected(self):
        e=Evaluator(tool_handler=lambda name,value:42)
        function=e.run('fn(){tool("secret",null)}')
        e.env.bind('wide',function)
        e.env.bind('locked',Native('locked',lambda:Completion(e,(1,function)),e,capabilities=frozenset(),captures=(function,)))
        result=e.run('multiple_value_list(locked)')
        self.assertEqual(result[0],1)
        self.assertEqual(result[1].capabilities,frozenset())
        with self.assertRaises(LanguageError) as error:e.run('input()',{'input':result[1]})
        self.assertEqual(error.exception.code,'capability_error')

    def test_secondary_stale_task_rejected_even_in_primary_context(self):
        e=Evaluator();old=e.run('async fn f(){1};f()');e.env.bind('old',old)
        with self.assertRaises(LanguageError) as error:e.run('return_values(1,old)')
        self.assertIn('周期',error.exception.message)

    def test_cumulative_result_budget_and_argument_limit(self):
        e=Evaluator()
        with self.assertRaises(ResourceLimit):make(e,['a'*150000,'b'*150000])
        with self.assertRaises(ResourceLimit):e.run('multiple_value_bind(10001,fn(){return_values()},fn(*xs){xs})')

    def test_completed_handle_producers_reject_foreign_and_stale_owners(self):
        other=Evaluator();foreign=other.run('async fn f(){return_values(1,2)};let t=f();await t;t')
        e=Evaluator();e.env.bind('foreign',foreign)
        with self.assertRaises(LanguageError):e.run('multiple_value_list(foreign)')
        old=e.run('async fn f(){return_values(1,2)};let t=f();await t;t');e.env.bind('old',old)
        with self.assertRaises(LanguageError) as error:e.run('multiple_value_list(old)')
        self.assertIn('周期',error.exception.message)

    def test_await_primary_assignment_projects_before_leaving_narrow_scope(self):
        from chaos.language import TailCall
        effects=[];e=Evaluator(tool_handler=lambda name,value:effects.append(name) or 42)
        function=e.run('fn(){tool("secret",null)}')
        def create():
            def finish():
                if False:yield
                return Completion(e,(function,1))
            task=e.suspension.task(finish(),capture_roots=(function,))
            task.capabilities=task.runner.capabilities=frozenset()
            return task
        e.env.bind('make_task',Native('make_task',create,e,captures=(function,)))
        e.env.bind('restricted',Native('restricted',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        with self.assertRaises(LanguageError) as error:
            e.run('var escaped=null;let t=make_task();await restricted(async fn(){escaped=await t;null});escaped()')
        self.assertEqual(error.exception.code,'capability_error')
        self.assertEqual(effects,[])

