"""Rethrow is a private dynamic scope, preserved by the real trampoline."""
import unittest

from chaos.language import Evaluator, LanguageError, Native, Parser


class ExceptionRethrowTests(unittest.TestCase):
    def run_code(self, source, **kwargs): return Evaluator(**kwargs).run(source)

    def test_bare_throw_is_distinct_from_explicit_null_and_parses_terminators(self):
        self.assertEqual(Parser('throw').program()[1][0], ('rethrow',))
        self.assertEqual(Parser('throw null').program()[1][0][0], 'throw')
        for body in ('throw;', '(throw)', '[throw]', 'fn(x){x}(throw)'):
            with self.subTest(body=body):
                self.assertEqual(self.run_code('try{'+body+'}catch e{e.code}'), 'rethrow_error')
        self.assertIsNone(self.run_code('try{throw null}catch e{e.value}'))

    def test_nested_handler_restores_outer_error_and_guard_uses_new_error(self):
        source = '''fn current(){try{throw}catch e{e.value}};
          try{throw 20}catch outer{let before=current();
          let nested=try{throw 22}catch inner if current()==22{current()};
          [before,nested,current()]}'''
        self.assertEqual(self.run_code(source), [20, 22, 20])
        source = 'try{try{throw 7}catch e if do{throw}{0}catch later{99}}catch out{out.value}'
        self.assertEqual(self.run_code(source), 7)

    def test_error_record_mutation_cannot_change_rethrow_identity(self):
        failure = LanguageError('original', line=9, column=4, file='original.chaos',
                                code='fixture_error', value={'items': [1]})
        def fail(*args): raise failure
        source = 'fn helper(){throw};try{tool("fail",null)}catch e{mutate(e);helper()}'
        evaluator = Evaluator(tool_handler=fail)
        def mutate(record):
            record['code'] = 'changed'
            record['value']['items'][0] = 99
        evaluator.env.bind('mutate', Native('mutate', mutate, evaluator))
        with self.assertRaises(LanguageError) as raised:
            evaluator.run(source)
        self.assertIs(raised.exception, failure)
        self.assertEqual((failure.code, failure.value, failure.line), ('fixture_error', {'items': [1]}, 9))
        value = self.run_code('try{try{throw 7}catch e{throw e}}catch out{[out.code,out.value.code,out.value.value]}')
        self.assertEqual(value, ['user_throw', 'user_throw', 7])

    def test_direct_return_pipe_partial_apply_and_composed_helpers_rethrow(self):
        expressions = ['again()', 'return again()', 'null |> again', 'partial(again)()',
                       'apply(again,[])', 'compose(again,fn(x){x})(null)',
                       'compose(fn(x){x},again)(null)']
        for expression in expressions:
            source = 'fn again(x=null){throw};fn f(){try{throw 42}catch e{'+expression+'}};try{f()}catch out{out.value}'
            with self.subTest(expression=expression): self.assertEqual(self.run_code(source), 42)

    def test_long_handler_tail_recursion_and_boolean_contract_are_preserved(self):
        source = 'fn again(){throw};fn f(n){try{throw n}catch e{if n==0{again()}else{f(n-1)}}};try{f(1500)}catch out{out.value}'
        self.assertEqual(self.run_code(source, step_limit=150000), 0)
        self.assertEqual(self.run_code('fn again()->Bool{throw};fn f(){try{throw 42}catch e{true&&again()}};try{f()}catch out{out.value}'), 42)
        self.assertEqual(self.run_code('fn f(){try{throw 1}catch e{true&&fn(){42}()}};try{f()}catch out{out.code}'), 'runtime_error')

    def test_compose_restores_outer_context_after_inner_tail_call(self):
        source = '''fn current(x=null){try{throw}catch e{e.value}};
          fn inner(x){try{throw 22}catch e{current()}};
          try{throw 20}catch e{compose(current,inner)(null)}'''
        self.assertEqual(self.run_code(source), 20)

    def test_compose_cold_handle_early_return_restores_pending_context(self):
        source = '''fn current(x){try{throw}catch e{e.value}};async fn cold(){42};
          fn inner(x){try{throw 22}catch e{cold()}};
          try{throw 20}catch e{compose(current,inner)(null)}'''
        self.assertEqual(self.run_code(source), 20)

    def test_returned_closure_and_cold_task_do_not_capture_creation_error(self):
        source = '''let later=try{throw 42}catch e{fn(){throw}};
          let task=try{throw 21}catch e{async fn(){throw}()};
          [try{later()}catch e{e.code},try{await task}catch e{e.code}]'''
        self.assertEqual(self.run_code(source), ['rethrow_error', 'rethrow_error'])

    def test_pending_error_finally_activates_original_and_nested_restores(self):
        self.assertEqual(self.run_code('fn again(){throw};try{try{throw 42}finally{again()}}catch e{e.value}'), 42)
        source = '''fn current(){try{throw}catch e{e.value}};var seen=[];
          let value=try{throw 20}catch outer{try{try{throw 22}finally{
          seen=append(seen,current());try{throw 33}catch inner{seen=append(seen,current())};
          seen=append(seen,current())}}catch nested{null};current()};[value,seen]'''
        self.assertEqual(self.run_code(source), [20, [22, 33, 22]])

    def test_normal_finally_inherits_outer_and_cleanup_return_resolves_helper(self):
        source = 'fn again(){throw};try{try{throw 42}catch e{try{1}finally{again()}}}catch out{out.value}'
        self.assertEqual(self.run_code(source), 42)
        source = 'fn again(){throw};fn f(){try{throw 42}finally{return again()}};try{f()}catch out{out.value}'
        self.assertEqual(self.run_code(source), 42)

    def test_native_reentrant_drive_inherits_and_restores_active_error(self):
        evaluator = Evaluator()
        def reenter(function): return evaluator.invoke(function, [])
        evaluator.env.bind('reenter', Native('reenter', reenter, evaluator))
        source = 'fn again(){throw};[try{try{throw 42}catch e{reenter(again)}}catch out{out.value},try{again()}catch e{e.code}]'
        self.assertEqual(evaluator.run(source), [42, 'rethrow_error'])
        self.assertIsNone(evaluator.handled_error)

    def test_host_python_exception_state_is_not_language_context(self):
        try: raise ValueError('incidental host context')
        except ValueError:
            self.assertEqual(self.run_code('try{throw}catch e{e.code}'), 'rethrow_error')

    def test_no_state_leak_after_return_error_or_nested_scope(self):
        evaluator = Evaluator()
        source = 'fn f(){try{throw 42}catch e{return 1}};f();try{throw}catch e{e.code}'
        self.assertEqual(evaluator.run(source), 'rethrow_error')
        self.assertIsNone(evaluator.handled_error)
        with self.assertRaises(LanguageError): evaluator.run('try{throw 42}catch e{throw}')
        self.assertIsNone(evaluator.handled_error)


if __name__ == '__main__': unittest.main()
