"""Authoritative dynamic context and resource protocol execution phases."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ContextProtocolRequest:
    operation: str
    values: tuple

def context_gateway(evaluator,request):
    from .execution_frames import request_proxy,ensure_cursor
    proxy=request_proxy(request)
    if getattr(evaluator,'_execution_cursor',None) is not None:return proxy
    return ensure_cursor(evaluator,proxy,observer=True)

def context_protocol(evaluator,operation,*values):
    return context_gateway(evaluator,ContextProtocolRequest(operation,values))

def _share(e,task):
    from .conditions import active_state as recovery
    from .control_prompts import active_state as control
    from .dynamic_contexts import active_state as context
    s=e.suspension
    task.runner.condition_state=recovery(s);task.runner.control_state=control(s)
    task.runner.context_state=context(s);task.runner.error_context=e.error_context
    task.recovery_protocol=True

class _ResourceFrame:
    ROOTS=()
    capabilities=None
    def ceiling(self,name):
        from .conditions import intersection
        ceiling=self.capabilities
        if getattr(self,'authority_entered',False):ceiling=intersection(ceiling,self.stack.capabilities)
        return ceiling
    def roots(self):
        from .capture_frames import Edge
        saved_names=('authority_runner','previous_authority','previous_team','previous_context','previous','state','current')
        roots=[];saved=[]
        for name in self.ROOTS:
            value=getattr(self,name,None)
            values=tuple(value) if type(value) is tuple else (value,)
            if name in saved_names:saved.extend(values)
            else:roots.append(Edge(self.owner,self.ceiling(name),values))
        return tuple(roots)+(Edge(self.owner,getattr(self,'previous_authority',self.capabilities),tuple(saved)),)
    def enter_authority(self,e,stack):
        from .conditions import intersection
        self.authority_runner=e.suspension.current;self.previous_authority=self.previous_team=None
        if self.authority_runner is not None:
            self.previous_authority=self.authority_runner.capabilities;self.previous_team=self.authority_runner.team_scope
            self.authority_runner.capabilities=intersection(self.previous_authority,stack.capabilities)
            if stack.team_scope is not None:self.authority_runner.team_scope=stack.team_scope
        self.authority_entered=True
    def release_authority(self):
        if getattr(self,'authority_entered',False):
            self.authority_entered=False
            if self.authority_runner is not None:
                self.authority_runner.capabilities=self.previous_authority;self.authority_runner.team_scope=self.previous_team
    def leaf(self,iterator):
        from .execution_frames import Push,ProtocolBridge
        bridge=ProtocolBridge(iterator);bridge.supplementary=True
        return Push(bridge)
    def close(self,cursor):self.release_authority()

class ContextRunFrame(_ResourceFrame):
    ROOTS=('request','context','function','args','kwargs','state','previous','result','error')
    def __init__(self,request):
        self.request=request;self.context=request.context;self.function=request.function
        self.args=request.args;self.kwargs=request.kwargs;self.state=self.previous=self.result=self.error=None
        self.phase='start';self.entered=False
    def ceiling(self,name):
        from .conditions import intersection
        from .execution_iteration import _caps
        return intersection(self.capabilities,_caps(self.function)) if name in ('args','kwargs','result') else self.capabilities
    def retire(self):
        if self.entered:self.entered=False;self.state.current=self.previous;self.context.active=False
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,InvocationFrame,InvocationRequest
        from .dynamic_contexts import owned,Context,active_state,error as fail
        e=cursor.owner
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='start':
                self.context=owned(self.context,e,Context)
                if self.context.active:fail('Context 已在运行','context_busy')
                self.state=active_state(e.suspension);self.previous=self.state.current
                if self.context is self.previous:fail('不能重入当前 Context','context_busy')
                self.context.active=True;self.state.current=self.context;self.entered=True;self.phase='invoke'
                return Push(InvocationFrame(InvocationRequest(self.function,self.args,self.kwargs,{'force':True,'_context_callback':True,'all_results':True})))
            self.result=value;self.retire();return Done(value)
        except BaseException:self.retire();raise
    def close(self,cursor):self.retire()

class ResourceAwaitFrame(_ResourceFrame):
    ROOTS=('task','current','group','source','original','drained','result','error')
    def __init__(self,task,protocol_owned=False,cleanup_operation=False,all_results=False):
        self.task,self.protocol_owned,self.cleanup_operation,self.all_results=task,protocol_owned,cleanup_operation,all_results
        self.phase='start';self.current=self.group=self.source=self.original=self.drained=self.result=self.error=None
        self.structured=self.same_group=False;self.entered=False
    def failure(self,e,error):
        from .suspension import TERMINAL,Drain
        from .task_groups import GroupAbort,TaskGroup
        from .execution_frames import External
        self.original=error;s=e.suspension
        if self.structured:
            if self.protocol_owned and self.task.state not in TERMINAL:
                self.group=self.current.scope_group or self.current.group
                self.same_group=(type(self.group) is TaskGroup and self.group.owner is s and self.group.epoch==s.epoch and self.current.owner is s and self.current.epoch==s.epoch and self.task.group is self.group)
                self.source=self.group if self.same_group else object()
                if isinstance(error,GroupAbort) and (self.cleanup_operation or getattr(self.task,'stack_operation_kind',None)=='aclose'):s.interrupt(self.task,error)
                else:s._cancel(self.task,self.source)
                self.phase='drain';return External(Drain(self.task),native_leaf=True)
            raise error
        if self.task.state not in TERMINAL:s.cancel(self.task)
        raise error
    def step(self,cursor,value,error):
        from .execution_frames import External,Done
        from .suspension import Task,Await,error_priority
        from .multiple_values import primary,prepare
        e=cursor.owner;s=e.suspension
        if self.phase=='start':
            if error is not None:raise error
            s.validate(self.task,Task);self.current=s.current.task if s.current else None
            c=self.current
            self.structured=c is not None and (c.scope_group is not None or c.group is not None or c.cancel_shields or getattr(c,'context_structured',False) or getattr(c,'stack_operation',None) is not None)
            self.entered=True;self.phase='await';return External(Await(self.task),native_leaf=True)
        if self.phase=='drain':
            self.drained=error
            if error is not None:
                internal=s._local_cancel(error,self.task) and (self.task.cancel_sources=={self.source} or self.same_group and self.group._internal_cancel(self.task,error))
                if not internal and error_priority(error)>error_priority(self.original):raise error
            raise self.original
        try:
            if error is not None:self.error=error;raise error
            self.result=prepare(e,value);return Done(self.result if self.all_results else primary(self.result,e))
        except BaseException as failure:return self.failure(e,failure)
    def close(self,cursor):
        if self.entered and self.phase=='await':
            from .execution_frames import External
            try:action=self.failure(cursor.owner,GeneratorExit())
            except GeneratorExit:return
            if type(action) is External:raise RuntimeError('generator ignored GeneratorExit')
        elif self.phase=='drain':
            try:self.step(cursor,None,GeneratorExit())
            except GeneratorExit:return

class ResourceInvokeFrame(_ResourceFrame):
    ROOTS=('function','args','kwargs','result','current','task','error')
    def __init__(self,function,args,asynchronous=False,kwargs=None):
        self.function,self.args,self.asynchronous,self.kwargs=function,args,asynchronous,kwargs
        self.phase='start';self.before=0;self.result=self.current=self.task=self.error=None
    def ceiling(self,name):
        from .conditions import intersection
        from .execution_iteration import _caps
        caps=super().ceiling(name)
        return intersection(caps,_caps(self.function)) if name in ('args','kwargs','result') else caps
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,InvocationFrame,InvocationRequest
        from .suspension import Task
        from .contexts import _operation
        e=cursor.owner;s=e.suspension
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            self.before=len(s.tasks);self.phase='invoke'
            return Push(InvocationFrame(InvocationRequest(self.function,self.args,self.kwargs)))
        self.result=value
        if self.phase=='invoke' and self.asynchronous:
            self.current=s.current.task if s.current else None
            if (type(value) is Task and value in s.tasks[self.before:] and value.state=='created' and value.parent_task_id==(self.current.id if self.current else None) and value.kind=='task'):_share(e,value)
            self.task=value;private=_operation(e,value,s.tasks[self.before:]);self.phase='await'
            return Push(ResourceAwaitFrame(value,private))
        return Done(self.result)

class GeneratorContextOperationFrame(_ResourceFrame):
    ROOTS=('manager','error','task','result')
    def ceiling(self,name):
        from .conditions import intersection
        from .execution_iteration import _caps
        from .contexts import MANAGERS
        return intersection(self.capabilities,_caps(self.manager.generator)) if name=='result' and type(self.manager) in MANAGERS else self.capabilities
    def __init__(self,manager,error=None,closing=False):
        self.manager,self.error,self.closing=manager,error,closing;self.phase='start';self.task=self.result=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .contexts import ContextManager,_operation
        from .execution_iteration import StreamCloseFrame,StreamResumeFrame
        e=cursor.owner;s=e.suspension
        if error is not None:raise error
        if self.phase!='start':self.result=value;return Done(value)
        if type(self.manager) is ContextManager:
            self.phase='stream'
            return Push(StreamCloseFrame(self.manager.generator,context=self.manager) if self.closing else StreamResumeFrame(self.manager.generator,error=self.error,context=self.manager))
        iterator=s._s_close(self.manager.generator,context=self.manager) if self.closing else s._s_resume(self.manager.generator,error=self.error,context=self.manager)
        before=len(s.tasks);self.task=s.task(iterator,capture_roots=(self.manager,self.error));_share(e,self.task)
        private=_operation(e,self.task,s.tasks[before:]);self.phase='await';return Push(ResourceAwaitFrame(self.task,private))

class GeneratorContextExitFrame(_ResourceFrame):
    ROOTS=('manager','error','item','pending','result')
    def ceiling(self,name):
        from .conditions import intersection
        from .execution_iteration import _caps
        from .contexts import MANAGERS
        return intersection(self.capabilities,_caps(self.manager.generator)) if name=='item' and type(self.manager) in MANAGERS else self.capabilities
    def __init__(self,manager,error=None):self.manager,self.error=manager,error;self.item=self.pending=self.result=None;self.phase='start'
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .language import LanguageError
        if self.phase=='start':
            if error is not None:raise error
            self.manager.state='exiting';self.phase='resume';return Push(GeneratorContextOperationFrame(self.manager,self.error))
        if self.phase=='closing':
            if error is not None:raise error
            raise self.pending
        if error is not None:
            if self.error is not None and error is self.error:return Done(False)
            raise error
        self.item=value
        if not self.item['done']:
            self.pending=LanguageError('上下文生成器必须恰好 yield 一次',code='context_error');self.phase='closing'
            return Push(GeneratorContextOperationFrame(self.manager,closing=True))
        self.result=self.error is not None;return Done(self.result)

class ResourceAcquireFrame(_ResourceFrame):
    ROOTS=('manager','enter_function','exit_function','item','value','entry','error')
    def __init__(self,manager,asynchronous=False):
        self.manager,self.asynchronous=manager,asynchronous;self.phase='start'
        self.enter_function=self.exit_function=self.item=self.value=self.entry=self.error=None;self.generator_entered=False
    def ceiling(self,name):
        from .conditions import intersection
        from .execution_iteration import _caps
        from .contexts import MANAGERS
        ceiling=super().ceiling(name)
        if name=='value' and self.phase=='invoke':return intersection(ceiling,_caps(self.enter_function))
        if name in ('item','value') and self.phase=='generator' and type(self.manager) in MANAGERS:return intersection(ceiling,_caps(self.manager.generator))
        return ceiling
    def failed(self,e):
        if self.generator_entered:
            self.generator_entered=False
            from .contexts import abandon
            abandon(e,self.manager)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .contexts import ExitEntry,MANAGERS,AsyncContextManager,context_owned
        from .language import LanguageError
        from .objects import ChaosInstance,owned,special_method,require_callable
        e=cursor.owner
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='enter_access':
                self.enter_function=value;self.phase='exit_access'
                return self.leaf(e._s_access_result(special_method(self.manager,self.names[1],e)))
            if self.phase=='exit_access':
                self.exit_function=value;require_callable(self.enter_function,e);require_callable(self.exit_function,e)
                self.phase='invoke';return Push(ResourceInvokeFrame(self.enter_function,[],self.asynchronous))
            if self.phase=='invoke':
                self.value=value;self.entry=ExitEntry('function',function=self.exit_function,asynchronous=self.asynchronous)
                return Done((value,self.entry))
            if self.phase=='generator':
                self.item=value
                if value['done']:raise LanguageError('上下文生成器在 yield 前已结束',code='context_error')
                self.manager.state='active';self.generator_entered=False;self.value=value['value'];self.entry=ExitEntry('generator',self.manager)
                return Done((self.value,self.entry))
            from .dynamic_contexts import ContextScope,ContextToken,acquire as binding_acquire
            if type(self.manager) in (ContextScope,ContextToken):
                if self.asynchronous:raise LanguageError('绑定 scope 使用同步 with',code='argument_error')
                self.value,lease=binding_acquire(e,self.manager);self.entry=ExitEntry('binding',lease);return Done((self.value,self.entry))
            from .conditions import SCOPES,enter as recovery_enter
            if type(self.manager) in SCOPES:
                if self.asynchronous:raise LanguageError('恢复 scope 使用同步 with',code='argument_error')
                self.value=recovery_enter(e,self.manager);self.entry=ExitEntry('recovery',self.manager);return Done((self.value,self.entry))
            from .decimal_values import DecimalContext,acquire as decimal_acquire
            if type(self.manager) is DecimalContext:
                if self.asynchronous:raise LanguageError('DecimalContext 使用同步 with',code='argument_error')
                self.value,lease=decimal_acquire(e,self.manager);self.entry=ExitEntry('decimal',lease);return Done((self.value,self.entry))
            from .cancellation import CancelShield
            from .task_groups import TaskGroup
            if type(self.manager) is CancelShield:raise LanguageError('CancelShield 必须由词法 with 管理',code='cancel_shield_error')
            from .resource_stacks import STACKS,context_enter as stack_enter
            from .computer_values import Computer,context_enter
            if type(self.manager) is TaskGroup:raise LanguageError('TaskGroup 必须由词法 async with 管理',code='task_group_error')
            if type(self.manager) in STACKS:
                self.value=stack_enter(e,self.manager,self.asynchronous);self.entry=ExitEntry('stack',self.manager);return Done((self.value,self.entry))
            if type(self.manager) in MANAGERS:
                context_owned(self.manager,e)
                if self.asynchronous!=(type(self.manager) is AsyncContextManager):raise LanguageError('上下文管理器的同步／异步类型不匹配',code='argument_error')
                if self.manager.state!='created':raise LanguageError('生成器上下文管理器只能进入一次',code='context_error')
                self.manager.state='entering';self.generator_entered=True;self.phase='generator';return Push(GeneratorContextOperationFrame(self.manager))
            from .robot_values import Robot,context_enter as robot_enter
            from .device_values import Device,context_enter as device_enter
            if type(self.manager) is Robot:
                if not self.asynchronous:raise LanguageError('Robot requires async with',code='argument_error')
                self.value=robot_enter(e,self.manager);self.entry=ExitEntry('robot',self.manager,asynchronous=True);return Done((self.value,self.entry))
            if type(self.manager) is Device:
                if not self.asynchronous:raise LanguageError('Device requires async with',code='argument_error')
                self.value=device_enter(e,self.manager);self.entry=ExitEntry('device',self.manager,asynchronous=True);return Done((self.value,self.entry))
            if type(self.manager) is Computer:
                if not self.asynchronous:raise LanguageError('Computer 需要 async with',code='argument_error')
                self.value=context_enter(e,self.manager);self.entry=ExitEntry('computer',self.manager,asynchronous=True);return Done((self.value,self.entry))
            if not isinstance(self.manager,ChaosInstance):raise LanguageError('with 需要实现上下文协议的实例',code='argument_error')
            owned(self.manager,e);self.names=('__aenter__','__aexit__') if self.asynchronous else ('__enter__','__exit__');self.phase='enter_access'
            return self.leaf(e._s_access_result(special_method(self.manager,self.names[0],e)))
        except BaseException:self.failed(e);raise
    def close(self,cursor):self.failed(cursor.owner)

class ResourceExitCallFrame(_ResourceFrame):
    ROOTS=('function','error','computer','task','result')
    def __init__(self,function,error,asynchronous,computer):
        self.function,self.error,self.asynchronous,self.computer=function,error,asynchronous,computer;self.phase='start';self.task=self.result=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .language import LanguageError
        from .contexts import _operation
        e=cursor.owner;s=e.suspension
        if error is not None:raise error
        if self.phase=='await':return Done(False)
        if self.phase=='invoke':
            self.result=value
            if value is not None and type(value)is not bool:raise LanguageError('上下文退出协议必须返回 Bool 或 Null',code='argument_error')
            return Done(value is True)
        if self.computer is None:self.phase='invoke';return Push(ResourceInvokeFrame(self.function,[self.error],self.asynchronous))
        from .robot_values import Robot
        from .device_values import Device
        if type(self.computer)is Robot:from .robot_values import context_exit
        elif type(self.computer)is Device:from .device_values import context_exit
        else:from .computer_values import context_exit
        before=len(s.tasks);self.task=context_exit(e,self.computer);private=_operation(e,self.task,s.tasks[before:]);self.phase='await'
        return Push(ResourceAwaitFrame(self.task,private))

class ResourceExitEntryFrame(_ResourceFrame):
    ROOTS=('entry','error','previous_context','record','result')
    def __init__(self,entry,error=None):self.entry,self.error=entry,error;self.previous_context=self.record=self.result=None;self.phase='start';self.entered=False
    def retire(self,e):
        if self.entered:
            self.entered=False;e.error_context=self.previous_context
            from .contexts import release
            release(e,self.entry)
    def advance(self,e,value,error):
        from .execution_frames import Push,Done
        from .task_groups import GroupAbort
        from .errors import catchable
        from .language import LanguageError
        if error is not None:raise error
        if self.phase!='start':self.result=value;return Done(value)
        if self.entry.released:return Done(False)
        self.previous_context=e.error_context;self.entered=True
        if catchable(self.error)and self.entry.kind!='stack':e.error_context=self.error
        self.phase='exit'
        if self.entry.kind in ('decimal','recovery','binding'):return Done(False)
        if self.entry.kind=='generator':return Push(GeneratorContextExitFrame(self.entry.target,self.error))
        if self.entry.kind=='stack':return Push(StackUnwindFrame(self.entry.target,self.error,managed=True))
        record_error=self.error.error if isinstance(self.error,GroupAbort)else self.error
        from .error_values import record_for
        self.record=e.copy_value(record_for(e,record_error))if isinstance(record_error,LanguageError)else None
        return Push(ResourceExitCallFrame(self.entry.function,self.record,self.entry.asynchronous,self.entry.target if self.entry.kind in ('computer','device','robot')else None))
    def step(self,cursor,value,error):
        from .execution_frames import Done
        from .language import LanguageError
        e=cursor.owner
        if self.phase=='start' and error is not None:raise error
        try:action=self.advance(e,value,error)
        except BaseException as raised:
            try:
                if isinstance(raised,LanguageError):
                    from .errors import observe
                    observe(e,raised)
            finally:self.retire(e)
            raise
        if type(action)is Done:self.retire(e)
        return action
    def close(self,cursor):self.retire(cursor.owner)

class StackRequestFrame(_ResourceFrame):
    ROOTS=('request','stack','task','current','result')
    def __init__(self,request):self.request=request;self.stack=request.stack;self.operation=request.operation;self.phase='start';self.task=self.current=self.result=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .resource_stacks import owned,decimal_access,_execute
        e=cursor.owner;s=e.suspension
        if error is not None:raise error
        if self.phase!='start':self.result=value;return Done(value)
        owned(self.stack,e)
        if self.operation not in ('enter_async_context','aclose'):self.phase='execute';return Push(StackExecuteFrame(self.request))
        from .decimal_values import active_state
        decimal_access(e,self.stack,closing=self.operation=='aclose')
        self.task=s.task(_execute(e,self.request),capture_roots=(self.stack,self.request.value)+tuple(self.request.args)+tuple((self.request.kwargs or {}).values()))
        self.task.runner.decimal_state=active_state(s);_share(e,self.task)
        self.task.stack_operation=self.stack;self.task.stack_operation_kind=self.operation
        self.current=s.current.task if s.current else None
        self.task.context_structured=bool(self.current and (self.current.scope_group is not None or self.current.group is not None or getattr(self.current,'context_structured',False)))
        return Done(self.task)

class StackExecuteFrame(_ResourceFrame):
    ROOTS=('request','stack','current','value','entry','function','transferred','authority_runner','previous_authority','previous_team','error')
    def __init__(self,request,scoped=True):
        self.request=request;self.stack=request.stack;self.operation=request.operation;self.scoped=scoped;self.phase='start'
        self.current=self.value=self.entry=self.function=self.transferred=self.error=None;self.reserved=self.operating=self.changes_busy=False;self.authority_entered=False
    def retire(self,e):
        try:
            if self.reserved:self.reserved=False;e.suspension.stack_entries-=1
            if self.changes_busy:self.changes_busy=False;self.stack.busy=False
            if self.operating:
                self.operating=False
                if self.stack.operation_task is self.current:self.stack.operation_task,self.stack.operation_kind=None,None
        finally:self.release_authority()
    def commit(self,e,entry,value):
        from .execution_frames import Done
        self.entry=entry;self.value=value;self.stack.entries.append(entry);self.reserved=False
        if getattr(entry,'kind',None)=='stack':entry.target.parent_stack=self.stack
        self.retire(e);return Done(value)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .resource_stacks import available,reserve,STACKS,owned,_cycle,create,AsyncExitStack,CallbackEntry
        from .contexts import ExitEntry,MANAGERS,release
        from .objects import ChaosInstance,special_method,require_callable,declaration
        from .computer_values import Computer
        from .robot_values import Robot
        from .task_groups import TaskGroup
        from .language import LanguageError
        e=cursor.owner
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='close':self.retire(e);return Done(None)
            if self.phase=='acquire':
                self.value,self.entry=value
                if self.stack.state!='open':release(e,self.entry);raise LanguageError('ExitStack 在资源获取期间已关闭',code='context_error')
                return self.commit(e,self.entry,self.value)
            if self.phase=='access':self.function=value;self.phase='push'
            if self.phase=='push':
                require_callable(self.function,e)
                return self.commit(e,ExitEntry('function',function=self.function,asynchronous=self.operation=='push_async_exit'),self.request.value)
            if self.scoped:self.enter_authority(e,self.stack)
            available(e,self.stack,closing=self.operation in ('close','aclose'))
            if self.operation in ('close','aclose'):
                self.current=e.suspension.current.task if e.suspension.current else None
                self.stack.operation_task,self.stack.operation_kind=self.current,self.operation;self.operating=True;self.phase='close'
                return Push(StackUnwindFrame(self.stack))
            if self.operation=='pop_all':
                self.transferred=create(e,type(self.stack)is AsyncExitStack,self.stack.max_entries)
                self.transferred.entries,self.stack.entries=self.stack.entries,[]
                for entry in self.transferred.entries:
                    if getattr(entry,'kind',None)=='stack':entry.target.parent_stack=self.transferred
                self.retire(e);return Done(self.transferred)
            self.stack.busy=True;self.changes_busy=True
            self.current=e.suspension.current.task if e.suspension.current else None
            self.stack.operation_task,self.stack.operation_kind=self.current,self.operation;self.operating=True
            reserve(e,self.stack);self.reserved=True
            if self.operation in ('enter_context','enter_async_context'):
                if type(self.request.value)in STACKS:owned(self.request.value,e);_cycle(e,self.request.value,self.stack)
                self.phase='acquire';return Push(ResourceAcquireFrame(self.request.value,self.operation=='enter_async_context'))
            if self.operation in ('push','push_async_exit'):
                self.function=self.request.value
                if type(self.function)in MANAGERS+STACKS+(Computer,TaskGroup,Robot):raise LanguageError('原生资源必须通过 enter_context/enter_async_context 获取',code='context_error')
                if isinstance(self.function,ChaosInstance):
                    name='__aexit__'if self.operation=='push_async_exit'else'__exit__'
                    if declaration(self.function.klass,name)is not None:self.phase='access';return self.leaf(e._s_access_result(special_method(self.function,name,e)))
                self.phase='push';return self.step(cursor,None,None)
            require_callable(self.request.value,e)
            self.entry=CallbackEntry(self.request.value,e.copy_value(list(self.request.args)),e.copy_value(self.request.kwargs),self.operation=='push_async_callback')
            return self.commit(e,self.entry,self.request.value)
        except BaseException:self.retire(e);raise
    def close(self,cursor):self.retire(cursor.owner)

class StackUnwindFrame(_ResourceFrame):
    ROOTS=('stack','error','operation','current','source','pending','group_error','previous_context','entry','result','raised','authority_runner','previous_authority','previous_team')
    def __init__(self,stack,error=None,finalizing=False,managed=False,scoped=True):
        self.stack,self.error,self.finalizing,self.managed,self.scoped=stack,error,finalizing,managed,scoped
        self.phase='start';self.operation=self.current=self.source=self.pending=self.group_error=self.previous_context=self.entry=self.result=self.raised=None
        self.authority_entered=self.unwinding=False
    def retire(self,e):
        try:
            if self.unwinding:
                self.unwinding=False;e.error_context=self.previous_context
                from .resource_stacks import abandon
                abandon(e,self.stack)
        finally:self.release_authority()
    def handle(self,e,error):
        from .control_prompts import NonlocalTransfer
        from .task_groups import GroupAbort
        from .language import LanguageError,ResourceLimit
        from .errors import observe
        self.raised=error
        if isinstance(error,NonlocalTransfer):
            if self.group_error is None:self.pending=error
        elif isinstance(error,GroupAbort):self.group_error=self.pending=error
        elif isinstance(error,LanguageError):
            if isinstance(error,ResourceLimit):raise error
            observe(e,error);self.pending=error
        else:raise error
        e.error_context=self.previous_context;self.phase='loop'
    def step(self,cursor,value,error):
        from .execution_frames import Push,External,Done
        from .resource_stacks import owned,decimal_access,CallbackEntry
        from .contexts import _await
        from .suspension import Drain,Task,Generator
        from .task_groups import GroupAbort
        from .errors import catchable
        from .language import LanguageError
        e=cursor.owner;s=e.suspension
        try:
            if self.phase=='start':
                if error is not None:raise error
                if self.scoped:self.enter_authority(e,self.stack)
                owned(self.stack,e,finalizing=self.finalizing)
                if not self.finalizing:decimal_access(e,self.stack,closing=True)
                if self.stack.busy and self.managed and self.stack.operation_task is not None:
                    self.operation=self.stack.operation_task;self.current=s.current.task if s.current else None
                    if self.operation is not self.current:
                        if self.stack.operation_kind in ('close','aclose'):
                            if self.error is not None:s.interrupt(self.operation,self.error)
                            self.phase='join';return Push(ResourceAwaitFrame(self.operation,True,cleanup_operation=True))
                        self.source=object();s._cancel(self.operation,self.source);self.phase='drain';return External(Drain(self.operation),native_leaf=True)
                self.phase='ready'
            elif self.phase=='join':
                if error is not None and error is not self.error:raise error
                self.phase='ready'
            elif self.phase=='drain':
                if error is not None and not(s._local_cancel(error,self.operation)and self.operation.cancel_sources=={self.source}):raise error
                self.phase='ready'
            elif self.phase in ('callback','entry'):
                if error is not None:self.handle(e,error)
                else:
                    self.result=value
                    if self.phase=='callback'and isinstance(value,(Task,Generator)):self.handle(e,LanguageError('关闭回调返回了未完成的 Task/Generator/AsyncGenerator',code='context_error'))
                    else:
                        if self.phase=='entry'and value and isinstance(self.pending,LanguageError):self.pending=None
                        e.error_context=self.previous_context;self.phase='loop'
            elif error is not None:raise error
            if self.phase=='ready':
                if self.stack.busy:raise LanguageError('ExitStack 操作不能重入或并发',code='context_busy')
                if self.stack.state=='closed':self.release_authority();return Done(False)
                self.stack.busy,self.stack.state=True,'closing';self.pending=self.error;self.group_error=self.error if isinstance(self.error,GroupAbort)else None
                self.previous_context=e.error_context;self.unwinding=True;self.phase='loop'
            while self.stack.entries:
                e.error_context=self.pending if catchable(self.pending)else self.previous_context
                try:
                    e.tick();self.entry=self.stack.entries.pop();s.stack_entries-=1
                    if isinstance(self.entry,CallbackEntry):
                        self.phase='callback';return Push(ResourceInvokeFrame(self.entry.function,self.entry.args,self.entry.asynchronous,self.entry.kwargs))
                    self.phase='entry';return Push(ResourceExitEntryFrame(self.entry,self.pending))
                except BaseException as raised:self.handle(e,raised)
            if self.pending is None and self.group_error is not None:self.pending=self.group_error
            if self.pending is not None and self.pending is not self.error:raise self.pending
            self.result=False if self.group_error is not None else self.error is not None and self.pending is None
            self.retire(e);return Done(self.result)
        except BaseException:self.retire(e);raise
    def close(self,cursor):self.retire(cursor.owner)

class StackFinalizeScopeFrame(_ResourceFrame):
    ROOTS=('scope','stack','value','error')
    def __init__(self,scope,hard=False):self.scope,self.hard=scope,hard;self.phase='start';self.changed=False;self.stack=self.value=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .resource_stacks import abandon
        e=cursor.owner
        if error is not None:self.error=error;raise error
        self.value=value;self.phase='scan'
        while True:
            self.stack=next((v for v in reversed(e.suspension.resource_stacks)if v.recording_scope==self.scope and v.state!='closed'and not v.context_active),None)
            if self.stack is None:
                for v in e.suspension.resource_stacks:
                    if v.recording_scope==self.scope and v.state!='closed':self.changed=True;abandon(e,v)
                return Done(self.changed)
            self.changed=True
            if self.hard:abandon(e,self.stack)
            else:self.phase='unwind';return Push(StackUnwindFrame(self.stack,managed=True))

CONTEXT_PROTOCOL_FRAMES=(ContextRunFrame,ResourceAwaitFrame,ResourceInvokeFrame,GeneratorContextOperationFrame,GeneratorContextExitFrame,ResourceAcquireFrame,ResourceExitCallFrame,ResourceExitEntryFrame,StackRequestFrame,StackExecuteFrame,StackUnwindFrame,StackFinalizeScopeFrame)

def context_frame(request):
    from .dynamic_contexts import RunRequest
    from .resource_stacks import StackRequest
    if type(request)is RunRequest:return ContextRunFrame(request)
    if type(request)is StackRequest:return StackRequestFrame(request)
    if type(request)is ContextProtocolRequest:
        classes={'await':ResourceAwaitFrame,'invoke':ResourceInvokeFrame,'manager_operation':GeneratorContextOperationFrame,'manager_exit':GeneratorContextExitFrame,'acquire':ResourceAcquireFrame,'exit_call':ResourceExitCallFrame,'exit_entry':ResourceExitEntryFrame,'stack_execute':StackExecuteFrame,'stack_unwind':StackUnwindFrame,'finalize_scope':StackFinalizeScopeFrame}
        return classes[request.operation](*request.values)
    return None
