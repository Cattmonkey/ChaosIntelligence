"""Native dynamic situation handlers and recoveries on the original AST stack.

Real child tasks/streams start empty. Transparent protocol runners share a
logical holder; private case transfers cannot jump into another holder.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from .condition_values import Condition, info as condition_info, validate
from .control_prompts import NonlocalTransfer

MAX_BINDINGS, MAX_SCOPES, MAX_CREATED = 128, 128, 10000


def error(message,code='condition_protocol_error'):
    from .language import LanguageError
    raise LanguageError(message,code=code)


class DynamicState:
    def __init__(self,scheduler):
        self.owner=scheduler; self.scopes=[]; self.hidden=set()


def active_state(scheduler):
    runner=scheduler.stream.runner if scheduler.stream is not None else scheduler.current
    return runner.condition_state if runner is not None else scheduler.condition_state


def caps(evaluator):
    from .agent_tools import current_caps
    return current_caps(evaluator)


def intersection(*values):
    result=None
    for value in values:
        if value is not None: result=value if result is None else result & value
    return result


class HandlerScope:
    def __init__(self,evaluator,bindings):
        scheduler=evaluator.suspension
        self.owner,self.epoch=scheduler,scheduler.epoch
        self.id='handlers-'+str(scheduler.next_id())
        self.bindings=tuple(bindings); self.state='created'; self.holder=None
        self.capabilities=caps(evaluator)
        current=scheduler.current.task if scheduler.current else None
        self.recording_scope=current.recording_scope if current else None


class RestartScope(HandlerScope):
    def __init__(self,evaluator,bindings,mode='inline'):
        super().__init__(evaluator,bindings)
        self.id='restarts-'+self.id.split('-')[-1]; self.mode=mode
        self.restarts=tuple(Restart(self,index,binding) for index,binding in enumerate(bindings))


class Restart:
    def __init__(self,scope,index,binding):
        self.owner,self.epoch=scope.owner,scope.epoch
        self.scope,self.index=scope,index
        self.id=scope.id+'/'+str(index)
        self.name,self.function,self.report,self.test=binding
        self.capabilities=scope.capabilities
        self.recording_scope=scope.recording_scope


SCOPES=(HandlerScope,RestartScope)
VALUES=SCOPES+(Restart,)


class RestartTransfer(NonlocalTransfer):
    def __init__(self,restart,args,kwargs,authority,span):
        self.restart,self.args,self.kwargs=restart,args,kwargs
        self.authority,self.span=authority,span


@dataclass
class Request:
    operation: str
    value: object=None
    extra: object=None


def owned(value,evaluator,*,active=False):
    scheduler=evaluator.suspension
    if type(value) not in VALUES or value.owner is not scheduler or value.epoch!=scheduler.epoch or not scheduler.active:
        error('恢复句柄不能跨 owner 或执行周期使用','ownership_error')
    current=scheduler.current.task if scheduler.current else None
    recording=current.recording_scope if current else None
    if value.recording_scope!=recording: error('恢复句柄不能跨录制 scope 使用','mode_error')
    if active:
        scope=value.scope if type(value) is Restart else value
        if scope.state!='active' or scope.holder is not active_state(scheduler) or scope not in scope.holder.scopes:
            error('Restart 不在当前逻辑执行的活动动态范围','restart_error')
    return value


def describe(value):
    if type(value) is Restart:
        return {'$handle':'Restart','id':value.id,'name':value.name,'report':value.report,
                'mode':value.scope.mode,'active':value.scope.state=='active'}
    return {'$handle':type(value).__name__,'id':value.id,'state':value.state,
            'bindingCount':len(value.bindings),**({'mode':value.mode} if type(value) is RestartScope else {})}


def restart_info(evaluator,value):
    if type(value) is not Restart:error('restart_info 需要 Restart','argument_error')
    owned(value,evaluator)
    return {key:item for key,item in describe(value).items() if key!='$handle'}


def create(evaluator,bindings,restarts=False,mode='inline'):
    from .language import ResourceLimit
    from .objects import require_callable
    s=evaluator.suspension
    if not s.active:error('恢复 scope 需要活动执行周期','ownership_error')
    if type(bindings) is not list or len(bindings)>MAX_BINDINGS:error('恢复 bindings 需要最多128项 List','argument_error')
    parsed=[]
    for binding in bindings:
        evaluator.tick()
        if type(binding) is not dict:error('恢复 binding 需要 Record','argument_error')
        if restarts:
            if set(binding)-{'name','function','report','test'} or not {'name','function'}.issubset(binding):error('Restart binding 字段无效','argument_error')
            name=binding['name']; report=binding.get('report',name or '')
            if name is not None and (type(name) is not str or not name or len(name)>160):error('Restart name 需要 Null 或1..160字符 String','argument_error')
            if type(report) is not str or len(report)>2000:error('Restart report 需要最多2000字符 String','argument_error')
            function=require_callable(binding['function'],evaluator)
            test=binding.get('test')
            if test is not None:require_callable(test,evaluator)
            parsed.append((name,function,report,test))
        else:
            if set(binding)!= {'predicate','handler'}:error('Handler binding 需要 predicate 和 handler','argument_error')
            parsed.append((require_callable(binding['predicate'],evaluator),require_callable(binding['handler'],evaluator)))
    if len(s.condition_scopes)>=MAX_CREATED:raise ResourceLimit('恢复 scope 数量超过10000')
    scope=RestartScope(evaluator,parsed,mode) if restarts else HandlerScope(evaluator,parsed)
    s.condition_scopes.append(scope)
    return scope


def enter(evaluator,scope):
    from .language import ResourceLimit
    owned(scope,evaluator)
    if scope.state!='created':error('恢复 scope 只能进入一次','context_error')
    holder=active_state(evaluator.suspension)
    if len(holder.scopes)>=MAX_SCOPES:raise ResourceLimit('恢复 scope 嵌套超过128')
    scope.holder,scope.state=holder,'active'; holder.scopes.append(scope)
    return scope


def preflight_release(evaluator,scope):
    if scope.state!='active':return
    if scope.holder is not active_state(evaluator.suspension):error('恢复租约属于另一逻辑执行','ownership_error')
    if not scope.holder.scopes or scope.holder.scopes[-1] is not scope:error('恢复租约必须按LIFO关闭','context_error')


def release(scope):
    if scope.state=='closed':return
    if scope.holder is not None:
        if scope in scope.holder.scopes:scope.holder.scopes.remove(scope)
        scope.holder.hidden.discard(scope)
    scope.state='closed'


@contextmanager
def callback_authority(evaluator,*authorities):
    from .agent_tools import scope
    with scope(evaluator,intersection(*authorities)):yield


def callback(evaluator,function,args,kwargs=None,authority=None,*,all_results=False):
    from .execution_conditions import RecoveryCallbackRequest
    from .execution_frames import gateway
    return gateway(evaluator,RecoveryCallbackRequest(function,args,kwargs,authority,all_results))


def signal(evaluator,value,required=False):
    return resolve(evaluator,Request('error' if required else 'signal',value))


def debugger(evaluator,value):
    return resolve(evaluator,Request('debugger',value))


def compute(evaluator,condition=None,name=None):
    return resolve(evaluator,Request('_compute',(condition,name)))


def invoke(evaluator,designator,args,kwargs):
    return resolve(evaluator,Request('invoke',designator,(args,kwargs)))


def case(evaluator,body,bindings):
    return resolve(evaluator,Request('case',body,bindings))


def resolve(evaluator,request):
    from .execution_frames import gateway
    return gateway(evaluator,request)


def member(evaluator,value,name):
    owned(value,evaluator)
    metadata=describe(value)
    aliases={'binding_count':'bindingCount'}
    key=aliases.get(name,name)
    if key not in metadata or key=='$handle':error('恢复句柄成员不存在','attribute_error')
    return metadata[key]


def finish(scheduler):
    for scope in scheduler.condition_scopes:release(scope)


def builtins(evaluator):
    from .condition_values import make,info
    return {
        'condition':lambda kind,message='',data=None:make(kind,message,data,evaluator.tick),
        'condition_info':lambda value:info(value,evaluator.tick),
        'handler_bind':lambda bindings:create(evaluator,bindings),
        'restart_bind':lambda bindings:create(evaluator,bindings,True),
        'restart_case':lambda body,bindings:Request('case',body,bindings),
        'signal_condition':lambda value:Request('signal',value),
        'error_condition':lambda value:Request('error',value),
        'invoke_debugger':lambda value:Request('debugger',value),
        'compute_restarts':lambda condition=None:Request('compute',condition),
        'find_restart':lambda name,condition=None:Request('find',(name,condition)),
        'invoke_restart':lambda restart,args=[],kwargs={}:Request('invoke',restart,(args,kwargs)),
        'restart_info':lambda value:restart_info(evaluator,value),
    }
