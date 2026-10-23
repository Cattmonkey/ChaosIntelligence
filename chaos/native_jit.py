"""Optional LLVM backend for internally generated, proven scalar expression IR.

There is no entry point for source-provided LLVM IR, symbol names or pointers.
Machine code only loads scalar arguments, performs bounded integer arithmetic and ordered comparisons,
and calls the invocation's checkpoint function. Engines outlive their kernels.
"""
import ctypes
import sys
import threading

_LOCK = threading.RLock()

from .native_code_cache import DEFAULT_NATIVE_CODE_CACHE, compile_generated_module


class BackendUnavailable(Exception):
    pass


def _backend():
    if sys.version_info < (3, 10):
        raise BackendUnavailable('python_version')
    try:
        import llvmlite
        if not llvmlite.__version__.startswith('0.45.'):
            raise BackendUnavailable('llvmlite_version')
        from llvmlite import binding, ir
        binding.check_jit_execution()
        binding.initialize_native_target()
        binding.initialize_native_asmprinter()
        return binding, ir, llvmlite.__version__
    except BackendUnavailable:
        raise
    except (ImportError, OSError, RuntimeError) as error:
        raise BackendUnavailable('backend_unavailable') from error


_CHECKPOINT = ctypes.CFUNCTYPE(ctypes.c_uint8, ctypes.c_int32)
_KERNEL = ctypes.CFUNCTYPE(ctypes.c_uint8, ctypes.POINTER(ctypes.c_int64),
                          ctypes.POINTER(ctypes.c_int64), _CHECKPOINT)


class NativeKernel:
    def __init__(self, engine, module, machine, address, version, target, optimization=None):
        self._engine, self._module, self._machine = engine, module, machine
        self._call = _KERNEL(address)
        self.version, self.target = version, target
        self.optimization = optimization

    def invoke(self, arguments, checkpoint):
        values = (ctypes.c_int64 * max(1, len(arguments)))(*arguments)
        result = ctypes.c_int64()
        failure = []
        def checked(index):
            if failure: return 0
            try:
                checkpoint(index)
                return 1
            except BaseException as error:
                # Exceptions cannot unwind through a C callback. The generated
                # branch exits before subsequent checkpoints or output commit.
                failure.append(error)
                return 0
        callback = _CHECKPOINT(checked)
        completed = self._call(values, ctypes.byref(result), callback)
        if failure: raise failure[0]
        if completed != 1:
            raise RuntimeError('native kernel returned without a checkpoint failure')
        return result.value


def compile_kernel(expression, prefix, *, code_cache=DEFAULT_NATIVE_CODE_CACHE):
    """Compile a trusted, interval-proven expression assembled by chaos.jit."""
    with _LOCK:
        from .scalar_optimizer import optimize
        plan = optimize(expression, prefix)
        llvm, ir, version = _backend()
        target = llvm.get_default_triple()
        machine = llvm.Target.from_default_triple().create_target_machine(cpu='', features='', opt=2, reloc='default', codemodel='jitdefault', printmc=False, jit=False, abiname='')
        module = ir.Module(name='chaos_integer_kernel')
        module.triple, module.data_layout = target, str(machine.target_data)
        i64, i32, i8 = ir.IntType(64), ir.IntType(32), ir.IntType(8)
        callback_type = ir.FunctionType(i8, [i32]).as_pointer()
        function = ir.Function(module, ir.FunctionType(i8, [i64.as_pointer(), i64.as_pointer(), callback_type]), name='chaos_kernel')
        arguments, output, checkpoint = function.args
        builder = ir.IRBuilder(function.append_basic_block('entry'))
        abort = function.append_basic_block('abort')
        def check(index):
            ok = builder.call(checkpoint, [ir.Constant(i32, index)])
            proceed = function.append_basic_block('step')
            builder.cbranch(builder.icmp_unsigned('==', ok, ir.Constant(i8, 1)), proceed, abort)
            builder.position_at_end(proceed)
        # Each input is a private immutable invocation snapshot. Keep every
        # logical event before physical total computation, in the original
        # order. A failed event can never reach the output store.
        for index in plan.checkpoints: check(index)
        emitted = {}
        for index in plan.schedule:
            item = plan.values[index]
            operands = [emitted[child] for child in item.children]
            if item.kind == 'literal': value = ir.Constant(i64, item.value)
            elif item.kind == 'name': value = builder.load(builder.gep(arguments, [ir.Constant(i32, item.value)]))
            elif item.kind == 'unary': value = builder.sub(ir.Constant(i64, 0), operands[0])
            elif item.value in ('<', '<=', '>', '>='): value = builder.zext(builder.icmp_signed(item.value, *operands), i64)
            else: value = {'+': builder.add, '-': builder.sub, '*': builder.mul}[item.value](*operands)
            emitted[index] = value
        value = emitted[plan.result]
        builder.store(value, output)
        builder.ret(ir.Constant(i8, 1))
        builder.position_at_end(abort)
        builder.ret(ir.Constant(i8, 0))
        artifact = compile_generated_module(
            llvm, module, machine, 'chaos_kernel',
            'chaos.scalar/i64ptr-i64ptr-checkpoint-i32-u8/1', version, target,
            code_cache=code_cache)
        # Logical wrappers never share optimization diagnostics or callbacks.
        kernel = NativeKernel(artifact.engine, artifact.module, artifact.machine,
                            artifact.address, artifact.version, artifact.target, plan)
        kernel._artifact = artifact
        return kernel
