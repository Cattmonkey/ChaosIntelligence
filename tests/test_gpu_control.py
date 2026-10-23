"""Typed GPU control flow, lexical identity, laziness and real device execution."""
import hashlib
import json
import struct
import sys
import threading
import unittest
from unittest import mock

from chaos.language import Evaluator, LanguageError, Node
from chaos.suspension import TaskCancelled
from chaos import gpu_compute as gpu, gpu_ir as ir, gpu_metal as metal
from test_gpu_compute import MPS

try:
    if sys.version_info < (3, 10): raise ImportError
    import torch
except ImportError:
    torch = None


def lower(body):
    evaluator = Evaluator(step_limit=200000)
    function = evaluator.run('fn(x:Number)->Number{let seed=x;' + body + '}')
    return gpu._lower(evaluator, function)


def portable(body, values):
    return ir.cpu(lower(body), tuple(map(ir.f32, values)), lambda: None)


CASES = (
    ('let y=x; if x>0{let y=99;y}else{let y=88;y};y+1', [1, -1], [2., 0.]),
    ('var y=x; y=y+2; y', [1, -1], [3., 1.]),
    ('var y=x; y+=2; y*=3; y-=1; y', [1, -1], [8., 2.]),
    ('let flag=x<0; if !flag{x+2}else{x-2}', [1, -1], [3., -3.]),
    ('if x>0{return x+1}else{if x<0{return -x}else{0}}', [1, -1, 0], [2., 1., 0.]),
    ('var y=x; let z=y+if true{y=99;y}else{0};z', [1, 2], [100., 101.]),
    ('var n=0;var y=x;while n<4{n=n+1;if n==2{continue}else{y=y+n}};y', [1, -1], [9., 7.]),
    ('var n=0;var y=x;while n<4{n=n+1;if n==2{break}else{y=y+n}}else{y=99};y', [1, -1], [2., 0.]),
    ('var y=x;while false{y=99}else{y=y+3};y', [1, -1], [4., 2.]),
    ('var y=x;for n in [1,2,3]{y=y+n};y', [1, -1], [7., 5.]),
    ('var y=x;for n in (1,-2,3){if n<0{continue}else{y=y+n}}else{y=y+1};y', [1, -1], [6., 4.]),
    ('var y=x;for n in [1,2,3]{if n==2{break}else{y=y+n}}else{y=99};y', [1, -1], [2., 0.]),
    ('var y=x;for n in []{y=99}else{y=y+2};y', [1, -1], [3., 1.]),
    ('var y=x;for n in [1,2]{var local=n;for m in [3,4]{if m==3{continue}else{local=local+m}};y=y+local};y', [1, -1], [12., 10.]),
    ('let flag=(x>=0&&x<=2)||(x!=0&&x==3);if flag{10}else{20}', [0, 1, 3, 4], [10., 10., 10., 20.]),
    ('if x<0{while false{}else{return -x};0}else{x}', [-1, 1], [1., 1.]),
)


class StructuredPortableTests(unittest.TestCase):
    def test_scalar_interpreter_and_gpu_cpu_agree_on_control_cases(self):
        for body, values, expected in CASES:
            with self.subTest(body=body):
                scalar = Evaluator().run('let f=fn(x){' + body + '};[' + ','.join('f(%s)' % x for x in values) + ']')
                self.assertEqual(scalar, expected)
                self.assertEqual(portable(body, values), expected)

    def test_lazy_selected_paths_do_not_evaluate_nonfinite_arithmetic(self):
        for body in ('if x>0{x}else{x*x}', 'if false&&(x*x>0){0}else{x}', 'if true||(x*x>0){x}else{0}'):
            with self.subTest(body=body): self.assertEqual(portable(body, [1e30]), [ir.f32(1e30)])
        for body in ('if x>0{x*x}else{x}', 'if true&&(x*x>0){x}else{0}'):
            with self.subTest(body=body), self.assertRaises(ir.ProgramFault) as caught: portable(body, [1e30])
            self.assertEqual(caught.exception.kind, 'nonfinite')

    def test_loop_and_global_fuel_are_bounded_and_cancel_is_observed(self):
        with self.assertRaises(ir.ProgramFault) as caught: portable('while true{};x', [1])
        self.assertEqual(caught.exception.kind, 'loop_limit')
        with self.assertRaises(ir.ProgramFault) as caught: portable('var n=0;while n<1000{n=n+1};x', [1])
        self.assertEqual(caught.exception.kind, 'step_limit')
        calls = []
        def cancel():
            calls.append(1)
            if len(calls) == 10: raise TaskCancelled('cancelled')
        with self.assertRaises(TaskCancelled): ir.cpu(lower('while true{};x'), [1.], cancel)
        self.assertEqual(len(calls), 10)

    def test_first_failing_input_lane_and_first_executed_fault_are_stable(self):
        program = lower('if x>0{while true{};x}else{x*x}')
        with self.assertRaises(ir.ProgramFault) as caught: ir.cpu(program, [1., -1e30], lambda: None)
        self.assertEqual((caught.exception.lane, caught.exception.kind), (0, 'loop_limit'))
        program = lower('let bad=x*x;while true{};bad')
        with self.assertRaises(ir.ProgramFault) as caught: ir.cpu(program, [1e30], lambda: None)
        self.assertEqual(caught.exception.kind, 'nonfinite')

    def test_numeric_precision_every_primitive_signed_zero_and_exact_bool(self):
        body = 'let product=x*1.0000001192092896;product-1'
        self.assertEqual(portable(body, [1.0000001192092896]), [2.384185791015625e-7])
        zeros = portable('let y=x;y*(-1)', [0., -0.])
        self.assertEqual([struct.pack('!f', x).hex() for x in zeros], ['80000000', '00000000'])
        for body in ('let flag=x<2;flag+1', 'let flag=x<2;if flag{true}else{false}', 'if x{x}else{0}', '(x<2)<3', 'var y=x;y=true;y'):
            with self.subTest(body=body), self.assertRaises(LanguageError): lower(body)

    def test_let_parameter_for_binder_immutability_and_free_variables_reject(self):
        for body in ('let y=x;y=9;y', 'x=9;x', 'for n in [1]{n=9};x', 'let y=free;y', 'var y:Int=1;y'):
            with self.subTest(body=body), self.assertRaises(LanguageError): lower(body)
        self.assertEqual(portable('let x=3;x+1', [90]), [4.])

    def test_effects_calls_dynamic_iteration_and_outer_control_conditions_reject(self):
        for body in ('tool("probe",x)', 'for n in range(3){};x', 'for n in [x]{};x',
                     'var n=0;while n<3{n=n+1;while if x>0{break}else{false}{n=99}};n',
                     'var n=0;while n<3{n=n+1;while if x>0{continue}else{false}{n=99}};n'):
            with self.subTest(body=body), self.assertRaises(LanguageError): lower(body)
        calls = []
        with self.assertRaises(LanguageError):
            Evaluator(tool_handler=lambda *a: calls.append(a)).run('gpu_map(fn(x){if false{tool("probe",x)}else{x}},[1],device="cpu")')
        self.assertEqual(calls, [])

    def test_independent_literal_table_bound_256_and_257_rejection(self):
        body = 'var y=x;for n in [' + ','.join('1' for _ in range(256)) + ']{y=y+n};y'
        self.assertEqual(portable(body, [0]), [256.])
        with self.assertRaises(LanguageError): lower(body.replace('[' + ','.join('1' for _ in range(256)) + ']', '[' + ','.join('1' for _ in range(257)) + ']'))

    def test_program_identity_alpha_names_and_signed_zero_and_schema_are_distinct(self):
        a = lower('let y=x;y+2'); b = lower('let renamed=x;renamed+2')
        self.assertEqual(metal.identity(a), metal.identity(b)); self.assertEqual(metal.source(a), metal.source(b))
        self.assertNotEqual(metal.identity(lower('let y=x;y+0')), metal.identity(lower('let y=x;y+(-0.0)')))
        self.assertNotEqual(metal.identity(a), metal.identity(('binary', '+', ('input',), ('literal', 2.))))
        for program in (('program', True, ('block', ())), ('program', 2, ('block', (('slot', 8),))),
                        ('program', 2, ('block', (('bind', 1, True, 'Any', ('literal', 1.)), ('bind', 1, False, 'Any', ('literal', 2.))))),
                        ('program', 2, ('block', (('literal', True),))), ('program', 2, ('block', (('break',),)))):
            with self.subTest(program=program), self.assertRaises(ValueError): ir.plan(program)

    def test_old_ir1_source_and_identity_are_exactly_the_historical_generator(self):
        value = ('binary', '+', ('binary', '*', ('input',), ('input',)), ('literal', 2.))
        canonical = ('binary', '+', ('binary', '*', ('input',), ('input',)), ('literal', 0x40000000))
        expected = hashlib.sha256(b'chaos.metal.float32-ir/1\0' + json.dumps(canonical, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(metal.identity(value), expected)
        self.assertEqual(hashlib.sha256(metal.source(value).encode('ascii')).hexdigest(), '7d0a52c09a3e41ee315f3a33934191204deb1a4e5efe7a8dade08251a59eb46f')
        self.assertNotIn('faults', metal.source(value)); self.assertNotIn('fuel', metal.source(value))
        function = Evaluator().run('fn(x){x*x+2}')
        self.assertEqual(gpu._lower(function.owner, function), value)

    def test_shader_uses_control_flow_guards_and_only_generated_slot_names(self):
        code = metal.source(lower('var y=x;while y<3{y=y+1};if y>0{y}else{-y}'))
        self.assertIn('while (true)', code); self.assertIn('faults[index]', code)
        self.assertIn('4096u', code); self.assertIn('1024u', code); self.assertIn('volatile float', code)
        self.assertNotIn('seed', code); self.assertNotIn('y=', code)
        self.assertIn('contract(off)', code); self.assertIn('reassociate(off)', code)

    def test_hygienic_macro_aliases_nested_bindings_and_cold_frozen_snapshot(self):
        source = 'macro build(){quote{fn(x:Number)->Number{var y=x;for n in [1,2]{y=y+n};y}}};await gpu_map(build!(),[39],device="cpu")'
        self.assertEqual(Evaluator().run(source)['values'], [42.])
        e = Evaluator(); fn = e.run('fn(x){let y=x;y+2}'); e.suspension.begin()
        try:
            values = [40]; task = gpu.gpu_map(e, fn, values, device='cpu'); before = gpu.gpu_kernel_info(e, task)
            self.assertEqual(before['status'], 'created'); self.assertIsNone(e.suspension.pool)
            values[0] = 1; fn.body[1][:] = [Node(('literal', 9))]
            result = e.suspension.drive(e._s_evaluate(Node(('await', Node(('literal', task)))), e.env))
            self.assertEqual(result['values'], [42.]); self.assertEqual(gpu.gpu_kernel_info(e, task)['irHash'], before['irHash'])
        finally: e.suspension.finish()


@unittest.skipUnless(torch is not None, 'optional Torch unavailable on base runtime')
class StructuredTensorTests(unittest.TestCase):
    def test_cpu_tensor_executes_the_same_validated_control_program(self):
        for body, values, expected in CASES:
            with self.subTest(body=body):
                result = ir.tensor(torch, lower(body), torch.tensor(values, dtype=torch.float32), lambda: None)
                self.assertEqual(result.tolist(), expected)

    def test_unselected_lanes_are_not_computed_and_first_fault_matches_cpu(self):
        self.assertEqual(ir.tensor(torch, lower('if x>0{x}else{x*x}'), torch.tensor([1e30]), lambda: None).tolist(), [ir.f32(1e30)])
        for body, values in (('if x>0{while true{};x}else{x*x}', [1., -1e30]), ('let bad=x*x;while true{};bad', [1e30])):
            program = lower(body)
            with self.assertRaises(ir.ProgramFault) as cpu: ir.cpu(program, values, lambda: None)
            with self.assertRaises(ir.ProgramFault) as tensor: ir.tensor(torch, program, torch.tensor(values), lambda: None)
            self.assertEqual((tensor.exception.lane, tensor.exception.code), (cpu.exception.lane, cpu.exception.code))

    def test_tensor_cancellation_reaches_async_worker_boundary(self):
        event = threading.Event(); calls = []
        def cancel():
            calls.append(1)
            if len(calls) == 9: event.set()
            gpu._check(event)
        with self.assertRaises(TaskCancelled): ir.tensor(torch, lower('while true{};x'), torch.tensor([1.]), cancel)
        self.assertEqual(len(calls), 9)


@unittest.skipUnless(MPS, 'optional real MPS hardware unavailable')
class StructuredActualMPSTests(unittest.TestCase):
    def test_structured_shader_cancel_during_compile_and_after_dispatch_drains(self):
        # Reuse the established real child instrumentation with an IR2 payload.
        from test_gpu_metal import ActualMetalCompilerTests
        program = lower('let y=x;y+2')
        with mock.patch('test_gpu_metal.B', return_value=program):
            ActualMetalCompilerTests('test_cancel_during_compile_or_after_dispatch_drains_reaps_and_does_not_block_parent').test_cancel_during_compile_or_after_dispatch_drains_reaps_and_does_not_block_parent()

    def test_structured_tensor_cancel_synchronizes_and_releases_device_lock(self):
        program = lower('while true{};x'); event = threading.Event(); calls = []; original = ir.tensor
        def cancelled(torch_module, tree, values, callback):
            def check():
                calls.append(1)
                if len(calls) == 13: event.set()
                gpu._check(event)
            return original(torch_module, tree, values, check)
        with mock.patch('chaos.gpu_ir.tensor', side_effect=cancelled), mock.patch.object(torch.mps, 'synchronize', wraps=torch.mps.synchronize) as synced:
            with self.assertRaises(TaskCancelled):
                gpu._torch_result(torch, {'operation':'map', 'ir':program, 'values':(1.,)}, 'mps', event)
            self.assertEqual(synced.call_count, 1)
        self.assertFalse(gpu._DEVICE_LOCKS['mps'].locked()); self.assertEqual(len(calls), 13)

    def test_real_mps_tensor_and_isolated_metal_control_differential(self):
        for body, values, expected in CASES:
            for compiler in ('tensor', 'metal'):
                with self.subTest(body=body, compiler=compiler):
                    result = Evaluator(step_limit=200000).run('let t=gpu_map(fn(x){' + body + '},' + repr(values) + ',device="mps",compiler="' + compiler + '");let before=gpu_kernel_info(t);let r=await t;[before,r,gpu_kernel_info(t)]')
                    before, value, info = result
                    self.assertEqual(value['values'], expected); self.assertEqual(value['device'], 'mps:0'); self.assertEqual(value['backend'], 'torch')
                    self.assertEqual(len(value), 5); self.assertEqual(len(info), 9); self.assertIsNone(before['compiler'])
                    self.assertEqual(info['compiler'], compiler); self.assertEqual(info['status'], 'completed')
                    self.assertEqual(info['isolated'], compiler == 'metal')
                    if compiler == 'metal': self.assertEqual(info['dispatches'], 1); self.assertEqual(len(info['sourceHash']), 64)

    def test_real_metal_lazy_overflow_first_lane_and_bounded_faults(self):
        for body, values in (('if x>0{x}else{x*x}', [1e30]), ('if false&&(x*x>0){0}else{x}', [1e30]), ('if true||(x*x>0){x}else{0}', [1e30])):
            result = Evaluator().run('await gpu_map(fn(x){' + body + '},' + repr(values) + ',device="mps",compiler="metal")')
            self.assertEqual(result['values'], list(map(ir.f32, values)))
        for body, values in (('if x>0{while true{};x}else{x*x}', [1., -1e30]), ('let bad=x*x;while true{};bad', [1e30]), ('var n=0;while n<1000{n=n+1};x', [1.])):
            program = lower(body)
            with self.assertRaises(ir.ProgramFault) as expected: ir.cpu(program, values, lambda: None)
            with self.assertRaises(metal.MetalFailure) as actual: metal.execute(program, values, threading.Event())
            self.assertIn(str(expected.exception), str(actual.exception)); self.assertEqual(actual.exception.phase, 'execute'); self.assertEqual(actual.exception.dispatches, 1)

    def test_real_empty_and_256_item_table_and_float32_fma_contract(self):
        empty = Evaluator().run('let t=gpu_map(fn(x){let y=x;y+2},[],device="mps",compiler="metal");let r=await t;[r,gpu_kernel_info(t)]')
        self.assertEqual(empty[0]['values'], []); self.assertEqual(empty[1]['dispatches'], 0)
        body = 'var y=x;for n in [' + ','.join('1' for _ in range(256)) + ']{y=y+n};y'
        self.assertEqual(metal.execute(lower(body), [0.], threading.Event())[0], [256.])
        self.assertEqual(metal.execute(lower('let product=x*1.0000001192092896;product-1'), [1.0000001192092896], threading.Event())[0], [2.384185791015625e-7])


if __name__ == '__main__': unittest.main()
