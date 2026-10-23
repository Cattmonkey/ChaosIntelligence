"""Persistent generic functions on the existing callable and effect stack.

Selectors are explicit runtime contracts; selection never invokes user code.
Around and explicitly opted-in primary methods receive a zero-argument next.
It captures the immutable combination and original arguments, never a registry.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType

MAX_METHODS, MAX_ARITY = 128, 32
QUALIFIERS = ('primary', 'before', 'after', 'around')
# Strict inclusion of the existing check_type contracts, not Python's type MRO.
PARENTS = {
    'Int': ('Number', 'Rational'), 'Number': ('Real',),
    'Fraction': ('Rational',), 'Rational': ('Real',),
    'Decimal': ('Real',), 'Real': ('Numeric',), 'Complex': ('Numeric',),
    'Type': ('Class',), 'Class': ('Function',),
    **{name: ('Function',) for name in ('Bot', 'Plan', 'Goal', 'Skill', 'Team', 'Program',
                                       'Process', 'AgentSession', 'Computer', 'Device')},
}


def fail(message, code='argument_error', value=None):
    from .language import LanguageError
    raise LanguageError(message, code=code, value=value)


def intersection(*ceilings):
    result = None
    for ceiling in ceilings:
        if ceiling is not None: result = ceiling if result is None else result & ceiling
    return result


@dataclass(frozen=True, eq=False)
class Method:
    specializers: tuple
    implementation: object
    qualifier: str
    capabilities: object
    with_next: bool = False


@dataclass(frozen=True, eq=False)
class Descriptor:
    owner: object
    name: str
    arity: int
    methods: tuple = ()


@dataclass(eq=False)
class NextDescriptor:
    owner: object
    capabilities: object
    available: bool = True


def enter(evaluator, function, arguments, keywords):
    """Seal explicit native captures after the actual callable ceiling applies."""
    from .language import Native
    from .agent_tools import current_caps
    for value in (function,)+tuple(arguments)+tuple(keywords.values()):
        if type(value) is Native and type(value.generic) is NextDescriptor:
            if value.owner is not evaluator or value.generic.owner is not evaluator:
                fail('Generic next cannot cross evaluators', 'ownership_error')
    ceiling = current_caps(evaluator)
    if type(function) is Native and type(function.generic) is NextDescriptor:
        ceiling = intersection(ceiling,function.capabilities,function.generic.capabilities)
        runner=evaluator.suspension.current
        if runner is not None:runner.capabilities=intersection(runner.capabilities,ceiling)
    if not getattr(evaluator,'_generic_next_capsules',None):return
    from .capture_graph import seal
    seal(evaluator,(function,)+tuple(arguments)+tuple(keywords.values()),ceiling)
    # This very call may discover a stricter alias in the captured graph.
    # Apply the committed capsule ceiling before dispatching its continuation.
    if type(function) is Native and type(function.generic) is NextDescriptor:
        runner=evaluator.suspension.current
        if runner is not None:
            runner.capabilities=intersection(runner.capabilities,function.capabilities,function.generic.capabilities)


@dataclass(frozen=True)
class Combination:
    primary: tuple
    before: tuple
    after: tuple
    around: tuple


@dataclass(frozen=True)
class GenericRequest:
    descriptor: object
    args: tuple
    kwargs: object
    combination: object = None
    index: int = 0
    phase: str = 'around'


def validate_specializers(evaluator, descriptor):
    from .eql_specializers import EqlSpecializer
    from .class_creation import validate_value
    values=[value for method in descriptor.methods for value in method.specializers if type(value) is EqlSpecializer]
    if values:validate_value(evaluator,values)


def owned(evaluator, function):
    from .language import Native
    if type(function) is not Native or type(getattr(function, 'generic', None)) is not Descriptor:
        fail('Expected a native generic Function')
    if function.owner is not evaluator or function.generic.owner is not evaluator:
        fail('Generic functions cannot cross evaluators', 'ownership_error')
    validate_specializers(evaluator,function.generic)
    return function.generic


def project(evaluator, value, *sources):
    """Preserve generic identity when an ordinary callable projection narrows it."""
    from .agent_tools import current_caps
    from .language import Native
    if type(value) is Native and type(getattr(value, 'generic', None)) is NextDescriptor:
        if value.owner is not evaluator or value.generic.owner is not evaluator:
            fail('Generic next cannot cross evaluators', 'ownership_error')
    else: owned(evaluator, value)
    ceiling = intersection(current_caps(evaluator), value.capabilities,
                           *(getattr(source, 'capabilities', None) for source in sources))
    return value if ceiling == value.capabilities else replace(value, capabilities=ceiling)


def make(evaluator, descriptor, ceiling=None):
    from .language import Native
    def call(*args, **kwargs): return GenericRequest(descriptor, tuple(args), MappingProxyType(dict(kwargs)))
    return Native(descriptor.name, call, evaluator, capabilities=ceiling, generic=descriptor, captures=(descriptor,))


def create(evaluator, name, arity=1):
    if type(name) is not str or not 1 <= len(name) <= 160:
        fail('Generic name requires a 1..160 character String')
    if type(arity) is not int or not 1 <= arity <= MAX_ARITY:
        fail('Generic arity requires an Int in 1..32')
    return make(evaluator, Descriptor(evaluator, name, arity))


def selectors(evaluator, values, arity):
    from .class_creation import validate_contract
    if type(values) is not list or len(values) != arity:
        fail('Generic specializers require a List matching dispatch arity')
    from .contracts import Contract
    if any(type(value) is Contract for value in values):
        fail('Composite Contract is not a generic specializer; use nominal or builtin types')
    from .eql_specializers import EqlSpecializer
    from .class_creation import validate_value
    actual=[value for value in values if type(value) is EqlSpecializer]
    if actual:validate_value(evaluator,actual)
    return tuple(value if type(value) is EqlSpecializer else validate_contract(evaluator, value) for value in values)


def same(left, right):
    from .eql_specializers import EqlSpecializer, equivalent
    if type(left) is EqlSpecializer or type(right) is EqlSpecializer:return equivalent(left,right)
    return left is right or type(left) is str and type(right) is str and left == right


def signature(left, right):
    return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))


def qualifier(value):
    if type(value) is not str or value not in QUALIFIERS:
        fail('Generic qualifier requires primary, before, after or around')
    return value


def require_auxiliary(evaluator, implementation):
    """Reject known cold callables without invoking properties or descriptors."""
    from .objects import require_callable, StaticMethod, BoundMethod, ChaosInstance, declaration, member_value
    from .language import Function
    from .agent_tools import BoundAgent
    from .team_bindings import BoundTeam
    from .session_tools import BoundSession
    require_callable(implementation, evaluator, setter=True)
    seen = set()
    while id(implementation) not in seen:
        seen.add(id(implementation))
        if type(implementation) in (BoundAgent, BoundTeam, BoundSession):
            fail('Generic before/after methods must be synchronous')
        if type(implementation) is Function and implementation.flavor != 'fn':
            fail('Generic before/after methods must be synchronous')
        if type(implementation) is StaticMethod: implementation = implementation.value
        elif type(implementation) is BoundMethod: implementation = implementation.function
        elif type(implementation) is ChaosInstance:
            member = declaration(implementation.klass, '__call__')
            if member is None: break
            implementation = member_value(member)
        else: break


def extend(evaluator, function, specializers, implementation, qualifier='primary', *, with_next=False):
    from .agent_tools import current_caps
    from .objects import require_callable
    from .class_creation import project_value
    from .language import ResourceLimit
    descriptor = owned(evaluator, function)
    kind = globals()['qualifier'](qualifier)
    if type(with_next) is not bool or with_next and kind != 'primary':
        fail('with_next requires Bool and is available only for primary methods')
    selected = selectors(evaluator, specializers, descriptor.arity)
    require_callable(implementation, evaluator)
    if kind in ('before', 'after'): require_auxiliary(evaluator, implementation)
    ceiling = intersection(current_caps(evaluator), function.capabilities)
    implementation = project_value(evaluator, implementation, function)
    method = Method(selected, implementation, kind, ceiling, with_next)
    methods = list(descriptor.methods)
    for index, prior in enumerate(methods):
        evaluator.tick()
        if prior.qualifier == kind and signature(prior.specializers, selected):
            methods[index] = method
            break
    else:
        if len(methods) >= MAX_METHODS: raise ResourceLimit('Generic method table exceeds 128 entries')
        methods.append(method)
    return make(evaluator, replace(descriptor, methods=tuple(methods)), ceiling)


def remove(evaluator, function, specializers, qualifier='primary'):
    from .agent_tools import current_caps
    descriptor = owned(evaluator, function)
    kind = globals()['qualifier'](qualifier)
    selected = selectors(evaluator, specializers, descriptor.arity)
    methods = tuple(method for method in descriptor.methods
                    if not (method.qualifier == kind and signature(method.specializers, selected)))
    return make(evaluator, replace(descriptor, methods=methods),
                intersection(current_caps(evaluator), function.capabilities))


def methods(evaluator, function):
    from .class_creation import project_value
    descriptor = owned(evaluator, function)
    return [project_value(evaluator, {'specializers': list(method.specializers),
                                     'implementation': method.implementation,
                                     'qualifier': method.qualifier,
                                     **({'withNext': True} if method.with_next else {})}, function, method)
            for method in descriptor.methods]


def info(evaluator, function):
    descriptor = owned(evaluator, function)
    return {'name': descriptor.name, 'arity': descriptor.arity,
            'methodCount': len(descriptor.methods),
            'qualifiers': [kind for kind in QUALIFIERS if any(method.qualifier == kind for method in descriptor.methods)],
            'precedence': 'left-to-right', 'nextArguments': 'captured'}


def accepts(evaluator, selector, value):
    from .language import check_type
    evaluator.tick()
    from .eql_specializers import EqlSpecializer, equal
    if type(selector) is EqlSpecializer:return equal(selector.payload,value)
    return check_type(value, selector, _probe=True)


def narrower(left, right):
    if left == right: return False
    if right == 'Any': return True
    pending, seen = list(PARENTS.get(left, ())), set()
    while pending:
        parent = pending.pop()
        if parent == right: return True
        if parent not in seen:
            seen.add(parent); pending.extend(PARENTS.get(parent, ()))
    return False


def compare_selector(left, right, value):
    """-1: left more specific; +1: right; None: incomparable."""
    from .language import DataType
    from .objects import ChaosClass, ChaosInstance
    if same(left, right): return 0
    from .eql_specializers import EqlSpecializer
    if type(left) is EqlSpecializer or type(right) is EqlSpecializer:
        if type(left) is type(right):return None
        return -1 if type(left) is EqlSpecializer else 1
    if type(left) is ChaosClass or type(right) is ChaosClass:
        if type(left) is not ChaosClass: return 1
        if type(right) is not ChaosClass: return -1
        actual = value.metaclass if type(value) is ChaosClass else value.klass if type(value) is ChaosInstance else None
        # Both selectors already matched the actual nominal hierarchy.
        return -1 if actual.mro.index(left) < actual.mro.index(right) else 1
    if type(left) is DataType or type(right) is DataType:
        if type(left) is DataType and type(right) is DataType: return None
        if type(left) is DataType: return -1 if right in ('Record', 'Any') else None
        return 1 if left in ('Record', 'Any') else None
    if narrower(left, right): return -1
    if narrower(right, left): return 1
    return None


def compare(left, right, arguments):
    for a, b, value in zip(left.specializers, right.specializers, arguments):
        result = compare_selector(a, b, value)
        if result != 0: return result
    return 0


def choose(evaluator, candidates, arguments):
    winners = []
    for method in candidates:
        evaluator.tick()
        if not any(compare(other, method, arguments) == -1 for other in candidates if other is not method):
            winners.append(method)
    if len(winners) != 1:
        fail('Generic dispatch has incomparable applicable methods', 'ambiguous_method')
    return winners[0]


def ordered(evaluator, candidates, arguments):
    result, pending = [], list(candidates)
    while pending:
        winner = choose(evaluator, pending, arguments)
        result.append(winner); pending.remove(winner)
    return tuple(result)


def select(evaluator, descriptor, arguments):
    from .class_creation import validate_value
    if len(arguments) < descriptor.arity:
        fail('Generic dispatch requires at least '+str(descriptor.arity)+' positional arguments')
    validate_specializers(evaluator,descriptor)
    for value in arguments[:descriptor.arity]: validate_value(evaluator, value)
    applicable = {kind: [] for kind in QUALIFIERS}
    for method in descriptor.methods:
        if all(accepts(evaluator, specializer, argument)
               for specializer, argument in zip(method.specializers, arguments)):
            applicable[method.qualifier].append(method)
    if not applicable['primary']:
        fail('No applicable primary method for '+descriptor.name, 'no_applicable_method')
    # Complete every potentially executed combination order before effects.
    primary, pending = [], list(applicable['primary'])
    while pending:
        method = choose(evaluator, pending, arguments)
        primary.append(method); pending.remove(method)
        # An ordinary primary cannot reach lower primary methods. Preserve the
        # original rule that its unique win resolves an ambiguous lower tail.
        if not method.with_next: break
    return Combination(tuple(primary), ordered(evaluator, applicable['before'], arguments),
                       tuple(reversed(ordered(evaluator, applicable['after'], arguments))),
                       ordered(evaluator, applicable['around'], arguments))


def delegate(evaluator, method, args, kwargs):
    """A normal Native ceiling carrier avoids projecting opaque callables."""
    from .language import Native, TailCall
    def call(*arguments, **keywords):
        return TailCall(method.implementation, list(arguments), kwargs=keywords)
    ceiling = Native('generic.method', call, evaluator, capabilities=method.capabilities, captures=(method,))
    return TailCall(ceiling, list(args), kwargs=kwargs)


def next_available(evaluator, next):
    from .language import Native
    if type(next) is not Native or type(next.generic) is not NextDescriptor:
        fail('generic_next_available requires a native generic next Function')
    if next.owner is not evaluator or next.generic.owner is not evaluator:
        fail('Generic next cannot cross evaluators', 'ownership_error')
    return next.generic.available


def continuation(evaluator, request, combination, index, phase):
    from .language import Native
    from .agent_tools import current_caps
    available = phase == 'around' or index < len(combination.primary)
    def next_call():
        if not available: fail('No next primary method', 'no_next_method')
        return GenericRequest(request.descriptor, request.args, request.kwargs, combination, index, phase)
    import weakref
    token = NextDescriptor(evaluator,current_caps(evaluator),available)
    if not hasattr(evaluator,'_generic_next_capsules'):evaluator._generic_next_capsules=weakref.WeakSet()
    evaluator._generic_next_capsules.add(token)
    return Native(request.descriptor.name+'.next', next_call, evaluator, capabilities=current_caps(evaluator),
                  generic=token, captures=(request,combination))


def primary_call(evaluator, request, combination, index):
    from .agent_tools import scope
    if index >= len(combination.primary): fail('No next primary method', 'no_next_method')
    method = combination.primary[index]
    with scope(evaluator, method.capabilities):
        arguments = request.args
        if method.with_next:
            next = continuation(evaluator, request, combination, index+1, 'primary')
            arguments = [next]+list(arguments)
        return delegate(evaluator, method, arguments, request.kwargs)


def resolve(evaluator, request):
    # The native generator itself owns this protocol loop. In particular there
    # is no yield-from wrapper between its GeneratorExit handler and the typed
    # cleanup state: a rejected close leaves this very iterator resumable.
    from .execution_callables import generic_cursor
    from .execution_frames import Done
    cursor = generic_cursor(evaluator, request)
    value = error = None
    closing = False
    closing_error = None
    while True:
        if closing:
            # close_request reports completion with Done. A StopIteration from
            # cleanup is an algorithm failure, never driver completion.
            action = cursor.close_request(closing_error)
            if type(action) is Done:
                if not cursor.close_suppressed:
                    raise closing_error
                return action.value
            pending = action.request
        else:
            try:
                pending = cursor.throw(error) if error is not None else cursor.send(value)
            except StopIteration as stop:
                return stop.value
        try:
            value = yield pending
            error = None
            closing = False
        except GeneratorExit as failure:
            value = error = None
            closing_error = failure
            closing = True
        except BaseException as failure:
            value = None
            error = failure
            closing = False


def builtins(evaluator):
    from .eql_specializers import create as eql_create, info as eql_info
    return {
        'generic_eql': lambda value: eql_create(evaluator,value),
        'generic_eql_info': lambda specializer: eql_info(evaluator,specializer),
        'generic_function': lambda name, arity=1: create(evaluator, name, arity),
        'generic_method': lambda function, specializers, implementation, qualifier='primary', *, with_next=False: extend(evaluator, function, specializers, implementation, qualifier, with_next=with_next),
        'generic_remove': lambda function, specializers, qualifier='primary': remove(evaluator, function, specializers, qualifier),
        'generic_methods': lambda function: methods(evaluator, function),
        'generic_info': lambda function: info(evaluator, function),
        'generic_next_available': lambda next: next_available(evaluator, next),
    }
