"""Evaluator-owned objects and explicit descriptors; no Python attribute forwarding."""

from .exception_classes import intrinsic
from dataclasses import dataclass, field, replace


def _capture_authority(value):
    from .agent_tools import current_caps
    caps=current_caps(value.owner)if value.owner is not None else None
    for source in (value,getattr(value,'receiver',None),getattr(value,'klass',None),getattr(value,'previous',None)):
        inherited=getattr(source,'capabilities',None)
        if inherited is not None:caps=inherited if caps is None else caps & inherited
    value.capabilities=caps


def _capturable_callable(value):
    """Runtime function authority carriers, distinct from nominal protocols."""
    from .language import Function, Native, Agent
    from .control_prompts import EscapeContinuation
    from .continuations import Continuation
    from .agent_tools import BoundAgent
    from .team_bindings import BoundTeam
    from .session_tools import BoundSession
    return type(value) in (Function, Native, Agent, BoundMethod, BoundAgent, BoundTeam, BoundSession,EscapeContinuation,Continuation) + DESCRIPTORS


def _project_callable(value,owner,*sources):
    """A scoped member projection must retain its ceiling after extraction."""
    from .robot_values import VALUES as ROBOT_VALUES,project as robot_project
    if type(value) in ROBOT_VALUES:return robot_project(owner,value,*sources)
    from .bot_values import Bot,project as bot_project
    if type(value) is Bot:return bot_project(owner,value,*sources)
    from .device_values import VALUES as DEVICE_VALUES, project as device_project
    if type(value) in DEVICE_VALUES:return device_project(owner,value,*sources)
    from .computer_values import VALUES as COMPUTER_VALUES, project as computer_project
    if type(value) in COMPUTER_VALUES:return computer_project(owner,value,*sources)
    from .set_values import SetValue
    from .set_runtime import project as set_project
    if type(value) is SetValue:return set_project(owner,value,*sources)
    from .eql_specializers import EqlSpecializer, project as eql_project
    if type(value) is EqlSpecializer:return eql_project(owner,value,*sources)
    from .agent_tools import current_caps
    caps=current_caps(owner)if owner is not None else None
    for source in sources:
        inherited=getattr(source,'capabilities',None)
        if inherited is not None:caps=inherited if caps is None else caps & inherited
    if caps is None:return owned(value,owner)
    captured=getattr(value,'capabilities',None)
    if captured is not None and captured.issubset(caps):return owned(value,owner)
    from .hash_maps import HashMap, project as hash_map_project
    if type(value) is HashMap:return hash_map_project(owner,value,*sources)
    from .control_prompts import VALUES as CONTROL_VALUES,project as control_project
    if type(value) in CONTROL_VALUES:return control_project(owner,value,*sources)
    if isinstance(value,DESCRIPTORS):
        inherited=value.capabilities
        return replace(value,capabilities=caps if inherited is None else caps & inherited)
    if not _capturable_callable(value):
        from .decimal_values import DecimalContext
        from .dynamic_contexts import VALUES as CONTEXT_VALUES
        if type(value) in (DecimalContext,)+CONTEXT_VALUES:return owned(value,owner)
        result=owned(value,owner)
        from .language import LanguageError
        if getattr(value,'owner',owner) not in (None,owner):
            raise LanguageError('成员活值不能跨执行上下文使用',code='ownership_error')
        if not intrinsic(owner, value) and hasattr(value,'capabilities') and (captured is None or not captured.issubset(caps)):
            raise LanguageError('成员活值权限超过投影范围',code='capability_error')
        return result
    from .language import Native,TailCall
    if isinstance(value,Native) and value.generic is not None:
        from .generic_functions import project
        return project(owner,value,*sources)
    return Native('scoped_member',lambda *args,**kwargs:TailCall(value,list(args),kwargs=kwargs),owner,capabilities=caps,captures=(value,))


@dataclass(eq=False)
class Member:
    value: object
    contract: object = 'Any'
    mutable: bool = False
    method: bool = False
    defining_class: object = None
    class_bound: bool = False
    static_bound: bool = False


@dataclass(frozen=True, eq=False)
class ClassMember:
    value: object
    contract: object
    mutable: bool
    method: bool
    owner: object
    capabilities: object = None

    def __post_init__(self):
        from .agent_tools import current_caps
        caps = current_caps(self.owner)
        for item in (self, self.value, self.contract):
            captured = getattr(item, 'capabilities', None)
            if captured is not None: caps = captured if caps is None else caps & captured
        object.__setattr__(self, 'capabilities', caps)


@dataclass(eq=False)
class ChaosClass:
    name: str
    bases: tuple
    owner: object
    members: dict = field(default_factory=dict)
    mro: tuple = ()
    building: bool = True
    capabilities: object = None
    metaclass: object = None
    __post_init__ = _capture_authority


@dataclass(eq=False)
class ChaosInstance:
    klass: ChaosClass
    owner: object
    fields: dict = field(default_factory=dict)
    capabilities: object = None
    __post_init__ = _capture_authority


@dataclass(eq=False)
class BoundMethod:
    function: object
    receiver: object
    defining_class: ChaosClass
    owner: object
    capabilities: object = None
    __post_init__ = _capture_authority


@dataclass(eq=False)
class SuperProxy:
    start_class: ChaosClass
    receiver: object
    owner: object
    capabilities: object = None
    metaclass_access: bool = False
    __post_init__ = _capture_authority


@dataclass(eq=False)
class StaticMethod:
    value: object
    owner: object
    capabilities: object = None
    __post_init__ = _capture_authority


@dataclass(eq=False)
class ClassMethod:
    value: object
    owner: object
    capabilities: object = None
    __post_init__ = _capture_authority


@dataclass(eq=False)
class Property:
    fget: object
    fset: object
    owner: object
    previous: object = None
    fdel: object = None
    capabilities: object = None
    __post_init__ = _capture_authority


@dataclass
class AccessRequest:
    operation: str
    target: object
    name: str
    item: object = None


@dataclass
class AccessorCall:
    function: object
    args: list
    write: bool = False
    item: object = None
    capabilities: object = None


@dataclass
class ProtocolCall:
    descriptor: object
    name: str
    args: list
    write: bool = False
    item: object = None


from .ui_artifacts import UIArtifact

DESCRIPTORS = (StaticMethod, ClassMethod, Property)
OBJECT_VALUES = (ChaosClass, ChaosInstance, BoundMethod, SuperProxy, ClassMember, UIArtifact) + DESCRIPTORS


def require_callable(value, owner, *, setter=False):
    from .bot_values import Bot,owned as bot_owned
    from .robot_values import Robot
    from .device_values import Device
    from .computer_values import Computer, owned as computer_owned
    from .language import Function, Native, Agent, LanguageError
    from .team_values import Team, owned as team_owned
    from .program_values import Program, owned as program_owned
    from .session_values import AgentSession, owned as session_owned
    from .process_values import Process, owned as process_owned
    from .modes import ModeValue, owned as mode_owned
    target = value
    while True:
        owned(target, owner)
        if isinstance(target, (Function, Native, Agent)) and target.owner is not None and target.owner is not owner:
            raise LanguageError('可调用值不能跨执行上下文使用', code='ownership_error')
        if isinstance(target, Computer):computer_owned(target,owner)
        if isinstance(target, AgentSession): session_owned(target,owner)
        if isinstance(target, Process): process_owned(target, owner)
        if isinstance(target, Program): program_owned(target, owner)
        if isinstance(target, Team): team_owned(target, owner)
        if isinstance(target, ModeValue): mode_owned(target, owner)
        if type(target) is Bot:bot_owned(target,owner)
        if not isinstance(target, (StaticMethod, BoundMethod)): break
        target = target.value if isinstance(target, StaticMethod) else target.function
    if not callable_object(value): raise LanguageError('描述符需要可调用值', code='argument_error')
    if setter and (isinstance(target, (Bot, Team, ModeValue, Program, Process, AgentSession, Computer, Device, Robot)) or isinstance(target, Function) and target.flavor != 'fn'):
        raise LanguageError('描述符写入、删除或命名 hook 必须是同步可调用值', code='argument_error')
    return value


def make_property(fget, fset, owner, previous=None, fdel=None):
    if fget is not None: require_callable(fget, owner)
    if fset is not None: require_callable(fset, owner, setter=True)
    if fdel is not None: require_callable(fdel, owner, setter=True)
    return Property(fget, fset, owner, previous, fdel)


def property_update(value, previous):
    """Only immutable accessor copies may replace an earlier own property."""
    seen = set()
    while isinstance(value, Property) and id(value) not in seen:
        seen.add(id(value)); value = value.previous
        if value is previous: return True
    return False


def c3_merge(sequences, tick=None):
    from .language import LanguageError, ResourceLimit
    sequences = [list(sequence) for sequence in sequences if sequence]
    result = []
    while sequences:
        candidate = None
        for sequence in sequences:
            if tick: tick()
            head = sequence[0]
            if not any(any(head is item for item in other[1:]) for other in sequences):
                candidate = head; break
        if candidate is None: raise LanguageError('类继承顺序无法建立一致的 C3 MRO', code='mro_error')
        result.append(candidate)
        if len(result) > 256: raise ResourceLimit('类 MRO 超过 256 项')
        sequences = [sequence[1:] if sequence[0] is candidate else sequence for sequence in sequences]
        sequences = [sequence for sequence in sequences if sequence]
    return result


def owned(value, owner):
    from .robot_values import VALUES as ROBOT_VALUES,owned as robot_owned
    if type(value) in ROBOT_VALUES:return robot_owned(value,owner)
    from .control_prompts import VALUES as CONTROL_VALUES,owned as control_owned
    if type(value) in CONTROL_VALUES:return control_owned(value,owner)
    from .set_values import SetValue
    from .set_runtime import owned as set_owned
    if type(value) is SetValue:return set_owned(value,owner)
    from .eql_specializers import EqlSpecializer, owned as eql_owned
    if type(value) is EqlSpecializer:return eql_owned(value,owner)
    from .contracts import Contract, owned as contract_owned
    if type(value) is Contract:return contract_owned(value,owner)
    from .hash_maps import HashMap, owned as hash_map_owned
    if type(value) is HashMap:return hash_map_owned(value,owner)
    from .error_values import Error, owned as error_owned
    if type(value) is Error:return error_owned(value,owner)
    from .code_values import Code,owned as code_owned
    if type(value) is Code:return code_owned(value,owner)
    from .dynamic_contexts import VALUES as CONTEXT_VALUES,owned as binding_owned
    if type(value) in CONTEXT_VALUES:return binding_owned(value,owner)
    from .conditions import VALUES as RECOVERY_VALUES,owned as recovery_owned
    if type(value) in RECOVERY_VALUES:recovery_owned(value,owner)
    from .decimal_values import DecimalContext,owned as decimal_owned
    if type(value) is DecimalContext:decimal_owned(value,owner)
    from .team_bindings import BoundTeam,owned as bound_team_owned
    if type(value)is BoundTeam:bound_team_owned(value,owner)
    from .bot_values import Bot,owned as bot_owned
    if type(value) is Bot:bot_owned(value,owner)
    from .session_tools import BoundSession,owned as session_binding_owned
    if type(value)is BoundSession:session_binding_owned(value,owner)
    from .agent_tools import AgentTool, BoundAgent, owned as binding_owned
    if type(value) in (AgentTool,BoundAgent): binding_owned(value,owner)
    from .resource_stacks import STACKS, owned as stack_owned
    if type(value) in STACKS: stack_owned(value, owner)
    from .contexts import MANAGERS, context_owned
    if type(value) in MANAGERS: context_owned(value, owner)
    from .device_values import VALUES as DEVICE_VALUES, owned as device_owned
    if type(value) in DEVICE_VALUES:device_owned(value,owner)
    from .computer_values import VALUES as COMPUTER_VALUES, owned as computer_owned
    if type(value) in COMPUTER_VALUES:computer_owned(value,owner)
    from .language import LanguageError
    if isinstance(value, OBJECT_VALUES) and value.owner is not owner:
        raise LanguageError('对象不能跨执行上下文使用', code='ownership_error')
    return value


def declaration(klass, name, after=None):
    chain = klass.mro
    if after is not None:
        index = next((i for i,item in enumerate(chain) if item is after), None)
        if index is None:
            from .language import LanguageError
            raise LanguageError('super 的类不在实例 MRO 中', code='argument_error')
        chain = chain[index+1:]
    for base in chain:
        if name in base.members: return base.members[name]
    return None


def member_value(member):
    from .language import Cell
    return member.value.value if isinstance(member.value, Cell) else member.value


def pattern_fields(value, klass, owner, requested, include_rest=False):
    """Pure nominal stored-field projection, with no descriptor execution."""
    from .language import LanguageError, ResourceLimit, Function, Native, Agent
    from .agent_tools import current_caps
    owned(klass, owner)
    if type(klass) is not ChaosClass or klass.building:
        raise LanguageError('类模式需要已完成的 Class')
    if type(value) is not ChaosInstance: return None
    owned(value, owner); owned(value.klass, owner)
    if type(value.klass) is not ChaosClass or value.klass.building:
        raise LanguageError('类模式需要已完成的实例类')
    nominal = False
    for base in value.klass.mro:
        owner.tick(); owned(base, owner)
        if base is klass: nominal = True
    if not nominal: return None
    missing = object()

    def raw(name):
        member = None
        for base in value.klass.mro:
            owner.tick()
            if name in base.members:
                member = base.members[name]; break
        candidate = owned(member_value(member), owner) if member is not None else None
        protocols = set()
        if isinstance(candidate, ChaosInstance):
            owned(candidate.klass, owner)
            for base in candidate.klass.mro:
                owner.tick(); owned(base, owner)
                protocols.update(name for name in ('__get__', '__set__', '__delete__', '__set_name__')
                                 if name in base.members)
        data_get = isinstance(candidate, Property) or ('__get__' in protocols and bool(protocols & {'__set__', '__delete__'}))
        if not data_get and name in value.fields:
            return value.fields[name].value
        if member is None or member.method or isinstance(candidate, DESCRIPTORS) or protocols - {'__set_name__'}:
            return missing
        return candidate

    captured = {}
    for key in requested:
        owner.tick()
        item = raw(key)
        if item is missing: return None
        captured[key] = item
    if include_rest:
        seen = set(requested)
        for names in [value.fields] + [base.members for base in value.klass.mro]:
            for key in names:
                owner.tick()
                if key in seen: continue
                seen.add(key)
                item = raw(key)
                if item is not missing:
                    captured[key] = item
                    if len(captured) > 10000: raise ResourceLimit('类模式提取超过 10000 字段')
    if len(captured) > 10000: raise ResourceLimit('类模式提取超过 10000 字段')

    ceiling = current_caps(owner)
    for source in (value, value.klass, klass):
        inherited = source.capabilities
        if inherited is not None: ceiling = inherited if ceiling is None else ceiling & inherited
    from .tuple_values import TupleValue
    from .set_values import SetValue
    from .suspension import Task, Generator
    from .iteration import HANDLES
    from .agent_tools import AgentTool, BoundAgent, owned as binding_owned
    from .team_bindings import BoundTeam, owned as bound_team_owned
    from .session_tools import BoundSession, owned as bound_session_owned
    from .channels import Channel, owned as channel_owned
    from .channel_select import ChannelCase, owned as case_owned
    from .task_groups import TaskGroup, owned as group_owned
    from .cancellation import CancelShield, owned as shield_owned
    from .contexts import MANAGERS, context_owned
    from .resource_stacks import STACKS, owned as stack_owned
    from .computer_values import VALUES as COMPUTER_VALUES, owned as computer_owned
    from .modes import ModeValue, owned as mode_owned
    from .team_values import Team, owned as team_owned
    from .program_values import Program, owned as program_owned
    from .process_values import Process, ProcessEvolution, owned as process_owned, evolution_owned
    from .session_values import AgentSession, owned as session_owned
    from .error_values import Error, owned as error_owned
    from .bot_values import Bot,owned as bot_owned
    validators = {Bot:bot_owned,Error: error_owned, AgentTool: binding_owned, BoundAgent: binding_owned, BoundTeam: bound_team_owned,
                  BoundSession: bound_session_owned, Channel: channel_owned, ChannelCase: case_owned,
                  TaskGroup: group_owned, CancelShield: shield_owned, Team: team_owned,
                  Program: program_owned, Process: process_owned, ProcessEvolution: evolution_owned,
                  AgentSession: session_owned}
    validators.update({kind: context_owned for kind in MANAGERS})
    validators.update({kind: stack_owned for kind in STACKS})
    from .device_values import VALUES as DEVICE_VALUES, owned as device_owned
    validators.update({kind: device_owned for kind in DEVICE_VALUES})
    validators.update({kind: computer_owned for kind in COMPUTER_VALUES})
    # Validate all captured values in one bounded walk, including owned values
    # hidden inside opaque map carriers. Projection below retains one shared memo.
    from .indexing import validate_owned
    validate_owned(owner, captured)
    from .hash_maps import HashMap, project as hash_map_project
    memo, active, count = {}, set(), 0

    def project(item, depth=0):
        nonlocal count
        owner.tick()
        identity = id(item)
        if identity in active: raise LanguageError('类字段容器不能包含循环', code='value_error')
        if identity in memo: return memo[identity]
        count += 1
        if count > 20000 or depth > 100: raise ResourceLimit('类字段投影超过节点数或深度限制')
        kind = type(item)
        from .eql_specializers import EqlSpecializer, project as eql_project
        if kind is Bot:
            from .bot_values import project as bot_project
            from .agent_tools import scope
            with scope(owner,ceiling):result=bot_project(owner,item)
        elif kind in DEVICE_VALUES:
            from .device_values import project as device_project
            from .agent_tools import scope
            with scope(owner,ceiling):result=device_project(owner,item)
        elif kind in COMPUTER_VALUES:
            from .computer_values import project as computer_project
            from .agent_tools import scope
            with scope(owner,ceiling):result=computer_project(owner,item)
        elif kind is EqlSpecializer:
            from .agent_tools import scope
            with scope(owner, ceiling): result = eql_project(owner,item)
        elif kind is HashMap:
            from .agent_tools import scope
            with scope(owner, ceiling): result = hash_map_project(owner, item)
        elif kind is SetValue:
            from .set_runtime import project as set_project
            from .agent_tools import scope
            with scope(owner,ceiling):result=set_project(owner,item)
        elif kind in (list, dict, TupleValue):
            if len(item) > 10000: raise ResourceLimit('类字段容器超过 10000 项')
            active.add(identity)
            try:
                if kind is dict:
                    if any(type(key) is not str for key in item): raise LanguageError('类字段 Record 键必须为 String')
                    result = {key: project(child, depth+1) for key,child in item.items()}
                else:
                    children = [project(child, depth+1) for child in item]
                    result = TupleValue(children) if kind is TupleValue else children
            finally: active.remove(identity)
        else:
            if isinstance(item, (list, dict, TupleValue, str, int, float)) and kind not in (bool, int, float, str):
                raise LanguageError('类字段不能使用宿主容器或标量子类', code='value_error')
            opaque = False
            if isinstance(item, (Function, Native, Agent)):
                opaque = True
                if item.owner is not None and item.owner is not owner:
                    raise LanguageError('类字段可调用值不能跨执行上下文使用', code='ownership_error')
            if isinstance(item, OBJECT_VALUES):
                owned(item, owner); opaque = True
                if type(item) is ChaosInstance: owned(item.klass, owner)
                if type(item) is BoundMethod:
                    owned(item.receiver, owner); owned(item.defining_class, owner)
                    function = item.function
                    if getattr(function, 'owner', None) not in (None, owner):
                        raise LanguageError('类字段方法不能跨执行上下文使用', code='ownership_error')
            if isinstance(item, (Task, Generator) + HANDLES):
                owner.suspension.validate(item, type(item)); opaque = True
            elif kind in validators:
                validators[kind](item, owner); opaque = True
            elif isinstance(item, ModeValue):
                mode_owned(item, owner); opaque = True
            if kind in (Function, Native, Agent, BoundMethod):
                result = _project_callable(item, owner, value, value.klass, klass)
            else:
                authority = getattr(item, 'capabilities', None) if opaque else None
                if opaque and not intrinsic(owner, item) and ceiling is not None and (authority is None or not authority.issubset(ceiling)):
                    raise LanguageError('类字段活值权限超过投影范围', code='capability_error')
                from .language import serializable
                serializable(item)
                result = item
        memo[identity] = result
        return result
    return {key: project(item) for key,item in captured.items()}


def descriptor_protocols(value):
    """Pure capability inspection: only the instance class MRO, never user hooks."""
    if not isinstance(value, ChaosInstance): return frozenset()
    return frozenset(name for name in ('__get__', '__set__', '__delete__', '__set_name__')
                     if declaration(value.klass, name) is not None)


def is_descriptor(value):
    return isinstance(value, DESCRIPTORS) or bool(descriptor_protocols(value) - {'__set_name__'})


def bind_member(result, member, receiver, klass, owner, instance_access=False):
    """Return a value or a deferred evaluator call; never execute a protocol here."""
    from .language import LanguageError
    owned(result, owner); owned(receiver, owner); owned(klass, owner)
    if isinstance(result, StaticMethod):
        return _project_callable(result.value,owner,result,receiver,klass)
    if isinstance(result, ClassMethod): return BoundMethod(result.value, klass, member.defining_class, owner,capabilities=result.capabilities)
    if isinstance(result, Property):
        if not isinstance(receiver, ChaosInstance) and not instance_access: return result
        if result.fget is None: raise LanguageError('属性不可读取', code='attribute_error')
        return AccessorCall(result.fget, [receiver],capabilities=result.capabilities)
    if '__get__' in descriptor_protocols(result):
        return ProtocolCall(result, '__get__', [receiver if isinstance(receiver, ChaosInstance) or instance_access else None, klass])
    if member.class_bound and member.method:
        return BoundMethod(result, klass, member.defining_class, owner)
    if (isinstance(receiver, ChaosInstance) or instance_access) and member.method and not member.static_bound:
        return BoundMethod(result, receiver, member.defining_class, owner)
    return _project_callable(result,owner,receiver,klass)


def get_member(value, name, owner):
    from .language import LanguageError, Native
    owned(value, owner)
    if not isinstance(name, str): raise LanguageError('对象成员名称必须是 String', code='argument_error')
    from .dynamic_contexts import VALUES as CONTEXT_VALUES,member as binding_member
    if type(value) in CONTEXT_VALUES:return binding_member(value,name,owner)
    from .condition_values import Condition,member as condition_member
    from .conditions import VALUES as RECOVERY_VALUES,member as recovery_member
    if type(value) is Condition:return condition_member(value,name,owner.tick)
    if type(value) in RECOVERY_VALUES:return recovery_member(owner,value,name)
    from .numeric_values import Fraction,Complex,Decimal,member as numeric_member
    from .decimal_values import DecimalContext,member as decimal_member
    if type(value) is DecimalContext:return decimal_member(value,name,owner)
    if type(value) in (Fraction,Complex,Decimal): return numeric_member(value,name)
    if type(value) is UIArtifact:
        from .ui_artifacts import get_member as ui_member
        return ui_member(value, name, owner)
    if type(value) is ClassMember:
        from .class_creation import project_field
        return project_field(owner, value, name)
    if isinstance(value, Property):
        if name == 'getter': return Native('property.getter', lambda function: make_property(function, value.fset, owner, value, value.fdel), owner, captures=(value,))
        if name == 'setter': return Native('property.setter', lambda function: make_property(value.fget, function, owner, value, value.fdel), owner, captures=(value,))
        if name == 'deleter': return Native('property.deleter', lambda function: make_property(value.fget, value.fset, owner, value, function), owner, captures=(value,))
        if name in ('fget', 'fset', 'fdel'):
            return _project_callable({'fget':value.fget, 'fset':value.fset, 'fdel':value.fdel}[name],owner,value)
        raise LanguageError('成员不存在 '+name, code='attribute_error')
    if isinstance(value, (StaticMethod, ClassMethod)):
        raise LanguageError('成员不存在 '+name, code='attribute_error')
    receiver, member, instance_access = None, None, False
    if isinstance(value, ChaosInstance):
        receiver, klass = value, value.klass
        owned(klass, owner)
        member = declaration(klass, name)
        candidate = owned(member_value(member), owner) if member else None
        protocols = descriptor_protocols(candidate)
        data_get = isinstance(candidate, Property) or ('__get__' in protocols and bool(protocols & {'__set__', '__delete__'}))
        if not data_get and name in value.fields: return owned(value.fields[name].value, owner)
    elif isinstance(value, ChaosClass):
        klass = value; meta = owned(value.metaclass, owner)
        meta_member = declaration(meta, name)
        candidate = owned(member_value(meta_member), owner) if meta_member else None
        protocols = descriptor_protocols(candidate)
        data_get = isinstance(candidate, Property) or ('__get__' in protocols and bool(protocols & {'__set__', '__delete__'}))
        if data_get:
            return bind_member(candidate, meta_member, value, meta, owner, instance_access=True)
        member = declaration(klass, name)
        if member is None and meta_member is not None:
            return bind_member(candidate, meta_member, value, meta, owner, instance_access=True)
    elif isinstance(value, SuperProxy):
        owned(value.receiver, owner); owned(value.start_class, owner)
        receiver = value.receiver
        instance_access = value.metaclass_access
        klass = receiver.metaclass if instance_access else receiver if isinstance(receiver, ChaosClass) else receiver.klass
        owned(klass, owner); member = declaration(klass, name, value.start_class)
    else: raise LanguageError('成员操作需要 Class、Instance 或 super 代理', code='argument_error')
    if member is None:
        if isinstance(value, SuperProxy) and isinstance(value.receiver, ChaosClass) and name == '__init_subclass__':
            def terminal(*args, **kwargs):
                if args or kwargs: raise LanguageError('终端 __init_subclass__ 不接受参数', code='argument_error')
                return None
            return Native('super.__init_subclass__', terminal, owner, capabilities=value.capabilities, captures=(value,))
        raise LanguageError('成员不存在 '+name, code='attribute_error')
    return bind_member(member_value(member), member, receiver, klass, owner, instance_access=instance_access)


def instance_descriptor(value, name, owner, operation, item=None):
    """Return (deferred hook, allows nondata shadow, member)."""
    from .language import LanguageError
    klass = value.metaclass if isinstance(value, ChaosClass) else value.klass
    owned(klass, owner)
    member = declaration(klass, name)
    descriptor = owned(member_value(member), owner) if member is not None else None
    if isinstance(descriptor, Property):
        function = descriptor.fset if operation == '__set__' else descriptor.fdel
        if function is None: raise LanguageError('属性不支持此操作 '+name, code='attribute_error')
        args = [value, item] if operation == '__set__' else [value]
        return AccessorCall(function, args, True, item,descriptor.capabilities), False, member
    protocols = descriptor_protocols(descriptor)
    if protocols & {'__set__', '__delete__'}:
        if operation not in protocols: raise LanguageError('描述符不支持此操作 '+name, code='attribute_error')
        args = [value, item] if operation == '__set__' else [value]
        return ProtocolCall(descriptor, operation, args, True, item), False, member
    return None, '__get__' in protocols, member


def set_member(value, name, item, owner):
    from .language import Cell, LanguageError, ResourceLimit, check_type
    owned(value, owner); owned(item, owner)
    if not isinstance(name, str): raise LanguageError('对象成员名称必须是 String', code='argument_error')
    if not isinstance(value, (ChaosClass, ChaosInstance)):
        raise LanguageError('仅可写入 Class 或 Instance 成员', code='argument_error')
    if intrinsic(owner, value): raise LanguageError("内置类的成员不可修改", code="attribute_error")
    shadow = False
    if isinstance(value, ChaosClass):
        access, _, _ = instance_descriptor(value, name, owner, "__set__", item)
        if access is not None: return access
    if isinstance(value, ChaosInstance):
        access, shadow, member = instance_descriptor(value, name, owner, '__set__', item)
        if access is not None: return access
    else: member = declaration(value, name)
    if member is not None and not member.mutable and not shadow: raise LanguageError('成员只读 '+name, code='attribute_error')
    fields = value.fields if isinstance(value, ChaosInstance) else value.members
    contract = member.contract if member is not None and not shadow else 'Any'
    if isinstance(value, ChaosInstance) and name in fields and not shadow: contract = fields[name].contract
    item = check_type(item, contract)
    if name not in fields and len(fields) >= 10000: raise ResourceLimit('对象成员超过 10000 项')
    if isinstance(value, ChaosInstance):
        if name in fields: fields[name].value, fields[name].contract = item, contract
        else: fields[name] = Cell(item, contract)
    elif name in fields and isinstance(fields[name].value, Cell):
        fields[name].value.value = item
    else:
        fields[name] = Member(Cell(item, contract), contract, True, defining_class=value)
    return item


def delete_member(value, name, owner):
    from .language import LanguageError
    owned(value, owner)
    if not isinstance(name, str): raise LanguageError('对象成员名称必须是 String', code='argument_error')
    if not isinstance(value, (ChaosClass, ChaosInstance)):
        raise LanguageError('仅可删除 Class 或 Instance 成员', code='argument_error')
    if intrinsic(owner, value): raise LanguageError("内置类的成员不可修改", code="attribute_error")
    if isinstance(value, ChaosClass):
        access, _, _ = instance_descriptor(value, name, owner, "__delete__")
        if access is not None: return access
    if isinstance(value, ChaosInstance):
        access, shadow, member = instance_descriptor(value, name, owner, '__delete__')
        if access is not None: return access
        if member is not None and not member.mutable and not shadow:
            raise LanguageError('成员只读 '+name, code='attribute_error')
        fields = value.fields
    else:
        member = value.members.get(name)
        if member is not None and not member.mutable: raise LanguageError('成员只读 '+name, code='attribute_error')
        fields = value.members
    if name not in fields: raise LanguageError('自有成员不存在 '+name, code='attribute_error')
    del fields[name]
    return None


def special_method(value, name, owner):
    """Special calls use the class MRO, never an instance's dynamic fields."""
    from .language import LanguageError
    owned(value, owner); owned(value.klass, owner)
    member = declaration(value.klass, name)
    if member is None: raise LanguageError('成员不存在 '+name, code='attribute_error')
    return bind_member(member_value(member), member, value, value.klass, owner)


def bind_special(value, member, owner):
    return bind_member(member_value(member), member, value, value.klass, owner)


def callable_object(value, seen=None):
    from .control_prompts import EscapeContinuation
    from .continuations import Continuation
    if type(value) in (EscapeContinuation,Continuation):return True
    from .team_bindings import BoundTeam
    if type(value)is BoundTeam:return True
    from .agent_tools import BoundAgent
    from .session_tools import BoundSession
    if type(value) in (BoundAgent,BoundSession): return True
    from .bot_values import Bot,owned as bot_owned
    from .robot_values import Robot
    from .device_values import Device
    from .computer_values import Computer
    from .language import Function, Native, Agent
    from .team_values import Team
    from .program_values import Program
    from .session_values import AgentSession
    from .process_values import Process
    from .modes import ModeValue
    if isinstance(value, (Bot, Function, Native, Agent, Team, ModeValue, Program, Process, AgentSession, Computer, Device, Robot, ChaosClass, BoundMethod)): return True
    if isinstance(value, StaticMethod): return callable_object(value.value, seen)
    if isinstance(value, ChaosInstance):
        seen = set() if seen is None else seen
        if id(value) in seen: return False
        seen.add(id(value))
        member = declaration(value.klass, '__call__')
        if member is None: return False
        target = member_value(member)
        if isinstance(target, ClassMethod): return callable_object(target.value, seen)
        if isinstance(target, Property): return target.fget is not None
        if '__get__' in descriptor_protocols(target): return True
        return member.method or callable_object(target, seen)
    return False


def describe(value):
    from .language import Function, Native, Agent
    if type(value) is UIArtifact:
        from .ui_artifacts import describe as describe_ui
        return describe_ui(value)
    if isinstance(value, DESCRIPTORS): return {'$descriptor': type(value).__name__}
    if type(value) is ClassMember:
        contract = value.contract if type(value.contract) is str else value.contract.name
        return {'$classMember': {'mutable': value.mutable, 'method': value.method, 'contract': contract}}
    if isinstance(value, ChaosClass): return {'$class': value.name}
    if isinstance(value, ChaosInstance): return {'$instance': value.klass.name}
    if isinstance(value, BoundMethod):
        name = value.function.name if isinstance(value.function, (Function, Native, Agent, ChaosClass)) else '<callable>'
        return {'$boundMethod': name, '$class': value.defining_class.name}
    if isinstance(value, SuperProxy): return {'$super': value.start_class.name, '$instance': value.receiver.name if isinstance(value.receiver, ChaosClass) else value.receiver.klass.name}
    raise TypeError('not an object value')
