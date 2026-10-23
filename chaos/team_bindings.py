"""Live role implementations on the existing AST scheduler, never serialized code."""
import uuid
from dataclasses import dataclass
from .language import LanguageError,Agent,Native,serializable
from .team_values import Team,owned as team_owned,_role
from .agent_tools import current_caps
from .execution_scope import scope


@dataclass(frozen=True,eq=False)
class BoundTeam:
    owner: object
    epoch: int
    team: Team
    bindings: tuple
    capabilities: object


def owned(value,evaluator):
    if type(value)is not BoundTeam or value.owner is not evaluator or value.epoch!=evaluator.suspension.epoch:
        raise LanguageError('Team bindings cannot cross evaluators or epochs',code='ownership_error')
    return value


def implementation_kind(value):
    from .agent_tools import BoundAgent
    from .session_tools import BoundSession
    from .session_values import AgentSession
    from .program_values import Program
    from .process_values import Process
    from .robot_values import Robot
    from .computer_values import Computer
    from .modes import Plan,Goal,Skill
    from .bot_values import Bot
    kinds=(Bot,Agent,BoundAgent,AgentSession,BoundSession,Team,BoundTeam,Program,Process,Plan,Goal,Skill,Computer,Robot)
    return type(value).__name__ if type(value)in kinds else 'Function'


def bind(evaluator,value,bindings):
    from .objects import require_callable
    team_owned(value,evaluator)
    if type(bindings)is not dict or len(bindings)>64:raise LanguageError('Team bindings require a role Record',code='team_error')
    for key,target in bindings.items():
        _role(value,key);require_callable(target,evaluator)
    return BoundTeam(evaluator,evaluator.suspension.epoch,value,tuple(sorted(bindings.items())),current_caps(evaluator))


def binding_info(value):
    return {'team':value.team.definition,'definitionHash':value.team.definition_hash,
            'bindings':{key:{'kind':implementation_kind(target)}for key,target in value.bindings}}


@dataclass(frozen=True)
class RoleRequest:
    binding: BoundTeam
    role: str
    args: tuple
    kwargs: dict


def call(evaluator,value,args,kwargs=None,role=None):
    owned(value,evaluator);role=role or value.team.definition['entry'];_role(value.team,role)
    evaluator.argument_budget(args,kwargs or {})
    with scope(evaluator,value.capabilities):
        task=evaluator.suspension.task(_run(evaluator,value,role,tuple(args),dict(kwargs or {})), capture_roots=(value,) + tuple(args) + tuple((kwargs or {}).values()))
    task.agent_operation=True;task.context_structured=True
    return task


def _run(evaluator,value,role_id,args,kwargs):
    from .contexts import _await,_operation
    from .suspension import Task
    owned(value,evaluator)
    role,team=_role(value.team,role_id)
    target=dict(value.bindings).get(role_id)
    if target is None:target=Agent(role['agent']['name'],role['agent']['config'],evaluator)
    team={**team,'implementationKind':implementation_kind(target),'invocationId':'role-'+uuid.uuid4().hex}
    task=evaluator.suspension.current.task
    provenance={key:item for key,item in team.items()if key!='capabilities'}
    def emit(event):
        if evaluator.trace_handler:evaluator.trace_handler({'event':'team.role.'+event,**provenance,'taskId':task.id,'location':dict(task.location)})
    with scope(evaluator,value.capabilities,team),scope(evaluator,frozenset(role['capabilities'])):
        evaluator.tick();emit('started')
        try:
            created=len(evaluator.suspension.tasks)
            result=yield from evaluator._s_invoke(target,list(args),kwargs,all_results=True)
            # Only a raw Task retains the legacy role auto-await protocol.
            # Explicit completion components are ordinary returned values.
            if isinstance(result,Task):result=yield from _await(evaluator,result,_operation(evaluator,result,evaluator.suspension.tasks[created:]),all_results=True)
            evaluator.tick()
        except BaseException:
            emit('failed');raise
        emit('completed');return result


def role_handle(evaluator,value,role):
    owned(value,evaluator);_role(value.team,role)
    def invoke(*args,**kwargs):return RoleRequest(value,role,args,kwargs)
    return Native('BoundTeam.'+role,invoke,evaluator,captures=(value,))


def builtins(evaluator):
    return {'team_bind':lambda value,bindings:bind(evaluator,value,bindings),
            'team_binding_info':lambda value:binding_info(owned(value,evaluator))}
