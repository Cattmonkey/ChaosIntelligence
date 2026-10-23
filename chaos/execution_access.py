"""Authoritative object access, item conversion and access-expression phases.

Pure MRO/storage selection stays in objects; HashMap and class construction
retain their separate protocol boundaries. No migrated access algorithm runs
inside an opaque whole-operation generator.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class AccessFrameRequest:
    operation: str
    values: tuple


def access_protocol(evaluator,operation,*values):
    from .execution_frames import request_proxy,ensure_cursor
    proxy=request_proxy(AccessFrameRequest(operation,values))
    if getattr(evaluator,'_execution_cursor',None) is not None:return proxy
    return ensure_cursor(evaluator,proxy,observer=True)


class _AccessFrame:
    capabilities=None
    ROOTS=()
    def ceiling(self,name):
        from .conditions import intersection
        from .execution_iteration import _caps
        caps=intersection(self.capabilities,getattr(self,'active_caps',None))
        if name in ('args','result') and getattr(self,'function',None) is not None:
            caps=intersection(caps,_caps(self.function))
        return caps
    def restore_anchor(self):
        runner=getattr(self,'authority_runner',None)
        if runner is None:return
        from .suspension import Runner
        from .execution_frames import ExecutionCursor
        from .language import LanguageError
        if (type(runner) is not Runner or type(runner.iterator) is not ExecutionCursor
                or runner.iterator.owner is not self.owner
                or getattr(self,'authority_epoch',None)!=self.owner.suspension.epoch):
            raise LanguageError('Access restoration runner cannot cross owner or epoch',code='ownership_error')
    def roots(self):
        from .capture_frames import Edge
        self.restore_anchor()
        saved=('previous_context',)
        result=[]
        for name in self.ROOTS+('previous_authority',):
            value=getattr(self,name,None)
            caps=getattr(self,'previous_authority',self.capabilities) if name in saved else self.ceiling(name)
            values=tuple(value) if name in ('args','parts') and type(value) in (tuple,list) else (value,)
            result.append(Edge(self.owner,caps,values))
        return tuple(result)
    def enter_authority(self,e,*ceilings):
        from .conditions import intersection
        self.authority_runner=e.suspension.current;self.authority_epoch=e.suspension.epoch
        self.previous_authority=getattr(self.authority_runner,'capabilities',None)
        self.active_caps=intersection(self.previous_authority,*ceilings)
        if self.authority_runner is not None:self.authority_runner.capabilities=self.active_caps
        self.authority_entered=True
    def retire(self,e):
        if getattr(self,'context_entered',False):
            self.context_entered=False;e.error_context=self.previous_context
        if getattr(self,'locked',False):self.locked=False;self.cursor.busy=False
        if getattr(self,'capture_entered',False):
            from .capture_frames import leave
            from .language import MISSING
            self.capture_entered=False;leave(self.capture,MISSING)
        if getattr(self,'authority_entered',False):
            self.authority_entered=False
            if self.authority_runner is not None:self.authority_runner.capabilities=self.previous_authority
    def done(self,e,value):
        from .execution_frames import Done
        self.result=value;self.retire(e);self.phase='done';return Done(value)
    def step(self,cursor,value,error):
        try:return self.advance(cursor,value,error)
        except BaseException:
            self.retire(cursor.owner);raise
    def close(self,cursor):self.retire(cursor.owner)
    def leaf(self,iterator):
        from .execution_frames import Push,ProtocolBridge
        frame=ProtocolBridge(iterator);frame.supplementary=True;return Push(frame)


class AttributeHookFrame(_AccessFrame):
    ROOTS=('target','actual','label','member','access_request','result')
    def __init__(self,target,actual,label):
        self.target,self.actual,self.label=target,actual,label
        self.phase='start';self.member=self.access_request=self.result=None
    def advance(self,cursor,value,error):
        from .objects import declaration,member_value,bind_member,ChaosClass
        from .attribute_protocols import NO_HOOK
        from .execution_frames import Push
        if error is not None:raise error
        if self.phase=='start':
            self.member=declaration(self.actual,self.label) if self.actual is not None else None
            if self.member is None:return self.done(cursor.owner,NO_HOOK)
            self.access_request=bind_member(member_value(self.member),self.member,self.target,self.actual,cursor.owner,instance_access=type(self.target) is ChaosClass)
            self.phase='access';return Push(AccessResultFrame(self.access_request))
        return self.done(cursor.owner,value)


class AttributeReadFrame(_AccessFrame):
    ROOTS=('target','actual','label','function','result','original','previous_context')
    def __init__(self,target,label,default=False):
        self.target,self.label,self.default=target,label,default
        self.phase='start';self.actual=self.function=self.result=self.original=self.previous_context=None
    def fallback(self,e,failure):
        from .language import LanguageError
        from .execution_frames import Push
        if not isinstance(failure,LanguageError) or failure.code!='attribute_error':raise failure
        self.original=failure;self.previous_context=e.error_context;e.error_context=failure;self.context_entered=True
        self.phase='fallback_hook';return Push(AttributeHookFrame(self.target,self.actual,'__getattr__'))
    def advance(self,cursor,value,error):
        from .attribute_protocols import name,kind,project,NO_HOOK
        from .objects import _project_callable
        from .execution_frames import Push,InvocationFrame,InvocationRequest
        e=cursor.owner
        if error is not None:
            if self.phase in ('hook','read','invoke'):return self.fallback(e,error)
            raise error
        if self.phase=='start':
            self.label=name(e,self.label);self.actual=kind(e,self.target)
            self.enter_authority(e,getattr(self.target,'capabilities',None),getattr(self.actual,'capabilities',None))
            if self.actual is None:self.phase='native';return Push(ScopedGetMemberFrame(self.target,self.label))
            if self.default:self.phase='default';return Push(ScopedGetMemberFrame(self.target,self.label))
            self.phase='hook';return Push(AttributeHookFrame(self.target,self.actual,'__getattribute__'))
        if self.phase=='native':return self.done(e,_project_callable(value,e,self.target))
        if self.phase in ('hook','fallback_hook'):
            self.function=value
            if value is NO_HOOK:
                if self.phase=='fallback_hook':raise self.original
                self.phase='read';return Push(ScopedGetMemberFrame(self.target,self.label))
            self.phase='fallback_invoke' if self.phase=='fallback_hook' else 'invoke'
            return Push(InvocationFrame(InvocationRequest(self.function,[self.label])))
        try:return self.done(e,project(e,value,self.target,self.actual))
        except BaseException as failure:
            if self.phase in ('read','invoke'):return self.fallback(e,failure)
            raise


class AttributeWriteFrame(_AccessFrame):
    ROOTS=('target','actual','label','item','function','result')
    def __init__(self,target,label,item=None,delete=False,default=False):
        self.target,self.label,self.item,self.delete,self.default=target,label,item,delete,default
        self.phase='start';self.actual=self.function=self.result=None
    def advance(self,cursor,value,error):
        from .attribute_protocols import name,kind,NO_HOOK
        from .class_creation import validate_value
        from .exception_classes import intrinsic
        from .language import LanguageError,check_type
        from .objects import require_callable
        from .execution_frames import Push,InvocationFrame,InvocationRequest
        e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            self.label=name(e,self.label);self.actual=kind(e,self.target)
            if not self.delete:validate_value(e,self.item)
            if intrinsic(e,self.target):raise LanguageError('内置类的成员不可修改',code='attribute_error')
            self.enter_authority(e,getattr(self.target,'capabilities',None),getattr(self.actual,'capabilities',None))
            self.phase='hook'
            if not self.default and self.actual is not None:return Push(AttributeHookFrame(self.target,self.actual,'__delattr__' if self.delete else '__setattr__'))
            value=NO_HOOK
        if self.phase=='hook':
            self.function=value
            if value is NO_HOOK:
                self.phase='raw';return Push(ScopedDeleteMemberFrame(self.target,self.label) if self.delete else ScopedSetMemberFrame(self.target,self.label,self.item))
            require_callable(value,e,setter=True);self.phase='invoke'
            return Push(InvocationFrame(InvocationRequest(value,[self.label] if self.delete else [self.label,self.item],options={'synchronous':True})))
        if self.phase=='invoke':check_type(value,'Null');return self.done(e,None if self.delete else self.item)
        return self.done(e,value)


class AttributeDirectoryFrame(_AccessFrame):
    ROOTS=('target','actual','raw_directory','function','source','cursor','step_value','result','capture')
    def __init__(self,target,raw_directory,default=False):
        self.target,self.raw_directory,self.default=target,raw_directory,default
        self.phase='start';self.actual=self.function=self.source=self.cursor=self.step_value=self.result=self.capture=None
        self.locked=False;self.chars=0
    def advance(self,cursor,value,error):
        from .attribute_protocols import kind,NO_HOOK,directory_hook
        from .language import LanguageError,ResourceLimit,serializable
        from .suspension import Task,AsyncGenerator
        from .iteration import AsyncIterator
        from .capture_frames import enter,retain
        from .class_creation import validate_value
        from .execution_frames import Push
        e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            self.actual=kind(e,self.target)
            self.enter_authority(e,getattr(self.target,'capabilities',None),getattr(self.actual,'capabilities',None));self.phase='hook'
            if not self.default and self.actual is not None:return Push(AttributeHookFrame(self.target,self.actual,'__dir__'))
            value=NO_HOOK
        if self.phase=='hook':
            self.function=value
            if value is NO_HOOK:return self.done(e,self.raw_directory(self.target))
            directory_hook(e,value);self.phase='invoke'
            from .execution_frames import InvocationFrame,InvocationRequest
            return Push(InvocationFrame(InvocationRequest(value,[])))
        if self.phase=='invoke':
            self.source=value
            if type(value) in (Task,AsyncGenerator,AsyncIterator):raise LanguageError('__dir__ requires a synchronous iterable, without implicit await',code='argument_error')
            validate_value(e,value);self.capture=enter(e,'attribute_directory',self.target,self.function,self.source);self.capture_entered=True
            self.phase='acquire';return self.leaf(e.suspension.iteration.acquire(self.source,temporary=True))
        if self.phase=='acquire':
            self.cursor=value;retain(self.capture,value)
            if value.busy:raise LanguageError('Directory iterator cannot be re-entered',code='argument_error')
            value.busy=True;self.locked=True;self.result=[];self.chars=0;self.phase='step'
        elif self.phase=='step':
            self.step_value=value
            if value['done']:return self.done(e,sorted(self.result))
            if len(self.result)>=10000:raise ResourceLimit('Directory exceeds 10000 names')
            item=value['value']
            if type(item) is not str:raise LanguageError('__dir__ members require exact String names',code='argument_error')
            e.tick();self.chars+=len(item)
            if self.chars>250000:raise ResourceLimit('Directory text exceeds 250000 characters')
            serializable(item);self.result.append(item)
        return self.leaf(e.suspension.iteration.step(self.cursor,locked=True))


class AccessResultFrame(_AccessFrame):
    ROOTS=('value','access_request','function','args','result')
    def __init__(self,value,protocol_depth=0):
        self.value=value;self.protocol_depth=protocol_depth;self.phase='start'
        self.access_request=self.function=self.args=self.result=None
    def advance(self,cursor,value,error):
        from .objects import AccessorCall,ProtocolCall,special_method,require_callable
        from .language import ResourceLimit,check_type
        from .execution_frames import Push,InvocationFrame,InvocationRequest
        e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            if not isinstance(self.value,(AccessorCall,ProtocolCall)):return self.done(e,self.value)
            e.tick()
            if self.protocol_depth>=60:raise ResourceLimit('描述符协议解析深度超过 60')
            if isinstance(self.value,ProtocolCall):
                self.access_request=special_method(self.value.descriptor,self.value.name,e);self.phase='function'
                return Push(AccessResultFrame(self.access_request,self.protocol_depth+1))
            value=self.value.function;self.phase='function'
        if self.phase=='function':
            self.function=value
            if self.value.write:require_callable(value,e,setter=True)
            self.enter_authority(e,getattr(self.value,'capabilities',None));self.args=self.value.args;self.phase='invoke'
            return Push(InvocationFrame(InvocationRequest(value,self.args,options={'synchronous':self.value.write})))
        if self.value.write:e.tick();check_type(value,'Null');value=self.value.item
        return self.done(e,value)


class ItemKeyFrame(_AccessFrame):
    ROOTS=('node','env','parts','result')
    def __init__(self,node,env,flow_parent=None,flow_path=()):
        self.node,self.env=node,env;self.phase='start';self.parts=[];self.index=0;self.result=None
        self._flow_access_parent=flow_parent;self._flow_access_path=flow_path
    def step(self,cursor,value,error):
        if self._flow_access_parent is not None:
            from .compiled_flow_access import key_step
            return key_step(self,cursor,value,error,super().step)
        return super().step(cursor,value,error)
    def evaluate(self,node):
        if self._flow_access_parent is not None:
            from .compiled_flow_access import evaluate
            return evaluate(self,node)
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,self.env)))
    def advance(self,cursor,value,error):
        from .language import ResourceLimit
        from .tuple_values import TupleValue
        from .indexing import IndexRequest
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        e=cursor.owner
        if error is not None:raise error
        if self.phase=='scalar' or self.phase=='resolve':return self.done(e,value)
        if self.phase=='part':self.parts.append(value);self.index+=1;self.phase='next'
        if self.phase=='start':
            if self.node[0]=='tuple':
                if len(self.node[1])>10000:raise ResourceLimit('Tuple 超过 10000 项限制')
            elif self.node[0]!='slice':self.phase='scalar';return self.evaluate(self.node)
            self.phase='next'
        items=self.node[1] if self.node[0]=='tuple' else self.node[1:]
        while self.index<len(items):
            part=items[self.index]
            if part is None:self.parts.append(None);self.index+=1;continue
            self.phase='part'
            if self.node[0]=='tuple' and part[0]=='slice':return Push(ItemKeyFrame(part,self.env,self._flow_access_parent,self._flow_access_path+(self.index,)))
            return self.evaluate(part)
        if self.node[0]=='tuple':return self.done(e,TupleValue(self.parts))
        self.phase='resolve';return Push(IndexResolveFrame(IndexRequest('slice',tuple(self.parts))))


class LegacyIndexFrame(_AccessFrame):
    ROOTS=('value','index','result')
    def __init__(self,value,index):self.value,self.index=value,index;self.phase='start';self.result=None
    def advance(self,cursor,value,error):
        from . import language as L
        from .objects import ChaosClass,ChaosInstance,ClassMember,UIArtifact,SuperProxy,DESCRIPTORS
        from .indexing import Slice
        from .task_groups import TaskGroup
        from .cancellation import CancelShield
        from .resource_stacks import STACKS
        from .conditions import VALUES as RECOVERY_VALUES
        from .execution_frames import Push
        e=cursor.owner
        if error is not None:raise error
        if self.phase!='start':return self.done(e,value)
        if type(self.value) is L.Context and type(self.index) is L.ContextVar:
            from .dynamic_contexts import get_in
            return self.done(e,get_in(e,self.value,self.index))
        if type(self.value) in (L.Error,L.Condition,L.Fraction,L.Complex,L.Decimal,L.DecimalContext,Slice,TaskGroup,CancelShield)+STACKS+RECOVERY_VALUES+L.CONTEXT_VALUES+(L.PromptTag,L.PromptScope,L.EscapeContinuation,L.Continuation)+L.ROBOT_VALUES or isinstance(self.value,(ChaosClass,ChaosInstance,ClassMember,UIArtifact,SuperProxy)+DESCRIPTORS):
            self.phase='member';return Push(AttributeReadFrame(self.value,self.index))
        if not isinstance(self.value,(str,list,dict,L.TupleValue)):raise L.LanguageError('仅可索引 List、Tuple、String、Record')
        if isinstance(self.value,dict) and not isinstance(self.index,str):raise L.LanguageError('Record 索引必须为 String')
        if isinstance(self.value,(str,list,L.TupleValue)) and (type(self.index) is not int or self.index<0):raise L.LanguageError('序列索引必须为非负 Int')
        return self.done(e,self.value[self.index])


class ScopedGetMemberFrame(_AccessFrame):
    ROOTS=('target','name','access_request','result')
    def __init__(self,target,name):self.target,self.name=target,name;self.phase='start';self.access_request=self.result=None
    def advance(self,cursor,value,error):
        from . import language as L
        from .execution_frames import Push
        e=cursor.owner;t=self.target;n=self.name
        if error is not None:raise error
        if self.phase!='start':return self.done(e,value)
        if type(t) is L.Bot:
            from .bot_values import owned
            owned(t,e);raise L.LanguageError('Bot has no public live members',code='attribute_error')
        if type(t) in L.ROBOT_VALUES:
            from .robot_values import member
            return self.done(e,member(e,t,n))
        if type(t) in (L.PromptTag,L.PromptScope,L.EscapeContinuation,L.Continuation):
            from .control_prompts import member
            return self.done(e,member(e,t,n))
        if type(t) is L.Error:
            from .error_values import member
            return self.done(e,member(t,n,e))
        if type(t) is L.DecimalContext:
            from .decimal_values import member
            return self.done(e,member(t,n,e))
        if type(t) in (L.Fraction,L.Complex,L.Decimal):
            from .numeric_values import member
            return self.done(e,member(t,n))
        from .resource_stacks import STACKS,get_member as stack_member
        if type(t) in STACKS:return self.done(e,stack_member(t,n,e))
        from .cancellation import CancelShield,owned
        if type(t) is CancelShield:owned(t,e);raise L.LanguageError('CancelShield 没有公开成员',code='attribute_error')
        from .task_groups import TaskGroup,get_member
        if type(t) is TaskGroup:return self.done(e,get_member(t,n,e))
        from .indexing import Slice
        if type(t) is Slice:
            if n not in ('start','stop','step'):raise L.LanguageError('Slice 成员不存在',code='attribute_error')
            return self.done(e,{'start':t.start,'stop':t.stop,'step':t.step}[n])
        from .objects import get_member
        self.access_request=get_member(t,n,e);self.phase='access';return Push(AccessResultFrame(self.access_request))


def _readonly(e,t):
    from . import language as L
    if type(t) in L.ROBOT_VALUES:
        from .robot_values import owned
        owned(t,e);raise L.LanguageError('Robot members are read-only',code='attribute_error')
    if type(t) in (L.PromptTag,L.PromptScope,L.EscapeContinuation,L.Continuation):
        from .control_prompts import owned
        owned(t,e);raise L.LanguageError('Control members are read-only',code='attribute_error')
    if type(t) is L.Error:
        from .error_values import owned
        owned(t,e);raise L.LanguageError('Error 成员只读',code='attribute_error')
    from .resource_stacks import STACKS,owned
    if type(t) in STACKS:owned(t,e);raise L.LanguageError('ExitStack 成员只读',code='attribute_error')
    from .cancellation import CancelShield,owned
    if type(t) is CancelShield:owned(t,e);raise L.LanguageError('CancelShield 成员只读',code='attribute_error')
    from .task_groups import TaskGroup,owned
    if type(t) is TaskGroup:owned(t,e);raise L.LanguageError('TaskGroup 成员只读',code='attribute_error')
    from .indexing import Slice
    if type(t) is Slice:raise L.LanguageError('Slice 字段只读',code='attribute_error')


class ScopedSetMemberFrame(_AccessFrame):
    ROOTS=('target','name','item','access_request','result')
    def __init__(self,target,name,item):self.target,self.name,self.item=target,name,item;self.phase='start';self.access_request=self.result=None
    def advance(self,cursor,value,error):
        from .execution_frames import Push
        if error is not None:raise error
        if self.phase!='start':return self.done(cursor.owner,value)
        _readonly(cursor.owner,self.target)
        from .objects import set_member
        self.access_request=set_member(self.target,self.name,self.item,cursor.owner);self.phase='access';return Push(AccessResultFrame(self.access_request))


class ScopedDeleteMemberFrame(_AccessFrame):
    ROOTS=('target','name','access_request','result')
    def __init__(self,target,name):self.target,self.name=target,name;self.phase='start';self.access_request=self.result=None
    def advance(self,cursor,value,error):
        from .execution_frames import Push
        if error is not None:raise error
        if self.phase!='start':return self.done(cursor.owner,value)
        _readonly(cursor.owner,self.target)
        from .objects import delete_member
        self.access_request=delete_member(self.target,self.name,cursor.owner);self.phase='access';return Push(AccessResultFrame(self.access_request))


class IntegerIndexFrame(_AccessFrame):
    ROOTS=('value','access_request','function','result')
    def __init__(self,value):self.value=value;self.phase='start';self.access_request=self.function=self.result=None
    def advance(self,cursor,value,error):
        from .objects import ChaosInstance,owned,declaration,special_method,require_callable
        from .language import LanguageError,ResourceLimit
        from .execution_frames import Push,InvocationFrame,InvocationRequest
        e=cursor.owner
        if error is not None:raise error
        if self.phase=='start':
            e.tick();owned(self.value,e)
            if type(self.value) is not int:
                if not isinstance(self.value,ChaosInstance):raise LanguageError('index 需要 Int 或定义 __index__ 的 Instance',code='argument_error')
                owned(self.value.klass,e)
                if declaration(self.value.klass,'__index__') is None:raise LanguageError('对象未定义 __index__',code='argument_error')
                self.access_request=special_method(self.value,'__index__',e);self.phase='function';return Push(AccessResultFrame(self.access_request))
        elif self.phase=='function':
            self.function=value;require_callable(value,e,setter=True);self.phase='invoke';return Push(InvocationFrame(InvocationRequest(value,[],options={'synchronous':True})))
        elif self.phase=='invoke':
            self.value=value;e.tick()
            if type(value) is not int:raise LanguageError('__index__ 必须返回精确 Int，不能返回 Bool、Number 或挂起值',code='argument_error')
        if self.value.bit_length()>14000:raise ResourceLimit('index 整数超过 14000 bit')
        return self.done(e,self.value)


class IndexResolveFrame(_AccessFrame):
    ROOTS=('request','parts','result')
    def __init__(self,request):self.request=request;self.phase='start';self.parts=[];self.index=0;self.result=None
    def advance(self,cursor,value,error):
        from .indexing import Slice,slice_indices
        from .language import LanguageError
        from .execution_frames import Push
        e=cursor.owner;r=self.request
        if error is not None:raise error
        if self.phase=='index':return self.done(e,value)
        if self.phase=='indices':return self.done(e,slice_indices(r.values[0],value))
        if self.phase=='part':self.parts.append(value);self.index+=1;self.phase='slice'
        if self.phase=='start':
            if r.operation=='index':self.phase='index';return Push(IntegerIndexFrame(r.values[0]))
            if r.operation=='indices':
                if type(r.values[0]) is not Slice:raise LanguageError('slice_indices 需要 Slice',code='argument_error')
                self.phase='indices';return Push(IntegerIndexFrame(r.values[1]))
            if r.operation!='slice':raise LanguageError('未知整数索引操作',code='argument_error')
            self.phase='slice'
        while self.index<len(r.values):
            item=r.values[self.index]
            if item is None:self.parts.append(None);self.index+=1;continue
            self.phase='part';return Push(IntegerIndexFrame(item))
        return self.done(e,Slice(*self.parts))


class ItemInvokeFrame(_AccessFrame):
    ROOTS=('function','request','args','result')
    def __init__(self,function,request):self.function,self.request=function,request;self.phase='start';self.args=self.result=None
    def advance(self,cursor,value,error):
        from .objects import require_callable
        from .indexing import validate_owned
        from .language import check_type,serializable
        from .execution_frames import Push,InvocationFrame,InvocationRequest
        e=cursor.owner;r=self.request;write=r.operation!='get'
        if error is not None:raise error
        if self.phase=='start':self.phase='access';return Push(AccessResultFrame(self.function))
        if self.phase=='access':
            self.function=value
            if write:require_callable(value,e,setter=True)
            self.args=[r.key,r.item] if r.operation=='set' else [r.key];self.phase='invoke'
            return Push(InvocationFrame(InvocationRequest(value,self.args,options={'synchronous':write})))
        e.tick()
        if write:return self.done(e,check_type(value,'Null'))
        serializable(value);validate_owned(e,value);return self.done(e,value)


class ItemOperationFrame(_AccessFrame):
    ROOTS=('request','target','key','item','member','function','result')
    def __init__(self,request):
        self.request=request;self.target,self.key,self.item=request.target,request.key,request.item
        self.phase='start';self.member=self.function=self.result=None
    def advance(self,cursor,value,error):
        from .objects import ChaosClass,ChaosInstance,SuperProxy,DESCRIPTORS,owned,declaration,special_method,bind_member,member_value
        from .indexing import validate_owned,native_get,Slice
        from .language import LanguageError
        from .execution_frames import Push
        e=cursor.owner;t=self.target
        from .indexing import ItemRequest
        r=ItemRequest(self.request.operation,t,self.key,self.item,self.request.fallback)
        if error is not None:raise error
        if self.phase in ('call','hash','write'):return self.done(e,value)
        if self.phase=='read':validate_owned(e,value);return self.done(e,value)
        if self.phase=='class_getitem':self.function=value;self.phase='call';return Push(ItemInvokeFrame(value,r))
        if self.phase=='integer':self.key=value;self.phase='native'
        if self.phase=='start':
            e.tick();owned(t,e);validate_owned(e,self.key)
            if r.operation!='get':validate_owned(e,self.item)
            from .hash_maps import HashMap,Request as HashMapRequest,resolve
            if type(t) is HashMap:
                if r.operation!='get':raise LanguageError('HashMap 为持久值；请使用 hash_set/hash_remove 返回新映射',code='argument_error')
                self.phase='hash';return self.leaf(resolve(e,HashMapRequest('index',t,self.key)))
            label={'get':'__getitem__','set':'__setitem__','delete':'__delitem__'}[r.operation] if isinstance(t,(ChaosInstance,ChaosClass)) else None
            if isinstance(t,ChaosInstance):
                owned(t.klass,e);self.member=declaration(t.klass,label)
                if self.member is not None:self.function=special_method(t,label,e);self.phase='call';return Push(ItemInvokeFrame(self.function,r))
                if r.operation=='get':self.phase='read';return Push(AttributeReadFrame(t,self.key))
                if r.operation=='set' and r.fallback:self.phase='write';return Push(_ordinary_write(e,t,self.key,self.item))
                raise LanguageError('对象未定义 '+label,code='argument_error')
            if isinstance(t,ChaosClass):
                meta=owned(t.metaclass,e);self.member=declaration(meta,label)
                if self.member is not None:
                    self.enter_authority(e,t.capabilities);self.function=bind_member(member_value(self.member),self.member,t,meta,e,instance_access=True)
                    self.phase='call';return Push(ItemInvokeFrame(self.function,r))
                if r.operation=='get' and (declaration(t,'__class_getitem__') is not None or declaration(meta,'__class_getitem__') is not None):
                    self.phase='class_getitem';return Push(AttributeReadFrame(t,'__class_getitem__',True))
            if isinstance(t,(ChaosClass,SuperProxy)+DESCRIPTORS):
                if r.operation=='get':self.phase='read';return Push(AttributeReadFrame(t,self.key))
                if r.operation=='set' and r.fallback:self.phase='write';return Push(_ordinary_write(e,t,self.key,self.item))
                raise LanguageError('显式项目修改需要 Instance 协议',code='argument_error')
            from .dynamic_contexts import Context,ContextVar,get_in
            if type(t) is Context and type(self.key) is ContextVar and r.operation=='get':return self.done(e,get_in(e,t,self.key))
            if r.operation!='get':raise LanguageError('原生 List/Tuple/String/Record 是值式容器，不支持项目修改',code='argument_error')
            from .tuple_values import TupleValue
            if type(t) in (list,str,TupleValue) and type(self.key) is not Slice:self.phase='integer';return Push(IntegerIndexFrame(self.key))
        result=native_get(t,self.key);validate_owned(e,result);return self.done(e,result)


def _ordinary_write(e,target,name,item):
    from .bot_values import Bot,owned
    from .language import LanguageError
    if type(target) is Bot:
        owned(target,e);raise LanguageError('Bot has no writable members',code='attribute_error')
    return AttributeWriteFrame(target,name,item)


class ObjectAccessEvaluationFrame(_AccessFrame):
    KINDS={'subscript','set_item','set_member','index','aug_assign'}
    ROOTS=('env','target','key','item','previous','right','location','result')
    def __init__(self,boundary):
        self.boundary=boundary;self.node=boundary.node;self.env=boundary.env;self.options=boundary.options
        self.phase='start';self.target=self.key=self.item=self.previous=self.right=self.location=self.result=None
        if getattr(boundary,'flow_structural',False):
            from .compiled_flow_access import attach
            attach(self,boundary)
    def step(self,cursor,value,error):
        if getattr(self,'_flow_boundary',None) is not None:
            from .compiled_flow_access import step
            return step(self,cursor,value,error,super().step)
        return super().step(cursor,value,error)
    def key_frame(self,node):
        frame=ItemKeyFrame(node,self.env,self if getattr(self,'_flow_boundary',None) is not None else None)
        if getattr(self,'_flow_boundary',None) is not None:self._flow_access_key_root=frame
        return frame
    def child(self,node,regions=False):
        if getattr(self,'_flow_boundary',None) is not None:
            from .compiled_flow_access import evaluate
            return evaluate(self,node,regions=regions)
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        options={}
        if regions and self.options.get('_compiled_regions') is not None:options['_compiled_regions']=self.options['_compiled_regions']
        return Push(EvaluationBoundaryFrame(EvaluationRequest(node,self.env,options=options)))
    def advance(self,cursor,value,error):
        from .execution_frames import Push
        from .indexing import ItemRequest
        from .language import check_type
        from .capture_frames import retain
        e=cursor.owner;k=self.node[0];node=self.node
        if error is not None:raise error
        if self.phase=='start':
            e.tick()
            if k=='aug_assign':
                self.target_node,self.operator,self.rhs=node[1:];self.target_kind=self.target_node[0]
                if self.target_kind=='name':
                    self.location=self.env.resolve_cell(self.target_node[1]);self.phase='previous'
                    from .metaclasses import lookup_name
                    return self.leaf(lookup_name(e,self.env,self.target_node[1]))
                self.phase='target';return self.child(self.target_node[1])
            self.phase='target';return self.child(node[1])
        if self.phase=='target':
            self.target=value;self.phase='key'
            if k=='aug_assign':return Push(self.key_frame(self.target_node[2]))
            if k in ('subscript','set_item'):return Push(self.key_frame(node[2]))
            return self.child(node[2])
        if self.phase=='key':
            self.key=value
            if k=='aug_assign':self.phase='previous';return Push(ItemOperationFrame(ItemRequest('get',self.target,self.key)) if self.target_kind=='subscript' else LegacyIndexFrame(self.target,self.key))
            if k in ('set_item','set_member'):self.phase='item';return self.child(node[3])
            self.phase='finish';return Push(ItemOperationFrame(ItemRequest('get',self.target,self.key,None,True)) if k=='subscript' else LegacyIndexFrame(self.target,self.key))
        if self.phase=='item':
            self.item=value;self.phase='finish';return Push(ItemOperationFrame(ItemRequest('set',self.target,self.key,self.item,True)) if k=='set_item' else _ordinary_write(e,self.target,self.key,self.item))
        if self.phase=='previous':
            self.previous=value;retain(self.boundary.capture,value);self.phase='right';return self.child(self.rhs,True)
        if self.phase=='right':
            self.right=value;self.phase='operation'
            from .operators import inplace
            return self.leaf(inplace(e,self.operator,self.previous,self.right))
        if self.phase=='operation':
            self.item=value;e.tick();self.phase='assigned'
            if self.target_kind=='name':
                from .metaclasses import assign_name
                return self.leaf(assign_name(e,self.env,self.target_node[1],check_type(value,self.location.contract)))
            return Push(ItemOperationFrame(ItemRequest('set',self.target,self.key,value,True)) if self.target_kind=='subscript' else _ordinary_write(e,self.target,self.key,value))
        if self.phase=='assigned':return self.done(e,self.item)
        return self.done(e,value)


ACCESS_FRAMES=(AttributeHookFrame,AttributeReadFrame,AttributeWriteFrame,AttributeDirectoryFrame,
    AccessResultFrame,ItemKeyFrame,LegacyIndexFrame,ScopedGetMemberFrame,ScopedSetMemberFrame,
    ScopedDeleteMemberFrame,IntegerIndexFrame,IndexResolveFrame,ItemInvokeFrame,ItemOperationFrame,
    ObjectAccessEvaluationFrame)

def access_frame(request):
    if type(request) is not AccessFrameRequest:return None
    if request.operation=='evaluate':return ObjectAccessEvaluationFrame(*request.values)
    mapping=dict(zip(('hook','read','write','directory','access','item_key','legacy_index','scoped_get','scoped_set','scoped_delete','integer','resolve','invoke_item','item'),ACCESS_FRAMES[:-1]))
    return mapping[request.operation](*request.values)
