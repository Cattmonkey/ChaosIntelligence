"""Identity prompts and genuine dynamic-extent escapes on the original stack.

These operators unwind the existing computation. They do not copy, replay, or
restore an exited stack. Reusable continuations require future execution frames.
"""
from dataclasses import dataclass,replace
from .continuations import Continuation

MAX_DEPTH=128
MAX_CREATED=10000


class NonlocalTransfer(BaseException):
    """Private soft control, shared with condition-case restart transfers."""


class PromptTransfer(NonlocalTransfer):
    def __init__(self,target,completion,authority):
        self.target,self.completion,self.authority=target,completion,authority


class ControlState:
    def __init__(self,scheduler):self.owner=scheduler;self.epoch=scheduler.epoch;self.stack=[]


@dataclass(frozen=True,eq=False)
class PromptTag:
    owner:object
    id:str
    name:str


class PromptScope:
    def __init__(self,evaluator,tag,handler=None):
        tag_owned(evaluator,tag)
        if handler is not None:
            from .objects import require_callable
            require_callable(handler,evaluator)
        scheduler=evaluator.suspension
        if not scheduler.active:fail('Prompt scope requires an active epoch','ownership_error')
        if len(scheduler.control_scopes)>=MAX_CREATED:limit('Control scopes exceed 10000')
        from .agent_tools import current_caps
        self.owner,self.epoch=scheduler,scheduler.epoch
        self.id='prompt-'+str(scheduler.next_id())
        self.tag,self.handler=tag,handler
        self.capabilities=current_caps(evaluator)
        task=scheduler.current.task if scheduler.current else None
        self.recording_scope=task.recording_scope if task else None
        self.state,self.holder='created',None
        scheduler.control_scopes.append(self)


@dataclass(frozen=True,eq=False)
class EscapeContinuation:
    owner:object
    epoch:int
    scope:object
    id:str
    capabilities:object=None


class Barrier:
    def __init__(self,evaluator):
        self.owner=evaluator.suspension
        self.epoch=self.owner.epoch
        self.state,self.holder='created',None
        self.capabilities=None


@dataclass(frozen=True,eq=False)
class Request:
    operation:str
    values:tuple


VALUES=(PromptTag,PromptScope,EscapeContinuation,Continuation)


def fail(message,code='control_error'):
    from .language import LanguageError
    raise LanguageError(message,code=code)


def limit(message):
    from .language import ResourceLimit
    raise ResourceLimit(message)


def active_state(scheduler):
    runner=scheduler.stream.runner if scheduler.stream is not None else scheduler.current
    return runner.control_state if runner is not None else scheduler.control_state


def tag_owned(evaluator,value):
    if type(value) is not PromptTag:fail('Expected native PromptTag','argument_error')
    if value.owner is not evaluator:fail('PromptTag cannot cross evaluators','ownership_error')
    return value


def owned(value,evaluator):
    if type(value)is Continuation:
        from .continuations import owned as continuation_owned
        return continuation_owned(evaluator,value)
    if type(value) is PromptTag:return tag_owned(evaluator,value)
    if type(value) not in (PromptScope,EscapeContinuation):fail('Expected native control value','argument_error')
    scheduler=evaluator.suspension
    if value.owner is not scheduler or value.epoch!=scheduler.epoch or not scheduler.active:
        fail('Control value cannot cross owners or epochs','ownership_error')
    scope=value.scope if type(value) is EscapeContinuation else value
    task=scheduler.current.task if scheduler.current else None
    if scope.recording_scope!=(task.recording_scope if task else None):
        fail('Control value cannot cross recording scopes','mode_error')
    return value


def tag(evaluator,name=''):
    if type(name) is not str or len(name)>200:fail('Prompt name requires at most 200 characters','argument_error')
    return PromptTag(evaluator,'prompt-tag-'+str(evaluator.suspension.epoch)+'-'+str(evaluator.suspension.next_id()),name)


def enter(evaluator,value):
    if type(value) is PromptScope:owned(value,evaluator)
    if value.state!='created':fail('Control scope may be entered only once','context_error')
    state=active_state(evaluator.suspension)
    if len(state.stack)>=MAX_DEPTH:limit('Control nesting exceeds 128')
    value.state,value.holder='active',state
    state.stack.append(value)
    return value


def release(value):
    if value.state=='closed':return
    state=value.holder
    if state is not None:
        if not state.stack or state.stack[-1] is not value:
            fail('Control scopes must close in LIFO order','context_error')
        state.stack.pop()
    value.state,value.holder='closed',None
    if type(value) is PromptScope:value.handler=None


def reachable(evaluator,target):
    state=active_state(evaluator.suspension)
    if target.state!='active' or target.holder is not state:return False
    for frame in reversed(state.stack):
        if frame is target:return True
        if type(frame) is Barrier:return False
    return False


def find(evaluator,value,*,required=False):
    tag_owned(evaluator,value)
    blocked=False
    for frame in reversed(active_state(evaluator.suspension).stack):
        if type(frame) is Barrier:blocked=True
        elif type(frame) is PromptScope and frame.tag is value:
            if blocked:
                if required:fail('Control barrier blocks outward transfer','control_barrier_error')
                return None
            return frame
    if required:fail('No active prompt for this tag','prompt_error')
    return None


def transfer(evaluator,target,items,authority=None):
    owned(target,evaluator)
    state=active_state(evaluator.suspension)
    if target.state!='active' or target.holder is not state or target not in state.stack:
        fail('Escape target is outside the current dynamic extent','escape_error')
    if not reachable(evaluator,target):fail('Control barrier blocks outward transfer','control_barrier_error')
    from .agent_tools import current_caps,scope
    from .conditions import intersection
    from .multiple_values import make,prepare
    ceiling=intersection(current_caps(evaluator),target.capabilities,authority)
    with scope(evaluator,ceiling):completion=prepare(evaluator,make(evaluator,items))
    raise PromptTransfer(target,completion,ceiling)


def invoke(evaluator,value,args,kwargs=None):
    owned(value,evaluator)
    if kwargs:fail('EscapeContinuation takes positional result values','argument_error')
    evaluator.argument_budget(args,{})
    transfer(evaluator,value.scope,args,value.capabilities)


def info(evaluator,value):
    owned(value,evaluator)
    if type(value) is not EscapeContinuation:fail('escape_info requires EscapeContinuation','argument_error')
    return {'id':value.id,'scopeId':value.scope.id,'state':value.scope.state,
            'active':value.scope.state=='active','available':reachable(evaluator,value.scope)}


def describe(value):
    if type(value)is Continuation:
        from .continuations import describe as continuation_describe
        return continuation_describe(value)
    if type(value) is PromptTag:return {'$handle':'PromptTag','id':value.id,'name':value.name}
    if type(value) is PromptScope:return {'$handle':'PromptScope','id':value.id,'state':value.state,'active':value.state=='active'}
    return {'$handle':'EscapeContinuation','id':value.id,'scopeId':value.scope.id,
            'state':value.scope.state,'active':value.scope.state=='active'}


def member(evaluator,value,name):
    if type(value)is Continuation:
        from .continuations import member as continuation_member
        return continuation_member(evaluator,value,name)
    owned(value,evaluator)
    if type(value) is PromptTag:fields={'id':value.id,'name':value.name}
    elif type(value) is PromptScope:fields={'id':value.id,'tag':value.tag,'state':value.state,'active':value.state=='active'}
    else:fields=info(evaluator,value)
    if name not in fields:fail('Control member does not exist: '+str(name),'attribute_error')
    return fields[name]


def project(evaluator,value,*sources):
    if type(value)is Continuation:
        from .continuations import project as continuation_project
        return continuation_project(evaluator,value,*sources)
    owned(value,evaluator)
    if type(value) is not EscapeContinuation:return value
    from .conditions import intersection
    from .agent_tools import current_caps
    ceiling=intersection(current_caps(evaluator),value.capabilities,
                    *(getattr(source,'capabilities',None) for source in sources))
    return value if ceiling==value.capabilities else replace(value,capabilities=ceiling)


def same_identity(left,right):
    """Authority views retain the same unforgeable control target identity."""
    if type(left)is Continuation or type(right)is Continuation:
        from .continuations import same_identity as continuation_same
        return continuation_same(left,right)
    return (type(left) is EscapeContinuation and type(right) is EscapeContinuation and
            left.owner is right.owner and left.epoch==right.epoch and
            left.scope is right.scope and left.id==right.id)


def callback(evaluator,function,args=(),authority=None):
    from .execution_frames import gateway
    from .execution_control import CallbackRequest
    return gateway(evaluator,CallbackRequest(function,args,authority))


def handled(evaluator,scope,handler,signal):
    from .execution_frames import gateway
    from .execution_control import PromptHandlerRequest
    return gateway(evaluator,PromptHandlerRequest(scope,handler,signal))


def resolve(evaluator,request):
    from .execution_frames import gateway
    return gateway(evaluator,request)


def request(evaluator,operation,values):
    from .objects import require_callable
    if operation=='prompt':
        tag_owned(evaluator,values[0]);require_callable(values[1],evaluator)
        if values[2] is not None:require_callable(values[2],evaluator)
    else:
        for function in values:require_callable(function,evaluator)
    return Request(operation,tuple(values))


def builtins(evaluator):
    def abort(tag,*items):transfer(evaluator,find(evaluator,tag,required=True),items)
    result={'prompt_tag':lambda name='':tag(evaluator,name),
            'call_prompt':lambda tag,body,handler=None:request(evaluator,'prompt',(tag,body,handler)),
            'prompt_scope':lambda tag,handler=None:PromptScope(evaluator,tag,handler),
            'abort_prompt':abort,
            'prompt_available':lambda tag:find(evaluator,tag) is not None,
            'call_escape':lambda body:request(evaluator,'escape',(body,)),
            'escape_info':lambda continuation:info(evaluator,continuation),
            'control_barrier':lambda body:request(evaluator,'barrier',(body,)),
            'dynamic_wind':lambda before,body,after:request(evaluator,'wind',(before,body,after))}
    from .continuations import builtins as continuation_builtins
    result.update(continuation_builtins(evaluator))
    return result
