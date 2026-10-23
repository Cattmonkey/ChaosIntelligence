"""Bounded process-local reuse of private raw syntax; never execution state.

Checkpoint-bearing compilation takes the original parser path. Cached syntax is
only an incremental front-end artifact: linking, validation, macro expansion and
module initialization still run for every ProgramBundle.
"""
from collections import OrderedDict
import hashlib
import json
import threading

from .language import Node, Parser, ResourceLimit

COMPILER_ABI = 'chaos.frontend/1'
_HARD_LIMITS = (256, 4000000, 400000)


def unit_identity(source, filename, language_version):
    """Portable content identity, independent of process cache statistics."""
    source_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
    encoded = json.dumps([COMPILER_ABI, filename, language_version, source_hash],
                         ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    return {'sourceHash': source_hash, 'unitId': hashlib.sha256(encoded).hexdigest()}


def _parse(source, filename, language_version, checkpoint=None):
    try:
        return Parser(source, filename, ranges=True, legacy_items=language_version == 1,
                      checkpoint=checkpoint).program()
    except RecursionError:
        raise ResourceLimit('源码语法嵌套过深') from None


def _weight(tree, limit):
    """Bound logical retention, counting spans, scalar sizes and containers.

    Parser output consists only of these native values. An unrecognized value or
    a cycle disables retention instead of introducing a new compilation failure.
    This measures logical units, not the interpreter's platform-specific bytes.
    """
    pending = [(tree, False)]
    active = set()
    weight = 0
    while pending:
        value, leaving = pending.pop()
        if leaving:
            active.remove(id(value))
            continue
        kind = type(value)
        weight += 1
        if kind is str:
            weight += len(value.encode('utf-8', errors='surrogatepass')) // 16
        elif kind is int:
            weight += value.bit_length() // 64
        elif kind in (bool, float, type(None)):
            pass
        elif kind in (Node, tuple, list, dict):
            identity = id(value)
            if identity in active:
                return None
            active.add(identity)
            pending.append((value, True))
            if kind is dict:
                for key, item in value.items():
                    pending.append((key, False)); pending.append((item, False))
            else:
                pending.extend((item, False) for item in value)
                if kind is Node:
                    pending.append((value.__dict__, False))
        else:
            return None
        if weight > limit:
            return None
    return weight


def _clone(value, memo=None):
    """Clone the bounded parser-only value algebra without generic host hooks."""
    kind = type(value)
    if kind in (str, int, float, bool, type(None)):
        return value
    if memo is None:
        memo = {}
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if kind is list:
        result = []
        memo[identity] = result
        result.extend(_clone(item, memo) for item in value)
    elif kind is dict:
        result = {}
        memo[identity] = result
        result.update((_clone(key, memo), _clone(item, memo)) for key, item in value.items())
    elif kind in (tuple, Node):
        children = tuple(_clone(item, memo) for item in value)
        result = Node(children) if kind is Node else children
        memo[identity] = result
        if kind is Node:
            result.__dict__.update(_clone(value.__dict__, memo))
    else:
        raise TypeError('编译缓存只接受解析器语法值')
    return result


class CompilationCache:
    """Thread-safe LRU of successful parse units, isolated from public ASTs.

    Limits are bounded configuration, not language execution budgets. Oversized
    units compile normally without being retained. Source byte accounting counts
    the exact source retained in each key; AST accounting uses logical units.
    """
    def __init__(self, *, max_entries=128, max_source_bytes=2000000, max_ast_units=200000):
        configured = (max_entries, max_source_bytes, max_ast_units)
        for value, maximum in zip(configured, _HARD_LIMITS):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError('编译缓存限制需要硬上限内的非负整数')
        self._limits = configured
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._generation = 0
        self._source_bytes = self._ast_units = 0
        self._hits = self._misses = self._evictions = self._oversize = 0

    @property
    def max_entries(self): return self._limits[0]

    @property
    def max_source_bytes(self): return self._limits[1]

    @property
    def max_ast_units(self): return self._limits[2]

    def clear(self):
        """Discard retained syntax and counters, including in-flight publication."""
        with self._lock:
            self._entries.clear()
            self._generation += 1
            self._source_bytes = self._ast_units = 0
            self._hits = self._misses = self._evictions = self._oversize = 0

    def info(self):
        """Return counters and limits only; never source or mutable syntax."""
        with self._lock:
            return {'entries': len(self._entries), 'sourceBytes': self._source_bytes,
                    'astUnits': self._ast_units, 'hits': self._hits, 'misses': self._misses,
                    'evictions': self._evictions, 'oversize': self._oversize,
                    'limits': {'maxEntries': self.max_entries,
                               'maxSourceBytes': self.max_source_bytes,
                               'maxAstUnits': self.max_ast_units}}

    def parse(self, source, filename, language_version):
        """Return (isolated raw tree, status); never called with a checkpoint."""
        # Keep the exact source in the key: a digest collision cannot reuse a
        # different program. Filename and dialect are source-span semantics.
        key = (COMPILER_ABI, filename, language_version, source)
        with self._lock:
            entry = self._entries.get(key)
            generation = self._generation
            if entry is not None:
                self._entries.move_to_end(key)
                self._hits += 1
            else:
                self._misses += 1
        if entry is not None:
            try:
                return _clone(entry[0]), 'hit'
            except RecursionError:
                # A deeper host caller may have less recursion headroom than
                # the caller that populated this unit. Fall back to parsing.
                with self._lock:
                    if self._entries.get(key) is entry:
                        del self._entries[key]
                        self._source_bytes -= entry[1]
                        self._ast_units -= entry[2]
                        self._evictions += 1
                        self._oversize += 1
                return _parse(source, filename, language_version), 'oversize'

        tree = _parse(source, filename, language_version)
        size = len(source.encode('utf-8'))
        weight = (_weight(tree, self.max_ast_units) if self.max_entries and
                  size <= self.max_source_bytes and self.max_ast_units else None)
        retained = None
        if weight is not None:
            try:
                retained = _clone(tree)
            except RecursionError:
                # Parsing's valid recursion envelope need not match cloning's.
                # Cache eligibility must not reject an otherwise valid program.
                pass
        if retained is None:
            with self._lock:
                if generation == self._generation:
                    self._oversize += 1
            return tree, 'oversize'
        with self._lock:
            if generation != self._generation:
                return tree, 'miss'
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._source_bytes -= previous[1]
                self._ast_units -= previous[2]
            while self._entries and (len(self._entries) >= self.max_entries or
                    self._source_bytes + size > self.max_source_bytes or
                    self._ast_units + weight > self.max_ast_units):
                _, removed = self._entries.popitem(last=False)
                self._source_bytes -= removed[1]
                self._ast_units -= removed[2]
                self._evictions += 1
            self._entries[key] = (retained, size, weight)
            self._source_bytes += size
            self._ast_units += weight
        return tree, 'miss'


DEFAULT_COMPILER_CACHE = CompilationCache()


def parse_unit(source, filename, language_version, *, compiler_cache=DEFAULT_COMPILER_CACHE,
               checkpoint=None):
    """Central front-end hook; callbacks retain the exact uncached parser path."""
    identity = unit_identity(source, filename, language_version)
    if checkpoint is not None:
        tree, status = _parse(source, filename, language_version, checkpoint), 'checkpoint'
    elif compiler_cache is None:
        tree, status = _parse(source, filename, language_version), 'disabled'
    else:
        if not isinstance(compiler_cache, CompilationCache):
            raise TypeError('compiler_cache 必须是 CompilationCache 或 None')
        tree, status = compiler_cache.parse(source, filename, language_version)
    return tree, dict(identity, cacheStatus=status)
