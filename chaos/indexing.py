"""Owned item protocols and lossless integer indices; pure Slice values."""
from dataclasses import dataclass
from .tuple_values import TupleValue
from .set_values import SetValue

from .language import LanguageError, ResourceLimit, check_type, serializable


@dataclass(frozen=True)
class Slice:
    start: object = None
    stop: object = None
    step: object = None

    def __post_init__(self):
        for value in (self.start, self.stop, self.step):
            if value is not None:
                if type(value) is not int: raise LanguageError('Slice 端点需要 Int 或 null', code='argument_error')
                if value.bit_length() > 14000: raise ResourceLimit('Slice 整数超过 14000 bit')
        if self.step == 0: raise LanguageError('Slice step 不能为 0', code='argument_error')


def slice_key(start=None, stop=None, step=None):
    return Slice(start, stop, step)


def describe(value):
    return {'$slice': {'start': value.start, 'stop': value.stop, 'step': value.step}}


def slice_indices(spec, length):
    if type(spec) is not Slice: raise LanguageError('slice_indices 需要 Slice', code='argument_error')
    if type(length) is not int or length < 0: raise LanguageError('slice_indices length 需要非负 Int', code='argument_error')
    if length.bit_length() > 14000: raise ResourceLimit('length 整数超过 14000 bit')
    return list(slice(spec.start, spec.stop, spec.step).indices(length))


@dataclass(frozen=True)
class IndexRequest:
    operation: str
    values: tuple


def integer_index(evaluator,value):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'integer',value)



def resolve_index(evaluator,request):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'resolve',request)



def native_get(value, key):
    """Pure native access, also usable by the closed macro evaluator."""
    if type(value) is dict:
        if type(key) is not str: raise LanguageError('Record 索引必须为 String', code='argument_error')
        return value[key]
    if type(value) not in (list, str, TupleValue): raise LanguageError('索引需要 List、Tuple、String、Record 或用户对象', code='argument_error')
    if type(key) is Slice:
        start, stop, step = slice_indices(key, len(value))
        count = max(0, (stop-start+step-1)//step) if step > 0 else max(0, (start-stop-step-1)//(-step))
        if count > (250000 if type(value) is str else 10000): raise ResourceLimit('切片结果超过资源限制')
        # Use the original endpoints: normalized -1 has a different meaning when
        # passed back as an explicit negative stop to a second slicing operation.
        return value[slice(key.start, key.stop, key.step)]
    if type(key) is not int: raise LanguageError('序列索引必须为 Int 或 Slice', code='argument_error')
    return value[key]


@dataclass(frozen=True)
class ItemRequest:
    operation: str
    target: object
    key: object
    item: object = None
    fallback: bool = False


def validate_owned(evaluator, value):
    """Check nested ownership without expanding shared primitive DAGs."""
    from .objects import owned
    from .language import Function, Native, Agent
    from .suspension import Task, Generator
    from .iteration import HANDLES
    from .hash_maps import HashMap, children as hash_map_children
    from .eql_specializers import EqlSpecializer
    from .multiple_values import Completion,components
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        identity = id(item)
        if identity in seen: continue
        seen.add(identity)
        if len(seen) > 20000: raise ResourceLimit('索引值超过 20000 个独立节点')
        owned(item, evaluator)
        if isinstance(item, (Function, Native, Agent)) and item.owner is not None and item.owner is not evaluator:
            raise LanguageError('可调用值不能跨执行上下文使用', code='ownership_error')
        if isinstance(item, (Task, Generator)+HANDLES): evaluator.suspension.validate(item, type(item))
        if type(item) is Completion:
            evaluator.tick();pending.extend(components(item,evaluator))
        elif type(item) is EqlSpecializer:
            evaluator.tick();pending.append(item.payload)
        elif type(item) is HashMap:
            evaluator.tick()
            pending.extend(hash_map_children(item))
        elif type(item) in (list, dict, TupleValue, SetValue):
            evaluator.tick()
            pending.extend(item.values() if type(item) is dict else item)


def invoke_item(evaluator,function,request):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'invoke_item',function,request)



def operation(evaluator,request):
    from .execution_access import access_protocol
    return access_protocol(evaluator,'item',request)

