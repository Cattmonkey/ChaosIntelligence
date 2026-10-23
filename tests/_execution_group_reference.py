"""Test-only pre-frame catch* reference; never imported by the product."""
from chaos.language import LanguageError, ResourceLimit, Env, check_type
from chaos.errors import leaves, originates, project, merged, observe, GroupedError


def legacy_group(evaluator, node, env, completion=None):
    from chaos.error_values import capture
    from chaos.multiple_values import primary
    from chaos.language import validate_try_group
    validate_try_group(node, tick=evaluator.tick)
    try:
        result = yield from evaluator._s_protected_value(node[1], env)
    except ResourceLimit:
        raise
    except LanguageError as original:
        occurrences = list(leaves(original))
        remaining = {path for path, _ in occurrences}
        reraised, failures, result, replacements = set(), [], None, {}
        previous, previous_context = evaluator.handled_error, evaluator.error_context
        try:
            for case in node[2]:
                _, pattern, guard, name, body = case
                selected = set()
                try:
                    for path, leaf in occurrences:
                        if path not in remaining: continue
                        evaluator.tick()
                        evaluator.handled_error = evaluator.error_context = leaf
                        bindings = {}
                        from chaos.exception_classes import match as match_exception
                        if not match_exception(evaluator, pattern, leaf, env, bindings): continue
                        scope = Env(env)
                        for key, item in bindings.items(): scope.bind(key, item)
                        if guard is not None and not check_type(primary((yield from evaluator._s_protected_value(guard, scope)),evaluator), 'Bool'):
                            continue
                        selected.add(path)
                except ResourceLimit:
                    raise
                except LanguageError as selection_error:
                    observe(evaluator, selection_error)
                    # The partition commits only when every guard has completed.
                    if not originates(selection_error, leaf):
                        failures.append(selection_error)
                    elif selection_error is not leaf: replacements[path] = selection_error
                    break
                if not selected: continue
                remaining -= selected
                subgroup = project(original, selected)
                if type(subgroup) is not GroupedError:
                    subgroup = GroupedError('捕获的错误', [subgroup])
                evaluator.handled_error = evaluator.error_context = subgroup
                scope = Env(env)
                scope.bind(name, capture(evaluator, subgroup))
                try:
                    result = yield from evaluator._s_protected_value(body, scope)
                except ResourceLimit:
                    raise
                except LanguageError as handler_error:
                    observe(evaluator, handler_error)
                    if originates(handler_error, subgroup):
                        reraised |= selected
                        if handler_error is not subgroup:
                            selected_paths = [path for path, _ in occurrences if path in selected]
                            replacements.update(zip(selected_paths, (leaf for _, leaf in leaves(handler_error))))
                    else: failures.append(handler_error)
            evaluator.error_context = previous_context
            error = merged(original, remaining, reraised, failures, replacements)
            if error is not None: raise error
            if completion is not None: completion.state = 'handled'
            return result
        finally:
            evaluator.handled_error, evaluator.error_context = previous, previous_context
    else:
        if completion is not None: completion.state = 'normal'
        return result

