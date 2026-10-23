"""Evaluator-owned Error values; diagnostic records cannot revive exceptions."""
from dataclasses import dataclass

UNSET = object()


@dataclass(frozen=True, eq=False)
class Error:
    exception: object
    owner: object
    capabilities: object = None


def owned(value, evaluator):
    from .language import LanguageError
    if type(value) is not Error or value.owner is not evaluator:
        raise LanguageError('Error 不能跨执行上下文使用', code='ownership_error')
    from .errors import graph
    graph(value.exception)
    return value


def capture(evaluator, exception):
    from .errors import graph
    from .agent_tools import current_caps
    graph(exception)
    caps = current_caps(evaluator)
    inherited = getattr(exception, "_error_capabilities", None)
    if inherited is not None: caps = inherited if caps is None else caps & inherited
    return Error(exception, evaluator, caps)


def create(evaluator, value, *, cause=UNSET):
    from .exception_classes import raised
    from .class_creation import project_value
    return explicit_cause(evaluator, capture(evaluator, raised(evaluator, project_value(evaluator, value))), cause)


def current(evaluator):
    return None if evaluator.handled_error is None else capture(evaluator, evaluator.handled_error)


def explicit_cause(evaluator, value, cause):
    from .errors import graph
    if cause is UNSET: return value
    value.exception._error_cause = None if cause is None else propagate(evaluator, owned(cause, evaluator))
    value.exception._suppress_context = True
    graph(value.exception)
    return value


def propagate(evaluator, value):
    from .agent_tools import current_caps
    from .class_creation import project_value
    from .errors import GroupedError, graph, copy_chain
    from .language import LanguageError, Raised
    owned(value, evaluator)
    ordered = graph(value.exception)
    if value.capabilities is None and current_caps(evaluator) is None and all(getattr(e, "_error_capabilities", None) is None for e in ordered):
        return value.exception
    def unchanged(left, right):
        if left is right: return True
        from .tuple_values import TupleValue
        if type(left) is not type(right): return False
        if type(left) in (list, TupleValue):
            return len(left) == len(right) and all(unchanged(a, b) for a, b in zip(left, right))
        if type(left) is dict:
            return left.keys() == right.keys() and all(unchanged(left[k], right[k]) for k in left)
        return False
    def intersect(left, right):
        return right if left is None else left if right is None else left & right
    ceilings = {id(value.exception): intersect(current_caps(evaluator), value.capabilities)}
    for error in reversed(ordered):
        ceiling = intersect(ceilings[id(error)], getattr(error, '_error_capabilities', None))
        ceilings[id(error)] = ceiling
        children = list(error.exceptions) if type(error) is GroupedError else []
        children += [child for child in (getattr(error, '_error_cause', None), getattr(error, '_error_context', None)) if child is not None]
        for child in children:
            key = id(child)
            ceilings[key] = intersect(ceilings[key], ceiling) if key in ceilings else ceiling
    memo = {}
    for error in ordered:
        cause, context = getattr(error, '_error_cause', None), getattr(error, '_error_context', None)
        projected_cause = memo[id(cause)] if cause is not None else None
        projected_context = memo[id(context)] if context is not None else None
        if type(error) is GroupedError:
            children = [memo[id(child)] for child in error.exceptions]
            same = all(a is b for a, b in zip(children, error.exceptions))
        else:
            record = project_value(evaluator, error.record(), value, Error(error, evaluator, ceilings[id(error)]))
            same = unchanged(error.value, record['value'])
        if same and cause is projected_cause and context is projected_context and ceilings[id(error)] == getattr(error, '_error_capabilities', None):
            memo[id(error)] = error; continue
        if type(error) is GroupedError:
            result = error.derive(children)
        elif isinstance(error, Raised):
            result = Raised(record['value'], message=record['message'], code=record['code'], **record['location'])
        else:
            result = LanguageError(record['message'], code=record['code'], value=record['value'], **record['location'])
        copy_chain(error, result)
        result._error_capabilities = ceilings[id(error)]
        result._error_cause, result._error_context = projected_cause, projected_context
        result._projection_origin = error
        memo[id(error)] = result
    return memo[id(value.exception)]


def record_for(evaluator, exception):
    return propagate(evaluator, capture(evaluator, exception)).record()


def group(evaluator, message, errors, *, cause=UNSET):
    from .language import LanguageError
    from .errors import GroupedError
    from .agent_tools import current_caps
    if type(errors) is not list:
        raise LanguageError('error_group errors 需要 List[Error]', code='argument_error')
    children, ceiling = [], current_caps(evaluator)
    for item in errors:
        owned(item, evaluator)
        inherited = item.capabilities
        if inherited is not None: ceiling = inherited if ceiling is None else ceiling & inherited
        children.append(item.exception)
    return explicit_cause(evaluator, Error(GroupedError(message, children), evaluator, ceiling), cause)


def describe(value):
    from .errors import GroupedError, graph
    graph(value.exception)
    seen, pending = {}, []
    def reference(error, depth):
        if error is None: return None
        identity = id(error)
        if identity in seen: return {'$errorRef': seen[identity]}
        index = len(seen); seen[identity] = index
        if depth >= 16 or index >= 512:
            return {'$error': {'id': index, 'code': error.code, 'message': error.message, 'truncated': True}}
        result = error.record(); result['id'] = index
        pending.append((error, result, depth))
        return {'$error': result}
    root = reference(value.exception, 0)
    while pending:
        error, result, depth = pending.pop()
        result['exceptions'] = [reference(child, depth+1) for child in error.exceptions] if type(error) is GroupedError else []
        result['cause'] = reference(getattr(error, '_error_cause', None), depth+1)
        result['context'] = reference(getattr(error, '_error_context', None), depth+1)
        result['suppress_context'] = getattr(error, '_suppress_context', False)
    return root


def member(value, name, evaluator):
    from .language import LanguageError, TupleValue
    from .errors import GroupedError
    from .class_creation import project_value
    owned(value, evaluator)
    error = value.exception
    def wrapped(linked):
        from .agent_tools import current_caps
        caps = current_caps(evaluator)
        for inherited in (value.capabilities, getattr(error, '_error_capabilities', None), getattr(linked, '_error_capabilities', None)):
            if inherited is not None: caps = inherited if caps is None else caps & inherited
        return Error(linked, evaluator, caps)
    if name in ('cause', 'context'):
        linked = getattr(error, '_error_' + name, None)
        return wrapped(linked) if linked is not None else None
    if name == 'suppress_context': return getattr(error, '_suppress_context', False)
    if name == 'instance':
        from .exception_classes import instance
        return instance(evaluator, propagate(evaluator, wrapped(error)))
    if name == 'exceptions':
        return TupleValue([wrapped(child) for child in error.exceptions]) if type(error) is GroupedError else TupleValue([])
    if name not in ('code', 'message', 'value', 'location'):
        raise LanguageError('Error 成员不存在 ' + str(name), code='attribute_error')
    return project_value(evaluator, propagate(evaluator, wrapped(error)).record()[name], value, wrapped(error))
