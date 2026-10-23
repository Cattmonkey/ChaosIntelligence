"""Owned persistent general-key maps on the original language protocol stack.

User hashing/equality is explicit, synchronous and cooperative. Buckets store
insertion-time hashes: a key's hash/equality stability is the program's contract.
Native values never fall through to host object hashing or host value equality.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType
import weakref

MAX_ENTRIES = 10000


def error(message, code='argument_error', value=None):
    from .language import LanguageError
    raise LanguageError(message, code=code, value=value)


def intersection(*ceilings):
    result = None
    for ceiling in ceilings:
        if ceiling is not None: result = ceiling if result is None else result & ceiling
    return result


@dataclass(frozen=True, eq=False)
class HashMap:
    owner: object
    entries: tuple
    buckets: object
    capabilities: object = None

    def __len__(self): return len(self.entries)


@dataclass(frozen=True)
class Request:
    operation: str
    mapping: object = None
    key: object = None
    value: object = None


def owned(value, evaluator):
    if type(value) is not HashMap:
        error('Expected a native HashMap')
    if value.owner is not evaluator:
        error('HashMap cannot cross evaluators', 'ownership_error')
    return value


def project(evaluator, value, *sources):
    from .agent_tools import current_caps
    owned(value, evaluator)
    ceiling = intersection(current_caps(evaluator), value.capabilities,
                           *(getattr(source, 'capabilities', None) for source in sources))
    return value if ceiling == value.capabilities else replace(value, capabilities=ceiling)


def children(value):
    """Shared pure traversal bridge for existing ownership/budget validators."""
    return tuple(child for key, item, fingerprint in value.entries for child in (key, item))


def describe(value):
    # Deliberately opaque. hash_items is the explicit live-value/data projection.
    return {'$hashMap': {'size': len(value), 'ordered': True, 'persistent': True}}


def info(evaluator, mapping):
    owned(mapping, evaluator)
    return {'size': len(mapping), 'ordered': True, 'persistent': True,
            'maxEntries': MAX_ENTRIES, 'keyPolicy': 'native-hash-equality'}


def make(evaluator, entries=(), capabilities=None):
    from .agent_tools import current_caps
    buckets = {}
    for index, (key, value, fingerprint) in enumerate(entries):
        evaluator.tick()
        buckets.setdefault(fingerprint, []).append(index)
    return HashMap(evaluator, tuple(entries),
                   MappingProxyType({fingerprint: tuple(indices) for fingerprint, indices in buckets.items()}),
                   intersection(current_caps(evaluator), capabilities))


def project_key(evaluator, key, mapping):
    from .class_creation import project_value
    projected = project_value(evaluator, key, mapping)
    if not evaluator.equal(key, projected):
        error('A HashMap key cannot change identity during authority projection', 'capability_error')
    return key


def items(evaluator, mapping, kind='items'):
    from .class_creation import project_value
    from .tuple_values import TupleValue
    from .agent_tools import scope
    owned(mapping, evaluator)
    if kind not in ('items', 'keys', 'values'): error('Unknown HashMap projection')
    result = []
    with scope(evaluator, mapping.capabilities):
        for key, value, fingerprint in mapping.entries:
            evaluator.tick()
            if kind == 'keys': result.append(project_key(evaluator, key, mapping))
            elif kind == 'values': result.append(project_value(evaluator, value, mapping))
            else:
                result.append(TupleValue((project_key(evaluator, key, mapping),
                                          project_value(evaluator, value, mapping))))
    return result


def _identity(evaluator, value):
    """Opaque evaluator-local numbering; no memory address leaves the runtime."""
    table = getattr(evaluator, '_hash_identity_keys', None)
    if table is None:
        table = evaluator._hash_identity_keys = {}
        evaluator._hash_identity_sequence = 0
    identity = id(value)
    prior = table.get(identity)
    if prior is not None and prior[0]() is value: return prior[1]
    evaluator._hash_identity_sequence += 1
    number = evaluator._hash_identity_sequence
    def release(reference):
        current = table.get(identity)
        if current is not None and current[0] is reference: table.pop(identity, None)
    table[identity] = (weakref.ref(value, release), number)
    return number


def _encoded(number):
    return (b'-' if number < 0 else b'+') + abs(number).to_bytes((number.bit_length()+7)//8, 'big')


class _HashBudget:
    def __init__(self, evaluator):
        from .set_values import _Budget
        self.evaluator = evaluator
        self.pure = _Budget(evaluator.tick)
        self.nodes, self.active, self.memo = 0, set(), {}

    def step(self):
        from .language import ResourceLimit
        self.evaluator.tick(); self.nodes += 1
        if self.nodes > 20000: raise ResourceLimit('Hash key exceeds 20000 nodes')

    def hash(self, value, depth=0):
        from .execution_hash_collections import hash_collection_protocol
        return (yield from hash_collection_protocol(self.evaluator, 'hash_key', self, value, depth))

    def instance(self, value):
        from .execution_hash_collections import hash_collection_protocol
        return (yield from hash_collection_protocol(self.evaluator, 'instance_hash', self, value))


def hash_value(evaluator, value):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'hash_value', value))


def _find(evaluator, mapping, key, fingerprint):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'find', mapping, key, fingerprint))


def _store(evaluator, mapping, key, value):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'store', mapping, key, value))


class _Builder:
    """Private construction draft; only an immutable snapshot can escape."""
    def __init__(self, evaluator):
        from .agent_tools import current_caps
        self.owner = evaluator
        self.entries, self.buckets = [], {}
        self.capabilities = current_caps(evaluator)

    def add(self, evaluator, key, value):
        from .execution_hash_collections import hash_collection_protocol
        return (yield from hash_collection_protocol(evaluator, 'map_add', self, key, value))


def construct(evaluator, entries):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'construct', entries))


def equal(evaluator, left, right):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'equal', left, right))


def resolve(evaluator, request):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'map_resolve', request))


def builtins(evaluator):
    return {
        'hash': lambda value: Request('hash', key=value),
        'hash_map': lambda entries=[]: Request('create', value=entries),
        'hash_get': lambda mapping, key, default=None: Request('get', mapping, key, default),
        'hash_set': lambda mapping, key, value: Request('set', mapping, key, value),
        'hash_remove': lambda mapping, key: Request('remove', mapping, key),
        'hash_has': lambda mapping, key: Request('has', mapping, key),
        'hash_items': lambda mapping: items(evaluator, mapping),
        'hash_keys': lambda mapping: items(evaluator, mapping, 'keys'),
        'hash_values': lambda mapping: items(evaluator, mapping, 'values'),
        'hash_info': lambda mapping: info(evaluator, mapping),
    }
