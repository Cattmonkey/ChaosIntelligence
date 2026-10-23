"""Test-only pre-frame lexical algorithms; no product imports this module."""
from chaos.language import Env, LanguageError, ResourceLimit, ReturnSignal, BreakSignal, ContinueSignal
from chaos.suspension import GeneratorClose, error_priority
from chaos.task_groups import ScopeOutcome, GroupRun, GroupAbort

def legacy_group(evaluator, group, node, env):
    from chaos.task_groups import owned
    from chaos.control_prompts import NonlocalTransfer
    owned(group, evaluator)
    if node[0] not in ('async_with', 'async_with_pattern'): raise LanguageError('TaskGroup 需要 async with', code='argument_error')
    if group.state != 'new': raise LanguageError('TaskGroup 只能进入一次', code='task_group_error')
    scope = Env(env)
    def body():
        try:
            evaluator.bind_scope_target(node, group, scope, env)
            return ScopeOutcome(value=(yield from evaluator._s_protected_value(node[3], scope)))
        except (ReturnSignal, BreakSignal, ContinueSignal, GeneratorClose, GroupAbort,NonlocalTransfer) as signal:
            return ScopeOutcome(signal=signal)
        except BaseException as error:
            return ScopeOutcome(error=error)
    outcome = yield GroupRun(group, body())
    if outcome.signal is not None: raise outcome.signal
    return outcome.value


def legacy_shield(evaluator, value, node, env):
    from chaos.cancellation import owned, requested
    owned(value, evaluator)
    scheduler = evaluator.suspension
    task = scheduler.current.task if scheduler.current else None
    if task is None:
        raise LanguageError('CancelShield 必须位于活动 Task 内', code='cancel_shield_error')
    if value.state != 'new':
        raise LanguageError('CancelShield 只能进入一次', code='cancel_shield_error')
    # A shield cannot rescue control already delivered before lexical entry.
    evaluator.tick()
    value.task, value.stream = task, scheduler.stream
    value.depth, value.state = len(task.cancel_shields) + 1, 'active'
    task.cancel_shields.append(value)
    error, result = None, None
    try:
        scheduler.event('shield.entered', value)
        scope = Env(env)
        evaluator.bind_scope_target(node, value, scope, env)
        result = yield from evaluator._s_protected_value(node[3], scope)
    except BaseException as caught:
        error = caught
    finally:
        value.requested = requested(task)
        task.cancel_shields.remove(value)
        value.state, value.depth = 'closed', 0
        # Delivery uses the original stable worker event and original sources.
        # Inner scopes cannot consume or clear an outer scope's pending control.
        if not task.cancel_shields:
            pending, task.deferred_interrupt = task.deferred_interrupt, None
            if task.cancel_sources and not task.cancel_event.is_set():
                scheduler._cancel(task, next(iter(task.cancel_sources)))
            if task.cancel_event.is_set():
                cancelled = scheduler.cancelled_error(task)
                if error_priority(cancelled) >= error_priority(pending): pending = cancelled
            if pending is not None:
                priority, previous = error_priority(pending), error_priority(error)
                # Preserve a foreign cancellation already observed by this Task;
                # replacing it with an internal group cancel would lose provenance.
                if priority > previous or priority == previous and priority < 2:
                    error = pending
        try:
            scheduler.event('shield.closed', value)
        except BaseException as notification:
            if error_priority(notification) > error_priority(error): error = notification
    if error is not None: raise error
    return result


def legacy_prompt(evaluator,scope,node,env):
    from chaos.control_prompts import enter, release, PromptTransfer, handled
    from chaos.language import Env
    handler=scope.handler
    enter(evaluator,scope);signal=None
    try:
        local=Env(env);evaluator.bind_scope_target(node,scope,local,env)
        try:result=yield from evaluator._s_protected_value(node[3],local)
        except PromptTransfer as caught:
            if caught.target is not scope:raise
            signal=caught
    finally:release(scope)
    if signal is not None:return (yield from handled(evaluator,scope,handler,signal))
    return result


