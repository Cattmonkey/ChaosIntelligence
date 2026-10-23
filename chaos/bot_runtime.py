"""Native Bot work on the original callable, TaskGroup and effect machinery."""
from dataclasses import dataclass
import uuid


@dataclass(eq=False)
class Decision:
    owner:object
    epoch:int
    scope_id:str
    revision:int
    decision_id:str
    ticket:str
    active:bool=True
    group:object=None
    phase:str='execution'
    body_entered:bool=False
    ancestry:tuple=()
    parents:tuple=()
    inherited_count:int=0


def credential(decision):
    return (decision.scope_id,decision.decision_id,decision.revision,decision.ticket)


def _valid(evaluator,decision):
    from .bot_values import fail
    if (decision.owner is not evaluator or decision.epoch!=evaluator.suspension.epoch or not decision.active
            or type(decision.inherited_count)is not int or not 0<=decision.inherited_count<=len(decision.ancestry)
            or len(decision.parents)+decision.inherited_count!=len(decision.ancestry) or len(decision.ancestry)>=16):
        fail('Bot decision is no longer active','bot_state_error')
    for parent,identity in zip(decision.parents,decision.ancestry[decision.inherited_count:]):
        if (type(parent)is not Decision or parent.owner is not evaluator or
                parent.epoch!=evaluator.suspension.epoch or not parent.active or credential(parent)!=identity):
            fail('Ancestor Bot decision is no longer active','bot_state_error')
    return decision


def _wire(parts):
    if not parts:return None
    def row(part):
        return dict(zip(('scopeId','decisionId','revision','ticket'),part))
    result=row(parts[-1])
    if len(parts)>1:result['ancestors']=[row(part) for part in parts[:-1]]
    return result


def active_decision(evaluator):
    runner=evaluator.suspension.current
    decision=getattr(runner,'bot_guard',None) if runner is not None else None
    if decision is None:return None
    return _valid(evaluator,decision)


def current_guard(evaluator):
    decision=active_decision(evaluator)
    if decision is None:return _wire(getattr(evaluator,"_inherited_bot_ancestry",()))
    # Admission itself runs before the new leaf has a ticket. Its already
    # admitted ancestors still guard that control operation.
    return _wire(decision.ancestry+((credential(decision),) if decision.ticket else ()))



def check_handle(evaluator,handle):
    decision=active_decision(evaluator)
    captured=getattr(handle,'bot_guard',None)
    if captured is not None:_valid(evaluator,captured)
    if decision is not None and captured is not decision:
        from .bot_values import fail
        fail('Bot cannot borrow work from another decision','bot_state_error')


@dataclass(frozen=True,eq=False)
class BotRequest:
    operation:str
    values:tuple


def _request(operation,*values):
    from .execution_frames import request_proxy
    return request_proxy(BotRequest(operation,values))


def host(evaluator,operation,payload):return _request('host',operation,payload)
def callback(evaluator,function,args):return _request('callback',function,args)
def close_owned(evaluator,token,original):return _request('close_owned',token,original)
def phase(evaluator,token,function,args):return _request('phase',token,function,args)
def decision(evaluator,scope_id,revision,phase='execution'):return _request('decision',scope_id,revision,phase)
def drain(evaluator,value,scope_id):return _request('drain',value,scope_id)
def save_outcome(evaluator,value,scope_id,token):return _request('outcome',value,scope_id,token)

def external_prefix(packet):
    return any(row.get('kind') not in ('agent','agent_transition') for row in packet['actions'])


def result(value):
    from .bot_values import data,snapshot
    return data({'done':value.state.status=='achieved','status':value.state.status,'state':snapshot(value),'output':value.state.output})



def commit(evaluator,value,scope_id,terminal,done,context,output):
    return _request('commit',value,scope_id,terminal,done,context,output)


def run(evaluator,value,input):return _request('run',value,input)
