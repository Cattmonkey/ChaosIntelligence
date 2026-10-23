"""Native logical-context bindings. Association copies retain original values.

No host thread context, eval, authority or active recovery stack is captured.
"""
from dataclasses import dataclass

MAX_CREATED = 10000
MAX_SLOTS = 100000


def error(message, code='context_error'):
    from .language import LanguageError
    raise LanguageError(message, code=code)


def missing():
    from .language import MISSING
    return MISSING


def register(value, scheduler):
    from .language import ResourceLimit
    if len(scheduler.context_values) >= MAX_CREATED:
        raise ResourceLimit('Context epoch 超过 10000 个原生值')
    scheduler.context_values.append(value)
    value.owner, value.epoch = scheduler, scheduler.epoch
    value.id = 'context-'+str(scheduler.epoch)+'-'+str(len(scheduler.context_values))


class Context:
    def __init__(self, scheduler, bindings=None):
        self.bindings={};self.active=False;self.leases=[]
        register(self, scheduler)
        bindings = {} if bindings is None else bindings
        charge(scheduler, len(bindings))
        self.bindings = dict(bindings)
        self.active = False
        self.leases = []


class ContextVar:
    def __init__(self, evaluator, name, default):
        if type(name) is not str or len(name)>200: error('ContextVar name 需要至多 200 字符 String','argument_error')
        if default is not missing(): validate(evaluator, default)
        register(self, evaluator.suspension)
        self.name, self.default = name, default


class ContextToken:
    def __init__(self, evaluator, variable, context):
        register(self, evaluator.suspension)
        self.variable, self.context = variable, context
        self.old_value = context.bindings.get(variable, missing())
        self.used = False
        self.entered=False


class ContextScope:
    def __init__(self, evaluator, bindings):
        if type(bindings) is not list or len(bindings)>128: error('context_bind 需要至多128项 List','argument_error')
        parsed=[]; seen=set()
        for binding in bindings:
            if type(binding) is not dict or set(binding)!={'variable','value'}: error('binding 需要 variable/value','argument_error')
            variable=owned(binding['variable'],evaluator,ContextVar)
            if variable in seen:error('同一 scope 变量不能重复','argument_error')
            seen.add(variable);validate(evaluator,binding['value']);parsed.append((variable,binding['value']))
        register(self,evaluator.suspension)
        self.bindings=parsed;self.state='created'


VALUES=(ContextVar,ContextToken,ContextScope,Context)


class State:
    def __init__(self,scheduler,context=None):
        self.owner=scheduler;self.current=context or Context(scheduler)
    def child(self):return State(self.owner,Context(self.owner,self.current.bindings))


def active_state(scheduler):
    runner=scheduler.stream.runner if scheduler.stream is not None else scheduler.current
    return runner.context_state if runner is not None else scheduler.context_state


def charge(scheduler,count):
    from .language import ResourceLimit
    if scheduler.context_slots+count>MAX_SLOTS:raise ResourceLimit('Context epoch 累计绑定复制超过100000项')
    scheduler.context_slots+=count
    for _ in range(count):scheduler.evaluator.tick()


def owned(value,evaluator,kind=None):
    s=evaluator.suspension
    if type(value) not in VALUES or (kind is not None and type(value) is not kind):error('需要原生上下文值','argument_error')
    if value.owner is not s or value.epoch!=s.epoch or not s.active:error('Context 值不能跨 owner/epoch 使用','ownership_error')
    return value


def validate(evaluator,value):
    from .class_creation import validate_value
    validate_value(evaluator,value)
    return value


def read(evaluator,value):
    # Validate original live graph at every explicit retrieval. Calls still enter
    # original _s_invoke and intersect current authority; data grants no caps.
    validate(evaluator,value)
    from .agent_tools import current_caps
    ceiling=current_caps(evaluator)
    if ceiling is not None:
        from .class_creation import _project_return
        return _project_return(evaluator,value,ceiling)
    return value


def current(evaluator):return active_state(evaluator.suspension).current


def get(evaluator,variable,default=None,*,provided=False):
    owned(variable,evaluator,ContextVar)
    value=current(evaluator).bindings.get(variable,missing())
    if value is missing():value=default if provided else variable.default
    if value is missing():error('ContextVar 未绑定且没有 default','context_lookup_error')
    return read(evaluator,value)


def set_value(evaluator,variable,value):
    owned(variable,evaluator,ContextVar);validate(evaluator,value)
    context=current(evaluator)
    token=ContextToken(evaluator,variable,context)
    if variable not in context.bindings:charge(evaluator.suspension,1)
    context.bindings[variable]=value
    return token


def reset(evaluator,variable,token):
    owned(variable,evaluator,ContextVar);owned(token,evaluator,ContextToken)
    if token.variable is not variable or token.context is not current(evaluator):error('Token 需要原变量及原 Context','context_token_error')
    if token.used or token.entered:error('Token 已使用或由活动with租约管理','context_token_error')
    old=token.old_value
    if old is not missing():validate(evaluator,old)
    if old is missing():token.context.bindings.pop(variable,None)
    else:token.context.bindings[variable]=old
    token.used=True
    return None


def copy(evaluator,context=None):
    context=current(evaluator) if context is None else owned(context,evaluator,Context)
    return Context(evaluator.suspension,context.bindings)


def get_in(evaluator,context,variable,default=None,*,provided=False):
    owned(context,evaluator,Context);owned(variable,evaluator,ContextVar)
    if variable in context.bindings:return read(evaluator,context.bindings[variable])
    if provided:return read(evaluator,default)
    error('Context 中没有该变量','context_lookup_error')


def items(evaluator,context,kind='items'):
    from .language import TupleValue
    owned(context,evaluator,Context)
    for variable in context.bindings:owned(variable,evaluator,ContextVar)
    if kind=='keys':return list(context.bindings)
    if kind=='values':return [read(evaluator,value) for value in context.bindings.values()]
    return [TupleValue([variable,read(evaluator,value)]) for variable,value in context.bindings.items()]


def describe(value):
    result={'$handle':type(value).__name__,'id':value.id}
    if type(value) is ContextVar:result.update(name=value.name,has_default=value.default is not missing())
    elif type(value) is ContextToken:result.update(used=value.used,has_old_value=value.old_value is not missing())
    elif type(value) is ContextScope:result.update(state=value.state,bindingCount=len(value.bindings))
    else:result.update(active=value.active,bindingCount=len(value.bindings))
    return result


class Lease:
    def __init__(self,evaluator,manager,context,previous):
        self.manager,self.context,self.previous=manager,context,previous;self.released=False
        context.leases.append(self)
    def release(self):
        if self.released:return
        self.released=True
        if type(self.manager) is ContextToken:
            if not self.manager.used:
                variable=self.manager.variable;old=self.manager.old_value
                if old is missing():self.context.bindings.pop(variable,None)
                else:self.context.bindings[variable]=old
                self.manager.used=True
        else:
            for variable,old in self.previous:
                if old is missing():self.context.bindings.pop(variable,None)
                else:self.context.bindings[variable]=old
            self.manager.state='closed';self.manager.bindings=[]
        self.previous=[]
        if self in self.context.leases:self.context.leases.remove(self)


def acquire(evaluator,manager):
    owned(manager,evaluator)
    context=current(evaluator)
    if type(manager) is ContextToken:
        if manager.used or manager.entered or manager.context is not context:error('Token scope 需要未使用的原 Context Token','context_token_error')
        manager.entered=True
        previous=[]
    else:
        if manager.state!='created':error('ContextScope 只能进入一次')
        for variable,value in manager.bindings:owned(variable,evaluator,ContextVar);validate(evaluator,value)
        charge(evaluator.suspension,sum(variable not in context.bindings for variable,_ in manager.bindings))
        previous=[(variable,context.bindings.get(variable,missing())) for variable,_ in manager.bindings]
        for variable,value in manager.bindings:context.bindings[variable]=value
        manager.state='active'
    lease=Lease(evaluator,manager,context,previous)
    return manager,lease


@dataclass
class RunRequest:
    context:object
    function:object
    args:object
    kwargs:object


def run(evaluator,context,function,args=None,kwargs=None):
    from .objects import require_callable
    owned(context,evaluator,Context);require_callable(function,evaluator)
    args=[] if args is None else args;kwargs={} if kwargs is None else kwargs
    if type(args) is not list or type(kwargs) is not dict:error('context_run args/kwargs 需要 List/Record','argument_error')
    validate(evaluator,args);validate(evaluator,kwargs)
    return RunRequest(context,function,args,kwargs)


def resolve(evaluator,request):
    from .execution_context_protocols import context_gateway
    return context_gateway(evaluator,request)


def member(value,name,evaluator):
    from .language import Native
    owned(value,evaluator)
    if type(value) is ContextToken and name=='var':return owned(value.variable,evaluator,ContextVar)
    if type(value) is ContextToken and name=='old_value':
        if value.old_value is missing():error('Token 没有 old_value','context_lookup_error')
        return read(evaluator,value.old_value)
    methods={}
    if type(value) is ContextVar:
        methods={'get':lambda default=missing():get(evaluator,value,default,provided=default is not missing()),'set':lambda item:set_value(evaluator,value,item),'reset':lambda token:reset(evaluator,value,token)}
    elif type(value) is Context:
        methods={'get':lambda variable,default=missing():get_in(evaluator,value,variable,default,provided=default is not missing()),'copy':lambda:copy(evaluator,value),'run':lambda function,args=None,kwargs=None:run(evaluator,value,function,args,kwargs),'has':lambda variable:has(evaluator,value,variable),'items':lambda:items(evaluator,value),'keys':lambda:items(evaluator,value,'keys'),'values':lambda:items(evaluator,value,'values')}
    if name in methods:return Native(type(value).__name__+'.'+name,methods[name],evaluator,spec_id=type(value).__name__+'.'+name,captures=(value,))
    metadata=describe(value)
    if name in metadata and name!='$handle':return metadata[name]
    error('Context 成员不存在 '+str(name),'attribute_error')


def has(evaluator,context,variable):
    owned(context,evaluator,Context);owned(variable,evaluator,ContextVar)
    return variable in context.bindings


def finish(scheduler):
    for value in reversed(scheduler.context_values):
        if type(value) is Context:
            for lease in reversed(list(value.leases)):lease.release()
    for value in scheduler.context_values:
        if type(value) is Context:value.bindings.clear()
        elif type(value) is ContextVar:value.default=missing()
        elif type(value) is ContextToken:value.old_value=missing()
        else:value.bindings=[]
    scheduler.context_values.clear();scheduler.context_slots=0


def builtins(evaluator):
    return {'context_var':lambda name,default=missing():ContextVar(evaluator,name,default),
            'context_get':lambda variable,default=missing():get(evaluator,variable,default,provided=default is not missing()),
            'context_set':lambda variable,value:set_value(evaluator,variable,value),
            'context_reset':lambda variable,token:reset(evaluator,variable,token),
            'context_bind':lambda bindings:ContextScope(evaluator,bindings),
            'context':lambda:Context(evaluator.suspension),
            'copy_context':lambda:copy(evaluator),
            'context_copy':lambda context:copy(evaluator,context),
            'context_get_in':lambda context,variable,default=missing():get_in(evaluator,context,variable,default,provided=default is not missing()),
            'context_has':lambda context,variable:has(evaluator,context,variable),
            'context_items':lambda context:items(evaluator,context),
            'context_info':lambda value:{key:item for key,item in describe(owned(value,evaluator)).items() if key!='$handle'},
            'context_run':lambda context,function,args=None,kwargs=None:run(evaluator,context,function,args,kwargs)}
