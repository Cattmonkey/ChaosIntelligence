"""Native class creation requests and shared, evaluator-owned protocols.

Helpers never execute host classes. User hooks run through the original AST
generator and therefore retain effects, cancellation, ownership and authority.
"""

from .exception_classes import intrinsic
from dataclasses import dataclass, replace


def class_options(node):
    """Optional ordered header arguments; legacy class nodes remain four items."""
    return node[4] if len(node) == 5 else []


@dataclass(frozen=True)
class ClassRequest:
    name: str
    bases: object
    members: object
    options: object


def validate_value(evaluator, value, *, checkpoint=None, contract_validator=None, reject_cycles=False):
    """Validate bounded nested live values without invoking user protocols."""
    from .language import LanguageError, ResourceLimit, Function, Native, Agent, DataType, TupleValue, SetValue, serializable
    from .objects import (owned, ClassMember, OBJECT_VALUES, ChaosInstance, BoundMethod,
                          SuperProxy, StaticMethod, ClassMethod, Property)
    from .suspension import Task, Generator, AsyncGenerator
    from .iteration import HANDLES
    from .channels import Channel, owned as channel_owned
    from .channel_select import ChannelCase, owned as case_owned
    from .task_groups import TaskGroup, owned as group_owned
    from .cancellation import CancelShield, owned as shield_owned
    from .modes import ModeValue, owned as mode_owned
    from .team_values import Team, owned as team_owned
    from .program_values import Program, owned as program_owned
    from .process_values import Process, ProcessEvolution, owned as process_owned, evolution_owned
    from .session_values import AgentSession, owned as session_owned
    from .agent_tools import AgentTool, BoundAgent
    from .team_bindings import BoundTeam
    from .session_tools import BoundSession
    from .contexts import MANAGERS
    from .resource_stacks import STACKS
    from .robot_values import VALUES as ROBOT_VALUES,owned as robot_owned
    from .device_values import VALUES as DEVICE_VALUES
    from .computer_values import VALUES as COMPUTER_VALUES
    from .modes import VALUES as MODE_VALUES
    from .indexing import Slice
    from .operators import NOT_IMPLEMENTED
    from .code_values import Code,owned as code_owned
    from .error_values import Error, owned as error_owned
    from .hash_maps import HashMap, owned as hash_map_owned, children as hash_map_children
    from .contracts import Contract, owned as contract_owned, Budget as ContractBudget
    from .eql_specializers import EqlSpecializer, owned as eql_owned
    if contract_validator is None: contract_validator = ContractBudget(evaluator).validate
    from .bot_values import Bot,owned as bot_owned
    validators = {Bot:bot_owned,EqlSpecializer:eql_owned, Contract:contract_owned, HashMap:hash_map_owned, Error:error_owned, Code:code_owned, Channel: channel_owned, ChannelCase: case_owned, TaskGroup: group_owned,
                  CancelShield: shield_owned, Team: team_owned, Program: program_owned,
                  Process: process_owned, ProcessEvolution: evolution_owned, AgentSession: session_owned}
    from .condition_values import Condition,validate as condition_validate
    from .conditions import VALUES as RECOVERY_VALUES,owned as recovery_owned
    validators.update({kind:recovery_owned for kind in RECOVERY_VALUES})
    from .control_prompts import VALUES as CONTROL_VALUES,owned as control_owned
    validators.update({kind:control_owned for kind in CONTROL_VALUES})
    from .numeric_values import Fraction, Complex, Decimal
    from .decimal_values import DecimalContext,owned as decimal_owned
    validators[DecimalContext]=decimal_owned
    from .dynamic_contexts import VALUES as CONTEXT_VALUES,owned as binding_owned
    validators.update({kind:binding_owned for kind in CONTEXT_VALUES})
    allowed = {Condition,Fraction,Complex,Decimal,type(None), bool, int, float, str, list, dict, TupleValue, SetValue, DataType,
               Function, Native, Agent, Task, Generator, AsyncGenerator, AgentTool, BoundAgent,
               BoundTeam, BoundSession, Slice, type(NOT_IMPLEMENTED)}
    allowed.update(OBJECT_VALUES + HANDLES + MANAGERS + STACKS + COMPUTER_VALUES + DEVICE_VALUES + MODE_VALUES)
    validators.update({kind:robot_owned for kind in ROBOT_VALUES})
    allowed.update(validators)
    pending, seen, hidden_payloads = [(value, 0)], set(), []
    active=set()
    while pending:
        entry=pending.pop()
        if len(entry)==3:
            active.remove(id(entry[0]));continue
        item,depth=entry
        if reject_cycles and id(item) in active:
            raise LanguageError('Recursive contracts require finite acyclic data',code='argument_error')
        if id(item) in seen: continue
        seen.add(id(item))
        if reject_cycles and type(item) in (list,dict,TupleValue,SetValue,HashMap,EqlSpecializer):
            active.add(id(item));pending.append((item,depth,True))
        if checkpoint is None: evaluator.tick()
        else: checkpoint(depth)
        if len(seen) > 20000 or depth > 100: raise ResourceLimit('类值超过节点数或深度限制')
        if type(item) not in allowed:
            raise LanguageError('类值需要原生语言值；不接受宿主子类', code='argument_error')
        owned(item, evaluator)
        if type(item) is Condition:condition_validate(item,evaluator.tick)
        if type(item) in validators: validators[type(item)](item, evaluator)
        elif isinstance(item, ModeValue): mode_owned(item, evaluator)
        if isinstance(item, (Function, Native, Agent)) and item.owner not in (None, evaluator):
            raise LanguageError('类值不能跨执行上下文使用', code='ownership_error')
        if isinstance(item, (Task, Generator) + HANDLES): evaluator.suspension.validate(item, type(item))
        if type(item) is EqlSpecializer:
            hidden_payloads.append(item.payload)
            pending.append((item.payload, depth+1))
        elif type(item) is Contract:
            contract_validator(item,depth)
        elif type(item) is ClassMember:
            pending.extend([(item.value, depth+1), (item.contract, depth+1)])
        elif type(item) is ChaosInstance:
            pending.append((item.klass, depth+1))
        elif type(item) is BoundMethod:
            pending.extend((child, depth+1) for child in (item.function, item.receiver, item.defining_class))
        elif type(item) is SuperProxy:
            pending.extend((child, depth+1) for child in (item.start_class, item.receiver))
        elif type(item) in (StaticMethod, ClassMethod):
            pending.append((item.value, depth+1))
        elif type(item) is Property:
            pending.extend((child, depth+1) for child in (item.fget, item.fset, item.fdel, item.previous))
        elif type(item) is HashMap:
            if len(item) > 10000: raise ResourceLimit('HashMap 超过 10000 项')
            pending.extend((child, depth+1) for child in hash_map_children(item))
        elif type(item) in (list, dict, TupleValue, SetValue):
            if len(item) > 10000: raise ResourceLimit('类值容器超过 10000 项')
            if type(item) is dict and any(type(key) is not str for key in item):
                raise LanguageError('类值 Record 的键必须为 String', code='argument_error')
            pending.extend((child, depth+1) for child in (item.values() if type(item) is dict else item))
    serializable([value]+hidden_payloads if hidden_payloads else value)


def validate_contract(evaluator, contract):
    from .language import LanguageError, TYPES, DataType
    from .objects import ChaosClass, owned
    from .contracts import Contract, validate_closed as composite_validate
    if type(contract) is Contract: return composite_validate(evaluator,contract)
    if type(contract) is str and contract in TYPES: return contract
    if type(contract) is ChaosClass:
        owned(contract, evaluator)
        if contract.building: raise LanguageError('成员契约需要已完成的 Class', code='argument_error')
        return contract
    if type(contract) is DataType: return contract
    raise LanguageError('成员契约需要内置类型名称或实际 Class/DataType', code='argument_error')


def member_value(evaluator, value, *, mutable=False, method=False, contract='Any'):
    from .language import LanguageError, Function, check_type
    from .objects import ClassMember
    if type(mutable) is not bool or type(method) is not bool:
        raise LanguageError('成员 mutable 和 method 必须为 Bool', code='argument_error')
    if mutable and method: raise LanguageError('方法不能同时声明为可变成员', code='argument_error')
    validate_value(evaluator, value); contract = validate_contract(evaluator, contract)
    check_type(value, contract)
    if method and (type(value) is not Function or not value.params or (value.params[0][2] if len(value.params[0]) > 2 else 'positional') not in ('positional', 'positional_only')):
        raise LanguageError('方法需要原生 Function 和首个位置接收者参数', code='argument_error')
    return ClassMember(value, contract, mutable, method, evaluator)


def _project(evaluator, value, ceiling, depth=0, memo=None):
    """Project metadata without losing native Function shape or widening caps."""
    from .language import Function, LanguageError, ResourceLimit, TupleValue
    from .objects import _project_callable, _capturable_callable, ChaosClass, ChaosInstance
    from .agent_tools import current_caps
    memo = {} if memo is None else memo
    if id(value) in memo: return memo[id(value)]
    evaluator.tick()
    if depth > 100 or len(memo) > 20000: raise ResourceLimit('成员投影超过资源限制')
    caps = current_caps(evaluator)
    if ceiling is not None: caps = ceiling if caps is None else caps & ceiling
    captured = getattr(value, 'capabilities', None)
    from .conditions import VALUES as RECOVERY_VALUES,owned as recovery_owned
    if type(value) in RECOVERY_VALUES:return recovery_owned(value,evaluator)
    from .control_prompts import VALUES as CONTROL_VALUES,EscapeContinuation,project as control_project,owned as control_owned
    if type(value) in CONTROL_VALUES:
        from .agent_tools import scope
        with scope(evaluator,caps):return control_project(evaluator,value)
    from .eql_specializers import EqlSpecializer, project as eql_project
    from .hash_maps import HashMap, project as hash_map_project
    from .set_values import SetValue
    from .set_runtime import project as set_project
    from .bot_values import Bot,project as bot_project
    if type(value) is Bot:
        from .agent_tools import scope
        with scope(evaluator,caps):return bot_project(evaluator,value)
    from .robot_values import VALUES as ROBOT_VALUES,project as robot_project
    if type(value) in ROBOT_VALUES:
        from .agent_tools import scope
        with scope(evaluator,caps):return robot_project(evaluator,value)
    from .device_values import VALUES as DEVICE_VALUES, project as device_project
    from .computer_values import VALUES as COMPUTER_VALUES, project as computer_project
    if type(value) in COMPUTER_VALUES:
        from .agent_tools import scope
        with scope(evaluator,caps):return computer_project(evaluator,value)
    if type(value) in DEVICE_VALUES:
        from .agent_tools import scope
        with scope(evaluator,caps):return device_project(evaluator,value)
    if type(value) is SetValue:
        from .agent_tools import scope
        with scope(evaluator,caps):result=set_project(evaluator,value)
    elif type(value) is EqlSpecializer:
        from .agent_tools import scope
        with scope(evaluator, caps): result = eql_project(evaluator,value)
    elif type(value) is HashMap:
        from .agent_tools import scope
        with scope(evaluator, caps): result = hash_map_project(evaluator,value)
    elif type(value) in (list, dict, TupleValue):
        children = value.values() if type(value) is dict else value
        projected = [_project(evaluator, child, caps, depth+1, memo) for child in children]
        result = dict(zip(value, projected)) if type(value) is dict else TupleValue(projected) if type(value) is TupleValue else projected
    elif type(value) is Function:
        result = value if caps is None or captured is not None and captured.issubset(caps) else replace(value, capabilities=caps if captured is None else caps & captured)
    elif type(value) in (ChaosClass, ChaosInstance):
        if intrinsic(evaluator, value):
            return value
        if caps is not None and (captured is None or not captured.issubset(caps)):
            raise LanguageError('成员活值权限超过投影范围', code='capability_error')
        result = value
    elif _capturable_callable(value):
        # The enclosing authority scope is installed by ordinary member access.
        from .agent_tools import scope
        with scope(evaluator, caps): result = _project_callable(value, evaluator)
    else:
        if caps is not None and hasattr(value, 'capabilities') and (captured is None or not captured.issubset(caps)):
            raise LanguageError('成员活值权限超过投影范围', code='capability_error')
        result = value
    memo[id(value)] = result
    return result


def _project_return(evaluator, value, ceiling, depth=0, memo=None):
    """Retain returned function authority without changing nominal values.

    Program and other owned runtime values have their own call/scope protocols;
    a function wrapper would destroy the type those protocols require. Metadata
    reflection continues to use the stricter, independent _project above.
    """
    from .language import ResourceLimit, TupleValue
    from .objects import _capturable_callable
    memo = {} if memo is None else memo
    if id(value) in memo: return memo[id(value)]
    evaluator.tick()
    if depth > 100 or len(memo) > 20000: raise ResourceLimit('返回值投影超过资源限制')
    from .multiple_values import Completion,components
    if type(value) is Completion:
        result=Completion(evaluator,tuple(_project_return(evaluator,child,ceiling,depth+1,memo) for child in components(value,evaluator)))
    elif type(value) in (list, dict, TupleValue):
        children = value.values() if type(value) is dict else value
        projected = [_project_return(evaluator, child, ceiling, depth+1, memo) for child in children]
        result = dict(zip(value, projected)) if type(value) is dict else TupleValue(projected) if type(value) is TupleValue else projected
    elif _capturable_callable(value):
        result = _project(evaluator, value, ceiling, depth, memo)
    elif hasattr(value, 'capabilities'):
        # Retaining nominal shape must not release a wider Class, Instance or
        # deferred resource. Type's exact identity exception lives in _project.
        result = _project(evaluator, value, ceiling, depth, memo)
    else:
        # Set values contain only immutable scalar/Tuple/Set keys, so cannot
        # retain a callable. Other nominal values keep their module semantics.
        result = value
    memo[id(value)] = result
    return result


def project_field(evaluator, member, name):
    from .language import LanguageError
    from .objects import owned
    owned(member, evaluator)
    if name not in ('value', 'mutable', 'method', 'contract'):
        raise LanguageError('成员不存在 '+name, code='attribute_error')
    value = getattr(member, name)
    validate_value(evaluator, value)
    if name == 'contract':
        # Nominal contracts retain identity; they cannot become callable wrappers.
        from .agent_tools import current_caps
        caps = current_caps(evaluator)
        if member.capabilities is not None: caps = member.capabilities if caps is None else caps & member.capabilities
        captured = getattr(value, 'capabilities', None)
        if not intrinsic(evaluator, value) and caps is not None and hasattr(value, 'capabilities') and (captured is None or not captured.issubset(caps)):
            raise LanguageError('成员类型权限超过投影范围', code='capability_error')
        return value
    return _project(evaluator, value, member.capabilities)


def project_value(evaluator, value, *sources):
    """Pure reflection retains the authority of every object supplying fields."""
    from .agent_tools import current_caps
    validate_value(evaluator, value)
    ceiling = current_caps(evaluator)
    for source in sources:
        captured = getattr(source, 'capabilities', None)
        if captured is not None: ceiling = captured if ceiling is None else ceiling & captured
    return _project(evaluator, value, ceiling)


def snapshot_members(evaluator, klass):
    from .objects import ChaosClass, ClassMember, member_value as raw, owned
    from .language import LanguageError
    if type(klass) is not ChaosClass or klass.building: raise LanguageError('class_members 需要已完成的 Class', code='argument_error')
    owned(klass, evaluator)
    result = {}
    for label, member in klass.members.items():
        evaluator.tick(); validate_value(evaluator, raw(member)); validate_contract(evaluator, member.contract)
        result[label] = ClassMember(raw(member), member.contract, member.mutable, member.method, evaluator, capabilities=klass.capabilities)
    return result


def validate_bases(evaluator, bases):
    from .objects import ChaosClass, c3_merge, owned
    from .language import LanguageError, ResourceLimit
    if type(bases) is not list or len(bases) > 32: raise LanguageError('类基类需要至多 32 项 List', code='argument_error')
    for index, base in enumerate(bases):
        if type(base) is not ChaosClass or base.building: raise LanguageError('基类需要已完成的 Class')
        evaluator.tick(); owned(base, evaluator)
        if any(base is earlier for earlier in bases[:index]): raise LanguageError('不能重复继承同一个类', code='mro_error')
    linear = c3_merge([base.mro for base in bases] + [bases], evaluator.tick)
    if len(linear) >= 256: raise ResourceLimit('类 MRO 超过 256 项')
    return linear


def preflight_base(evaluator, base, earlier):
    """Pure presence/identity validation preserves legacy header error order."""
    from .objects import ChaosClass, ChaosInstance, owned, declaration
    from .language import LanguageError
    owned(base, evaluator); evaluator.tick()
    if type(base) is ChaosClass:
        if base.building: raise LanguageError('基类需要已完成的 Class')
        if any(base is item for item in earlier): raise LanguageError('不能重复继承同一个类', code='mro_error')
    elif type(base) is not ChaosInstance or ('__mro_entries__' not in base.fields and declaration(base.klass, '__mro_entries__') is None):
        raise LanguageError('基类需要已完成的 Class 或带 __mro_entries__ 的原生 Instance')


def resolve_bases(evaluator, bases):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'resolve_bases',bases))


def validate_options(evaluator, options):
    from .language import LanguageError, ResourceLimit
    if type(options) is not dict or any(type(key) is not str for key in options):
        raise LanguageError('类 options 需要 String 键 Record', code='argument_error')
    if len(options) > 10000: raise ResourceLimit('类 options 超过 10000 项')
    validate_value(evaluator, options)
    return dict(options)


def validate_name(name):
    from .language import LanguageError, TYPES
    if type(name) is not str or not name or len(name) > 100000 or name in TYPES:
        raise LanguageError('类名称需要非空 String 且不能重定义内置类型', code='argument_error')
    name.encode('utf-8')
    return name


def shell(evaluator, name, bases, linear, metaclass=None):
    from .language import LanguageError, ResourceLimit, TYPES
    from .objects import ChaosClass
    validate_name(name)
    evaluator.object_class_count += 1
    if evaluator.object_class_count > 1000: raise ResourceLimit('类创建超过 1000 个')
    klass = ChaosClass(name, tuple(bases), evaluator, metaclass=metaclass or evaluator.native_type)
    if klass.metaclass.capabilities is not None:
        klass.capabilities = klass.metaclass.capabilities if klass.capabilities is None else klass.capabilities & klass.metaclass.capabilities
    for base in bases:
        if base.capabilities is not None:
            klass.capabilities = base.capabilities if klass.capabilities is None else klass.capabilities & base.capabilities
    klass.mro = (klass,) + tuple(linear)
    return klass


def install(evaluator, klass, label, value, contract='Any', mutable=False, method=False, capabilities=None):
    from .language import Cell, check_type, Function
    from .objects import Member
    validate_value(evaluator, value); check_type(value, contract)
    if capabilities is not None:
        klass.capabilities = capabilities if klass.capabilities is None else klass.capabilities & capabilities
    stored = Cell(value, contract) if mutable else value
    klass.members[label] = Member(stored, contract, mutable, method, klass,
                                 bool(label in ('__init_subclass__', '__prepare__', '__class_getitem__') and method and type(value) is Function),
                                 bool(label == '__new__' and method and type(value) is Function))
    return stored


def finalize(evaluator, klass, options):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'finalize',klass,options))


def _finalize(evaluator, klass, options):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'finalize_body',klass,options))


def create(evaluator, request):
    from .metaclasses import create as metaclass_create
    return (yield from metaclass_create(evaluator, request))

def allocate(evaluator, klass):
    from .language import LanguageError, ResourceLimit
    from .objects import ChaosClass, ChaosInstance, owned
    if type(klass) is not ChaosClass or klass.building:
        raise LanguageError('alloc_instance 需要已完成的 Class', code='argument_error')
    owned(klass, evaluator)
    from .metaclasses import is_metaclass
    if is_metaclass(evaluator, klass): raise LanguageError('元类实例必须通过 Type.__new__ 创建 Class', code='argument_error')
    evaluator.object_instance_count += 1
    if evaluator.object_instance_count > 10000: raise ResourceLimit('实例创建超过 10000 个')
    return ChaosInstance(klass, evaluator)


def construct(evaluator, klass, args, kwargs, span):
    from .execution_class_creation import class_protocol
    return (yield from class_protocol(evaluator,'construct',klass,args,kwargs,span))
