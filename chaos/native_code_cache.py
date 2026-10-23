"""Bounded reuse of successful code emitted by the two trusted LLVM builders.

There is no language, CLI or HTTP entry point for LLVM text or symbols. Callers
still validate/prove their current program and generate the complete module on
every logical compilation. Only engine/module/machine/address lifetime is
shared; Function, Env, AST, permissions, callbacks and invocation state are not.
"""
from collections import OrderedDict
from dataclasses import dataclass
import ctypes
import platform
import sys

CODEGEN_ABI = 'chaos.native-object/1'
# Exact existing trusted-builder defaults, pinned rather than host CPU guesses.
MACHINE_OPTIONS = ('cpu=', 'features=', 'opt=2', 'reloc=default',
    'codemodel=jitdefault', 'printmc=False', 'jit=False', 'abiname=')


def _lock():
    # Import lazily: native_jit owns the existing LLVM lock and imports this
    # module without loading the optional backend. No second lock is introduced.
    from .native_jit import _LOCK
    return _LOCK


@dataclass(frozen=True, eq=False)
class CodeArtifact:
    engine: object
    module: object
    machine: object
    address: int
    version: str
    target: str
    object_file: object = None
    object_bytes: object = None


class NativeCodeCache:
    """Process-local successful-artifact LRU, bounded by entries and IR bytes.

    All operations share native_jit._LOCK. Eviction/clear only release this
    cache's strong references; already returned wrappers retain their engines.
    A reentrant clear during a build prevents its subsequent publication.
    """
    def __init__(self, *, max_entries=32, max_ir_bytes=2000000, object_cache=None):
        for value, maximum in ((max_entries, 64), (max_ir_bytes, 4000000)):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError('原生代码缓存限制需要硬上限内的非负整数')
        if object_cache is not None:
            from .native_objects import NativeObjectCache
            if type(object_cache) is not NativeObjectCache:
                raise TypeError('object_cache requires an explicit NativeObjectCache')
        self._object_cache = object_cache
        self._limits = (max_entries, max_ir_bytes)
        self._entries = OrderedDict()
        self._ir_bytes = self._generation = 0
        self._hits = self._misses = self._builds = self._evictions = self._oversize = 0

    @property
    def object_cache(self): return self._object_cache

    @property
    def max_entries(self): return self._limits[0]

    @property
    def max_ir_bytes(self): return self._limits[1]

    def info(self):
        """Return aggregate counters only, never IR, addresses or live objects."""
        with _lock():
            return {'schema': 'chaos.native-code-cache/1',
                    'entries': len(self._entries), 'irBytes': self._ir_bytes,
                    'hits': self._hits, 'misses': self._misses,
                    'builds': self._builds, 'evictions': self._evictions,
                    'oversize': self._oversize, 'generation': self._generation,
                    'limits': {'maxEntries': self.max_entries,
                               'maxIRBytes': self.max_ir_bytes}}

    def clear(self):
        with _lock():
            self._entries.clear()
            self._generation += 1
            self._ir_bytes = 0
            self._hits = self._misses = self._builds = self._evictions = self._oversize = 0

    def _get_or_build(self, key, ir_bytes, build):
        # This private boundary accepts only closed native metadata keys. The
        # trusted helper below computes exact byte weight from generated IR.
        if type(key) is not tuple or any(type(part) is not str for part in key):
            raise TypeError('原生代码缓存键需要不可变字符串元组')
        if type(ir_bytes) is not int or ir_bytes < 0:
            raise TypeError('原生代码缓存 IR 大小需要非负整数')
        with _lock():
            found = self._entries.get(key)
            if found is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return found[0]
            self._misses += 1
            generation = self._generation
            # Publication occurs only after parse/verify/finalize/address all
            # succeed. A failure remains a miss and cannot poison future calls.
            artifact = build()
            if type(artifact) is not CodeArtifact:
                raise TypeError('原生代码缓存仅接受已成功构建的 CodeArtifact')
            if generation != self._generation:
                return artifact
            self._builds += 1
            if not self.max_entries or not self.max_ir_bytes or ir_bytes > self.max_ir_bytes:
                self._oversize += 1
                return artifact
            while self._entries and (len(self._entries) >= self.max_entries or
                    self._ir_bytes + ir_bytes > self.max_ir_bytes):
                _, removed = self._entries.popitem(last=False)
                self._ir_bytes -= removed[1]
                self._evictions += 1
            self._entries[key] = (artifact, ir_bytes)
            self._ir_bytes += ir_bytes
            return artifact


DEFAULT_NATIVE_CODE_CACHE = NativeCodeCache()


def compile_generated_module(llvm, module, machine, symbol, calling_abi,
                             version, target, *, code_cache=DEFAULT_NATIVE_CODE_CACHE):
    """Finalize one module from an existing trusted builder, never source LLVM.

    Backend availability must already have been checked by that builder on every
    call, including warm hits. The complete generated text includes layout,
    instruction flags, checkpoint calls and parameter/function signatures.
    CODEGEN_ABI also identifies the fixed default target-machine options.
    """
    if type(module) is str:
        raise TypeError('需要内部生成的 LLVM Module，不能传入 LLVM 源码')
    if code_cache is not None and type(code_cache) is not NativeCodeCache:
        raise TypeError('code_cache 必须为 NativeCodeCache 或 None')
    if any(type(value) is not str or not value or len(value) > 4096
           for value in (symbol, calling_abi, version, target)):
        raise TypeError('原生代码制品元数据需要有界字符串')
    with _lock():
        text = str(module)
        layout = str(module.data_layout)
        backend_version = llvm.llvm_version_info
        if (type(backend_version) is not tuple or not backend_version or
                any(type(value) is not int or value < 0 for value in backend_version)):
            raise TypeError('LLVM 后端版本需要非负整数元组')
        backend_version = '.'.join(str(value) for value in backend_version)
        host = (platform.system(), platform.release(), platform.machine(),
                sys.byteorder, str(ctypes.sizeof(ctypes.c_void_p) * 8))
        key = (CODEGEN_ABI, text, symbol, calling_abi, version,
               backend_version, target, layout) + MACHINE_OPTIONS + host

        def build():
            object_cache = code_cache.object_cache if code_cache is not None else None
            if object_cache is not None:
                def link(data):
                    # MCJIT needs an empty container module. Cached IR is neither
                    # parsed nor compiled on a disk hit; authenticated .o links.
                    empty = llvm.parse_assembly('')
                    empty.triple = target; empty.data_layout = layout
                    engine = llvm.create_mcjit_compiler(empty, machine)
                    object_file = llvm.ObjectFileRef.from_data(data)
                    engine.add_object_file(object_file)
                    engine.finalize_object()
                    address = engine.get_function_address(symbol)
                    if not address:
                        from .native_jit import BackendUnavailable
                        raise BackendUnavailable('symbol_unavailable')
                    return CodeArtifact(engine, empty, machine, address, version, target, object_file, data)
                built = []
                def emit():
                    compiled = llvm.parse_assembly(text)
                    compiled.verify()
                    data = machine.emit_object(compiled)
                    if type(data) is not bytes or not data:
                        raise RuntimeError('Native emitter returned no object bytes')
                    # Publish only after the newly generated object actually
                    # linked and resolved; a failed build cannot poison disk.
                    built.append(link(data))
                    return data
                data = object_cache._get_or_emit(key, emit)
                return built[0] if built else link(data)
            compiled = llvm.parse_assembly(text)
            compiled.verify()
            engine = llvm.create_mcjit_compiler(compiled, machine)
            engine.finalize_object()
            address = engine.get_function_address(symbol)
            if not address:
                from .native_jit import BackendUnavailable
                raise BackendUnavailable('symbol_unavailable')
            return CodeArtifact(engine, compiled, machine, address, version, target)

        return build() if code_cache is None else code_cache._get_or_build(
            key, len(text.encode('utf-8')), build)
