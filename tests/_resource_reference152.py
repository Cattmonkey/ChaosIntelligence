"""Exact pre152 resource algorithms, bound to separate original module namespaces."""
import types
from contextlib import contextmanager,ExitStack
from unittest.mock import patch
from chaos import contexts as _contexts,dynamic_contexts as _dynamic_contexts,resource_stacks as _resource_stacks
def _manager_operation(evaluator, manager, error=None, closing=False):
    scheduler = evaluator.suspension
    iterator = (scheduler._s_close(manager.generator, context=manager) if closing else
                scheduler._s_resume(manager.generator, error=error, context=manager))
    if type(manager) is ContextManager:
        return (yield from iterator)
    before = len(scheduler.tasks)
    task = scheduler.task(iterator, capture_roots=(manager, error))
    from chaos.conditions import active_state as recovery_state
    task.runner.condition_state=recovery_state(scheduler)
    from chaos.control_prompts import active_state as control_state
    task.runner.control_state=control_state(scheduler)
    from chaos.dynamic_contexts import active_state as context_state
    task.runner.context_state=context_state(scheduler)
    task.runner.error_context=evaluator.error_context
    task.recovery_protocol=True
    private = _operation(evaluator, task, scheduler.tasks[before:])
    return (yield from _await(evaluator, task, private))


def _manager_exit(evaluator, manager, error=None):
    manager.state = 'exiting'
    try:
        item = yield from _manager_operation(evaluator, manager, error)
    except BaseException as raised:
        # Identity distinguishes propagation from a new cleanup failure. User
        # catch records remain data and cannot forge this internal relationship.
        if error is not None and raised is error: return False
        raise
    if not item['done']:
        try:
            raise LanguageError('上下文生成器必须恰好 yield 一次', code='context_error')
        finally:
            yield from _manager_operation(evaluator, manager, closing=True)
    return error is not None


def _await(evaluator, task, protocol_owned=False, *, cleanup_operation=False, all_results=False):
    from chaos.task_groups import GroupAbort, TaskGroup
    from chaos.suspension import Drain, error_priority
    scheduler = evaluator.suspension
    scheduler.validate(task, Task)
    current = scheduler.current.task if scheduler.current else None
    structured = current is not None and (current.scope_group is not None or current.group is not None or current.cancel_shields or getattr(current, 'context_structured', False) or getattr(current, 'stack_operation', None) is not None)
    try:
        from chaos.multiple_values import primary,prepare
        result=prepare(evaluator,(yield Await(task)))
        return result if all_results else primary(result,evaluator)
    except BaseException as original:
        if structured:
            # Borrowed Tasks retain their lifecycle even when a resource scope
            # happens to await them. Only a Task created by this protocol call
            # is an operation whose cancellation this scope owns.
            if protocol_owned and task.state not in TERMINAL:
                group = current.scope_group or current.group
                same_group = (type(group) is TaskGroup and group.owner is scheduler and
                              group.epoch == scheduler.epoch and current.owner is scheduler and
                              current.epoch == scheduler.epoch and task.group is group)
                # A private operation may have joined this caller's group while
                # awaiting. Its cleanup belongs to that same real owner; adding
                # an anonymous source would make an internal cancellation look
                # external and displace the group's original failure.
                source = group if same_group else object()
                if isinstance(original, GroupAbort) and (cleanup_operation or getattr(task, 'stack_operation_kind', None) == 'aclose'):
                    scheduler.interrupt(task, original)
                else: scheduler._cancel(task, source)
                try:
                    yield Drain(task)
                except BaseException as drained:
                    internal = scheduler._local_cancel(drained, task) and (
                        task.cancel_sources == {source} or
                        same_group and group._internal_cancel(task, drained))
                    if not internal and error_priority(drained) > error_priority(original): raise
            raise
        if task.state not in TERMINAL:
            scheduler.cancel(task)
        raise


def _invoke(evaluator, function, args, asynchronous):
    before = len(evaluator.suspension.tasks)
    result = yield from evaluator._s_invoke(function, args)
    if asynchronous:
        scheduler=evaluator.suspension
        current=scheduler.current.task if scheduler.current else None
        # Only the fresh protocol-call Task is transparent. An aliased/borrowed
        # user Task retains its independent dynamic holder and lifecycle.
        if (type(result) is Task and result in scheduler.tasks[before:] and result.state=='created'
                and result.parent_task_id==(current.id if current else None) and result.kind=='task'):
            from chaos.conditions import active_state as recovery_state
            result.runner.condition_state=recovery_state(scheduler)
            from chaos.control_prompts import active_state as control_state
            result.runner.control_state=control_state(scheduler)
            from chaos.dynamic_contexts import active_state as context_state
            result.runner.context_state=context_state(scheduler)
            result.runner.error_context=evaluator.error_context
            result.recovery_protocol=True
        private = _operation(evaluator, result, evaluator.suspension.tasks[before:])
        result = yield from _await(evaluator, result, private)
    return result


def _exit(evaluator, function, error, asynchronous, computer):
    from chaos.robot_values import Robot
    if type(computer) is Robot:
        from chaos.robot_values import context_exit
        before=len(evaluator.suspension.tasks);task=context_exit(evaluator,computer)
        private=_operation(evaluator,task,evaluator.suspension.tasks[before:])
        yield from _await(evaluator,task,private)
        return False
    from chaos.device_values import Device
    if type(computer) is Device:
        from chaos.device_values import context_exit
        before=len(evaluator.suspension.tasks)
        task=context_exit(evaluator,computer)
        private=_operation(evaluator,task,evaluator.suspension.tasks[before:])
        yield from _await(evaluator,task,private)
        return False
    if computer is not None:
        from chaos.computer_values import context_exit
        before = len(evaluator.suspension.tasks)
        task = context_exit(evaluator, computer)
        private = _operation(evaluator, task, evaluator.suspension.tasks[before:])
        yield from _await(evaluator, task, private)
        return False
    result = yield from _invoke(evaluator, function, [error], asynchronous)
    if result is not None and type(result) is not bool:
        raise LanguageError('上下文退出协议必须返回 Bool 或 Null', code='argument_error')
    return result is True


def acquire(evaluator, manager, asynchronous=False):
    from chaos.dynamic_contexts import ContextScope,ContextToken,acquire as binding_acquire
    if type(manager) in (ContextScope,ContextToken):
        if asynchronous:raise LanguageError('绑定 scope 使用同步 with',code='argument_error')
        value,lease=binding_acquire(evaluator,manager)
        return value,ExitEntry('binding',lease)
    from chaos.conditions import SCOPES as RECOVERY_SCOPES,enter as recovery_enter
    if type(manager) in RECOVERY_SCOPES:
        if asynchronous:raise LanguageError('恢复 scope 使用同步 with',code='argument_error')
        return recovery_enter(evaluator,manager),ExitEntry('recovery',manager)
    from chaos.decimal_values import DecimalContext,acquire as decimal_acquire
    if type(manager) is DecimalContext:
        if asynchronous:raise LanguageError('DecimalContext 使用同步 with',code='argument_error')
        value,lease=decimal_acquire(evaluator,manager)
        return value,ExitEntry('decimal',lease)
    from chaos.task_groups import TaskGroup
    from chaos.cancellation import CancelShield
    if type(manager) is CancelShield:
        raise LanguageError('CancelShield 必须由词法 with 管理', code='cancel_shield_error')
    from chaos.resource_stacks import STACKS, context_enter as stack_enter
    from chaos.computer_values import Computer, context_enter
    if type(manager) is TaskGroup:
        raise LanguageError('TaskGroup 必须由词法 async with 管理', code='task_group_error')
    if type(manager) in STACKS:
        return stack_enter(evaluator, manager, asynchronous), ExitEntry('stack', manager)
    if type(manager) in MANAGERS:
        context_owned(manager, evaluator)
        if asynchronous != (type(manager) is AsyncContextManager):
            raise LanguageError('上下文管理器的同步／异步类型不匹配', code='argument_error')
        if manager.state != 'created':
            raise LanguageError('生成器上下文管理器只能进入一次', code='context_error')
        manager.state = 'entering'
        try:
            item = yield from _manager_operation(evaluator, manager)
            if item['done']:
                raise LanguageError('上下文生成器在 yield 前已结束', code='context_error')
            manager.state = 'active'
            return item['value'], ExitEntry('generator', manager)
        except BaseException:
            abandon(evaluator, manager)
            raise
    from chaos.robot_values import Robot,context_enter as robot_enter
    if type(manager) is Robot:
        if not asynchronous:raise LanguageError("Robot requires async with",code="argument_error")
        return robot_enter(evaluator,manager),ExitEntry("robot",manager,asynchronous=True)
    from chaos.device_values import Device, context_enter as device_enter
    if type(manager) is Device:
        if not asynchronous:raise LanguageError('Device requires async with',code='argument_error')
        return device_enter(evaluator,manager),ExitEntry('device',manager,asynchronous=True)
    if type(manager) is Computer:
        if not asynchronous:
            raise LanguageError('Computer 需要 async with', code='argument_error')
        return context_enter(evaluator, manager), ExitEntry('computer', manager, asynchronous=True)
    if not isinstance(manager, ChaosInstance):
        raise LanguageError('with 需要实现上下文协议的实例', code='argument_error')
    owned(manager, evaluator)
    names = ('__aenter__', '__aexit__') if asynchronous else ('__enter__', '__exit__')
    enter_function = yield from evaluator._s_access_result(special_method(manager, names[0], evaluator))
    exit_function = yield from evaluator._s_access_result(special_method(manager, names[1], evaluator))
    require_callable(enter_function, evaluator)
    require_callable(exit_function, evaluator)
    value = yield from _invoke(evaluator, enter_function, [], asynchronous)
    return value, ExitEntry('function', function=exit_function, asynchronous=asynchronous)


def exit_entry(evaluator, entry, error=None):
    from chaos.task_groups import GroupAbort
    from chaos.errors import catchable, observe
    if entry.released: return False
    previous_context = evaluator.error_context
    if catchable(error) and entry.kind != 'stack': evaluator.error_context = error
    try:
        if entry.kind in ('decimal','recovery','binding'):return False
        if entry.kind == 'generator':
            return (yield from _manager_exit(evaluator, entry.target, error))
        if entry.kind == 'stack':
            from chaos.resource_stacks import unwind
            return (yield from unwind(evaluator, entry.target, error, managed=True))
        record_error = error.error if isinstance(error, GroupAbort) else error
        from chaos.error_values import record_for
        record = evaluator.copy_value(record_for(evaluator, record_error)) if isinstance(record_error, LanguageError) else None
        return (yield from _exit(evaluator, entry.function, record, entry.asynchronous,
                                entry.target if entry.kind in ('computer','device','robot') else None))
    except LanguageError as raised:
        observe(evaluator, raised)
        raise
    finally:
        evaluator.error_context = previous_context
        release(evaluator, entry)


CONTEXTS_ALGORITHMS={name:globals()[name]for name in ('_manager_operation', '_manager_exit', '_await', '_invoke', '_exit', 'acquire', 'exit_entry')}

def resolve(evaluator,request):
    context=owned(request.context,evaluator,Context)
    if context.active:error('Context 已在运行','context_busy')
    state=active_state(evaluator.suspension);previous=state.current
    if context is previous:error('不能重入当前 Context','context_busy')
    context.active=True;state.current=context
    try:
        return (yield from evaluator._s_invoke(request.function,request.args,request.kwargs,force=True,_context_callback=True,all_results=True))
    finally:
        state.current=previous;context.active=False


DYNAMIC_CONTEXTS_ALGORITHMS={name:globals()[name]for name in ('resolve',)}

def unwind(evaluator, stack, error=None, *, finalizing=False, managed=False):
    from chaos.execution_scope import scope
    with scope(evaluator, stack.capabilities, stack.team_scope):
        return (yield from _unwind(evaluator, stack, error, finalizing=finalizing, managed=managed))


def _unwind(evaluator, stack, error=None, *, finalizing=False, managed=False):
    from chaos.contexts import exit_entry, _await, _operation
    from chaos.task_groups import GroupAbort
    from chaos.control_prompts import NonlocalTransfer
    owned(stack, evaluator, finalizing=finalizing)
    if not finalizing:decimal_access(evaluator,stack,closing=True)
    if stack.busy and managed and stack.operation_task is not None:
        from chaos.suspension import Drain
        operation = stack.operation_task
        current = evaluator.suspension.current.task if evaluator.suspension.current else None
        if operation is not current:
            if stack.operation_kind in ('close', 'aclose'):
                # An already-running explicit close owns these exits. Wait for
                # it, forwarding an ordinary scope failure through its unwind.
                if error is not None: evaluator.suspension.interrupt(operation, error)
                try: yield from _await(evaluator, operation, True, cleanup_operation=True)
                except BaseException as raised:
                    if raised is not error: raise
            else:
                source = object()
                evaluator.suspension._cancel(operation, source)
                try: yield Drain(operation)
                except BaseException as raised:
                    local = (evaluator.suspension._local_cancel(raised, operation) and
                             operation.cancel_sources == {source})
                    if not local: raise
    if stack.busy: raise LanguageError('ExitStack 操作不能重入或并发', code='context_busy')
    if stack.state == 'closed': return False
    stack.busy, stack.state = True, 'closing'
    pending = error
    group_error = error if isinstance(error, GroupAbort) else None
    from chaos.errors import catchable, observe
    previous_context = evaluator.error_context
    try:
        while stack.entries:
            evaluator.error_context = pending if catchable(pending) else previous_context
            try:
                evaluator.tick()
                entry = stack.entries.pop()
                evaluator.suspension.stack_entries -= 1
                if isinstance(entry, CallbackEntry):
                    before = len(evaluator.suspension.tasks)
                    result = yield from evaluator._s_invoke(entry.function, entry.args, entry.kwargs)
                    if entry.asynchronous:
                        current=evaluator.suspension.current.task if evaluator.suspension.current else None
                        if (type(result) is Task and result in evaluator.suspension.tasks[before:]
                                and result.state=='created' and result.kind=='task'
                                and result.parent_task_id==(current.id if current else None)):
                            from chaos.conditions import active_state as recovery_state
                            result.runner.condition_state=recovery_state(evaluator.suspension)
                            from chaos.control_prompts import active_state as control_state
                            result.runner.control_state=control_state(evaluator.suspension)
                            from chaos.dynamic_contexts import active_state as context_state
                            result.runner.context_state=context_state(evaluator.suspension)
                            result.runner.error_context=evaluator.error_context
                            result.recovery_protocol=True
                        private = _operation(evaluator, result, evaluator.suspension.tasks[before:])
                        result = yield from _await(evaluator, result, private)
                    if isinstance(result, (Task, Generator)):
                        raise LanguageError('关闭回调返回了未完成的 Task/Generator/AsyncGenerator', code='context_error')
                elif (yield from exit_entry(evaluator, entry, pending)) and isinstance(pending, LanguageError):
                    pending = None
            except NonlocalTransfer as raised:
                # A member failure selected by a structured group is already
                # authoritative. Cleanup cannot turn it into successful recovery.
                if group_error is None:pending=raised
            except GroupAbort as raised:
                # A sibling may fail while an async cleanup is suspended. Its
                # private operation is drained by contexts._await; outer exits
                # still see the structured failure and must run in LIFO order.
                group_error = pending = raised
            except LanguageError as raised:
                # ResourceLimit is a LanguageError subclass and must never be
                # treated as a cleanup failure that a later exit can suppress.
                if isinstance(raised, ResourceLimit): raise
                observe(evaluator, raised)
                pending = raised
            finally:
                evaluator.error_context = previous_context
        if pending is None and group_error is not None: pending = group_error
        if pending is not None and pending is not error: raise pending
        if group_error is not None: return False
        return error is not None and pending is None
    finally:
        abandon(evaluator, stack)


def _execute(evaluator, request):
    from chaos.execution_scope import scope
    with scope(evaluator, request.stack.capabilities, request.stack.team_scope):
        return (yield from _execute_scoped(evaluator, request))


def _execute_scoped(evaluator, request):
    from chaos.contexts import acquire, ExitEntry
    from chaos.computer_values import Computer
    from chaos.robot_values import Robot
    from chaos.contexts import MANAGERS
    from chaos.task_groups import TaskGroup
    stack, operation = request.stack, request.operation
    available(evaluator, stack, closing=operation in ('close','aclose'))
    if operation in ('close','aclose'):
        current = evaluator.suspension.current.task if evaluator.suspension.current else None
        stack.operation_task, stack.operation_kind = current, operation
        try:
            yield from unwind(evaluator, stack)
            return None
        finally:
            if stack.operation_task is current: stack.operation_task, stack.operation_kind = None, None
    if operation == 'pop_all':
        transferred = create(evaluator, type(stack) is AsyncExitStack, stack.max_entries)
        transferred.entries, stack.entries = stack.entries, []
        for entry in transferred.entries:
            if getattr(entry, 'kind', None) == 'stack': entry.target.parent_stack = transferred
        return transferred
    stack.busy = True
    current = evaluator.suspension.current.task if evaluator.suspension.current else None
    # Synchronous protocol hooks can suspend on effects when called inside a
    # user Task. Their active caller owns this operation until the finally below.
    stack.operation_task, stack.operation_kind = current, operation
    reserved = False
    try:
        reserve(evaluator, stack); reserved = True
        if operation in ('enter_context','enter_async_context'):
            if type(request.value) in STACKS:
                owned(request.value, evaluator)
                _cycle(evaluator, request.value, stack)
            value, entry = yield from acquire(evaluator, request.value, operation == 'enter_async_context')
            if stack.state != 'open':
                from chaos.contexts import release
                release(evaluator, entry)
                raise LanguageError('ExitStack 在资源获取期间已关闭', code='context_error')
            stack.entries.append(entry); reserved = False
            if entry.kind == 'stack': entry.target.parent_stack = stack
            return value
        if operation in ('push','push_async_exit'):
            function = request.value
            if type(function) in MANAGERS + STACKS + (Computer, TaskGroup, Robot):
                raise LanguageError('原生资源必须通过 enter_context/enter_async_context 获取', code='context_error')
            if isinstance(function, ChaosInstance):
                name = '__aexit__' if operation == 'push_async_exit' else '__exit__'
                from chaos.objects import declaration
                if declaration(function.klass, name) is not None:
                    function = yield from evaluator._s_access_result(special_method(function, name, evaluator))
            require_callable(function, evaluator)
            entry = ExitEntry('function', function=function, asynchronous=operation == 'push_async_exit')
        else:
            require_callable(request.value, evaluator)
            entry = CallbackEntry(request.value, evaluator.copy_value(list(request.args)),
                                  evaluator.copy_value(request.kwargs), operation == 'push_async_callback')
        stack.entries.append(entry); reserved = False
        return request.value
    finally:
        if reserved: evaluator.suspension.stack_entries -= 1
        stack.busy = False
        if stack.operation_task is current: stack.operation_task, stack.operation_kind = None, None


def resolve(evaluator, request):
    owned(request.stack, evaluator)
    if request.operation in ('enter_async_context','aclose'):
        from chaos.decimal_values import active_state
        decimal_access(evaluator,request.stack,closing=request.operation=='aclose')
        task = evaluator.suspension.task(_execute(evaluator, request), capture_roots=(request.stack,request.value) + tuple(request.args) + tuple((request.kwargs or {}).values()))
        task.runner.decimal_state=active_state(evaluator.suspension)
        from chaos.conditions import active_state as recovery_state
        task.runner.condition_state=recovery_state(evaluator.suspension)
        from chaos.control_prompts import active_state as control_state
        task.runner.control_state=control_state(evaluator.suspension)
        from chaos.dynamic_contexts import active_state as context_state
        task.runner.context_state=context_state(evaluator.suspension)
        task.runner.error_context=evaluator.error_context
        task.stack_operation = request.stack
        task.stack_operation_kind = request.operation
        current = evaluator.suspension.current.task if evaluator.suspension.current else None
        task.context_structured = bool(current and (current.scope_group is not None or current.group is not None or
                                                   getattr(current, 'context_structured', False)))
        return task
    return (yield from _execute(evaluator, request))


def finalize_scope(evaluator, scope, hard=False):
    """Recorder barrier, after lexical generator owners have unwound."""
    changed = False
    while True:
        stack = next((value for value in reversed(evaluator.suspension.resource_stacks)
                      if value.recording_scope == scope and value.state != 'closed' and not value.context_active), None)
        if stack is None:
            for value in evaluator.suspension.resource_stacks:
                if value.recording_scope == scope and value.state != 'closed':
                    changed = True
                    abandon(evaluator, value)
            return changed
        changed = True
        if hard: abandon(evaluator, stack)
        else: yield from unwind(evaluator, stack, managed=True)


RESOURCE_STACKS_ALGORITHMS={name:globals()[name]for name in ('unwind', '_unwind', '_execute', '_execute_scoped', 'resolve', 'finalize_scope')}

ALGORITHMS=((_contexts,CONTEXTS_ALGORITHMS),(_dynamic_contexts,DYNAMIC_CONTEXTS_ALGORITHMS),(_resource_stacks,RESOURCE_STACKS_ALGORITHMS))

@contextmanager
def reference(seen=None):
    with ExitStack() as patches:
        for module,algorithms in ALGORITHMS:
            namespace=dict(vars(module));bound={}
            for name,original in algorithms.items():
                fn=types.FunctionType(original.__code__,namespace,name,original.__defaults__,original.__closure__)
                fn.__kwdefaults__=original.__kwdefaults__
                def observed(*args,_fn=fn,_label=module.__name__+'.'+name,**kwargs):
                    if seen is not None:seen.append(_label)
                    return _fn(*args,**kwargs)
                bound[name]=observed
            namespace.update(bound)
            for name,fn in bound.items():patches.enter_context(patch.object(module,name,fn))
        yield
