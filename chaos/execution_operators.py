"""Authoritative cooperative operator phases on the shared execution cursor.

Descriptor access, Set and HashMap protocols retain their own named boundaries.
No original whole operator generator is used as an execution fallback.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class OperatorFrameRequest:
    operation: str
    values: tuple


def operator_protocol(evaluator, operation, *values):
    from .execution_frames import request_proxy,ensure_cursor
    proxy=request_proxy(OperatorFrameRequest(operation,values))
    if getattr(evaluator,'_execution_cursor',None) is not None:return proxy
    return ensure_cursor(evaluator,proxy,observer=True)


class _OperatorFrame:
    capabilities=None
    ROOTS=()
    def ceiling(self,name):return self.capabilities
    def roots(self):
        from .capture_frames import Edge
        roots=[]
        for name in self.ROOTS:
            value=getattr(self,name,None)
            if name in ('todo','candidates') and type(value) in (tuple,list):
                values=tuple(child for row in value for child in (row if type(row) is tuple else (row,)))
            elif name in ('args','operands','current','pair') and type(value) in (tuple,list):values=tuple(value)
            else:values=(value,)
            roots.append(Edge(self.owner,self.ceiling(name),values))
        return tuple(roots)
    def leaf(self,iterator):
        from .execution_frames import Push,ProtocolBridge
        bridge=ProtocolBridge(iterator);bridge.supplementary=True
        return Push(bridge)
    def close(self,cursor):pass


class OperatorInvokeFrame(_OperatorFrame):
    ROOTS=('receiver','args','access_request','function','result','error')
    def __init__(self,receiver,method,args,synchronous=False):
        self.receiver,self.method,self.args,self.synchronous=receiver,method,args,synchronous
        self.phase='start';self.access_request=self.function=self.result=self.error=None
    def ceiling(self,name):
        from .conditions import intersection
        from .execution_iteration import _caps
        if name in ('args','result'):
            source=self.function if self.function is not None else self.receiver
            return intersection(self.capabilities,_caps(source))
        return self.capabilities
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,InvocationFrame,InvocationRequest
        from .objects import special_method,owned,require_callable
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            e.tick();self.access_request=special_method(self.receiver,self.method,e);self.phase='access'
            return self.leaf(e._s_access_result(self.access_request))
        if self.phase=='access':
            self.function=value
            if self.synchronous:require_callable(self.function,e,setter=True)
            self.phase='invoke'
            return Push(InvocationFrame(InvocationRequest(self.function,self.args,options={'synchronous':self.synchronous})))
        e.tick();self.result=value;owned(self.result,e);self.phase='done';return Done(self.result)


class OperatorCompareFrame(_OperatorFrame):
    ROOTS=('left','right','candidates','receiver','other','result','error')
    def __init__(self,operation,left,right,synchronous=False):
        self.operation,self.left,self.right,self.synchronous=operation,left,right,synchronous
        self.phase='start';self.candidates=[];self.index=0
        self.receiver=self.other=self.result=self.error=None;self.method=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .operators import candidates,COMPARISON,NOT_IMPLEMENTED
        from .language import check_type
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            self.candidates=candidates(self.left,self.right,*COMPARISON[self.operation],cursor.owner,comparison=True)
            self.phase='choose'
        elif self.phase=='invoke':
            self.result=value
            if self.result is not NOT_IMPLEMENTED:return Done(check_type(self.result,'Bool'))
            self.index+=1;self.phase='choose'
        if self.index>=len(self.candidates):return Done(NOT_IMPLEMENTED)
        self.receiver,self.method,self.other=self.candidates[self.index];self.phase='invoke'
        return Push(OperatorInvokeFrame(self.receiver,self.method,[self.other],self.synchronous))


class OperatorEqualFrame(_OperatorFrame):
    ROOTS=('left','right','todo','current','a','b','pair','result','error')
    def __init__(self,left,right,synchronous=False):
        self.left,self.right,self.synchronous=left,right,synchronous
        self.phase='start';self.todo=[];self.seen=set();self.pure=set();self.effects=0
        self.current=self.a=self.b=self.pair=self.result=self.error=None;self.leaving=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .operators import NOT_IMPLEMENTED
        from .objects import ChaosInstance,owned
        from .hash_maps import HashMap,equal as hash_map_equal
        from .tuple_values import TupleValue
        from .set_values import SetValue
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='start':self.todo=[(self.left,self.right,None)];self.phase='walk'
        elif self.phase=='hooks':
            self.result=value
            if not (self.a is self.b if self.result is NOT_IMPLEMENTED else self.result):return Done(False)
            self.phase='walk'
        elif self.phase=='dependency':
            self.result=value
            if not self.result:return Done(False)
            self.phase='walk'
        while self.todo:
            e.tick();self.current=self.todo.pop();self.a,self.b,self.leaving=self.current
            a,b=self.a,self.b
            if self.leaving is not None:
                self.pair=(id(a),id(b));self.seen.remove(self.pair)
                if self.effects==self.leaving:self.pure.add(self.pair)
                continue
            owned(a,e);owned(b,e)
            if isinstance(a,ChaosInstance) or isinstance(b,ChaosInstance):
                self.effects+=1;self.phase='hooks'
                return Push(OperatorCompareFrame('==',a,b,self.synchronous))
            if type(a) is HashMap or type(b) is HashMap:
                self.effects+=1
                if type(a) is not type(b):return Done(False)
                self.phase='dependency';return self.leaf(hash_map_equal(e,a,b))
            if type(a) is not type(b):return Done(False)
            if type(a) is SetValue:
                from .set_runtime import algebra
                if a.owner is not None or b.owner is not None:self.effects+=1
                self.phase='dependency';return self.leaf(algebra(e,'==',a,b))
            if isinstance(a,(list,dict,TupleValue)):
                self.pair=(id(a),id(b))
                if self.pair in self.seen or self.pair in self.pure:continue
                self.seen.add(self.pair);self.todo.append((a,b,self.effects))
                if len(a)!=len(b):return Done(False)
                if isinstance(a,(list,TupleValue)):
                    self.todo.extend((x,y,None)for x,y in reversed(list(zip(a,b))))
                else:
                    if a.keys()!=b.keys():return Done(False)
                    self.todo.extend((a[key],b[key],None)for key in reversed(list(a)))
            elif not e.equal(a,b):return Done(False)
        self.phase='done';return Done(True)


class OperatorBinaryFrame(_OperatorFrame):
    ROOTS=('left','right','candidates','receiver','other','result','error')
    def __init__(self,operation,left,right):
        self.operation,self.left,self.right=operation,left,right;self.phase='start'
        self.candidates=[];self.index=0;self.method=None
        self.receiver=self.other=self.result=self.error=None
    def finish(self):
        from .operators import NOT_IMPLEMENTED
        from .language import LanguageError,serializable
        from .execution_frames import Done
        if self.result is NOT_IMPLEMENTED:raise LanguageError('操作数不支持运算 '+self.operation,code='argument_error')
        serializable(self.result);return Done(self.result)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .operators import primitive,candidates,ARITHMETIC,COMPARISON,NOT_IMPLEMENTED
        from .set_values import SetValue
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='direct':self.result=value;return Done(value)
        if self.phase=='not_equal':self.result=value;return Done(not value)
        if self.phase=='ne':
            self.result=value
            if value is not NOT_IMPLEMENTED:return Done(value)
            self.phase='not_equal';return Push(OperatorEqualFrame(self.left,self.right))
        if self.phase=='compare':
            self.result=value
            if value is NOT_IMPLEMENTED:self.result=primitive(self.operation,self.left,self.right,e.numeric_checkpoint)
            return self.finish()
        if self.phase=='invoke':
            self.result=value
            if value is not NOT_IMPLEMENTED:return self.finish()
            self.index+=1;self.phase='choose'
        if self.phase=='start':
            if type(self.left) is SetValue and type(self.right) is SetValue and self.operation in ('|','&','-','^','<','<=','>','>='):
                from .set_runtime import algebra
                self.phase='direct';return self.leaf(algebra(e,self.operation,self.left,self.right))
            if self.operation=='==':self.phase='direct';return Push(OperatorEqualFrame(self.left,self.right))
            if self.operation=='!=':self.phase='ne';return Push(OperatorCompareFrame('!=',self.left,self.right))
            if self.operation in COMPARISON:
                self.phase='compare';return Push(OperatorCompareFrame(self.operation,self.left,self.right))
            self.result=primitive(self.operation,self.left,self.right,e.numeric_checkpoint)
            if self.result is not NOT_IMPLEMENTED:return Done(self.result)
            self.candidates=candidates(self.left,self.right,*ARITHMETIC[self.operation],e)
            self.phase='choose'
        if self.index>=len(self.candidates):return self.finish()
        self.receiver,self.method,self.other=self.candidates[self.index];self.phase='invoke'
        return Push(OperatorInvokeFrame(self.receiver,self.method,[self.other]))


class OperatorInplaceFrame(_OperatorFrame):
    ROOTS=('left','right','result','error')
    def __init__(self,operation,left,right):
        self.operation,self.left,self.right=operation,left,right
        self.phase='start';self.method=self.result=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .operators import INPLACE,slot,NOT_IMPLEMENTED
        from .objects import owned
        from .language import serializable
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='binary':self.result=value;return Done(value)
        if self.phase=='invoke':
            self.result=value
            if self.result is not NOT_IMPLEMENTED:serializable(self.result);return Done(self.result)
        if self.phase=='start':
            e.tick();owned(self.right,e);self.method=INPLACE[self.operation]
            if slot(self.left,self.method,e) is not None:
                self.phase='invoke';return Push(OperatorInvokeFrame(self.left,self.method,[self.right]))
        self.phase='binary';return Push(OperatorBinaryFrame(self.operation,self.left,self.right))


class OperatorOperationFrame(_OperatorFrame):
    ROOTS=('operands','value','item','member','result','constant_site','error')
    def __init__(self,operation,operands,constant_site=None):
        self.operation,self.operands,self.constant_site=operation,operands,constant_site
        self.phase='start';self.value=self.item=self.member=self.result=self.error=None
        self.index=0;self.method=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .operators import ARITHMETIC,COMPARISON,slot,NOT_IMPLEMENTED
        from .language import LanguageError,ResourceLimit,check_type,serializable
        from .objects import ChaosInstance,owned
        from .set_values import SetValue
        from .tuple_values import TupleValue
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='direct':self.result=value;return Done(value)
        if self.phase=='unary':
            self.result=value
            if value is NOT_IMPLEMENTED:raise LanguageError('对象不支持 '+self.operation,code='argument_error')
            serializable(self.result);return Done(self.result)
        if self.phase=='length':
            self.result=check_type(value,'Int')
            if self.result<0:raise LanguageError('__len__ 必须返回非负 Int',code='argument_error')
            if self.result.bit_length()>14000:raise ResourceLimit('整数超过 14000 bit 资源限制')
            return Done(self.result)
        if self.phase in ('bool','contains'):
            self.result=check_type(value,'Bool');return Done(self.result)
        if self.phase=='length_bool':self.result=value;return Done(value!=0)
        if self.phase=='contains_equal':
            self.result=value
            if value:return Done(True)
            self.phase='contains_next'
        if self.phase=='contains_next':
            if self.index>=len(self.value):return Done(False)
            self.member=self.value[self.index];self.index+=1;self.phase='contains_equal'
            return Push(OperatorEqualFrame(self.member,self.item))
        e.tick()
        if self.constant_site is not None:
            from .portable_optimizer import ConstantSite
            if type(self.constant_site) is ConstantSite:
                handled,self.result=self.constant_site.match(self.operation,self.operands)
                if handled:e._literal_applied_operations+=1;return Done(self.result)
                e._literal_guard_misses+=1
        if self.operation in ARITHMETIC or self.operation in COMPARISON:
            self.phase='direct';return Push(OperatorBinaryFrame(self.operation,*self.operands))
        self.value=self.operands[0];owned(self.value,e)
        from .hash_maps import HashMap,Request as HashMapRequest,resolve as hash_map_resolve
        if type(self.value) is HashMap:
            if self.operation=='length':return Done(len(self.value))
            if self.operation=='bool':return Done(len(self.value)!=0)
            if self.operation=='contains':
                self.phase='direct';return self.leaf(hash_map_resolve(e,HashMapRequest('has',self.value,self.operands[1])))
        if self.operation in ('neg','abs','pos','invert'):
            if isinstance(self.value,ChaosInstance):
                self.method={'neg':'__neg__','abs':'__abs__','pos':'__pos__','invert':'__invert__'}[self.operation]
                if slot(self.value,self.method,e) is None:raise LanguageError('对象不支持 '+self.operation,code='argument_error')
                self.phase='unary';return Push(OperatorInvokeFrame(self.value,self.method,[]))
            from .numeric_values import Fraction,Complex,Decimal,unary as numeric_unary
            if type(self.value) in (Fraction,Complex,Decimal):self.result=numeric_unary(self.operation,self.value,e.numeric_checkpoint)
            else:
                check_type(self.value,'Int' if self.operation=='invert' else 'Number')
                self.result={'neg':lambda:-self.value,'abs':lambda:abs(self.value),'pos':lambda:+self.value,'invert':lambda:~self.value}[self.operation]()
            serializable(self.result);return Done(self.result)
        from .dynamic_contexts import Context,owned as context_owned,has as context_has
        if self.operation=='length' and type(self.value) is Context:return Done(len(context_owned(self.value,e).bindings))
        if self.operation=='contains' and type(self.value) is Context:return Done(context_has(e,self.value,self.operands[1]))
        if self.operation=='length':
            if isinstance(self.value,ChaosInstance):
                if slot(self.value,'__len__',e) is None:raise LanguageError('对象未定义 __len__',code='argument_error')
                self.phase='length';return Push(OperatorInvokeFrame(self.value,'__len__',[]))
            if type(self.value) is SetValue:return Done(len(self.value))
            if not isinstance(self.value,(list,dict,str,TupleValue)):raise LanguageError('length 需要 List、Tuple、Record、String 或 Instance',code='TypeError')
            return Done(len(self.value))
        if self.operation=='bool':
            if type(self.value) in (TupleValue,SetValue):return Done(len(self.value)!=0)
            if type(self.value) is bool:return Done(self.value)
            if isinstance(self.value,ChaosInstance):
                if slot(self.value,'__bool__',e) is not None:
                    self.phase='bool';return Push(OperatorInvokeFrame(self.value,'__bool__',[]))
                if slot(self.value,'__len__',e) is not None:
                    self.phase='length_bool';return Push(OperatorOperationFrame('length',(self.value,)))
            raise LanguageError('bool 需要 Bool 或显式 __bool__/__len__ 协议',code='argument_error')
        if self.operation=='contains':
            self.item=self.operands[1];owned(self.item,e)
            if type(self.value) is SetValue:
                from .set_runtime import contains
                self.phase='direct';return self.leaf(contains(e,self.value,self.item))
            if isinstance(self.value,ChaosInstance):
                if slot(self.value,'__contains__',e) is None:raise LanguageError('对象未定义 __contains__',code='argument_error')
                self.phase='contains';return Push(OperatorInvokeFrame(self.value,'__contains__',[self.item]))
            if isinstance(self.value,(str,dict)):return Done(check_type(self.item,'String') in self.value)
            if type(self.value) is not TupleValue:check_type(self.value,'List')
            self.phase='contains_next';return self.step(cursor,None,None)
        raise LanguageError('未知内部运算 '+self.operation)


OPERATOR_FRAMES=(OperatorInvokeFrame,OperatorCompareFrame,OperatorEqualFrame,OperatorBinaryFrame,OperatorInplaceFrame,OperatorOperationFrame)

def operator_frame(request):
    if type(request) is not OperatorFrameRequest:return None
    mapping=dict(zip(('invoke','compare','equal','binary','inplace','operation'),OPERATOR_FRAMES))
    return mapping[request.operation](*request.values)
