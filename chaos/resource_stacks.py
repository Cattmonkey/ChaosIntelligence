"""Dynamic resource composition on the evaluator's existing suspension machinery."""
from dataclasses import dataclass

from .language import LanguageError, ResourceLimit, Native
from .objects import ChaosInstance, require_callable, special_method
from .suspension import Task, Generator


class ExitStack:
    def __init__(self, scheduler, max_entries):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.capabilities = scheduler.current.capabilities if scheduler.current else None
        self.team_scope = scheduler.current.team_scope if scheduler.current else None
        self.id = 'stack-' + str(scheduler.next_id())
        self.state, self.busy = 'open', False
        self.context_active, self.context_used = False, False
        self.parent_stack = None
        self.operation_task, self.operation_kind = None, None
        self.entries, self.max_entries = [], max_entries
        current = scheduler.current.task if scheduler.current else None
        self.recording_scope = current.recording_scope if current else None


class AsyncExitStack(ExitStack):
    pass


STACKS = (ExitStack, AsyncExitStack)


@dataclass
class StackRequest:
    stack: object
    operation: str
    value: object = None
    args: tuple = ()
    kwargs: object = None


@dataclass
class CallbackEntry:
    function: object
    args: object
    kwargs: object
    asynchronous: bool = False


def owned(stack, evaluator, *, finalizing=False):
    from .agent_tools import access
    access(stack, evaluator)
    scheduler = evaluator.suspension
    if type(stack) not in STACKS or stack.owner is not scheduler or stack.epoch != scheduler.epoch or not scheduler.active:
        raise LanguageError('ExitStack 不能跨执行上下文或提交周期使用', code='ownership_error')
    current = scheduler.current.task if scheduler.current else None
    scope = current.recording_scope if current else None
    if stack.recording_scope != scope and not finalizing:
        raise LanguageError('ExitStack 不能跨录制 scope 使用', code='mode_error')
    return stack


def describe(stack):
    return {'$handle':type(stack).__name__, 'id':stack.id, 'state':stack.state,
            'entryCount':len(stack.entries), 'maxEntries':stack.max_entries}


def create(evaluator, asynchronous=False, max_entries=1024):
    if type(asynchronous) is not bool:
        raise LanguageError('asynchronous 需要 Bool', code='argument_error')
    if type(max_entries) is not int or not 0 <= max_entries <= 10000:
        raise LanguageError('max_entries 需要 0..10000 的 Int', code='argument_error')
    scheduler = evaluator.suspension
    if not scheduler.active: raise LanguageError('ExitStack 需要活动提交周期', code='ownership_error')
    if len(scheduler.resource_stacks) >= 10000: raise ResourceLimit('ExitStack 数量超过 10000')
    stack = (AsyncExitStack if asynchronous else ExitStack)(scheduler, max_entries)
    scheduler.resource_stacks.append(stack)
    return stack


def decimal_access(evaluator,stack,*,closing=False):
    """Preflight captured decimal leases without popping or running callbacks."""
    from .conditions import active_state as recovery_state,preflight_release
    recovery_holder=recovery_state(evaluator.suspension);recoveries=[]
    from .decimal_values import active_state
    state=active_state(evaluator.suspension)
    from .dynamic_contexts import current as binding_current
    binding_context=binding_current(evaluator);bindings=[]
    pending=list(reversed(stack.entries));leases=[]
    while pending:
        evaluator.tick();entry=pending.pop(0)
        kind=getattr(entry,'kind',None)
        if kind=='recovery' and not entry.released:
            target=entry.target
            if target.holder is not recovery_holder:raise LanguageError('恢复 ExitStack 租约属于另一 logical execution',code='ownership_error')
            if target.state=='active':recoveries.append(target)
        elif kind=='binding' and not entry.released:
            lease=entry.target
            if lease.context is not binding_context:raise LanguageError('Context租约属于另一Context',code='ownership_error')
            if not lease.released:bindings.append(lease)
        elif kind=='decimal' and not entry.released:
            lease=entry.target
            if lease.state is not state:
                raise LanguageError('Decimal ExitStack 租约属于另一 logical execution',code='ownership_error')
            if not lease.released:leases.append(lease)
        elif kind=='stack':pending[0:0]=list(reversed(entry.target.entries))
    if closing and bindings and list(reversed(binding_context.leases[-len(bindings):]))!=bindings:
        raise LanguageError('Context租约必须按LIFO关闭',code='context_error')
    if closing and recoveries and list(reversed(recovery_holder.scopes[-len(recoveries):]))!=recoveries:
        raise LanguageError('恢复租约必须按LIFO关闭',code='context_error')
    if closing and leases and list(reversed(state.leases[-len(leases):]))!=leases:
        raise LanguageError('Decimal 租约必须按 LIFO 关闭',code='context_error')


def available(evaluator, stack, *, closing=False):
    owned(stack, evaluator)
    decimal_access(evaluator,stack,closing=closing)
    if stack.busy: raise LanguageError('ExitStack 操作不能重入或并发', code='context_busy')
    if stack.state != 'open' and not (closing and stack.state == 'closed'):
        raise LanguageError('ExitStack 已关闭', code='context_error')


def reserve(evaluator, stack):
    # Includes in-flight acquisitions: descriptors and __enter__ cannot run
    # before both the local and epoch-wide resource budgets have a slot.
    if len(stack.entries) >= stack.max_entries:
        raise ResourceLimit('ExitStack 条目超过 max_entries')
    if evaluator.suspension.stack_entries >= 10000:
        raise ResourceLimit('ExitStack 总条目超过 10000')
    evaluator.suspension.stack_entries += 1


def context_enter(evaluator, stack, asynchronous):
    available(evaluator, stack)
    if asynchronous != (type(stack) is AsyncExitStack):
        raise LanguageError('ExitStack 的同步／异步类型不匹配', code='argument_error')
    if stack.context_used or stack.context_active:
        raise LanguageError('ExitStack 上下文只能进入一次', code='context_error')
    stack.context_used = stack.context_active = True
    return stack


def _cycle(evaluator, target, stack):
    ancestor, base_depth = stack, 1
    while ancestor.parent_stack is not None:
        evaluator.tick()
        ancestor, base_depth = ancestor.parent_stack, base_depth + 1
        if base_depth >= 60: raise ResourceLimit('ExitStack 嵌套超过 60 层')
    pending, seen = [(target, base_depth)], set()
    while pending:
        current, depth = pending.pop()
        evaluator.tick()
        if current is stack: raise LanguageError('ExitStack 不能形成资源所有权循环', code='context_error')
        if id(current) in seen: continue
        seen.add(id(current))
        if depth >= 60: raise ResourceLimit('ExitStack 嵌套超过 60 层')
        pending.extend((entry.target, depth+1) for entry in current.entries
                       if getattr(entry, 'kind', None) == 'stack')


def abandon(evaluator, stack):
    """Drop captured resources without language hooks on a hard-control path."""
    from .contexts import release
    if stack.state == 'closed': return
    active = stack.operation_task
    current = evaluator.suspension.current.task if evaluator.suspension.current else None
    if active is not None and active is not current:
        evaluator.suspension._cancel(active, stack)
    entries, stack.entries = stack.entries, []
    evaluator.suspension.stack_entries -= len(entries)
    stack.state, stack.busy = 'closed', False
    for entry in reversed(entries):
        if not isinstance(entry, CallbackEntry): release(evaluator, entry)


def unwind(evaluator, stack, error=None, *, finalizing=False, managed=False):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'stack_unwind',stack,error,finalizing,managed)


def _unwind(evaluator, stack, error=None, *, finalizing=False, managed=False):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'stack_unwind',stack,error,finalizing,managed,False)


def _execute(evaluator, request):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'stack_execute',request)


def _execute_scoped(evaluator, request):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'stack_execute',request,False)


def resolve(evaluator, request):
    from .execution_context_protocols import context_gateway
    return context_gateway(evaluator,request)


def get_member(stack, name, evaluator):
    owned(stack, evaluator)
    def enter_context(manager): return StackRequest(stack, 'enter_context', manager)
    def enter_async_context(manager): return StackRequest(stack, 'enter_async_context', manager)
    def callback(function, /, *args, **kwargs): return StackRequest(stack, 'callback', function, args, kwargs)
    def push_async_callback(function, /, *args, **kwargs): return StackRequest(stack, 'push_async_callback', function, args, kwargs)
    def push(exit_function): return StackRequest(stack, 'push', exit_function)
    def push_async_exit(exit_function): return StackRequest(stack, 'push_async_exit', exit_function)
    def close(): return StackRequest(stack, 'close')
    def aclose(): return StackRequest(stack, 'aclose')
    def pop_all(): return StackRequest(stack, 'pop_all')
    methods = {'enter_context':enter_context, 'callback':callback, 'push':push, 'pop_all':pop_all}
    if type(stack) is AsyncExitStack:
        methods.update(enter_async_context=enter_async_context, push_async_callback=push_async_callback,
                       push_async_exit=push_async_exit, aclose=aclose)
    else: methods['close'] = close
    if name not in methods: raise LanguageError('ExitStack 成员不存在 '+name, code='attribute_error')
    native_name = type(stack).__name__ + '.' + name
    return Native(native_name, methods[name], evaluator, spec_id=native_name, captures=(stack,))


def finalize_scope(evaluator, scope, hard=False):
    from .execution_context_protocols import context_protocol
    return context_protocol(evaluator,'finalize_scope',scope,hard)
