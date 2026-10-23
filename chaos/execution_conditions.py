"""Authoritative Condition/restart/debugger phases on the original cursor."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryCallbackRequest:
    function: object
    args: object
    kwargs: object = None
    authority: object = None
    all_results: bool = False


class _RecoveryFrame:
    ROOTS=()
    capabilities=None
    def field_ceiling(self,name):
        from .conditions import intersection
        return intersection(self.capabilities,getattr(self,'authority',None))
    def roots(self):
        from .capture_frames import Edge
        active=[];saved=[]
        for name in self.ROOTS:
            value=getattr(self,name,None)
            values=tuple(value) if type(value) in (tuple,set,frozenset) else (value,)
            if name in ('authority_runner','previous_authority','previous_hidden','saved_holder'):
                saved.extend(values)
            else:active.append(Edge(self.owner,self.field_ceiling(name),values))
        return tuple(active)+(Edge(self.owner,getattr(self,'previous_authority',self.capabilities),tuple(saved)),)
    def close(self,cursor):pass


class RecoveryCallbackFrame(_RecoveryFrame):
    ROOTS=('function','args','kwargs','authority','result','error','authority_runner','previous_authority')
    def __init__(self,function,args,kwargs=None,authority=None,all_results=False):
        self.function,self.args,self.kwargs,self.authority=function,args,kwargs,authority
        self.all_results=all_results;self.phase='start';self.result=self.error=None
        self.authority_runner=self.previous_authority=None;self.entered=False
    def release(self):
        if self.entered:
            self.entered=False
            if self.authority_runner is not None:self.authority_runner.capabilities=self.previous_authority
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done,InvocationFrame,InvocationRequest
        from .conditions import intersection
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='start':
                self.authority_runner=cursor.owner.suspension.current
                if self.authority_runner is not None:
                    self.previous_authority=self.authority_runner.capabilities
                    self.authority_runner.capabilities=intersection(self.previous_authority,self.authority)
                self.entered=True;self.phase='invoke'
                return Push(InvocationFrame(InvocationRequest(self.function,self.args,self.kwargs,
                            {'force':True,'_recovery_callback':True,'all_results':self.all_results})))
            self.result=value;self.release();return Done(self.result)
        except BaseException:self.release();raise
    def close(self,cursor):self.release()


class ConditionSignalFrame(_RecoveryFrame):
    def field_ceiling(self,name):
        from .conditions import intersection
        ceiling=super().field_ceiling(name)
        return intersection(ceiling,getattr(self.group,'capabilities',None)) if name in ('predicate','handler','result') else ceiling
    ROOTS=('value','holder','groups','group','predicate','handler','result','error','previous_hidden','saved_holder')
    def __init__(self,value,required=False):
        self.value,self.required=value,required;self.phase='start';self.holder=self.saved_holder=None
        self.groups=();self.group=self.predicate=self.handler=self.result=self.error=None
        self.previous_hidden=None;self.masked=False;self.index=self.binding_index=0
    def restore(self):
        if self.masked:self.holder.hidden=self.previous_hidden;self.masked=False
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .conditions import validate,active_state,HandlerScope,error as fail,condition_info
        from .language import LanguageError,check_type
        from .suspension import Task,Generator
        e=cursor.owner
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='start':
                validate(self.value,e.tick);self.holder=active_state(e.suspension);self.saved_holder=self.holder
                self.groups=tuple(scope for scope in self.holder.scopes if type(scope) is HandlerScope and scope not in self.holder.hidden)
                self.index=len(self.groups)-1;self.phase='group'
            elif self.phase=='predicate':
                self.result=value
                if check_type(self.result,'Bool'):
                    self.phase='handler';return Push(RecoveryCallbackFrame(self.handler,[self.value],authority=self.group.capabilities))
                self.binding_index+=1;self.phase='binding'
            elif self.phase=='handler':
                self.result=value
                if isinstance(self.result,(Task,Generator)):fail('Handler 返回冷 Task/Generator；请在语言回调中显式 await 或消费')
                self.restore();self.index-=1;self.phase='group'
            elif self.phase=='debugger':return Done(value)
            while True:
                if self.phase=='group':
                    if self.index<0:
                        if self.required:
                            if e.condition_pause:self.phase='debugger';return Push(ConditionDebuggerFrame(self.value))
                            raise LanguageError(self.value.message or self.value.kind,code='condition_error',value=condition_info(self.value))
                        return Done(None)
                    self.group=self.groups[self.index];self.previous_hidden=set(self.holder.hidden)
                    self.holder.hidden.update(self.groups[self.index:]);self.masked=True;self.binding_index=0;self.phase='binding'
                if self.phase=='binding':
                    if self.binding_index>=len(self.group.bindings):
                        self.restore();self.index-=1;self.phase='group';continue
                    self.predicate,self.handler=self.group.bindings[self.binding_index]
                    e.tick();self.phase='predicate';return Push(RecoveryCallbackFrame(self.predicate,[self.value],authority=self.group.capabilities))
        except BaseException:self.restore();raise
    def close(self,cursor):self.restore()


class RestartComputeFrame(_RecoveryFrame):
    ROOTS=('condition','name','holder','scopes','scope','restart','result','error')
    def __init__(self,condition=None,name=None,find=False):
        self.condition,self.name,self.find=condition,name,find;self.phase='start'
        self.holder=self.scope=self.restart=self.error=None;self.scopes=();self.result=[];self.index=self.restart_index=0
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .conditions import validate,active_state,RestartScope,owned
        from .language import check_type
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='start':
            if self.condition is not None:validate(self.condition,e.tick)
            self.holder=active_state(e.suspension);self.scopes=tuple(reversed(tuple(self.holder.scopes)));self.phase='scan'
        elif self.phase=='test':
            if check_type(value,'Bool'):owned(self.restart,e,active=True);self.result.append(self.restart)
            self.restart_index+=1;self.phase='scan'
        while self.index<len(self.scopes):
            self.scope=self.scopes[self.index]
            if type(self.scope) is not RestartScope or self.restart_index>=len(self.scope.restarts):
                self.index+=1;self.restart_index=0;continue
            self.restart=self.scope.restarts[self.restart_index];e.tick()
            if self.name is not None and self.restart.name!=self.name:self.restart_index+=1;continue
            if self.restart.test is not None:
                self.phase='test';return Push(RecoveryCallbackFrame(self.restart.test,[self.condition],authority=self.scope.capabilities))
            owned(self.restart,e,active=True);self.result.append(self.restart);self.restart_index+=1
        return Done((self.result[0] if self.result else None) if self.find else self.result)


class RestartInvokeFrame(_RecoveryFrame):
    def field_ceiling(self,name):
        from .conditions import intersection
        ceiling=super().field_ceiling(name)
        return intersection(ceiling,getattr(self.restart,'capabilities',None)) if name in ('args','kwargs','result') else ceiling
    ROOTS=('designator','args','kwargs','restart','matches','authority','result','error')
    def __init__(self,designator,args,kwargs):
        self.designator,self.args,self.kwargs=designator,args,kwargs;self.phase='start'
        self.restart=self.matches=self.authority=self.result=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .conditions import error as fail,Restart,owned,caps,intersection,RestartTransfer
        e=cursor.owner
        if error is not None:self.error=error;raise error
        if self.phase=='callback':self.result=value;return Done(self.result)
        if self.phase=='start':
            if type(self.args) is not list or type(self.kwargs) is not dict or any(type(key) is not str for key in self.kwargs):fail('invoke_restart 需要 List args 和 Record kwargs','argument_error')
            e.argument_budget(self.args,self.kwargs)
            if type(self.designator) is str:self.phase='found';return Push(RestartComputeFrame(name=self.designator))
            if type(self.designator) is Restart:self.restart=owned(self.designator,e,active=True)
            else:fail('invoke_restart 需要 Restart 或 String','argument_error')
        elif self.phase=='found':
            self.matches=value
            if not self.matches:fail('当前没有名为 '+self.designator+' 的 Restart','restart_error')
            self.restart=self.matches[0]
        owned(self.restart,e,active=True)
        self.args=e.copy_value(self.args);self.kwargs=e.copy_value(self.kwargs);self.authority=caps(e)
        if self.restart.scope.mode=='case':raise RestartTransfer(self.restart,self.args,self.kwargs,self.authority,dict(e.current_span))
        self.phase='callback';return Push(RecoveryCallbackFrame(self.restart.function,self.args,self.kwargs,intersection(self.restart.capabilities,self.authority),True))


class RestartCaseFrame(_RecoveryFrame):
    def field_ceiling(self,name):
        from .conditions import intersection
        ceiling=super().field_ceiling(name)
        return intersection(ceiling,getattr(self.scope,'capabilities',None),getattr(self.transfer,'authority',None)) if name=='transfer' else ceiling
    ROOTS=('body','bindings','scope','transfer','result','error')
    def __init__(self,body,bindings):
        self.body,self.bindings=body,bindings;self.phase='start';self.scope=self.transfer=self.result=self.error=None;self.entered=False
    def release(self):
        if self.entered:
            from .conditions import release
            self.entered=False;release(self.scope)
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .conditions import create,enter,RestartTransfer,intersection,caps
        from .objects import require_callable
        e=cursor.owner
        try:
            if self.phase=='body':
                if error is not None:
                    if not isinstance(error,RestartTransfer) or error.restart.scope is not self.scope:raise error
                    self.transfer=error
                else:self.result=value;self.release();return Done(self.result)
                self.release();self.phase='handler'
                return Push(RecoveryCallbackFrame(self.transfer.restart.function,self.transfer.args,self.transfer.kwargs,
                            intersection(self.scope.capabilities,self.transfer.authority,caps(e)),True))
            if error is not None:self.error=error;raise error
            if self.phase=='start':
                require_callable(self.body,e);self.scope=create(e,self.bindings,True,'case');enter(e,self.scope);self.entered=True
                self.phase='body';return Push(RecoveryCallbackFrame(self.body,[],all_results=True))
            self.result=value;return Done(self.result)
        except BaseException:self.release();raise
    def close(self,cursor):self.release()


class ConditionDebuggerFrame(_RecoveryFrame):
    def field_ceiling(self,name):
        from .conditions import intersection
        ceiling=super().field_ceiling(name)
        return intersection(ceiling,getattr(self.selected,'capabilities',None)) if name in ('args','kwargs','result') else ceiling
    ROOTS=('value','holder','saved_holder','authority','available','offered','metadata','node','env','frames','selection','args','kwargs','selected','result','error')
    def __init__(self,value):
        self.value=value;self.phase='start';self.depth_entered=False
        self.holder=self.saved_holder=self.authority=self.available=self.offered=self.metadata=None
        self.node=self.env=self.frames=self.selection=self.args=self.kwargs=self.selected=self.result=self.error=None
        self.epoch=None;self.elapsed=None
    def checkpoint(self,e):
        from .conditions import active_state,caps,error
        e.tick();s=e.suspension
        if not s.active or s.epoch!=self.epoch or active_state(s) is not self.holder:error('Condition debugger logical extent changed','ownership_error')
        if caps(e)!=self.authority:error('Condition debugger authority changed','capability_error')
    def release(self,e):
        if self.depth_entered:self.depth_entered=False;e.condition_debug_depth-=1
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .conditions import validate,condition_info,error as fail,active_state,caps,owned
        from .language import LanguageError
        from .capture_frames import holder as capture_holder
        from .debugger import recovery_metadata,restart_arguments
        e=cursor.owner
        try:
            if error is not None:self.error=error;raise error
            if self.phase=='invoke':self.result=value;return Done(self.result)
            if self.phase=='start':
                validate(self.value,e.tick)
                if e.condition_debug_hook is None:raise LanguageError(self.value.message or self.value.kind,code='condition_error',value=condition_info(self.value))
                if e.condition_debug_depth:fail('Condition debugger cannot reenter while discovering or waiting','condition_protocol_error')
                self.epoch=e.suspension.epoch;self.holder=active_state(e.suspension);self.saved_holder=self.holder;self.authority=caps(e)
                e.condition_debug_depth+=1;self.depth_entered=True;self.checkpoint(e);self.phase='compute'
                return Push(RestartComputeFrame(self.value))
            if self.phase=='compute':
                self.available=value;self.checkpoint(e);self.offered=self.available[:128]
                self.metadata=recovery_metadata(self.value,self.offered,len(self.available),redact=e.condition_debug_redact)
                current=capture_holder(e);context=getattr(current,'debug_context',None)
                self.node,self.env=context if context is not None else (None,e.env);self.frames=tuple(e.frames)
                self.elapsed,self.selection=e.condition_debug_hook(self.metadata,self.node,self.env,self.frames)
                import math
                if type(self.elapsed) not in (int,float) or not math.isfinite(self.elapsed) or self.elapsed<0:fail('Invalid condition debugger pause duration','condition_debugger_error')
                if self.elapsed>0:e.deadline+=self.elapsed
                self.checkpoint(e)
                if self.selection is None:raise LanguageError(self.value.message or self.value.kind,code='condition_error',value=condition_info(self.value))
                if type(self.selection) is not dict or set(self.selection)!={'restartId','args','kwargs'}:fail('Invalid condition debugger selection','condition_debugger_error')
                self.args,self.kwargs=restart_arguments(self.selection['args'],self.selection['kwargs'])
                self.selected=next((restart for restart in self.offered if restart.id==self.selection['restartId']),None)
                if self.selected is None:fail('Condition debugger restart was not offered','restart_error')
                owned(self.selected,e,active=True);self.release(e);self.checkpoint(e);self.phase='invoke'
                return Push(RestartInvokeFrame(self.selected,self.args,self.kwargs))
            raise RuntimeError('Invalid condition debugger phase')
        except BaseException:self.release(e);raise
    def close(self,cursor):self.release(cursor.owner)


class ConditionRequestFrame(_RecoveryFrame):
    ROOTS=('request','result','error')
    def __init__(self,request):self.request=request;self.operation=request.operation;self.phase='start';self.result=self.error=None
    def step(self,cursor,value,error):
        from .execution_frames import Push,Done
        from .conditions import error as fail
        if error is not None:self.error=error;raise error
        if self.phase!='start':self.result=value;return Done(self.result)
        self.phase='result';r=self.request;op=self.operation
        if op in ('signal','error'):return Push(ConditionSignalFrame(r.value,op=='error'))
        if op=='debugger':return Push(ConditionDebuggerFrame(r.value))
        if op=='case':return Push(RestartCaseFrame(r.value,r.extra))
        if op=='compute':return Push(RestartComputeFrame(r.value))
        if op=='_compute':return Push(RestartComputeFrame(*r.value))
        if op=='find':
            name,condition=r.value
            if type(name) is not str:fail('find_restart name 需要 String','argument_error')
            return Push(RestartComputeFrame(condition,name,True))
        if op=='invoke':return Push(RestartInvokeFrame(r.value,*r.extra))
        fail('未知恢复请求')


RECOVERY_FRAMES=(ConditionRequestFrame,RecoveryCallbackFrame,ConditionSignalFrame,ConditionDebuggerFrame,RestartComputeFrame,RestartInvokeFrame,RestartCaseFrame)


def recovery_frame(request):
    from .conditions import Request
    if type(request) is Request:return ConditionRequestFrame(request)
    if type(request) is RecoveryCallbackRequest:return RecoveryCallbackFrame(request.function,request.args,request.kwargs,request.authority,request.all_results)
    return None
