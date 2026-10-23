"""Finite native decimal values; explicit bounded kernels and logical contexts.

Host decimal is used only through explicitly constructed Context objects. No
thread/global context, host arithmetic protocol or live host Decimal escapes.
"""
from dataclasses import dataclass
import decimal as _d
import re

MAX_DIGITS=1024
MAX_EXPONENT=2048
MAX_SCRATCH_BITS=56004
ROUNDINGS={'ceiling':_d.ROUND_CEILING,'down':_d.ROUND_DOWN,'floor':_d.ROUND_FLOOR,
           'half_down':_d.ROUND_HALF_DOWN,'half_even':_d.ROUND_HALF_EVEN,
           'half_up':_d.ROUND_HALF_UP,'up':_d.ROUND_UP,'05up':_d.ROUND_05UP}
SIGNALS={x.__name__:x for x in (_d.Clamped,_d.InvalidOperation,_d.DivisionByZero,
         _d.Inexact,_d.Rounded,_d.Subnormal,_d.Overflow,_d.Underflow,_d.FloatOperation)}
PRIORITY=('InvalidOperation','DivisionByZero','Overflow','Underflow','Subnormal','Inexact','Rounded','Clamped','FloatOperation')
DEFAULT_TRAPS=('InvalidOperation','DivisionByZero','Overflow')


def error(message,code='argument_error'):
    from .language import LanguageError
    raise LanguageError(message,code=code)

def limit():
    from .language import ResourceLimit
    raise ResourceLimit('Decimal 超过 1024 digits / 2048 exponent 资源限制')

def checkpoint(tick):
    if tick is not None:tick()

@dataclass(frozen=True,eq=False)
class Decimal:
    sign:int=0
    coefficient:int=0
    exponent:int=0
    def __post_init__(self):validate(self)


def validate(value,tick=None):
    checkpoint(tick)
    if type(value) is not Decimal:error('需要 Decimal')
    if type(value.sign) is not int or value.sign not in (0,1) or type(value.coefficient) is not int or value.coefficient<0 or type(value.exponent) is not int:error('无效 Decimal')
    if value.coefficient.bit_length()>3402 or value.coefficient>=10**MAX_DIGITS or abs(value.exponent)>MAX_EXPONENT:limit()
    return value


def host(value):
    validate(value)
    digits=tuple(map(int,str(value.coefficient)))
    return _d.Decimal((value.sign,digits,value.exponent))

def from_host(value,tick=None):
    checkpoint(tick)
    if not value.is_finite():error('Decimal 仅支持有限值','decimal_signal')
    t=value.as_tuple()
    if len(t.digits)>MAX_DIGITS or abs(t.exponent)>MAX_EXPONENT:limit()
    coefficient=0
    for digit in t.digits:coefficient=coefficient*10+digit
    return Decimal(t.sign,coefficient,t.exponent)


def text(value):
    # to_sci_string preserves scale without arithmetic or global capitals.
    ctx=_d.Context(prec=MAX_DIGITS,rounding=_d.ROUND_HALF_EVEN,Emin=-1024,Emax=1024,capitals=1,clamp=0,flags=[],traps=[])
    return ctx.to_sci_string(host(value))


def ratio(value,tick=None):
    validate(value,tick)
    n=(-1 if value.sign else 1)*value.coefficient
    checkpoint(tick)
    return (n*10**value.exponent,1) if value.exponent>=0 else (n,10**(-value.exponent))


def equal(a,b):
    if type(a) is not Decimal or type(b) is not Decimal:return False
    if not a.coefficient and not b.coefficient:return True
    if a.sign!=b.sign:return False
    an,ad=ratio(a);bn,bd=ratio(b)
    return an*bd==bn*ad


def key(value):
    validate(value)
    c,e=value.coefficient,value.exponent
    if not c:return (0,0,0)
    while c%10==0:c//=10;e+=1
    return value.sign,c,e

class DecimalContext:
    def __init__(self,owner=None,precision=28,rounding='half_even',emin=-999,emax=999,clamp=False,traps=DEFAULT_TRAPS):
        if type(precision) is not int or not 1<=precision<=MAX_DIGITS:error('precision 需要 1..1024 Int')
        if type(rounding) is not str or rounding not in ROUNDINGS:error('无效 Decimal rounding')
        if type(emin) is not int or type(emax) is not int or not -1024<=emin<=0<=emax<=1024:error('Decimal emin/emax 需要 -1024..0..1024')
        if type(clamp) is not bool:error('clamp 需要 Bool')
        if type(traps) not in (list,tuple) or any(type(x) is not str or x not in SIGNALS for x in traps) or len(set(traps))!=len(traps):error('traps 需要不重复的标准 signal List')
        self.owner=owner
        self.epoch=owner.epoch if owner is not None else None
        self.precision,self.rounding,self.emin,self.emax,self.clamp=precision,rounding,emin,emax,clamp
        self.traps=frozenset(traps);self.flags=set()
    def clone(self,owner=None):
        return DecimalContext(self.owner if owner is None else owner,self.precision,self.rounding,self.emin,self.emax,self.clamp,list(self.traps))
    def kernel(self):
        ctx=_d.Context(prec=self.precision,rounding=ROUNDINGS[self.rounding],Emin=self.emin,Emax=self.emax,capitals=1,clamp=int(self.clamp),flags=[],traps=[])
        for signal in ctx.traps:ctx.traps[signal]=False
        ctx.clear_flags()
        return ctx

class DecimalState:
    def __init__(self,owner,context=None):
        self.owner=owner;self.base=context or DecimalContext(owner);self.leases=[]
    @property
    def current(self):return self.leases[-1].context if self.leases else self.base
    def child(self):return DecimalState(self.owner,self.current.clone())

class Lease:
    def __init__(self,state,context):self.state,self.context,self.released=state,context,False
    def release(self):
        if self.released:return
        self.released=True
        if self in self.state.leases:self.state.leases.remove(self)


def active_state(scheduler):
    runner=scheduler.stream.runner if scheduler.stream is not None else scheduler.current
    return runner.decimal_state if runner is not None else scheduler.decimal_state

def current(evaluator):return active_state(evaluator.suspension).current

def owned(context,evaluator):
    s=evaluator.suspension
    if type(context) is not DecimalContext or context.owner is not s or context.epoch!=s.epoch or not s.active:error('DecimalContext 不能跨执行周期或 owner 使用','ownership_error')
    return context

def create(evaluator,precision=28,rounding='half_even',emin=-999,emax=999,clamp=False,traps=DEFAULT_TRAPS):
    return DecimalContext(evaluator.suspension,precision,rounding,emin,emax,clamp,traps)

def info(context):
    return {'precision':context.precision,'rounding':context.rounding,'emin':context.emin,'emax':context.emax,'clamp':context.clamp,'traps':sorted(context.traps),'flags':sorted(context.flags)}

def context_info(evaluator,context=None):return info(current(evaluator) if context is None else owned(context,evaluator))

def clear(evaluator,context=None):
    active=current(evaluator);context=active if context is None else owned(context,evaluator)
    if context is not active:error('只能清除当前 logical DecimalContext flags','context_error')
    context.flags.clear()

def acquire(evaluator,context):
    owned(context,evaluator)
    state=active_state(evaluator.suspension)
    lease=Lease(state,context.clone());state.leases.append(lease)
    return lease.context,lease


def context_for_tick(tick):
    owner=getattr(tick,'__self__',None)
    if owner is not None and hasattr(owner,'suspension'):return current(owner)
    if owner is not None and hasattr(owner,'decimal_context'):return owner.decimal_context
    return DecimalContext()


def commit(context,flags,operation,nonfinite=False):
    context.flags.update(flags)
    trapped=next((signal for signal in PRIORITY if signal in flags and signal in context.traps),None)
    if trapped or nonfinite:
        signal=trapped or next((s for s in PRIORITY if s in flags),'InvalidOperation')
        error('Decimal '+operation+' signal: '+signal,'decimal_signal')


def calculate(operation,*values,tick=None,context=None):
    context=context or context_for_tick(tick)
    checkpoint(tick);ctx=context.kernel()
    result=getattr(ctx,operation)(*values)
    flags={name for name,signal in SIGNALS.items() if ctx.flags[signal]}
    commit(context,flags,operation,not result.is_finite())
    return from_host(result,tick)


def make(value=0,tick=None,context=None):
    context=context or context_for_tick(tick);checkpoint(tick)
    from .numeric_values import Fraction
    if type(value) is Decimal:return validate(value,tick)
    if type(value) is int:
        if value.bit_length()>3402:limit()
        return Decimal(int(value<0),abs(value),0)
    if type(value) is str:
        if len(value)>1100:limit()
        if re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,5})?',value) is None:error('decimal 需要有限十进制 String')
        raw=_d.Decimal(value)
        return from_host(raw,tick)
    if type(value) is float:
        import math
        if not math.isfinite(value):error('decimal 需要有限 Number')
        commit(context,{'FloatOperation'},'conversion')
        return from_host(_d.Decimal.from_float(value),tick)
    if type(value) is Fraction:
        return calculate('divide',_d.Decimal(value.numerator),_d.Decimal(value.denominator),tick=tick,context=context)
    error('decimal 需要 String/Int/Decimal/Fraction/Number')


def from_ratio(n,d,tick=None,context=None):
    if max(n.bit_length(),d.bit_length())>MAX_SCRATCH_BITS:limit()
    return calculate('divide',_d.Decimal(n),_d.Decimal(d),tick=tick,context=context)


def binary(op,a,b,tick=None):
    if type(a) not in (Decimal,int) or type(b) not in (Decimal,int):error('Decimal 混合运算仅支持 Int；请显式 decimal 转换')
    x,y=make(a,tick),make(b,tick)
    if op in ('floordiv','%'):
        if not y.coefficient:return calculate('remainder' if op=='%' else 'divide',host(x),host(y),tick=tick)
        from .numeric_values import _scratch
        r=_scratch('/',ratio(x,tick),ratio(y,tick),tick,MAX_SCRATCH_BITS)
        q=r[0]//r[1]
        from .numeric_values import integer
        integer(q)
        if op=='floordiv':return q
        n,d=_scratch('-',ratio(x,tick),_scratch('*',ratio(y,tick),(q,1),tick,MAX_SCRATCH_BITS),tick,MAX_SCRATCH_BITS)
        return from_ratio(n,d,tick)
    names={'+':'add','-':'subtract','*':'multiply','/':'divide'}
    if op not in names:error('Decimal 不支持 '+op)
    return calculate(names[op],host(x),host(y),tick=tick)


def unary(op,value,tick=None):
    return calculate({'pos':'plus','neg':'minus','abs':'abs'}[op],host(value),tick=tick)

def exact_neg(value):return Decimal(1-value.sign,value.coefficient,value.exponent)

def quantize(value,quantum,tick=None):
    return calculate('quantize',host(value),host(quantum),tick=tick)

def sqrt(value,tick=None):return calculate('sqrt',host(value),tick=tick)

def member(value,name,evaluator=None):
    if type(value) is Decimal:
        validate(value)
        if name not in ('sign','coefficient','exponent'):error('Decimal 成员不存在','attribute_error')
        return object.__getattribute__(value,name)
    if evaluator is None:error('DecimalContext 需要 owner','ownership_error')
    owned(value,evaluator)
    if name not in info(value):error('DecimalContext 成员不存在','attribute_error')
    return info(value)[name]
