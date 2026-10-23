"""Invocation-local completion values, never a language container or register.

Raw ordinary values represent one result. Completion explicitly represents
zero, one, or many results and belongs to the evaluator (or pure macro phase).
Only receiver protocols inspect it; ordinary value/JSON APIs reject it.
"""
from dataclasses import dataclass


@dataclass(frozen=True, eq=False)
class Completion:
    owner: object
    items: tuple


@dataclass(frozen=True, eq=False)
class Request:
    owner: object
    operation: str
    function: object = None
    producers: tuple = ()
    count: object = None


def components(value, owner):
    if type(value) is Completion:
        if value.owner is not owner:
            from .language import LanguageError
            raise LanguageError('Completion cannot cross evaluators or macro phases', code='ownership_error')
        if type(value.items) is not tuple or len(value.items) > 10000:
            from .language import ResourceLimit
            raise ResourceLimit('Multiple results require at most 10000 values')
        return value.items
    return (value,)


def primary(value, owner):
    items = components(value, owner)
    return items[0] if items else None


def validate(evaluator, value):
    if type(value) is not Completion: return value
    items = components(value, evaluator)
    from .class_creation import validate_value
    # Validate every secondary value before any receiver discards it.
    validate_value(evaluator, list(items))
    return value


_DEFAULT_CEILING=object()


def prepare(evaluator,value,*,ceiling=_DEFAULT_CEILING):
    """Seal every packet component before any primary or count projection."""
    if type(value) is not Completion:return value
    validate(evaluator,value)
    from .agent_tools import current_caps
    from .capture_graph import seal
    from .class_creation import _project_return
    if ceiling is _DEFAULT_CEILING:ceiling=current_caps(evaluator)
    seal(evaluator,(value,),ceiling)
    return _project_return(evaluator,value,ceiling) if ceiling is not None else value


def make(evaluator, items):
    result = Completion(evaluator, tuple(items))
    return validate(evaluator, result)


def index(value, label):
    from .language import LanguageError
    if type(value) is not int or value < 0:
        raise LanguageError(label+' requires a non-negative Int', code='argument_error')
    return value


def producer(evaluator, value):
    from .suspension import Task,Generator,AsyncGenerator
    from .objects import require_callable
    if type(value) in (Task,Generator,AsyncGenerator):
        scheduler=evaluator.suspension
        scheduler.validate(value,type(value))
        # Await has a separate recording guard for Tasks; explicit queries do too.
        scheduler._recording_access(value)
        if value.state!='completed':
            from .language import LanguageError
            raise LanguageError('Multiple-value producer handle must be completed',code='multiple_value_state_error')
        return value
    return require_callable(value,evaluator)


def receive(evaluator, value):
    from .execution_multiple_values import values_protocol
    return values_protocol(evaluator, 'receive', value)


def request(evaluator, operation, function=None, producers=(), count=None):
    from .objects import require_callable
    if operation in ('call','bind'): require_callable(function, evaluator)
    for value in producers: producer(evaluator,value)
    if operation in ('nth', 'bind'): index(count, 'index' if operation == 'nth' else 'count')
    if operation == 'bind' and count > 10000:
        from .language import ResourceLimit
        raise ResourceLimit('Multiple-value bindings exceed 10000 arguments')
    return Request(evaluator, operation, function, tuple(producers), count)


def resolve(evaluator, value):
    from .execution_multiple_values import values_protocol
    return values_protocol(evaluator, 'resolve', value)


def builtins(evaluator):
    from .language import check_type
    return {
        'return_values': lambda *items: make(evaluator, items),
        'values_list': lambda items: make(evaluator, check_type(items, 'List')),
        'multiple_value_list': lambda producer: request(evaluator, 'list', producers=(producer,)),
        'nth_value': lambda index, producer: request(evaluator, 'nth', producers=(producer,), count=index),
        'multiple_value_call': lambda function, *producers: request(evaluator, 'call', function, producers),
        'multiple_value_bind': lambda count, producer, consumer: request(evaluator, 'bind', consumer, (producer,), count),
        'multiple_value_prog1': lambda first, *afters: request(evaluator, 'prog1', producers=(first,)+afters),
    }
