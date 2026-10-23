"""Native acquisition and exit protocols shared by cursor and dynamic scopes.

User cleanup obeys exactly the language finally boundary: hard execution control
and host interrupts propagate without invoking arbitrary language hooks. Native
host resources retain their independent run-level teardown.
"""
from dataclasses import dataclass

from .language import LanguageError
from .objects import ChaosInstance, owned, special_method, require_callable
from .suspension import Await, Task, Generator, AsyncGenerator, TERMINAL


class ContextManager:
    """An opaque, one-shot owner of one not-yet-started language generator."""
    def __init__(self, scheduler, generator):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.id = 'context-' + str(scheduler.next_id())
        self.generator, self.recording_scope = generator, generator.recording_scope
        self.state = 'created'


class AsyncContextManager(ContextManager):
    pass


MANAGERS = (ContextManager, AsyncContextManager)


def context_owned(value, evaluator):
    scheduler = evaluator.suspension
    if type(value) not in MANAGERS or value.owner is not scheduler or value.epoch != scheduler.epoch or not scheduler.active:
        raise LanguageError('上下文管理器不能跨执行上下文或提交周期使用', code='ownership_error')
    scheduler.validate(value.generator, AsyncGenerator if type(value) is AsyncContextManager else Generator)
    return value


def describe(value):
    return {'$handle': type(value).__name__, 'id': value.id, 'state': value.state}


def generator_context(evaluator, generator, asynchronous=False):
    if type(asynchronous) is not bool:
        raise LanguageError('asynchronous 需要 Bool', code='argument_error')
    scheduler = evaluator.suspension
    scheduler.validate(generator, AsyncGenerator if asynchronous else Generator)
    if generator.state != 'created' or generator.busy or generator.context_manager is not None:
        raise LanguageError('上下文需要尚未启动且未被占用的生成器', code='context_error')
    manager = (AsyncContextManager if asynchronous else ContextManager)(scheduler, generator)
    # Reservation lasts for the generator's lifetime; public aliases cannot
    # advance, close, or rewrap it before or during its resource scope.
    generator.context_manager = manager
    return manager


def abandon(evaluator, manager):
    """Bookkeeping only: never run language cleanup on a hard-control path."""
    generator = manager.generator
    if generator.state not in TERMINAL:
        evaluator.suspension._abort_stream(generator)
        generator.state = 'closed'
    generator.busy = False
    manager.state = 'closed'
    evaluator.suspension._retire_execution(generator)


def _manager_operation(evaluator, manager, error=None, closing=False):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'manager_operation',manager,error,closing)


def _manager_exit(evaluator, manager, error=None):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'manager_exit',manager,error)


def _await(evaluator, task, protocol_owned=False, *, cleanup_operation=False, all_results=False):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'await',task,protocol_owned,cleanup_operation,all_results)


def _operation(evaluator, task, created):
    """Identify this invocation's private cold operation without claiming aliases."""
    scheduler = evaluator.suspension
    scheduler.validate(task, Task)
    current = scheduler.current.task if scheduler.current else None
    if current is None: return False
    if current.scope_group is None and current.group is None and not current.cancel_shields and not getattr(current, 'context_structured', False):
        return (getattr(current, 'stack_operation', None) is not None and task in created and
                task.parent_task_id == current.id and task.state == 'created')
    fresh = {item for item in created if item.parent_task_id == current.id and item.state == 'created'}
    if task not in fresh: return False
    pending, seen = [task], set()
    while pending:
        member = pending.pop()
        if member in seen: continue
        seen.add(member)
        if member not in fresh or member.group is not None or member.scope_group is not None:
            raise LanguageError('TaskGroup 中新建的上下文 gather 不能包含借用 Task', code='task_group_error')
        scheduler.validate(member, Task)
        scheduler._recording_access(member)
        if member.kind == 'gather': pending.extend(member.members)
    return True


def _invoke(evaluator, function, args, asynchronous):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'invoke',function,args,asynchronous)


def _exit(evaluator, function, error, asynchronous, computer):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'exit_call',function,error,asynchronous,computer)


@dataclass
class ExitEntry:
    """Captured acquisition identity shared by lexical and dynamic scopes."""
    kind: str
    target: object = None
    function: object = None
    asynchronous: bool = False
    released: bool = False


def acquire(evaluator, manager, asynchronous=False):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'acquire',manager,asynchronous)


def release(evaluator, entry):
    """Bookkeeping only, including on cancellation or host interruption."""
    if entry.released: return
    entry.released = True
    if entry.kind=='recovery':
        from .conditions import release as release_recovery
        release_recovery(entry.target)
    elif entry.kind in ('decimal','binding'):entry.target.release()
    elif entry.kind == 'generator': abandon(evaluator, entry.target)
    elif entry.kind in ('device','robot'):entry.target.lease.context_active=False
    elif entry.kind == 'computer': entry.target.context_active = False
    elif entry.kind == 'stack':
        from .resource_stacks import abandon as abandon_stack
        abandon_stack(evaluator, entry.target)
        entry.target.context_active = False
        entry.target.parent_stack = None


def exit_entry(evaluator, entry, error=None):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'exit_entry',entry,error)


def hard_exit_entry(evaluator,entry):
    """Retire native robots only; never invoke user exits on hard control."""
    pending=[entry];seen=set()
    while pending:
        current=pending.pop()
        if id(current) in seen:continue
        seen.add(id(current))
        if current.kind=='robot':
            from .robot_values import hard_context_exit
            hard_context_exit(evaluator,current.target)
        elif current.kind=='stack':
            from .contexts import ExitEntry
            pending.extend(item for item in current.target.entries if type(item) is ExitEntry)
