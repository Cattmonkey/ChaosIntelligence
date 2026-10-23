"""Cooperative language operators. No Python-object arithmetic or attribute forwarding."""
from dataclasses import dataclass
from .tuple_values import TupleValue
from .set_values import SetValue, set_equal, set_contains, set_binary
import math


class _NotImplementedValue:
    __slots__ = ()


NOT_IMPLEMENTED = _NotImplementedValue()


@dataclass(frozen=True)
class OperationRequest:
    operation: str
    operands: tuple


# Shared syntax/runtime metadata; no user protocol executes while inspecting it.
PRECEDENCE = {'|>': 1, '||': 2, '&&': 3, '==': 4, '!=': 4,
              '<': 5, '>': 5, '<=': 5, '>=': 5, '|': 6, '^': 7, '&': 8,
              '<<': 9, '>>': 9, '+': 10, '-': 10, '*': 11, '/': 11, '%': 11, '**': 13}
UNARY_PRECEDENCE, AWAIT_PRECEDENCE = 12, 14
INPLACE = {'+': '__iadd__', '-': '__isub__', '*': '__imul__', '/': '__itruediv__',
           '%': '__imod__', '**': '__ipow__', '&': '__iand__', '|': '__ior__',
           '^': '__ixor__', '<<': '__ilshift__', '>>': '__irshift__'}
AUGMENTED = {operator + '=': operator for operator in INPLACE}


ARITHMETIC = {
    '+': ('__add__', '__radd__'), '-': ('__sub__', '__rsub__'),
    '*': ('__mul__', '__rmul__'), '/': ('__truediv__', '__rtruediv__'),
    '%': ('__mod__', '__rmod__'), 'pow': ('__pow__', '__rpow__'),
    'floordiv': ('__floordiv__', '__rfloordiv__'),
    '**': ('__pow__', '__rpow__'), '&': ('__and__', '__rand__'),
    '|': ('__or__', '__ror__'), '^': ('__xor__', '__rxor__'),
    '<<': ('__lshift__', '__rlshift__'), '>>': ('__rshift__', '__rrshift__'),
}
COMPARISON = {
    '<': ('__lt__', '__gt__'), '<=': ('__le__', '__ge__'),
    '>': ('__gt__', '__lt__'), '>=': ('__ge__', '__le__'),
    '==': ('__eq__', '__eq__'), '!=': ('__ne__', '__ne__'),
}


def primitive(operation, left, right, tick=None):
    """Strict pre-existing primitive rules, or NI for unsupported operands."""
    from .language import ResourceLimit, LanguageError
    if operation == '**': operation = 'pow'
    from .numeric_values import Fraction, Complex, Decimal, is_numeric, binary as numeric_binary
    if type(left) in (Fraction, Complex, Decimal) or type(right) in (Fraction, Complex, Decimal):
        if not is_numeric(left) or not is_numeric(right): return NOT_IMPLEMENTED
        return numeric_binary(operation, left, right, tick)
    if operation in ('&', '|', '^', '<<', '>>'):
        if type(left) is not int or type(right) is not int: return NOT_IMPLEMENTED
        if operation in ('<<', '>>'):
            if right < 0: raise LanguageError('移位数量必须为非负 Int', code='argument_error')
            if operation == '<<':
                if left == 0: return 0
                if left.bit_length() + right > 14000:
                    raise ResourceLimit('左移整数超过 14000 bit 资源限制')
                return left << right
            # Avoid converting arbitrarily large language Int counts to a host
            # machine-sized shift; arithmetic right shift converges by sign.
            if right >= left.bit_length(): return -1 if left < 0 else 0
            return left >> right
        value = {'&': lambda: left & right, '|': lambda: left | right,
                 '^': lambda: left ^ right}[operation]()
        if value.bit_length() > 14000: raise ResourceLimit('整数超过 14000 bit 资源限制')
        return value
    if operation == '+' and type(left) is type(right) and type(left) in (list, str, TupleValue):
        if len(left)+len(right) > (250000 if type(left) is str else 10000):
            raise ResourceLimit('集合超过资源限制')
        return TupleValue(left.items+right.items) if type(left) is TupleValue else left+right
    if type(left) not in (int, float) or type(right) not in (int, float): return NOT_IMPLEMENTED
    if operation == 'pow' and type(left) is int and type(right) is int and right >= 0:
        if abs(left) > 1 and left.bit_length()*right > 14000:
            raise ResourceLimit('pow 整数超过资源限制')
    functions={'+':lambda:left+right,'-':lambda:left-right,'*':lambda:left*right,
               '/':lambda:left/right,'%':lambda:left%right,'pow':lambda:left**right,
               'floordiv':lambda:left//right,'<':lambda:left<right,'<=':lambda:left<=right,
               '>':lambda:left>right,'>=':lambda:left>=right}
    value=functions[operation]()
    if type(value) is int and value.bit_length()>14000:raise ResourceLimit('整数超过 14000 bit 资源限制')
    if type(value) is float and not math.isfinite(value):raise LanguageError('不支持非有限数值')
    # Negative fractional powers can produce host complex values; reject them.
    if type(value) not in (int,float,bool):raise LanguageError('不支持的数值结果')
    return value


def slot(value, name, evaluator):
    from .objects import ChaosInstance, declaration, owned
    owned(value,evaluator)
    if not isinstance(value,ChaosInstance):return None
    owned(value.klass,evaluator)
    return declaration(value.klass,name)


def candidates(left, right, forward, reverse, evaluator, comparison=False):
    from .objects import ChaosInstance
    first=slot(left,forward,evaluator);second=slot(right,reverse,evaluator)
    both=isinstance(left,ChaosInstance) and isinstance(right,ChaosInstance)
    same=both and left.klass is right.klass
    subtype=both and not same and any(base is left.klass for base in right.klass.mro)
    reverse_first=second is not None and subtype and (comparison or second is not slot(left,reverse,evaluator))
    result=[]
    if reverse_first:result.append((right,reverse,left))
    if first is not None:result.append((left,forward,right))
    if second is not None and not reverse_first and (comparison or not same):result.append((right,reverse,left))
    return result


def invoke(evaluator, receiver, name, args, *, synchronous=False):
    from .execution_operators import operator_protocol
    return operator_protocol(evaluator,'invoke',receiver,name,args,synchronous)


def compare_hooks(evaluator, operation, left, right, *, synchronous=False):
    from .execution_operators import operator_protocol
    return operator_protocol(evaluator,'compare',operation,left,right,synchronous)


def equal(evaluator, left, right, *, synchronous=False):
    from .execution_operators import operator_protocol
    return operator_protocol(evaluator,'equal',left,right,synchronous)


def binary(evaluator, operation, left, right):
    from .execution_operators import operator_protocol
    return operator_protocol(evaluator,'binary',operation,left,right)


def inplace(evaluator, operation, left, right):
    from .execution_operators import operator_protocol
    return operator_protocol(evaluator,'inplace',operation,left,right)


def operation(evaluator, name, operands, *, _constant_site=None):
    from .execution_operators import operator_protocol
    return operator_protocol(evaluator,'operation',name,operands,_constant_site)
