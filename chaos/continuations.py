"""Epoch-local compositional continuation images; never source replay or deepcopy."""
from dataclasses import dataclass,replace

MAX_FRAMES=128
MAX_INVOCATIONS=1000
MAX_IMAGES=1000


def fail(message,code='continuation_barrier_error'):
    from .language import LanguageError,ResourceLimit
    if code=='resource_limit':raise ResourceLimit(message)
    raise LanguageError(message,code=code)


@dataclass(frozen=True,eq=False)
class Continuation:
    owner:object
    epoch:int
    holder:object
    recording_scope:object
    id:str
    image:object
    capabilities:object=None


def owned(e,value):
    from .control_prompts import active_state
    if type(value)is not Continuation:fail('Expected Continuation','argument_error')
    s=e.suspension
    if value.owner is not e or value.epoch!=s.epoch or not s.active or value.holder is not active_state(s):
        fail('Continuation cannot cross evaluator, epoch or logical holder','ownership_error')
    task=s.current.task if s.current else None
    if task is not None or s.stream is not None:fail('Continuation invocation cannot cross Task or stream holders','ownership_error')
    if value.recording_scope!=(task.recording_scope if task else None):fail('Continuation cannot cross recording scopes','mode_error')
    value.image.validate(e)
    return value


def info(e,value):
    owned(e,value)
    return {'id':value.id,'state':'available','available':True,'frames':len(value.image.rows),'invocations':value.image.invocations}


def describe(value):
    return {'$handle':'Continuation','id':value.id,'state':'available','available':True,'frames':len(value.image.rows),'invocations':value.image.invocations}


def member(e,value,name):
    fields=info(e,value)
    if name not in fields:fail('Continuation member does not exist: '+str(name),'attribute_error')
    return fields[name]


def project(e,value,*sources):
    owned(e,value)
    from .conditions import intersection
    from .agent_tools import current_caps
    ceiling=intersection(current_caps(e),value.capabilities,*(getattr(x,'capabilities',None)for x in sources))
    return value if ceiling==value.capabilities else replace(value,capabilities=ceiling)


def same_identity(a,b):
    return type(a)is Continuation and type(b)is Continuation and a.owner is b.owner and a.epoch==b.epoch and a.id==b.id and a.image is b.image


def capture_request(e,tag,receiver):
    from .control_prompts import tag_owned,Request
    from .objects import require_callable
    tag_owned(e,tag);require_callable(receiver,e)
    return Request('capture_continuation',(tag,receiver))


def invoke_request(e,value,args,kwargs=None):
    owned(e,value)
    if kwargs:fail('Continuation accepts positional result values','argument_error')
    e.argument_budget(args,{})
    from .control_prompts import Request
    from .multiple_values import make,prepare
    return Request('compose_continuation',(value,prepare(e,make(e,args))))


def builtins(e):
    return {'capture_continuation':lambda tag,receiver:capture_request(e,tag,receiver),'continuation_info':lambda continuation:info(e,continuation)}


def roots(e,value):
    owned(e,value)
    from .capture_frames import Edge
    return (Edge(e,value.capabilities,value.image.roots),)
