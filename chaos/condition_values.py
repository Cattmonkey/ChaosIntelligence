"""Immutable, bounded situation data shared by runtime and pure macros.

Conditions contain no dynamic handlers, continuations, owner or authority.
Their private typed tree is immutable; every exposed container is a fresh copy.
"""
from dataclasses import dataclass
import math

MAX_NODES, MAX_DEPTH, MAX_CHARS = 20000, 64, 250000
_CONSTRUCTOR = object()


def error(message, code='argument_error'):
    from .language import LanguageError
    raise LanguageError(message, code=code)


def limit():
    from .language import ResourceLimit
    raise ResourceLimit('Condition 数据超过 20000 nodes / 64 depth / 250000 chars')


def _freeze(value, tick=None, depth=0, budget=None, seen=None):
    from .numeric_values import NUMERIC_VALUES, validate
    from .tuple_values import TupleValue
    from .set_values import SetValue
    budget = [0, 0] if budget is None else budget
    seen = set() if seen is None else seen
    if tick: tick()
    budget[0] += 1
    if budget[0] > MAX_NODES or depth > MAX_DEPTH: limit()
    kind = type(value)
    if kind is type(None): return ('null',)
    if kind is bool: return ('bool', value)
    if kind is int:
        if value.bit_length() > 14000: limit()
        return ('int', value)
    if kind is float:
        if not math.isfinite(value): error('Condition 不支持非有限 Number')
        return ('float', value)
    if kind is str:
        value.encode('utf-8'); budget[1] += len(value)
        if budget[1] > MAX_CHARS: limit()
        return ('string', value)
    if kind in NUMERIC_VALUES:
        validate(value, tick); return ('numeric', value)
    if kind is Condition:
        # Rewalk exposed pure data, counting nested conditions in this budget.
        _freeze(value.kind,tick,depth+1,budget,seen)
        _freeze(value.message,tick,depth+1,budget,seen)
        return ('condition', value.kind, value.message,
                _freeze(value.data, tick, depth + 1, budget, seen))
    if kind is SetValue and value.owner is not None:error('Condition data requires portable pure Set')
    if kind not in (list, dict, TupleValue, SetValue):
        error('Condition data 仅接受纯语言数据；不能携带可调用值、类、实例或活句柄')
    if id(value) in seen: error('Condition data 不能包含循环')
    if len(value) > 10000: limit()
    seen.add(id(value))
    try:
        if kind is dict:
            if any(type(key) is not str for key in value): error('Condition Record 键需要 String')
            items=[]
            for key, item in value.items():
                _freeze(key,tick,depth+1,budget,seen)
                items.append((key,_freeze(item,tick,depth+1,budget,seen)))
            return ('record', tuple(items))
        tag = 'list' if kind is list else 'tuple' if kind is TupleValue else 'set'
        return (tag, tuple(_freeze(item,tick,depth+1,budget,seen) for item in value))
    finally: seen.remove(id(value))


def _thaw(tree,depth=0,budget=None):
    from .numeric_values import NUMERIC_VALUES,validate as numeric_validate
    from .tuple_values import TupleValue
    from .set_values import SetValue
    budget=[0,0] if budget is None else budget
    budget[0]+=1
    if budget[0]>MAX_NODES or depth>MAX_DEPTH:limit()
    if type(tree) is not tuple or not tree or type(tree[0]) is not str:error('无效 Condition 内部树')
    tag = tree[0]
    if tag=='null' and len(tree)==1:return None
    if tag in ('bool','int','float','string','numeric') and len(tree)==2:
        value=tree[1]
        expected={'bool':bool,'int':int,'float':float,'string':str}
        if tag in expected and type(value) is not expected[tag]:error('无效 Condition 标量')
        if tag=='int' and value.bit_length()>14000:limit()
        if tag=='float' and not math.isfinite(value):error('Condition Number 必须有限')
        if tag=='string':
            value.encode('utf-8');budget[1]+=len(value)
            if budget[1]>MAX_CHARS:limit()
        if tag=='numeric':
            if type(value) not in NUMERIC_VALUES:error('无效 Condition 数值')
            numeric_validate(value)
        return value
    if tag=='condition' and len(tree)==4:
        kind,message=tree[1:3]
        if type(kind) is not str or not kind or len(kind)>160 or type(message) is not str or len(message)>2000:error('无效嵌套 Condition')
        _thaw(('string',kind),depth+1,budget);_thaw(('string',message),depth+1,budget)
        _thaw(tree[3],depth+1,budget)
        return Condition(kind,message,tree[3])
    if tag not in ('list','tuple','set','record') or len(tree)!=2 or type(tree[1]) is not tuple:error('无效 Condition 容器树')
    if len(tree[1])>10000:limit()
    if tag=='record':
        result={}
        for entry in tree[1]:
            if type(entry) is not tuple or len(entry)!=2 or type(entry[0]) is not str:error('无效 Condition Record')
            key,value=entry;_thaw(('string',key),depth+1,budget)
            if key in result:error('Condition Record 不能重复键')
            result[key]=_thaw(value,depth+1,budget)
        return result
    items = [_thaw(item,depth+1,budget) for item in tree[1]]
    return items if tag == 'list' else TupleValue(items) if tag == 'tuple' else SetValue(tuple(items))


@dataclass(frozen=True, eq=False)
class Condition:
    kind: str
    message: str
    _payload: tuple

    @property
    def data(self): return _thaw(self._payload)


def make(kind, message='', data=None, tick=None):
    if type(kind) is not str or not kind or len(kind)>160: error('Condition kind 需要 1..160 字符 String')
    if type(message) is not str or len(message)>2000: error('Condition message 需要最多 2000 字符 String')
    kind.encode('utf-8'); message.encode('utf-8')
    return Condition(kind,message,_freeze(data,tick))


def validate(value, tick=None):
    if type(value) is not Condition: error('需要 Condition')
    # Accepted values are closed native trees, never a host object protocol.
    rebuilt=make(value.kind,value.message,value.data,tick)
    if rebuilt._payload != value._payload: error('无效 Condition 数据')
    return value


def info(value, tick=None):
    validate(value,tick)
    return {'kind':value.kind,'message':value.message,'data':value.data}


def describe(value): return {'$condition':info(value)}


def member(value,name,tick=None):
    validate(value,tick)
    if type(name) is not str or name not in ('kind','message','data'): error('Condition 成员不存在','attribute_error')
    return getattr(value,name)


def capture_constructor(): return _CONSTRUCTOR
def captured_constructor(value): return value is _CONSTRUCTOR
