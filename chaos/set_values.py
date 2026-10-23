"""Immutable native sets with bounded pure keys, never host/user value hashing."""
from dataclasses import dataclass
from types import MappingProxyType
import hashlib
import math
from .numeric_values import NUMERIC_VALUES


def _error(message):
    from .language import LanguageError
    raise LanguageError(message, code='argument_error')


def _limit(message):
    from .language import ResourceLimit
    raise ResourceLimit(message)


def _fingerprint(tag, parts):
    digest = hashlib.sha256(tag.encode('ascii'))
    for part in parts:
        digest.update(len(part).to_bytes(4, 'big')); digest.update(part)
    return digest.digest()


@dataclass(frozen=True, eq=False)
class _Key:
    kind: str
    payload: object
    fingerprint: bytes
    height: int = 0


class _Budget:
    def __init__(self, tick=None):
        self.tick, self.nodes, self.chars = tick, 0, 0
        self.memo, self.active, self.comparisons = {}, set(), {}

    def checkpoint(self):
        if self.tick: self.tick()

    def step(self):
        self.checkpoint()
        self.nodes += 1
        if self.nodes > 20000: _limit('Set 键操作超过 20000 节点')

    def key(self, value, depth=0):
        from .tuple_values import TupleValue
        self.step()
        if depth > 100: _limit('Set 键深度超过 100')
        kind, identity = type(value), id(value)
        if identity in self.active: _error('Set 键不能包含循环')
        if identity in self.memo:
            result = self.memo[identity][1]
            if depth + result.height > 100: _limit('Set 键深度超过 100')
            return result
        if value is None: tag, payload, parts = 'null', None, ()
        elif kind is bool: tag, payload, parts = 'bool', value, (b'1' if value else b'0',)
        elif kind is int:
            if value.bit_length() > 14000: _limit('Set 整数超过 14000 bit')
            tag, payload = 'int', value
            parts = (b'-' if value < 0 else b'+', abs(value).to_bytes((value.bit_length()+7)//8, 'big'))
        elif kind is float:
            if not math.isfinite(value): _error('Set 需要有限 Number')
            tag, payload = 'float', value
            parts = ((0.0 if value == 0 else value).hex().encode('ascii'),)
        elif kind in NUMERIC_VALUES:
            from .numeric_values import Fraction,Complex,Decimal,validate
            validate(value,self.checkpoint)
            from .decimal_values import key as decimal_key
            values=decimal_key(value) if kind is Decimal else (value.numerator,value.denominator) if kind is Fraction else (value.real,value.imag)
            children=tuple(self.key(x,depth+1) for x in values)
            tag,payload=('decimal' if kind is Decimal else 'fraction' if kind is Fraction else 'complex'),children
            parts=tuple(x.fingerprint for x in children)
        elif kind is str:
            self.chars += len(value)
            if self.chars > 250000: _limit('Set 字符串总量超过 250000')
            try: encoded = value.encode('utf-8')
            except UnicodeEncodeError: _error('Set String 需要有效 Unicode')
            tag, payload, parts = 'str', value, (encoded,)
        elif kind in (TupleValue, SetValue):
            if kind is SetValue and value.owner is not None:_error('Protocol Set is not a portable pure key')
            if len(value) > 10000: _limit('Set 键容器超过 10000 项')
            self.active.add(identity)
            try: children = tuple(self.key(child, depth+1) for child in value)
            finally: self.active.remove(identity)
            tag, payload = ('tuple' if kind is TupleValue else 'set'), children
            parts = tuple(child.fingerprint for child in children)
            if kind is SetValue: parts = tuple(sorted(parts))
        else: _error('Set 元素需要原生标量或递归不可变 Tuple/Set')
        height = 1 + max((child.height for child in payload), default=-1) if tag in ('tuple','set','fraction','complex','decimal') else 0
        result = _Key(tag, payload, _fingerprint(tag, parts), height)
        self.memo[identity] = (value, result)
        return result

    def equal(self, left, right):
        self.step()
        if left is right: return True
        pair = (id(left), id(right))
        if pair in self.comparisons: return self.comparisons[pair]
        if left.kind != right.kind or left.fingerprint != right.fingerprint: return False
        if left.kind in ('tuple','fraction','complex','decimal'):
            result = len(left.payload) == len(right.payload) and all(self.equal(a,b) for a,b in zip(left.payload,right.payload))
        elif left.kind == 'set':
            result = len(left.payload) == len(right.payload)
            if result:
                buckets = {}
                for key in right.payload:
                    self.step(); buckets.setdefault(key.fingerprint, []).append(key)
                for key in left.payload:
                    if not any(self.equal(key, other) for other in buckets.get(key.fingerprint, ())):
                        result = False; break
        else: result = left.payload == right.payload
        self.comparisons[pair] = result
        return result


@dataclass(frozen=True, eq=False, init=False)
class SetValue:
    items: tuple
    _keys: tuple
    _buckets: object
    owner: object = None
    capabilities: object = None
    _mapping: object = None

    def __init__(self, items=()):
        if type(items) not in (list, tuple): _error('Set 内部构造需要原生 List/宿主 tuple')
        builder = SetBuilder()
        for item in items: builder.add(item)
        value = builder.finish()
        object.__setattr__(self, 'items', value.items)
        object.__setattr__(self, '_keys', value._keys)
        object.__setattr__(self, '_buckets', value._buckets)
        object.__setattr__(self, 'owner', None)
        object.__setattr__(self, 'capabilities', None)
        object.__setattr__(self, '_mapping', None)

    @classmethod
    def _make(cls, items, keys, buckets):
        result = object.__new__(cls)
        object.__setattr__(result, 'items', tuple(items))
        object.__setattr__(result, '_keys', tuple(keys))
        object.__setattr__(result, '_buckets', MappingProxyType({key:tuple(rows) for key,rows in buckets.items()}))
        object.__setattr__(result, 'owner', None)
        object.__setattr__(result, 'capabilities', None)
        object.__setattr__(result, '_mapping', None)
        return result

    @classmethod
    def _owned(cls,mapping):
        result=cls._make([row[0] for row in mapping.entries],(),{})
        object.__setattr__(result,'owner',mapping.owner)
        object.__setattr__(result,'capabilities',mapping.capabilities)
        object.__setattr__(result,'_mapping',mapping)
        return result

    def __len__(self): return len(self.items)
    def __iter__(self): return iter(self.items)


class SetBuilder:
    def __init__(self, tick=None, limit=10000):
        self.budget, self.limit, self.attempts = _Budget(tick), limit, 0
        self.items, self.keys, self.buckets = [], [], {}

    def add(self, value):
        if self.attempts >= self.limit: _limit('Set 输入超过 '+str(self.limit)+' 项')
        self.attempts += 1
        key = self.budget.key(value)
        for previous in self.buckets.get(key.fingerprint, ()):
            if self.budget.equal(key, previous): return
        if len(self.items) >= 10000: _limit('Set 超过 10000 个元素')
        self.items.append(value); self.keys.append(key)
        self.buckets.setdefault(key.fingerprint, []).append(key)

    def finish(self): return SetValue._make(self.items, self.keys, self.buckets)


def _require(value):
    if type(value) is not SetValue: _error('需要原生 Set')
    return value


def _contains(value, key, budget):
    if value.owner is not None:_error('Protocol Set operations require the active evaluator')
    return any(budget.equal(key, candidate) for candidate in value._buckets.get(key.fingerprint, ()))


def set_contains(value, item, tick=None):
    _require(value); budget = _Budget(tick)
    return _contains(value, budget.key(item), budget)


def set_equal(left, right, tick=None):
    _require(left); _require(right); budget = _Budget(tick)
    if left.owner is not None or right.owner is not None:return left is right
    if len(left) != len(right): return False
    for key in left._keys:
        budget.step()
        if not _contains(right, key, budget): return False
    return True


def set_binary(operation, left, right, tick=None):
    _require(left); _require(right); budget = _Budget(tick)
    if left.owner is not None or right.owner is not None:_error('Protocol Set operations require the active evaluator')
    if operation in ('<','<=','>','>='):
        small, large = (right,left) if operation.startswith('>') else (left,right)
        if len(small) > len(large) or operation in ('<','>') and len(small) == len(large): return False
        for key in small._keys:
            budget.step()
            if not _contains(large,key,budget): return False
        return True
    if operation not in ('|','&','-','^'): _error('Set 不支持该运算')
    items, keys, buckets = [], [], {}
    def append(value, key):
        budget.checkpoint()
        if len(items) >= 10000: _limit('Set 超过 10000 个元素')
        items.append(value);keys.append(key);buckets.setdefault(key.fingerprint,[]).append(key)
    for item,key in zip(left.items,left._keys):
        present = _contains(right,key,budget) if operation != '|' else False
        if operation == '|' or (present if operation == '&' else not present): append(item,key)
    if operation in ('|','^'):
        for item,key in zip(right.items,right._keys):
            budget.checkpoint()
            if not _contains(left,key,budget): append(item,key)
    for item in items: budget.key(item)
    return SetValue._make(items,keys,buckets)


def set_add(value, item, tick=None):
    _require(value)
    if value.owner is not None:_error('Protocol Set operations require the active evaluator')
    builder = SetBuilder(tick, limit=10001)
    for previous in value: builder.add(previous)
    builder.add(item);return builder.finish()


def set_discard(value, item, tick=None):
    _require(value)
    if value.owner is not None:_error('Protocol Set operations require the active evaluator')
    budget = _Budget(tick); key = budget.key(item)
    items, keys, buckets = [], [], {}
    for previous, existing in zip(value.items,value._keys):
        budget.checkpoint()
        if existing.fingerprint == key.fingerprint and budget.equal(existing,key): continue
        items.append(previous);keys.append(existing);buckets.setdefault(existing.fingerprint,[]).append(existing)
    for item in items: budget.key(item)
    return SetValue._make(items,keys,buckets)


def set_is_disjoint(left, right, tick=None):
    _require(left);_require(right);budget=_Budget(tick)
    if left.owner is not None or right.owner is not None:_error('Protocol Set operations require the active evaluator')
    for key in left._keys:
        budget.step()
        if _contains(right,key,budget): return False
    return True
