"""Opaque actual-value generic selectors; no user equality or hash protocols."""
from dataclasses import dataclass, replace


@dataclass(frozen=True, eq=False)
class EqlSpecializer:
    owner: object
    payload: object
    capabilities: object = None


def owned(value, evaluator):
    from .language import LanguageError
    if type(value) is not EqlSpecializer:
        raise LanguageError('Expected a native EqlSpecializer', code='argument_error')
    if value.owner is not evaluator:
        raise LanguageError('EqlSpecializer cannot cross evaluators', code='ownership_error')
    return value


def validate(evaluator, value):
    from .class_creation import validate_value
    owned(value, evaluator)
    validate_value(evaluator, value)
    return value


def create(evaluator, value):
    from .agent_tools import current_caps
    result = EqlSpecializer(evaluator, value, current_caps(evaluator))
    return validate(evaluator, result)


def by_value(value):
    from .numeric_values import Fraction, Complex, Decimal
    return type(value) in (type(None), bool, int, float, str, Fraction, Decimal, Complex)


def equal(left, right):
    """Operands were validated by the caller; this kernel cannot run hooks."""
    if type(left) is not type(right): return False
    from .control_prompts import EscapeContinuation,same_identity
    from .continuations import Continuation
    if type(left) in (EscapeContinuation,Continuation):return same_identity(left,right)
    from .robot_values import VALUES as ROBOT_VALUES,same_identity as robot_identity
    if type(left) in ROBOT_VALUES:return robot_identity(left,right)
    from .bot_values import Bot,same_identity as bot_identity
    if type(left) is Bot:return bot_identity(left,right)
    if not by_value(left): return left is right
    from .numeric_values import Fraction, Complex, Decimal, equal as numeric_equal
    if type(left) in (Fraction, Complex, Decimal): return numeric_equal(left, right)
    return left == right


def equivalent(left, right):
    return type(left) is EqlSpecializer and type(right) is EqlSpecializer and equal(left.payload, right.payload)


def project(evaluator, value, *sources):
    from .agent_tools import current_caps
    from .generic_functions import intersection
    validate(evaluator, value)
    ceiling = intersection(current_caps(evaluator), value.capabilities,
                           *(getattr(source, 'capabilities', None) for source in sources))
    return value if ceiling == value.capabilities else replace(value, capabilities=ceiling)


def value_type(value):
    # These are exact runtime classes, not user names or dispatch heuristics.
    from .language import Function, Native, Agent, DataType
    from .tuple_values import TupleValue
    from .set_values import SetValue
    from .objects import ChaosClass, ChaosInstance, ClassMember, BoundMethod, SuperProxy, DESCRIPTORS
    from .agent_tools import BoundAgent
    from .team_bindings import BoundTeam
    from .session_tools import BoundSession
    names = {type(None):'Null', bool:'Bool', int:'Int', float:'Number', str:'String',
             list:'List', dict:'Record', TupleValue:'Tuple', SetValue:'Set',
             Function:'Function', Native:'Function', Agent:'Function', DataType:'Type',
             ChaosClass:'Class', ChaosInstance:'Instance', ClassMember:'ClassMember',
             BoundMethod:'Function', BoundAgent:'Function', BoundTeam:'Function',
             BoundSession:'Function', SuperProxy:'Super'}
    from .operators import NOT_IMPLEMENTED
    names[type(NOT_IMPLEMENTED)]='NotImplemented'
    from .robot_values import Robot
    names[Robot]='Robot'
    names.update({kind:'Descriptor' for kind in DESCRIPTORS})
    # Remaining allowed exact native classes already have public runtime names.
    return names.get(type(value), type(value).__name__)


def metadata(value):
    return {'kind':'eql', 'comparison':'value' if by_value(value.payload) else 'identity',
            'valueType':value_type(value.payload)}


def describe(value):
    return {'$eqlSpecializer':metadata(value)}


def info(evaluator, specializer):
    validate(evaluator, specializer)
    return metadata(specializer)
