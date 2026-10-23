"""Differential contracts for real native code and portable interpretation."""
import copy
import json
import sys
import unittest
from unittest import mock

from chaos.jit import compile_function, compile_info, analyze, prove, Unsupported
from chaos.language import Evaluator, LanguageError, ResourceLimit, Node, ExecutionControl
from chaos.native_jit import BackendUnavailable


def available():
    if sys.version_info < (3, 10): return False
    try:
        import llvmlite
        return llvmlite.__version__.startswith('0.45.')
    except ImportError:
        return False


NATIVE = available()


class PortableCompilationTests(unittest.TestCase):
    def test_auto_is_callable_with_visible_truthful_backend(self):
        result = Evaluator().run('let f=compile_function(fn(x){x*2});let answer=f(21);[answer,type_of(f),compile_info(f)]')
        self.assertEqual(result[:2], [42, 'Function'])
        info = result[2]
        self.assertEqual(info['schema'], 'chaos.compiled-function/1')
        self.assertEqual(info['status'], 'native' if NATIVE else 'interpreter')
        self.assertEqual(info['nativeCalls'], 1 if NATIVE else 0)
        self.assertEqual(info['interpreterCalls'], 0 if NATIVE else 1)

    def test_explicit_interpreter_and_plain_info_do_not_load_backend(self):
        with mock.patch('chaos.native_jit.compile_kernel', side_effect=AssertionError('must not load')):
            result = Evaluator().run('let plain=fn(x){x+2};let f=compile_function(plain,"interpreter");[f(40),compile_info(f),compile_info(plain)]')
        self.assertEqual(result[0], 42)
        self.assertEqual(result[1]['fallbackReason'], 'requested_interpreter')
        self.assertEqual(result[1]['interpreterCalls'], 1)
        self.assertEqual(result[2]['fallbackReason'], 'not_compiled')

    def test_unavailable_auto_falls_back_and_native_is_clear_error(self):
        with mock.patch('chaos.native_jit.compile_kernel', side_effect=BackendUnavailable('disabled')):
            result = Evaluator().run('let f=compile_function(fn(x){x+2});[f(40),compile_info(f)]')
            self.assertEqual(result[0], 42)
            self.assertEqual(result[1]['fallbackReason'], 'disabled')
            with self.assertRaises(LanguageError) as caught:
                Evaluator().run('compile_function(fn(x){x},"native")')
            self.assertEqual(caught.exception.code, 'compiler_error')

    def test_unsupported_effects_are_never_run_during_compilation(self):
        calls = []
        e = Evaluator(tool_handler=lambda name, value: calls.append((name, value)) or value)
        result = e.run('let f=compile_function(fn(x){tool("probe",x)});let before=compile_info(f);let v=f(42);[before,v,compile_info(f)]')
        self.assertEqual(calls, [('probe', 42)])
        self.assertEqual(result[0]['status'], 'unsupported')
        self.assertEqual(result[1], 42)
        self.assertEqual(result[2]['interpreterCalls'], 1)

    def test_free_variables_containers_and_async_use_interpreter(self):
        sources = [
            'let y=2;compile_function(fn(x){x+y})',
            'compile_function(fn(x){[x]})',
            'compile_function(async fn(x){x+1})',
            'compile_function(fn*(x){yield x})',
        ]
        for source in sources:
            with self.subTest(source=source):
                e = Evaluator();f = e.run(source)
                self.assertEqual(compile_info(e, f)['status'], 'unsupported')

    def test_invalid_inputs_targets_and_cross_owner_reject(self):
        for source in ('compile_function(42)', 'compile_info(42)', 'compile_function(fn(x){x},"gpu")'):
            with self.subTest(source=source), self.assertRaises(LanguageError): Evaluator().run(source)
        owner = Evaluator();fn = owner.run('fn(x){x}')
        with self.assertRaises(LanguageError) as caught: compile_function(Evaluator(), fn)
        self.assertEqual(caught.exception.code, 'ownership_error')

    def test_native_unsupported_raises_compiler_error(self):
        with self.assertRaises(LanguageError) as caught:
            Evaluator().run('compile_function(fn(x){x/2},"native")')
        self.assertEqual(caught.exception.code, 'compiler_error')

    def test_arbitrary_precision_is_preserved_outside_native_guard(self):
        e = Evaluator();f = e.run('compile_function(fn(x){x*x})')
        value = 2 ** 100
        self.assertEqual(e.invoke(f, [value]), value * value)
        self.assertEqual(compile_info(e, f)['interpreterCalls'], 1)
        if NATIVE: self.assertEqual(compile_info(e, f)['lastFallbackReason'], 'argument_guard')

    def test_interval_proof_halves_bounds_and_rejects_unsafe_constants(self):
        e = Evaluator();f = e.run('fn(x){x*x*x*x}')
        bound = prove(analyze(f))
        self.assertLess(bound, 2 ** 31 - 1)
        self.assertLessEqual(bound ** 4, 2 ** 63 - 1)
        g = e.run('fn(x){9223372036854775807+1}')
        with self.assertRaises(Unsupported): prove(analyze(g))

    def test_parameter_and_node_limits_are_bounded(self):
        e = Evaluator()
        f = e.run('fn(a,b,c,d,e,f,g,h,i){a}')
        self.assertEqual(compile_info(e, compile_function(e, f))['fallbackReason'], 'parameter_limit')
        source = 'fn(x){' + '+'.join(['x'] * 70) + '}'
        f = e.run(source)
        self.assertIn(compile_info(e, compile_function(e, f))['fallbackReason'], ('node_limit', 'depth_limit'))

    def test_operational_backend_failure_auto_falls_back_and_native_reports_error(self):
        with mock.patch('chaos.native_jit.compile_kernel', side_effect=RuntimeError('backend failed')):
            result = Evaluator().run('let f=compile_function(fn(x){x+2});[f(40),compile_info(f)]')
            self.assertEqual(result[0], 42)
            self.assertEqual(result[1]['fallbackReason'], 'backend_failure')
            with self.assertRaises(LanguageError) as caught:
                Evaluator().run('compile_function(fn(x){x},"native")')
            self.assertEqual(caught.exception.code, 'compiler_error')
        with mock.patch('chaos.native_jit.compile_kernel', side_effect=KeyboardInterrupt('stop')):
            with self.assertRaises(KeyboardInterrupt): Evaluator().run('compile_function(fn(x){x})')

    def test_info_contains_no_live_pointer_environment_or_source(self):
        result = Evaluator().run('let hidden="secret-marker";let f=compile_function(fn(x){x+2});compile_info(f)')
        text = json.dumps(result)
        self.assertNotIn('secret-marker', text)
        self.assertNotIn('address', text)
        self.assertEqual(set(result), {'schema','status','requestedTarget','backend','target','compilerVersion','parameterGuards','nodeCount','nativeCalls','interpreterCalls','fallbackReason','lastFallbackReason'})


@unittest.skipUnless(NATIVE, 'optional LLVM backend is not installed for this interpreter')
class NativeExecutionTests(unittest.TestCase):
    def function(self, source='fn(x,y){return -(x*x)+y*2}', target='native'):
        e = Evaluator(step_limit=1000000)
        f = e.run(source)
        return e, compile_function(e, f, target)

    def invoke_record(self, e, f, values, limit=1000000, cancelled=None):
        e.steps = 0; e.step_limit = limit
        e.cancelled = cancelled or (lambda: False)
        try: result = ('value', e.invoke(f, values))
        except LanguageError as error: result = ('error', error.record())
        return result, e.steps

    def test_real_native_positive_negative_zero_and_constants(self):
        cases = [('fn(x,y){x+y}', [40,2], 42), ('fn(x,y){x-y}', [-40,2], -42),
                 ('fn(x,y){x*y}', [-6,7], -42), ('fn(x){+(-x)}', [42], -42),
                 ('fn(){42}', [], 42), ('fn(x){0*x}', [0], 0)]
        for source, values, expected in cases:
            with self.subTest(source=source):
                e, f = self.function(source)
                self.assertEqual(e.invoke(f, values), expected)
                self.assertEqual(compile_info(e, f)['nativeCalls'], 1)

    def test_every_step_budget_threshold_matches_interpreter_and_source(self):
        native_e, native_f = self.function()
        plain_e, plain_f = self.function(target='interpreter')
        self.assertEqual(self.invoke_record(native_e,native_f,[3,26]), self.invoke_record(plain_e,plain_f,[3,26]))
        count = native_e.steps
        for limit in range(count + 2):
            with self.subTest(limit=limit):
                self.assertEqual(self.invoke_record(native_e,native_f,[3,26],limit), self.invoke_record(plain_e,plain_f,[3,26],limit))

    def test_every_cancellation_tick_matches_original_interpreter(self):
        native_e, native_f = self.function('fn(x){\n return (x+2)*(x-3)\n}')
        plain_e, plain_f = self.function('fn(x){\n return (x+2)*(x-3)\n}', 'interpreter')
        total = self.invoke_record(plain_e, plain_f, [7])[1]
        for threshold in range(1, total + 2):
            with self.subTest(threshold=threshold):
                a = self.invoke_record(native_e, native_f, [7], cancelled=lambda: native_e.steps >= threshold)
                b = self.invoke_record(plain_e, plain_f, [7], cancelled=lambda: plain_e.steps >= threshold)
                self.assertEqual(a, b)

    def test_callback_host_errors_are_converted_at_exact_expression_span(self):
        for exception in (ValueError('bad'), TypeError('bad'), KeyError('bad'), IndexError('bad'),
                          ZeroDivisionError('bad'), OverflowError('bad'), RecursionError('bad')):
            outcomes = []
            for target in ('native', 'interpreter'):
                e, f = self.function('fn(x){\n (x+2)*3\n}', target)
                base = e.tick; e.steps = 0
                def tick():
                    base()
                    if e.steps == 6: raise exception
                e.tick = tick
                with self.assertRaises(LanguageError) as caught: e.invoke(f, [4])
                outcomes.append((caught.exception.record(), e.steps))
            with self.subTest(error=type(exception).__name__): self.assertEqual(outcomes[0], outcomes[1])

    def test_callback_base_exception_is_not_swallowed_or_replayed(self):
        e, f = self.function('fn(x){x+2}')
        original = e.tick; e.steps = 0
        sentinel = KeyboardInterrupt('stop')
        def tick():
            original()
            if e.steps == 5: raise sentinel
        e.tick = tick
        with self.assertRaises(KeyboardInterrupt) as caught: e.invoke(f, [40])
        self.assertIs(caught.exception, sentinel)
        self.assertEqual(e.steps, 5)
        self.assertEqual(compile_info(e, f)['nativeCalls'], 1)
        self.assertEqual(compile_info(e, f)['interpreterCalls'], 0)

    def test_argument_guards_exclude_bool_float_subclasses_and_large_ints(self):
        e, f = self.function('fn(x){x+2}')
        p, plain = self.function('fn(x){x+2}', 'interpreter')
        class Integer(int): pass
        for value in (True, 1.5, Integer(40), 2**50, -(2**50)):
            with self.subTest(value=repr(value)):
                self.assertEqual(self.invoke_record(e,f,[value]), self.invoke_record(p,plain,[value]))
                self.assertEqual(compile_info(e,f)['lastFallbackReason'], 'argument_guard')
        self.assertEqual(compile_info(e,f)['nativeCalls'], 0)

    def test_defaults_keywords_and_positional_modes_use_existing_binder(self):
        e, f = self.function('fn(x:Int,/,y:Int=1,*,z:Int=2)->Int{x+y*z}')
        self.assertEqual(e.invoke(f,[40],{'z':2}), 42)
        self.assertEqual(compile_info(e,f)['nativeCalls'], 1)
        for args,kwargs in (([],{'x':40}),([40,1,2],{}),([40,2],{'y':2}),([40],{'unknown':1})):
            with self.subTest(args=args,kwargs=kwargs), self.assertRaises(LanguageError): e.invoke(f,args,kwargs)
        self.assertEqual(compile_info(e,f)['nativeCalls'], 1)

    def test_debugger_and_expression_trace_fall_back_without_losing_events(self):
        e, f = self.function('fn(x){x+2}')
        events=[];e.trace_handler=events.append
        self.assertEqual(e.invoke(f,[40]),42)
        self.assertTrue(any(item['event']=='expression' for item in events))
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'expression_trace')
        e.trace_handler=None;seen=[];e.debug_hook=lambda *args:seen.append(args[0][0])
        self.assertEqual(e.invoke(f,[40]),42)
        self.assertIn('binary',seen)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'debugger')
        self.assertEqual(compile_info(e,f)['nativeCalls'],0)

    def test_lifecycle_only_trace_does_not_disable_native_execution(self):
        e,f=self.function('fn(x){x+2}')
        e.trace_handler=lambda event:None;e.expression_trace_required=lambda:False
        self.assertEqual(e.invoke(f,[40]),42)
        self.assertEqual(compile_info(e,f)['nativeCalls'],1)
        e.expression_trace_required=lambda:True
        self.assertEqual(e.invoke(f,[40]),42)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'expression_trace')

    def test_body_mutation_invalidates_native_and_uses_new_source(self):
        e,f=self.function('fn(x){x+2}')
        binary=f.body[1][0]
        f.body[1][0]=Node(('binary','+',binary[2],Node(('literal',3),dict(binary[3].span))),dict(binary.span))
        self.assertEqual(e.invoke(f,[40]),43)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')
        self.assertEqual(compile_info(e,f)['nativeCalls'],0)

    def test_span_and_parameter_mutations_invalidate_native(self):
        e,f=self.function('fn(x){x+2}')
        f.body[1][0].span['line']=99
        self.assertEqual(e.invoke(f,[40]),42)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')
        e,f=self.function('fn(x){x+2}')
        f.params[0]=('x','Any','keyword_only',f.params[0][3])
        self.assertEqual(e.invoke(f,[],{'x':40}),42)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')

    def suspended(self, e, f, values):
        from chaos.suspension import CHECKPOINT
        iterator = e._s_invoke(f, values)
        self.assertIs(next(iterator), CHECKPOINT)
        return iterator

    def finish(self, iterator):
        with self.assertRaises(StopIteration) as finished:
            next(iterator)
        return finished.exception.value

    def test_mutation_at_body_checkpoint_deoptimizes_without_second_checkpoint(self):
        outcomes=[]
        for target in ('native','interpreter'):
            e,f=self.function('fn(x){x+2}',target)
            iterator=self.suspended(e,f,[40])
            replacement=Evaluator().run('fn(x){x+3}')
            f.body[1][:]=replacement.body[1]
            outcomes.append(self.finish(iterator))
        self.assertEqual(outcomes,[43,43])

    def test_replacing_body_at_checkpoint_keeps_already_captured_body(self):
        outcomes=[]
        for target in ('native','interpreter'):
            e,f=self.function('fn(x){x+2}',target)
            iterator=self.suspended(e,f,[40])
            f.body=Evaluator().run('fn(x){x+3}').body
            outcomes.append((self.finish(iterator),e.invoke(f,[40])))
        self.assertEqual(outcomes,[(42,43),(42,43)])

    def test_enabling_trace_at_body_checkpoint_preserves_expression_events(self):
        outcomes=[]
        for target in ('native','interpreter'):
            e,f=self.function('fn(x){x+2}',target)
            e.steps=0
            iterator=self.suspended(e,f,[40]);events=[]
            e.trace_handler=events.append
            outcomes.append((self.finish(iterator),events,e.steps))
        self.assertEqual(outcomes[0],outcomes[1])
        self.assertTrue(outcomes[0][1])

    def test_error_thrown_into_body_checkpoint_has_original_body_location(self):
        for factory in (lambda:LanguageError('failed'), lambda:ValueError('failed'),
                        lambda:RecursionError('failed'), lambda:ResourceLimit('failed')):
            outcomes=[]
            for target in ('native','interpreter'):
                e,f=self.function('fn(x){x+2}',target)
                e.current_span={'file':'caller.chaos','line':99,'column':2}
                iterator=self.suspended(e,f,[40])
                with self.assertRaises(LanguageError) as caught: iterator.throw(factory())
                outcomes.append(caught.exception.record())
            self.assertEqual(outcomes[0],outcomes[1])
            self.assertEqual(outcomes[0]['location']['file'],'main.chaos')

    def test_info_guards_are_immutable_and_bounded_after_parameter_mutation(self):
        e,f=self.function('fn(x,y){x+y}')
        expected=compile_info(e,f)['parameterGuards']
        f.params*=1000
        self.assertEqual(compile_info(e,f)['parameterGuards'],expected)
        report=compile_info(e,f);report['parameterGuards'][0]['minimum']=0
        self.assertEqual(compile_info(e,f)['parameterGuards'],expected)

    def test_return_contracts_still_apply_to_native_results(self):
        e,f=self.function('fn(x)->String{x+2}')
        with self.assertRaises(LanguageError):e.invoke(f,[40])
        self.assertEqual(compile_info(e,f)['nativeCalls'],1)

    def test_hygienic_macro_parameter_aliases_compile(self):
        source='macro build(){quote {fn(x){x+2}}};let f=compile_function(build!());let v=f(40);[v,compile_info(f)]'
        result=Evaluator().run(source)
        self.assertEqual(result[0],42)
        self.assertEqual(result[1]['nativeCalls'],1)

    def test_native_engine_survives_other_compilations_and_collection(self):
        import gc
        e,f=self.function('fn(x){x+2}')
        for _ in range(3):
            original=e.run('fn(x){x*2}');compile_function(e,original,'native')
        gc.collect()
        self.assertEqual(e.invoke(f,[40]),42)
        self.assertEqual(compile_info(e,f)['nativeCalls'],1)

    def test_per_evaluator_native_allocation_quota(self):
        e,f=self.function('fn(x){x+2}')
        e.native_kernel_count=32
        fallback=compile_function(e,f)
        self.assertEqual(compile_info(e,fallback)['fallbackReason'],'kernel_limit')
        self.assertEqual(e.invoke(fallback,[40]),42)
        with self.assertRaises(LanguageError):compile_function(e,f,'native')

    def test_async_outer_and_finally_keep_normal_scheduler_semantics(self):
        source='let f=compile_function(fn(x){x+2});var seen=[];async fn work(x){try{return f(x)}finally{seen=append(seen,x)}};let values=await gather([work(40),work(41)]);[values,seen,compile_info(f)]'
        result=Evaluator().run(source)
        self.assertEqual(result[0],[42,43]);self.assertEqual(sorted(result[1]),[40,41])
        self.assertEqual(result[2]['nativeCalls'],2)


if __name__=='__main__':unittest.main()
