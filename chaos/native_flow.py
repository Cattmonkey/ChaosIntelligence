"""Trusted CFG machine: typed rooted packets return before host semantics."""
import ctypes
from contextvars import ContextVar
from .native_jit import _LOCK, _backend, BackendUnavailable
from .native_code_cache import DEFAULT_NATIVE_CODE_CACHE, compile_generated_module
from .flow_abi import ABI_VERSION, NATIVE_ABI, EventPacket, EventTable, kind_code
from .native_values import _bad

_U64 = ctypes.c_uint64
_STEP = ctypes.CFUNCTYPE(ctypes.c_int32,
    ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint8),
    ctypes.POINTER(_U64), _U64, ctypes.POINTER(_U64))
# Kernels/artifacts may be shared by threads and nested invocations. The scoped
# packet sink belongs to this one resume_event call, never to the shared kernel.
_ACTIVE_RESUME = ContextVar('chaos_native_flow_resume', default=None)


class FlowKernel:
    def __init__(self, engine, module, machine, address, version, target,
                 instruction_count, flag_count, event_kinds):
        self._engine, self._module, self._machine = engine, module, machine
        self._call = _STEP(address)
        self.version, self.target = version, target
        self._instruction_count, self._flag_count = instruction_count, flag_count
        self._event_kinds = event_kinds
        self._compat_handles = (_U64 * instruction_count)()

    def resume(self, pc, flags):
        """Preserve the original two-argument low-level tuple contract/hooks."""
        if (type(pc) is not int or not 0 <= pc < self._instruction_count
                or type(flags) is not list or len(flags) != self._flag_count
                or any(type(v) is not int or v not in (0, 1) for v in flags)):
            raise ValueError('invalid native continuation state')
        active = _ACTIVE_RESUME.get()
        if active is not None and active[0] is self:
            _, table, sink = active
            handles, domain = table.native_handles, table.domain.id
        else:
            table, sink = None, None
            handles, domain = self._compat_handles, 0
        position = ctypes.c_int32(pc)
        values = (ctypes.c_uint8 * max(1, len(flags)))(*flags)
        words = (_U64 * 6)()
        event = self._call(ctypes.byref(position), values, handles, domain, words)
        flags[:] = list(values)[:len(flags)]
        if event < 0:
            raise RuntimeError('invalid native continuation PC')
        packet = EventPacket(int(words[0]), int(words[1]), int(words[2]),
                            int(words[3]) - 1, int(words[4]), int(words[5]))
        if (packet.abi != ABI_VERSION or packet.domain != domain
                or packet.next_pc != position.value
                or packet.site + 1 != event
                or not 0 <= packet.next_pc < self._instruction_count):
            _bad()
        if event == 0:
            if packet.kind != 0 or packet.token != 0:
                _bad()
        elif (not 0 < event <= self._instruction_count
                or packet.kind != self._event_kinds[event - 1]
                or not packet.kind):
            _bad()
        if sink is not None:
            sink.append(packet)
        return position.value, event

    def resume_event(self, pc, flags, table):
        """Canonical dispatch: the machine selects the actual rooted payload.

        Calling the old resume method here deliberately preserves its existing
        branch-observation hooks. No packet can be synthesized from its returned
        event index if the real rooted native call did not produce exactly one.
        """
        if type(table) is not EventTable:
            _bad()
        table.validate()
        if len(table.instructions) != self._instruction_count:
            _bad()
        sink = []
        binding = _ACTIVE_RESUME.set((self, table, sink))
        try:
            answer = self.resume(pc, flags)
        finally:
            _ACTIVE_RESUME.reset(binding)
        if (type(answer) is not tuple or len(answer) != 2
                or any(type(value) is not int for value in answer)
                or len(sink) != 1):
            _bad()
        packet = sink[0]
        if answer != (packet.next_pc, packet.site + 1):
            _bad()
        return packet


def compile_flow(program, *, code_cache=DEFAULT_NATIVE_CODE_CACHE):
    with _LOCK:
        llvm, ir, version = _backend()
        target = llvm.get_default_triple()
        machine = llvm.Target.from_default_triple().create_target_machine(
            cpu='', features='', opt=2, reloc='default', codemodel='jitdefault',
            printmc=False, jit=False, abiname='')
        module = ir.Module(name='chaos_resumable_flow')
        module.triple = target
        module.data_layout = str(machine.target_data)
        i32, i8, i64 = ir.IntType(32), ir.IntType(8), ir.IntType(64)
        fn = ir.Function(module, ir.FunctionType(i32, [i32.as_pointer(),
            i8.as_pointer(), i64.as_pointer(), i64, i64.as_pointer()]),
            name='chaos_flow_step')
        pc, flags, handles, domain, packet = fn.args
        entry = fn.append_basic_block('resume')
        invalid = fn.append_basic_block('invalid')
        blocks = [fn.append_basic_block('pc_' + str(i))
                  for i in range(len(program.instructions))]
        b = ir.IRBuilder(entry)
        switch = b.switch(b.load(pc), invalid)
        for i, block in enumerate(blocks):
            switch.add_case(ir.Constant(i32, i), block)
        kinds = []
        for item in program.instructions:
            kinds.append(0 if item[0] in ('jump', 'branch', 'flag', 'done')
                         else kind_code(item[0]))
        for i, item in enumerate(program.instructions):
            b = ir.IRBuilder(blocks[i])
            kind = item[0]
            if kind == 'jump':
                b.branch(blocks[item[1]])
            elif kind == 'branch':
                flag = b.load(b.gep(flags, [ir.Constant(i32, item[1])]))
                b.cbranch(b.icmp_unsigned('!=', flag, ir.Constant(i8, 0)),
                          blocks[item[2]], blocks[item[3]])
            elif kind == 'flag':
                b.store(ir.Constant(i8, item[2]),
                        b.gep(flags, [ir.Constant(i32, item[1])]))
                b.branch(blocks[i + 1])
            else:
                event = 0 if kind == 'done' else i + 1
                next_pc = i if kind == 'done' else i + 1
                # Only integer root tokens cross this boundary. The selected
                # actual row/AST/value is resolved by the invocation's owner.
                token = (ir.Constant(i64, 0) if kind == 'done' else
                         b.load(b.gep(handles, [ir.Constant(i32, i)])))
                fields = (ir.Constant(i64, ABI_VERSION), domain,
                          ir.Constant(i64, kinds[i]), ir.Constant(i64, event),
                          token, ir.Constant(i64, next_pc))
                for index, value in enumerate(fields):
                    b.store(value, b.gep(packet, [ir.Constant(i32, index)]))
                b.store(ir.Constant(i32, next_pc), pc)
                b.ret(ir.Constant(i32, event))
        b = ir.IRBuilder(invalid)
        b.ret(ir.Constant(i32, -1))
        artifact = compile_generated_module(llvm, module, machine,
            'chaos_flow_step', NATIVE_ABI, version, target, code_cache=code_cache)
        kernel = FlowKernel(artifact.engine, artifact.module, artifact.machine,
            artifact.address, artifact.version, artifact.target,
            len(program.instructions), program.flag_count, tuple(kinds))
        kernel._artifact = artifact
        return kernel
