"""Closed immutable numeric values and one bounded algebra for runtime and macros.

No host numeric protocol is invoked. Existing Int/Number arithmetic is unchanged.
"""
from dataclasses import dataclass
import math
import re
from .decimal_values import Decimal

MAX_BITS = 14000
MAX_INTERMEDIATE_BITS = 28002


def _error(message, code='argument_error'):
    from .language import LanguageError
    raise LanguageError(message, code=code)


def _limit():
    from .language import ResourceLimit
    raise ResourceLimit('精确数值超过 14000 bit 资源限制')


def _tick(tick):
    if tick is not None: tick()


def integer(value):
    if type(value) is not int: _error('fraction 需要 Int')
    if value.bit_length() > MAX_BITS: _limit()
    return value


def _mul(a, b, tick=None, limit=MAX_INTERMEDIATE_BITS):
    _tick(tick)
    if a and b and abs(a).bit_length() + abs(b).bit_length() > limit: _limit()
    return a * b


def _gcd(a, b, tick=None):
    a, b = abs(a), abs(b)
    while b:
        _tick(tick)
        a, b = b, a % b
    return a


@dataclass(frozen=True, eq=False)
class Fraction:
    numerator: int = 0
    denominator: int = 1

    def __post_init__(self):
        n, d = integer(self.numerator), integer(self.denominator)
        if not d: _error('fraction denominator 不能为 0', 'ZeroDivisionError')
        if d < 0: n, d = -n, -d
        g = math.gcd(n, d)
        object.__setattr__(self, 'numerator', n // g)
        object.__setattr__(self, 'denominator', d // g)


def is_real(value): return type(value) in (int, float, Fraction, Decimal)
def is_numeric(value): return is_real(value) or type(value) is Complex


def validate(value, tick=None):
    _tick(tick)
    if type(value) is int: integer(value)
    elif type(value) is float:
        if not math.isfinite(value): _error('不支持非有限数值')
    elif type(value) is Decimal:
        from .decimal_values import validate as decimal_validate
        decimal_validate(value,tick)
    elif type(value) is Fraction:
        integer(value.numerator); integer(value.denominator)
        if value.denominator <= 0 or math.gcd(value.numerator, value.denominator) != 1:
            _error('无效 Fraction')
    elif type(value) is Complex:
        if not is_real(value.real) or not is_real(value.imag): _error('Complex 部件必须为 Real')
        validate(value.real, tick); validate(value.imag, tick)
        if any(type(x) is Decimal for x in (value.real,value.imag)) and not all(type(x) is Decimal for x in (value.real,value.imag)): _error('无效 Decimal Complex promotion')
        if (type(value.real) is float) != (type(value.imag) is float): _error('无效 Complex promotion')
        if any(type(x) is Fraction and x.denominator == 1 for x in (value.real, value.imag)):
            _error('无效 Complex 部件归一化')
    else: _error('需要 Numeric')
    return value


def to_number(value):
    if not is_real(value): _error('number 需要 String 或 Real')
    validate(value)
    try:
        if type(value) is Decimal:
            from .decimal_values import host
            result=float(host(value))
        else:result = value.numerator / value.denominator if type(value) is Fraction else float(value)
    except (OverflowError, ValueError): _error('number 转换超过有限 Number 范围')
    if not math.isfinite(result): _error('number 转换超过有限 Number 范围')
    return result


def _component(value):
    if not is_real(value): _error('complex 部件需要 Real')
    validate(value)
    return value.numerator if type(value) is Fraction and value.denominator == 1 else value


@dataclass(frozen=True, eq=False)
class Complex:
    real: object = 0
    imag: object = 0

    def __post_init__(self):
        r, i = _component(self.real), _component(self.imag)
        if type(r) is Decimal or type(i) is Decimal:
            from .decimal_values import make
            if type(r) not in (int,Decimal) or type(i) not in (int,Decimal):_error('Complex Decimal 部件需显式 decimal 转换')
            r,i=make(r),make(i)
        if type(r) is float or type(i) is float: r, i = to_number(r), to_number(i)
        object.__setattr__(self, 'real', r)
        object.__setattr__(self, 'imag', i)


def make_fraction(numerator=0, denominator=1, tick=None):
    n, d = integer(numerator), integer(denominator)
    if not d: _error('fraction denominator 不能为 0', 'ZeroDivisionError')
    g = _gcd(n, d, tick)
    return Fraction(n // g, d // g)


def _ratio(value):
    if type(value) is Decimal:
        from .decimal_values import ratio
        return ratio(value)
    if type(value) is Fraction: return value.numerator, value.denominator
    if type(value) is int: return value, 1
    if type(value) is float: return value.as_integer_ratio()
    _error('需要 Real')


def _fraction(n, d, tick):
    if not d: _error('除数不能为 0', 'ZeroDivisionError')
    g = _gcd(n, d, tick)
    n, d = n // g, d // g
    if max(n.bit_length(), d.bit_length()) > MAX_BITS: _limit()
    return Fraction(n, d)


def compare(a, b, tick=None):
    if not is_real(a) or not is_real(b): _error('排序与比较需要 Real')
    validate(a, tick); validate(b, tick)
    an, ad = _ratio(a); bn, bd = _ratio(b)
    x, y = _mul(an, bd, tick), _mul(bn, ad, tick)
    return (x > y) - (x < y)


def equal(a, b):
    if type(a) is not type(b): return False
    if type(a) is Decimal:
        from .decimal_values import equal as decimal_equal
        return decimal_equal(a,b)
    if type(a) is Fraction: return a.numerator == b.numerator and a.denominator == b.denominator
    if type(a) is Complex: return equal(a.real, b.real) and equal(a.imag, b.imag)
    return a == b


def _zero(value):
    return value.coefficient==0 if type(value) is Decimal else value.numerator == 0 if type(value) is Fraction else value == 0


def _exact(op, a, b, tick):
    an, ad = _ratio(a); bn, bd = _ratio(b)
    if op in ('+', '-'):
        g = _gcd(ad, bd, tick)
        n = _mul(an, bd // g, tick) + (1 if op == '+' else -1) * _mul(bn, ad // g, tick)
        return _fraction(n, _mul(ad, bd // g, tick), tick)
    if op == '*':
        g, h = _gcd(an, bd, tick), _gcd(bn, ad, tick)
        return _fraction(_mul(an // g, bn // h, tick), _mul(ad // h, bd // g, tick), tick)
    if op == '/':
        if not bn: _error('除数不能为 0', 'ZeroDivisionError')
        g, h = _gcd(an, bn, tick), _gcd(ad, bd, tick)
        return _fraction(_mul(an // g, bd // h, tick), _mul(ad // h, bn // g, tick), tick)
    if op in ('floordiv', '%'):
        qn,qd=_scratch('/',(an,ad),(bn,bd),tick)
        quotient=qn//qd
        integer(quotient)
        if op=='floordiv':return quotient
        remainder=_scratch('-',(an,ad),_scratch('*',(bn,bd),(quotient,1),tick),tick)
        return _fraction(*remainder,tick)
    _error('不支持精确运算 '+op)


def _power(a, exponent, tick):
    integer(exponent)
    if abs(exponent) > MAX_BITS:
        # Unit powers are well-defined without proportional allocation/loops.
        if type(a) is Complex and _zero(a.imag) and equal(a.real, 1): return a
        if type(a) is Fraction and abs(a.numerator) == a.denominator:
            return Fraction(-1 if a.numerator < 0 and exponent % 2 else 1)
        _limit()
    if exponent < 0:
        a = binary('/', Complex(1) if type(a) is Complex else 1 if type(a) is Decimal else Fraction(1), a, tick)
        exponent = -exponent
    if type(a) is Decimal:
        from .decimal_values import make
        result=make(1,tick)
    elif type(a) is Complex and type(a.real) is Decimal:
        from .decimal_values import make
        result=Complex(make(1,tick),make(0,tick))
    else:result = (Complex(1.0,0.0) if type(a.real) is float else Complex(1)) if type(a) is Complex else Fraction(1)
    while exponent:
        _tick(tick)
        if exponent & 1: result = binary('*', result, a, tick)
        exponent >>= 1
        if exponent: a = binary('*', a, a, tick)
    return result


def _scratch(op, a, b, tick, limit=MAX_INTERMEDIATE_BITS):
    an,ad=a;bn,bd=b
    if op in ('+','-'):
        g=_gcd(ad,bd,tick)
        n=_mul(an,bd//g,tick,limit)+(1 if op=='+' else -1)*_mul(bn,ad//g,tick,limit)
        d=_mul(ad,bd//g,tick,limit)
    elif op=='*':
        g,h=_gcd(an,bd,tick),_gcd(bn,ad,tick)
        n,d=_mul(an//g,bn//h,tick,limit),_mul(ad//h,bd//g,tick,limit)
    else:
        if not bn: _error('除数不能为 0','ZeroDivisionError')
        g,h=_gcd(an,bn,tick),_gcd(ad,bd,tick)
        n,d=_mul(an//g,bd//h,tick,limit),_mul(ad//h,bn//g,tick,limit)
    g=_gcd(n,d,tick);n,d=n//g,d//g
    if d<0:n,d=-n,-d
    if max(n.bit_length(),d.bit_length())>limit:_limit()
    return n,d


def binary(op, a, b, tick=None):
    """New numeric operands only; caller retains legacy primitives otherwise."""
    validate(a, tick); validate(b, tick)
    if op == '**': op = 'pow'
    if op in ('<', '<=', '>', '>='):
        c = compare(a, b, tick)
        return {'<': c < 0, '<=': c <= 0, '>': c > 0, '>=': c >= 0}[op]
    if op == 'pow':
        if type(b) is not int: _error('Fraction/Complex 的指数需要 Int')
        return _power(a, b, tick)
    if type(a) is Complex or type(b) is Complex:
        if op not in ('+', '-', '*', '/'): _error('Complex 不支持 '+op)
        x, y = a if type(a) is Complex else Complex(a), b if type(b) is Complex else Complex(b)
        decimal_parts=any(type(v) is Decimal for v in (x.real,x.imag,y.real,y.imag))
        if decimal_parts:
            from .decimal_values import make
            if any(type(v) not in (Decimal,int) for v in (x.real,x.imag,y.real,y.imag)):_error('Decimal Complex 混合运算需显式 decimal 转换')
            x,y=Complex(make(x.real,tick),make(x.imag,tick)),Complex(make(y.real,tick),make(y.imag,tick))
        if any(type(v) is float for v in (x.real, x.imag, y.real, y.imag)):
            x, y = Complex(to_number(x.real), to_number(x.imag)), Complex(to_number(y.real), to_number(y.imag))
        calc = lambda p, q, operation: real_binary(operation, p, q, tick)
        if op in ('+', '-'):
            return Complex(calc(x.real, y.real, op), calc(x.imag, y.imag, op))
        if op == '*':
            return Complex(calc(calc(x.real, y.real, '*'), calc(x.imag, y.imag, '*'), '-'),
                           calc(calc(x.real, y.imag, '*'), calc(x.imag, y.real, '*'), '+'))
        # Scratch ratios have a separate bounded budget. Normalize/cross-cancel
        # before allocating, and enforce public 14000-bit parts only at output.
        xr,xi,yr,yi=map(_ratio,(x.real,x.imag,y.real,y.imag))
        scratch=lambda operation,p,q:_scratch(operation,p,q,tick,56004 if decimal_parts else MAX_INTERMEDIATE_BITS)
        denominator=scratch('+',scratch('*',yr,yr),scratch('*',yi,yi))
        if decimal_parts and not denominator[0]:
            from .decimal_values import from_ratio
            return from_ratio(0 if not xr[0] and not xi[0] else 1,0,tick)
        real=scratch('/',scratch('+',scratch('*',xr,yr),scratch('*',xi,yi)),denominator)
        imag=scratch('/',scratch('-',scratch('*',xi,yr),scratch('*',xr,yi)),denominator)
        if decimal_parts:
            from .decimal_values import from_ratio
            return Complex(from_ratio(*real,tick),from_ratio(*imag,tick))
        real_value,imag_value=_fraction(*real,tick),_fraction(*imag,tick)
        if type(x.real) is float:
            return Complex(to_number(real_value),to_number(imag_value))
        return Complex(real_value,imag_value)
    return real_binary(op, a, b, tick)


def real_binary(op, a, b, tick=None):
    if type(a) is Decimal or type(b) is Decimal:
        from .decimal_values import binary as decimal_binary
        return decimal_binary(op,a,b,tick)
    if type(a) is float or type(b) is float:
        x, y = to_number(a), to_number(b)
        functions = {'+':lambda:x+y, '-':lambda:x-y, '*':lambda:x*y, '/':lambda:x/y,
                     '%':lambda:x%y, 'floordiv':lambda:x//y}
        if op not in functions: _error('不支持 Real 运算 '+op)
        try: result = functions[op]()
        except ZeroDivisionError: _error('除数不能为 0', 'ZeroDivisionError')
        if not math.isfinite(result): _error('不支持非有限数值')
        return result
    return _exact(op, a, b, tick)


def unary(op, value, tick=None):
    validate(value, tick)
    if type(value) is Decimal:
        from .decimal_values import unary as decimal_unary
        return decimal_unary(op,value,tick)
    if op == 'pos': return value
    if op == 'neg':
        if type(value) is Fraction: return Fraction(-value.numerator, value.denominator)
        if type(value) is Complex: return Complex(unary('neg', value.real, tick), unary('neg', value.imag, tick))
        return -value
    if op == 'abs':
        if type(value) is Fraction: return Fraction(abs(value.numerator), value.denominator)
        if type(value) is Complex:
            result = math.hypot(to_number(value.real), to_number(value.imag))
            if not math.isfinite(result): _error('不支持非有限数值')
            return result
        return abs(value)
    _error('Numeric 不支持 '+op)


def conjugate(value, tick=None):
    validate(value, tick)
    if type(value) is Complex and type(value.imag) is Decimal:
        from .decimal_values import exact_neg
        return Complex(value.real,exact_neg(value.imag))
    return Complex(value.real, unary('neg', value.imag, tick)) if type(value) is Complex else value


def rounding(value, operation):
    if not is_real(value): _error(operation+' 需要 Real')
    validate(value)
    if type(value) is Decimal:
        n,d=_ratio(value)
    elif type(value) is not Fraction:
        return {'floor':math.floor, 'ceil':math.ceil, 'round':round}[operation](value)
    else:n, d = value.numerator, value.denominator
    q, r = divmod(n, d)
    if operation == 'floor': return q
    if operation == 'ceil': return q + (r != 0)
    twice = r * 2
    return q + (twice > d or (twice == d and q % 2 != 0))


def encode(value):
    validate(value)
    if type(value) is int: payload = {'kind':'Int','value':str(value)}
    elif type(value) is float: payload = {'kind':'Number','value':repr(value)}
    elif type(value) is Decimal: payload={'kind':'Decimal','sign':value.sign,'coefficient':str(value.coefficient),'exponent':value.exponent}
    elif type(value) is Fraction: payload = {'kind':'Fraction','numerator':str(value.numerator),'denominator':str(value.denominator)}
    else: payload = {'kind':'Complex','real':encode(value.real),'imag':encode(value.imag)}
    return {'$numeric':payload}


def decode(value, tick=None):
    _tick(tick)
    if type(value) is not dict or set(value) != {'$numeric'}: _error('numeric_decode 需要完整 numeric Record')
    p = value['$numeric']
    if type(p) is not dict or type(p.get('kind')) is not str: _error('无效 numeric kind')
    def text_int(v):
        if type(v) is not str or len(v) > 4216 or re.fullmatch(r'0|-?[1-9][0-9]*', v) is None:
            _error('numeric Int 需要有界规范十进制 String')
        return integer(int(v))
    kind = p['kind']
    if kind == 'Decimal' and set(p)=={'kind','sign','coefficient','exponent'}:
        c=p['coefficient']
        if type(c) is not str or len(c)>1024 or re.fullmatch(r'0|[1-9][0-9]*',c) is None:_error('无效 Decimal coefficient')
        return Decimal(p['sign'],int(c),p['exponent'])
    if kind == 'Int' and set(p) == {'kind','value'}: return text_int(p['value'])
    if kind == 'Fraction' and set(p) == {'kind','numerator','denominator'}:
        return make_fraction(text_int(p['numerator']), text_int(p['denominator']),tick)
    if kind == 'Number' and set(p) == {'kind','value'}:
        if type(p['value']) is not str or len(p['value']) > 32: _error('无效 Number 文本')
        try: v = float(p['value'])
        except ValueError: _error('无效 Number 文本')
        if not math.isfinite(v) or repr(v) != p['value']: _error('需要规范有限 Number 文本')
        return v
    if kind == 'Complex' and set(p) == {'kind','real','imag'}:
        # Exactly one level; complex children may not be nested complexes.
        for child in (p['real'], p['imag']):
            if type(child) is not dict or type(child.get('$numeric')) is not dict or child['$numeric'].get('kind') not in ('Int','Number','Fraction','Decimal'):
                _error('Complex codec 部件需要 Real')
        return Complex(decode(p['real'],tick), decode(p['imag'],tick))
    _error('无效 numeric Record 字段')

NUMERIC_VALUES = (Fraction, Complex, Decimal)
# Tokens exist only on compiler-owned expanded Nodes. Source/IR decoding cannot
# construct tokens, and runtime lookup resolves the evaluator's frozen Native.
_FRACTION_CAPTURE, _COMPLEX_CAPTURE, _DECIMAL_CAPTURE = object(), object(), object()

def capture_constructor(name): return _FRACTION_CAPTURE if name == 'fraction' else _DECIMAL_CAPTURE if name=='decimal' else _COMPLEX_CAPTURE

def captured_constructor(token):
    return 'fraction' if token is _FRACTION_CAPTURE else 'complex' if token is _COMPLEX_CAPTURE else 'decimal' if token is _DECIMAL_CAPTURE else None

def member(value, name):
    validate(value)
    if type(value) is Decimal:
        from .decimal_values import member as decimal_member
        return decimal_member(value,name)
    names = ('numerator','denominator') if type(value) is Fraction else ('real','imag')
    if type(name) is not str or name not in names: _error('数值成员不存在', 'attribute_error')
    return object.__getattribute__(value,name)
