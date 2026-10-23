"""Bounded exception trees and native leaf-pattern handlers.

The scheduler and evaluator share actual exception identities. Occurrence paths,
not identities, partition manually constructed trees (which may repeat leaves).
"""
from .language import LanguageError, ResourceLimit, Env, check_type


class GroupedError(LanguageError):
    def __init__(self, message, exceptions):
        if type(message) is not str or not message or len(message) > 10000:
            raise LanguageError('错误组 message 需要非空 String（最多 10000 字符）', code='argument_error')
        self.exceptions = tuple(exceptions)
        if not self.exceptions:
            raise LanguageError('错误组至少需要一个 Error', code='argument_error')
        super().__init__(message, code='exception_group')
        list(leaves(self))

    def derive(self, children):
        result = GroupedError(self.message, children)
        result.file, result.line, result.column = self.file, self.line, self.column
        copy_chain(self, result)
        return result

    def record(self):
        result = super().record()
        result['value'] = {'exceptions': [error.record() for error in self.exceptions]}
        return result


def originates(error, original):
    """Follow private authority projections without exposing host attributes."""
    seen = set()
    while error is not None:
        if error is original: return True
        identity = id(error)
        if identity in seen or len(seen) >= 10000:
            raise ResourceLimit('错误投影来源超过资源限制')
        seen.add(identity)
        error = getattr(error, '_projection_origin', None)
    return False


def catchable(error):
    return isinstance(error, LanguageError) and not isinstance(error, ResourceLimit)


def leaves(error):
    pending, count = [((), error)], 0
    while pending:
        path, item = pending.pop()
        count += 1
        if count > 10000 or len(path) > 32:
            raise ResourceLimit('错误组超过 10000 节点或 32 层限制')
        if not catchable(item):
            raise LanguageError('错误组仅接纳普通语言错误', code='argument_error')
        if type(item) is GroupedError:
            pending.extend((path + (i,), child) for i, child in reversed(list(enumerate(item.exceptions))))
        else:
            yield path, item


def project(error, paths, prefix=(), replacements=None):
    if type(error) is not GroupedError:
        return (replacements or {}).get(prefix, error) if prefix in paths else None
    children = [project(child, paths, prefix + (i,), replacements) for i, child in enumerate(error.exceptions)]
    if all(child is original for child, original in zip(children, error.exceptions)):
        return error
    kept = [child for child in children if child is not None]
    return error.derive(kept) if kept else None


def merged(original, remaining, reraised, failures, replacements):
    rest = project(original, remaining | reraised, replacements=replacements)
    if not failures: return rest
    children = failures + ([rest] if rest is not None else [])
    return children[0] if len(children) == 1 else GroupedError('分组处理中的错误', children)



CHAIN_FIELDS = ('_error_cause', '_error_context', '_suppress_context', '_context_seen', '_error_capabilities', '_exception_instance')


def copy_chain(source, target):
    """Preserve native causal metadata, never Python traceback/context state."""
    for name in CHAIN_FIELDS:
        if hasattr(source, name): setattr(target, name, getattr(source, name))
    return target


def graph(error):
    """Children-first bounded DAG, including group, cause and context edges."""
    pending, active, done, ordered, edges = [(error, False)], set(), {}, [], 0
    while pending:
        item, leaving = pending.pop()
        identity = id(item)
        if leaving:
            children = list(item.exceptions) if type(item) is GroupedError else []
            children += [child for child in (getattr(item, '_error_cause', None),
                                             getattr(item, '_error_context', None)) if child is not None]
            height = 1 + max((done[id(child)] for child in children), default=-1)
            done[identity] = height; active.remove(identity); ordered.append(item)
            continue
        if identity in active: raise LanguageError('错误因果图不能包含循环', code='value_error')
        if identity in done: continue
        if not catchable(item): raise LanguageError('错误因果图仅接纳普通语言错误', code='argument_error')
        if len(active) + len(done) >= 10000: raise ResourceLimit('错误图超过 10000 节点限制')
        children = list(item.exceptions) if type(item) is GroupedError else []
        children += [child for child in (getattr(item, '_error_cause', None),
                                         getattr(item, '_error_context', None)) if child is not None]
        edges += len(children)
        if edges > 20000: raise ResourceLimit('错误图超过 20000 条边限制')
        active.add(identity); pending.append((item, True))
        pending.extend((child, False) for child in reversed(children))
    return ordered


def observe(evaluator, error):
    """Seal the first propagation in the logical task's causal context.

    Reusing an Error cannot overwrite another task's provenance. Linking a
    descendant back to its parent (or an authority projection) is omitted;
    existing graphs and exception identities are never destructively edited.
    """
    if not catchable(error) or getattr(error, '_context_seen', False): return error
    previous = evaluator.error_context
    if previous is not None and catchable(previous) and not originates(error, previous):
        nodes = graph(previous)
        if not any(originates(node, error) or originates(error, node) for node in nodes):
            error._error_context = previous
            try: graph(error)
            except BaseException:
                del error._error_context
                raise
    from .agent_tools import current_caps
    caps, inherited = current_caps(evaluator), getattr(error, '_error_capabilities', None)
    error._error_capabilities = inherited if caps is None else caps if inherited is None else caps & inherited
    error._context_seen = True
    return error
