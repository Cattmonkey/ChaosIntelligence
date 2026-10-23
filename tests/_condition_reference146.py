"""Exact original complete recovery algorithms; test-only narrow resolve patch."""
from contextlib import contextmanager
from unittest.mock import patch
from chaos.conditions import *

@contextmanager
def callback_authority(evaluator,*authorities):
    from chaos.agent_tools import scope
    with scope(evaluator,intersection(*authorities)):yield

def callback(evaluator,function,args,kwargs=None,authority=None,*,all_results=False):
    with callback_authority(evaluator,authority):
        return (yield from evaluator._s_invoke(function,args,kwargs,force=True,_recovery_callback=True,all_results=all_results))

def signal(evaluator,value,required=False):
    from chaos.language import LanguageError,check_type
    from chaos.suspension import Task,Generator
    validate(value,evaluator.tick)
    holder=active_state(evaluator.suspension)
    groups=[scope for scope in holder.scopes if type(scope) is HandlerScope and scope not in holder.hidden]
    for index in range(len(groups)-1,-1,-1):
        group=groups[index]; previous=set(holder.hidden)
        # Mask identities existing now. A handler may establish new handlers.
        holder.hidden.update(groups[index:])
        try:
            for predicate,handler in group.bindings:
                evaluator.tick()
                match=yield from callback(evaluator,predicate,[value],authority=group.capabilities)
                if not check_type(match,'Bool'):continue
                result=yield from callback(evaluator,handler,[value],authority=group.capabilities)
                if isinstance(result,(Task,Generator)):error('Handler 返回冷 Task/Generator；请在语言回调中显式 await 或消费')
                break
        finally:holder.hidden=previous
    if required:
        if evaluator.condition_pause:return (yield from debugger(evaluator,value))
        raise LanguageError(value.message or value.kind,code='condition_error',value=condition_info(value))
    return None

def debugger(evaluator,value):
    """Pause the existing logical computation; only data leaves this stack.

    Inline recovery is an explicit Chaos return value; case recovery transfers
    through the original unwind path. No snapshot can reconstruct this extent.
    """
    from chaos.language import LanguageError
    from chaos.capture_frames import holder as capture_holder
    from chaos.debugger import recovery_metadata, restart_arguments
    validate(value,evaluator.tick)
    if evaluator.condition_debug_hook is None:
        raise LanguageError(value.message or value.kind,code="condition_error",value=condition_info(value))
    if evaluator.condition_debug_depth:
        error("Condition debugger cannot reenter while discovering or waiting", "condition_protocol_error")
    scheduler=evaluator.suspension
    epoch,holder,authority=scheduler.epoch,active_state(scheduler),caps(evaluator)
    def checkpoint():
        evaluator.tick()
        if not scheduler.active or scheduler.epoch!=epoch or active_state(scheduler) is not holder:
            error("Condition debugger logical extent changed", "ownership_error")
        if caps(evaluator)!=authority:
            error("Condition debugger authority changed", "capability_error")
    evaluator.condition_debug_depth+=1
    try:
        checkpoint()
        available=yield from compute(evaluator,value)
        checkpoint()
        offered=available[:128]
        metadata=recovery_metadata(value,offered,len(available),redact=evaluator.condition_debug_redact)
        current=capture_holder(evaluator)
        context=getattr(current,"debug_context",None)
        node,env=context if context is not None else (None,evaluator.env)
        elapsed,selection=evaluator.condition_debug_hook(metadata,node,env,tuple(evaluator.frames))
        import math
        if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:
            error("Invalid condition debugger pause duration", "condition_debugger_error")
        if elapsed>0:evaluator.deadline+=elapsed
        checkpoint()
        if selection is None:
            raise LanguageError(value.message or value.kind,code="condition_error",value=condition_info(value))
        if type(selection) is not dict or set(selection)!={"restartId","args","kwargs"}:
            error("Invalid condition debugger selection", "condition_debugger_error")
        args,kwargs=restart_arguments(selection["args"],selection["kwargs"])
        selected=next((restart for restart in offered if restart.id==selection["restartId"]),None)
        if selected is None:error("Condition debugger restart was not offered", "restart_error")
        owned(selected,evaluator,active=True)
    finally:evaluator.condition_debug_depth-=1
    checkpoint()
    return (yield from invoke(evaluator,selected,args,kwargs))

def compute(evaluator,condition=None,name=None):
    from chaos.language import check_type
    if condition is not None:validate(condition,evaluator.tick)
    holder=active_state(evaluator.suspension); result=[]
    for scope in reversed(tuple(holder.scopes)):
        if type(scope) is not RestartScope:continue
        for restart in scope.restarts:
            evaluator.tick()
            if name is not None and restart.name!=name:continue
            if restart.test is not None:
                visible=yield from callback(evaluator,restart.test,[condition],authority=scope.capabilities)
                if not check_type(visible,'Bool'):continue
            owned(restart,evaluator,active=True)
            result.append(restart)
    return result

def invoke(evaluator,designator,args,kwargs):
    from chaos.language import serializable
    if type(args) is not list or type(kwargs) is not dict or any(type(key) is not str for key in kwargs):error('invoke_restart 需要 List args 和 Record kwargs','argument_error')
    evaluator.argument_budget(args,kwargs)
    if type(designator) is str:
        matches=yield from compute(evaluator,name=designator)
        if not matches:error('当前没有名为 '+designator+' 的 Restart','restart_error')
        restart=matches[0]
    elif type(designator) is Restart:restart=owned(designator,evaluator,active=True)
    else:error('invoke_restart 需要 Restart 或 String','argument_error')
    owned(restart,evaluator,active=True)
    # Copies protect call arrays without projecting any live language values.
    args=evaluator.copy_value(args);kwargs=evaluator.copy_value(kwargs)
    authority=caps(evaluator)
    if restart.scope.mode=='case':
        raise RestartTransfer(restart,args,kwargs,authority,dict(evaluator.current_span))
    return (yield from callback(evaluator,restart.function,args,kwargs,intersection(restart.capabilities,authority),all_results=True))

def case(evaluator,body,bindings):
    from chaos.objects import require_callable
    require_callable(body,evaluator)
    scope=create(evaluator,bindings,True,'case'); enter(evaluator,scope)
    transfer=None
    try:
        try:return (yield from callback(evaluator,body,[],all_results=True))
        except RestartTransfer as signal:
            if signal.restart.scope is not scope:raise
            transfer=signal
    finally:release(scope)
    return (yield from callback(evaluator,transfer.restart.function,transfer.args,transfer.kwargs,
                               intersection(scope.capabilities,transfer.authority,caps(evaluator)),all_results=True))

def resolve(evaluator,request):
    op=request.operation
    if op in ('signal','error'):return (yield from signal(evaluator,request.value,op=='error'))
    if op=='debugger':return (yield from debugger(evaluator,request.value))
    if op=='case':return (yield from case(evaluator,request.value,request.extra))
    if op in ('compute','find'):
        if op=='find':
            name,condition=request.value
            if type(name) is not str:error('find_restart name 需要 String','argument_error')
            items=yield from compute(evaluator,condition,name)
            return items[0] if items else None
        return (yield from compute(evaluator,request.value))
    if op=='invoke':return (yield from invoke(evaluator,request.value,*request.extra))
    error('未知恢复请求')

@contextmanager
def reference():
    with patch("chaos.conditions.resolve",resolve):yield
