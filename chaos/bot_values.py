"""Owned callable Bot values; public snapshots cannot revive a work scope."""
from dataclasses import dataclass,field,replace
import json


def fail(message,code='bot_error'):
    from .language import LanguageError
    raise LanguageError(message,code=code)


def data(value):
    from .guidance_values import data as copied
    return copied(value,500000,20000,64)


@dataclass(eq=False)
class State:
    id:str
    goal:object
    context:object
    input:object=None
    guidance:list=field(default_factory=list)
    evidence:list=field(default_factory=list)
    history:list=field(default_factory=list)
    revision:int=0
    iteration:int=0
    status:str='idle'
    busy:bool=False
    output:object=None


@dataclass(frozen=True,eq=False)
class Bot:
    owner:object
    epoch:int
    state:State
    planner:object
    execute:object
    verify:object
    max_iterations:object=20
    capabilities:object=None
    mailbox:str='auto'
    role:object=None


def owned(value,evaluator):
    if type(value) is not Bot:fail('Expected an owned Bot','argument_error')
    scheduler=evaluator.suspension
    if value.owner is not evaluator or value.epoch!=scheduler.epoch or not scheduler.active:
        fail('Bot cannot cross evaluators or execution epochs','ownership_error')
    return value


def make(evaluator,goal,planner,execute,verify,context=None,max_iterations=20,*,mailbox="auto",role=None):
    from .objects import require_callable
    from .agent_tools import current_caps
    scheduler=evaluator.suspension
    if not scheduler.active:fail('Bot requires an active execution epoch','ownership_error')
    if max_iterations is not None and (type(max_iterations) is not int or not 1<=max_iterations<=10000):
        fail('Bot max_iterations requires 1..10000 or null','argument_error')
    if type(mailbox) is not str or mailbox not in ('auto','scoped'):
        fail('Bot mailbox requires auto or scoped','argument_error')
    if role is not None:
        import re
        if type(role)is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',role)is None:
            fail('Bot role requires an explicit Process role key','argument_error')
    callbacks=tuple(require_callable(value,evaluator) for value in (planner,execute,verify))
    value=Bot(evaluator,scheduler.epoch,State('bot-'+str(scheduler.next_id()),data(goal),data(context)),*callbacks,max_iterations,current_caps(evaluator),mailbox,role)
    from .capture_graph import seal
    seal(evaluator,(value,),current_caps(evaluator))
    return value


def snapshot(value):
    state=value.state
    return data({'goal':state.goal,'context':state.context,'input':state.input,
                 'guidance':state.guidance,'evidence':state.evidence,'history':state.history,
                 'revision':state.revision,'iteration':state.iteration})


def info(value):
    state=value.state
    return {'id':state.id,'status':state.status,'busy':state.busy,'revision':state.revision,
            'iteration':state.iteration,'maxIterations':value.max_iterations,
            'needsReconciliation':state.status=='needs_reconciliation'}


def describe(value):
    return {'$bot':value.state.id,'status':value.state.status,'revision':value.state.revision}


def same_identity(left,right):
    return type(left) is Bot and type(right) is Bot and left.owner is right.owner and left.epoch==right.epoch and left.state is right.state


def project(evaluator,value,*sources):
    from .agent_tools import current_caps
    from .generic_functions import intersection
    owned(value,evaluator)
    ceiling=intersection(current_caps(evaluator),value.capabilities,*(getattr(source,'capabilities',None) for source in sources))
    return value if ceiling==value.capabilities else replace(value,capabilities=ceiling)


def reconcile(evaluator,value,context,evidence):
    owned(value,evaluator)
    if value.state.busy or value.state.status!='needs_reconciliation':
        fail('Only an inactive Bot needing reconciliation can be reconciled','bot_state_error')
    context,evidence=data(context),data(evidence)
    history=data(value.state.history+[{'kind':'reconciliation','revision':value.state.revision,'evidence':evidence}])
    retained=data(value.state.evidence+[{'reconciliation':evidence}])
    value.state.context,value.state.history,value.state.evidence=context,history,retained
    value.state.status='idle'
    return value


def call(evaluator,value,input=None):
    from .agent_tools import scope
    from .bot_runtime import run
    owned(value,evaluator);input=data(input)
    with scope(evaluator,value.capabilities):
        task=evaluator.suspension.task(run(evaluator,value,input),kind='bot',capture_roots=(value,input))
    task.agent_operation=True;task.context_structured=True
    return task


def validate_embedded(evaluator,value):
    # Reuse the bounded ordinary-container walk without invoking user hooks.
    from .tuple_values import TupleValue
    from .set_values import SetValue
    from .hash_maps import HashMap,children
    from .eql_specializers import EqlSpecializer
    pending=[(value,0)];seen=set()
    while pending:
        item,depth=pending.pop();kind=type(item)
        if kind is Bot:owned(item,evaluator);continue
        if kind not in (list,dict,TupleValue,SetValue,HashMap,EqlSpecializer):continue
        if id(item) in seen:continue
        seen.add(id(item))
        if len(seen)>20000 or depth>100:
            from .language import ResourceLimit
            raise ResourceLimit('Bot result graph exceeds its bounded container limit')
        items=children(item) if kind is HashMap else (item.payload,) if kind is EqlSpecializer else item.values() if kind is dict else item
        pending.extend((child,depth+1) for child in items)
    return value


def builtins(evaluator):
    return {'bot':lambda goal,planner,execute,verify,context=None,max_iterations=20,*,mailbox='auto',role=None:make(evaluator,goal,planner,execute,verify,context,max_iterations,mailbox=mailbox,role=role),
            'bot_info':lambda bot:info(owned(bot,evaluator)),
            'bot_context':lambda bot:snapshot(owned(bot,evaluator)),
            'bot_reconcile':lambda bot,context,evidence:reconcile(evaluator,bot,context,evidence)}
