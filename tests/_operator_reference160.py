"""Test-only exact original operator algorithms; never a product fallback."""
from contextlib import contextmanager
from unittest.mock import patch
from chaos.operators import (primitive,slot,candidates,ARITHMETIC,COMPARISON,INPLACE,
    NOT_IMPLEMENTED,TupleValue,SetValue)

def invoke(evaluator, receiver, name, args, *, synchronous=False):
    from chaos.objects import special_method, owned
    evaluator.tick()
    function=yield from evaluator._s_access_result(special_method(receiver,name,evaluator))
    if synchronous:
        from chaos.objects import require_callable
        require_callable(function,evaluator,setter=True)
    value=yield from evaluator._s_invoke(function,args,synchronous=synchronous)
    evaluator.tick();owned(value,evaluator)
    return value

def compare_hooks(evaluator, operation, left, right, *, synchronous=False):
    from chaos.language import check_type
    for receiver,name,other in candidates(left,right,*COMPARISON[operation],evaluator,comparison=True):
        value=yield from invoke(evaluator,receiver,name,[other],synchronous=synchronous)
        if value is not NOT_IMPLEMENTED:return check_type(value,'Bool')
    return NOT_IMPLEMENTED

def equal(evaluator, left, right, *, synchronous=False):
    """Effectful equality, deterministic left order; pattern matching stays pure."""
    from chaos.objects import ChaosInstance, owned
    from chaos.hash_maps import HashMap, equal as hash_map_equal
    todo,seen,pure=[(left,right,None)],set(),set()
    effects=0
    while todo:
        evaluator.tick();a,b,leaving=todo.pop()
        if leaving is not None:
            pair=(id(a),id(b));seen.remove(pair)
            # Shared primitive DAGs retain the old bounded behavior. A subtree
            # containing any user instance is never memoized across occurrences.
            if effects==leaving:pure.add(pair)
            continue
        owned(a,evaluator);owned(b,evaluator)
        if isinstance(a,ChaosInstance) or isinstance(b,ChaosInstance):
            effects+=1
            result=yield from compare_hooks(evaluator,'==',a,b,synchronous=synchronous)
            if not (a is b if result is NOT_IMPLEMENTED else result):return False
        elif type(a) is HashMap or type(b) is HashMap:
            effects+=1
            if type(a) is not type(b) or not (yield from hash_map_equal(evaluator,a,b)): return False
        elif type(a) is not type(b):return False
        elif type(a) is SetValue:
            from chaos.set_runtime import algebra
            if a.owner is not None or b.owner is not None:effects+=1
            if not (yield from algebra(evaluator,'==',a,b)):return False
        elif isinstance(a,(list,dict,TupleValue)):
            pair=(id(a),id(b))
            if pair in seen or pair in pure:continue
            seen.add(pair)
            todo.append((a,b,effects))
            if len(a)!=len(b):return False
            if isinstance(a,(list,TupleValue)):todo.extend((x,y,None) for x,y in reversed(list(zip(a,b))))
            else:
                if a.keys()!=b.keys():return False
                todo.extend((a[key],b[key],None) for key in reversed(list(a)))
        elif not evaluator.equal(a,b):return False
    return True

def binary(evaluator, operation, left, right):
    from chaos.language import LanguageError, serializable
    if type(left) is SetValue and type(right) is SetValue and operation in ('|','&','-','^','<','<=','>','>='):
        from chaos.set_runtime import algebra
        return (yield from algebra(evaluator,operation,left,right))
    if operation=='==':return (yield from equal(evaluator,left,right))
    if operation=='!=':
        value=yield from compare_hooks(evaluator,'!=',left,right)
        return not (yield from equal(evaluator,left,right)) if value is NOT_IMPLEMENTED else value
    if operation in COMPARISON:
        value=yield from compare_hooks(evaluator,operation,left,right)
        if value is NOT_IMPLEMENTED:value=primitive(operation,left,right,evaluator.numeric_checkpoint)
    else:
        # Primitive operands never have language class protocols. Unsupported
        # primitive combinations can still be handled by the right reflected hook.
        value=primitive(operation,left,right,evaluator.numeric_checkpoint)
        if value is not NOT_IMPLEMENTED:return value
        if value is NOT_IMPLEMENTED:
            for receiver,name,other in candidates(left,right,*ARITHMETIC[operation],evaluator):
                value=yield from invoke(evaluator,receiver,name,[other])
                if value is not NOT_IMPLEMENTED:break
    if value is NOT_IMPLEMENTED:raise LanguageError('操作数不支持运算 '+operation,code='argument_error')
    serializable(value)
    return value

def inplace(evaluator, operation, left, right):
    """A missing/declining in-place hook falls back; exceptions never do."""
    from chaos.language import serializable
    from chaos.objects import owned
    evaluator.tick()
    owned(right, evaluator)
    method = INPLACE[operation]
    if slot(left, method, evaluator) is not None:
        result = yield from invoke(evaluator, left, method, [right])
        if result is not NOT_IMPLEMENTED:
            serializable(result)
            return result
    return (yield from binary(evaluator, operation, left, right))

def operation(evaluator, name, operands, *, _constant_site=None):
    from chaos.language import LanguageError, ResourceLimit, check_type, serializable
    from chaos.objects import ChaosInstance, owned
    evaluator.tick()
    if _constant_site is not None:
        from chaos.portable_optimizer import ConstantSite
        if type(_constant_site) is ConstantSite:
            handled,result = _constant_site.match(name,operands)
            if handled:
                evaluator._literal_applied_operations += 1
                return result
            evaluator._literal_guard_misses += 1
    if name in ARITHMETIC or name in COMPARISON:return (yield from binary(evaluator,name,*operands))
    value=operands[0];owned(value,evaluator)
    from chaos.hash_maps import HashMap, Request as HashMapRequest, resolve as hash_map_resolve
    if type(value) is HashMap:
        if name=='length':return len(value)
        if name=='bool':return len(value)!=0
        if name=='contains':return (yield from hash_map_resolve(evaluator,HashMapRequest('has',value,operands[1])))
    if name in ('neg','abs','pos','invert'):
        if isinstance(value,ChaosInstance):
            method={'neg':'__neg__', 'abs':'__abs__', 'pos':'__pos__', 'invert':'__invert__'}[name]
            if slot(value,method,evaluator) is None:raise LanguageError('对象不支持 '+name,code='argument_error')
            result=yield from invoke(evaluator,value,method,[])
            if result is NOT_IMPLEMENTED:raise LanguageError('对象不支持 '+name,code='argument_error')
        else:
            from chaos.numeric_values import Fraction, Complex, Decimal, unary as numeric_unary
            if type(value) in (Fraction, Complex, Decimal):
                result = numeric_unary(name,value,evaluator.numeric_checkpoint)
            else:
                check_type(value,'Int' if name=='invert' else 'Number')
                result={'neg':lambda:-value, 'abs':lambda:abs(value),
                        'pos':lambda:+value, 'invert':lambda:~value}[name]()
        serializable(result);return result
    from chaos.dynamic_contexts import Context,owned as context_owned,has as context_has
    if name=='length' and type(value) is Context:return len(context_owned(value,evaluator).bindings)
    if name=='contains' and type(value) is Context:return context_has(evaluator,value,operands[1])
    if name=='length':
        if isinstance(value,ChaosInstance):
            if slot(value,'__len__',evaluator) is None:raise LanguageError('对象未定义 __len__',code='argument_error')
            result=check_type((yield from invoke(evaluator,value,'__len__',[])),'Int')
            if result<0:raise LanguageError('__len__ 必须返回非负 Int',code='argument_error')
            if result.bit_length()>14000:raise ResourceLimit('整数超过 14000 bit 资源限制')
            return result
        if type(value) is SetValue: return len(value)
        if not isinstance(value,(list,dict,str,TupleValue)):raise LanguageError('length 需要 List、Tuple、Record、String 或 Instance',code='TypeError')
        return len(value)
    if name=='bool':
        if type(value) in (TupleValue,SetValue):return len(value)!=0
        if type(value) is bool:return value
        if isinstance(value,ChaosInstance):
            if slot(value,'__bool__',evaluator) is not None:
                return check_type((yield from invoke(evaluator,value,'__bool__',[])),'Bool')
            if slot(value,'__len__',evaluator) is not None:
                return (yield from operation(evaluator,'length',(value,)))!=0
        raise LanguageError('bool 需要 Bool 或显式 __bool__/__len__ 协议',code='argument_error')
    if name=='contains':
        item=operands[1];owned(item,evaluator)
        if type(value) is SetValue:
            from chaos.set_runtime import contains
            return (yield from contains(evaluator,value,item))
        if isinstance(value,ChaosInstance):
            if slot(value,'__contains__',evaluator) is None:raise LanguageError('对象未定义 __contains__',code='argument_error')
            return check_type((yield from invoke(evaluator,value,'__contains__',[item])),'Bool')
        if isinstance(value,(str,dict)):return check_type(item,'String') in value
        if type(value) is not TupleValue: check_type(value,'List')
        for member in value:
            if (yield from equal(evaluator,member,item)):return True
        return False
    raise LanguageError('未知内部运算 '+name)

_ORIGINALS={name:globals()[name]for name in ('invoke','compare_hooks','equal','binary','inplace','operation')}

@contextmanager
def reference():
    import chaos.operators as operators
    from contextlib import ExitStack
    reached={name:0 for name in _ORIGINALS}
    replacements={}
    for name,function in _ORIGINALS.items():
        def called(*args,_function=function,_name=name,**kwargs):
            reached[_name]+=1
            return _function(*args,**kwargs)
        replacements[name]=called
    with ExitStack() as stack:
        for name,function in replacements.items():
            stack.enter_context(patch.object(operators,name,function))
            stack.enter_context(patch.dict(globals(),{name:function}))
        yield reached
