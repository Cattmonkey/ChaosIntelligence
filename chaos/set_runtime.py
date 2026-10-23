"""One native Set, with portable pure storage or an owned protocol table.

All user hashing/equality uses the ordinary synchronous invocation pipeline.
Existing insertion hashes survive immutable updates; there is no implicit rehash.
"""
from dataclasses import dataclass
from .set_values import SetValue, SetBuilder


def owned(value,evaluator):
    from .language import LanguageError
    if type(value) is not SetValue:raise LanguageError('Expected native Set',code='argument_error')
    if value.owner is not None and value.owner is not evaluator:
        raise LanguageError('Protocol Set cannot cross evaluators',code='ownership_error')
    return value


def portable(value,tick=None):
    """Exact storage classification, never an exception-driven protocol probe."""
    from .tuple_values import TupleValue
    from .numeric_values import Fraction,Complex,Decimal
    from .language import ResourceLimit,LanguageError
    from .objects import ChaosInstance
    pending=[(value,0)];seen=set()
    while pending:
        item,depth=pending.pop();kind=type(item)
        if kind in (type(None),bool,int,float,str,Fraction,Complex,Decimal):continue
        if kind is SetValue:
            if item.owner is not None:return False
            continue
        if kind is ChaosInstance:return False
        if kind is not TupleValue:raise LanguageError('Set keys require immutable native values or Instance hash protocols',code='argument_error')
        if tick:tick()
        if depth>100 or len(seen)>20000:raise ResourceLimit('Set key classification exceeds resource limits')
        if id(item) in seen:continue
        seen.add(id(item));pending.extend((child,depth+1) for child in item)
    return True


def account(value,budget):
    """Share the original pure-member resource budget after promotion too."""
    from .tuple_values import TupleValue
    from .objects import ChaosInstance
    pending=[(value,0)];seen=set()
    while pending:
        item,depth=pending.pop()
        if type(item) is ChaosInstance:budget.step();continue
        if portable(item,budget.checkpoint):budget.key(item,depth);continue
        if id(item) in seen:continue
        seen.add(id(item));budget.step()
        if type(item) in (TupleValue,SetValue):pending.extend((child,depth+1) for child in item)


def project(evaluator,value,*sources):
    from .agent_tools import current_caps
    from .hash_maps import intersection
    owned(value,evaluator)
    if value.owner is None:return value
    ceiling=intersection(current_caps(evaluator),value.capabilities,*(getattr(s,'capabilities',None) for s in sources))
    if ceiling==value.capabilities:return value
    from dataclasses import replace
    return SetValue._owned(replace(value._mapping,capabilities=ceiling))


def items(evaluator,value):
    from .hash_maps import items as map_items
    owned(value,evaluator)
    return list(value.items) if value.owner is None else map_items(evaluator,value._mapping,'keys')


@dataclass(frozen=True)
class Request:
    operation:str
    value:object
    other:object=None


class Builder:
    """Private mutable draft; items is one permanent explicit capture root."""
    def __init__(self,evaluator,limit=10000):
        from .agent_tools import current_caps
        self.owner=evaluator;self.capabilities=current_caps(evaluator)
        self.limit=limit;self.attempts=0;self.items=[]
        self.pure=SetBuilder(evaluator.tick,limit=limit);self.table=None

    def add(self,value):
        from .execution_hash_collections import hash_collection_protocol
        return (yield from hash_collection_protocol(self.owner, 'set_add', self, value))

    def finish(self):
        from .hash_maps import make
        return self.pure.finish() if self.table is None else freeze(self.owner,make(self.owner,self.table.entries,self.capabilities))


def mapping(evaluator,value):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'mapping', value))


def freeze(evaluator,table):
    from .class_creation import validate_value
    value=SetValue._owned(table)
    validate_value(evaluator,value)
    return value


def contains(evaluator,value,item):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'contains', value, item))


def algebra(evaluator,operation,left,right):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'algebra', operation, left, right))


def update(evaluator,operation,value,item):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'update', operation, value, item))


def resolve(evaluator,request):
    from .execution_hash_collections import hash_collection_protocol
    return (yield from hash_collection_protocol(evaluator, 'set_resolve', request))
