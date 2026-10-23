"""Native structured Task scopes. All mutable state belongs to the scheduler thread."""
from dataclasses import dataclass

from .language import (Env, LanguageError, Native, ResourceLimit, ReturnSignal,
                       BreakSignal, ContinueSignal)
from .suspension import Task, TaskCancelled, GeneratorClose, Join, TERMINAL, error_priority


class GroupAbort(BaseException):
    """Private one-shot lexical unwind; ordinary language catch cannot consume it."""
    def __init__(self, group, error):
        self.group, self.error = group, error


@dataclass
class ScopeOutcome:
    value: object = None
    signal: object = None
    error: object = None


@dataclass
class GroupRun:
    group: object
    iterator: object


class TaskGroup:
    def __init__(self, scheduler, errors='first'):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.id = 'group-' + str(scheduler.next_id())
        self.state, self.location = 'new', dict(scheduler.evaluator.current_span)
        current = scheduler.current.task if scheduler.current else None
        self.recording_scope = current.recording_scope if current else None
        self.owner_task_id, self.parent_group_id = None, None
        self.body, self.runner = None, None
        self.members, self.pending = [], set()
        self.error, self.outcome = None, None
        self.abort_started = False
        self.errors_policy, self.failures, self.failure_ids = errors, [], set()

    def _internal_cancel(self, task, error):
        if not self.owner._local_cancel(error, task) or not task.cancel_sources: return False
        def internal(source):
            return source is self or (isinstance(source, Join) and source.runner is not None and
                                      source.runner.task is not None and source.runner.task.group is self)
        return all(internal(source) for source in task.cancel_sources)

    def fail(self, error):
        if error is None or self.state == 'closed': return
        # Awaiting a cancelled peer from body cleanup forwards the peer's exact
        # signal. Attribute it to that member, never to the receiving body Task.
        if (self.error is not None and isinstance(error, TaskCancelled) and
                any(self._internal_cancel(task, error) for task in self.members)):
            return
        self.remember_failure(error)
        changed = error_priority(error) > error_priority(self.error)
        if changed: self.error = error
        if error_priority(error) == 3 and self.owner.hard_failure is None:
            self.owner.hard_failure = error
        self.state = 'aborting'
        if not self.abort_started:
            self.abort_started = True
            for task in list(self.pending): self.owner._cancel(task, self)
        if changed and self.body is not None and self.body.state not in TERMINAL:
            signal = error if isinstance(error, GroupAbort) or error_priority(error) >= 2 else GroupAbort(self, error)
            self.owner.interrupt(self.body, signal)

    def remember_failure(self, error):
        if self.errors_policy == 'group' and isinstance(error, LanguageError) and not isinstance(error, ResourceLimit) and id(error) not in self.failure_ids:
            self.failure_ids.add(id(error)); self.failures.append(error)

    def final_error(self):
        if self.errors_policy == 'group' and self.failures and isinstance(self.error, LanguageError) and error_priority(self.error) == 1:
            from .errors import GroupedError
            return GroupedError('TaskGroup 中的任务失败', self.failures)
        return self.error

    def finished(self, task):
        if task is self.body:
            outcome = task.runner.result
            self.outcome = outcome if isinstance(outcome, ScopeOutcome) else ScopeOutcome(error=task.runner.error)
            if self.state == 'active': self.state = 'joining'
            if self.outcome.error is not None: self.fail(self.outcome.error)
            if isinstance(self.outcome.signal, GroupAbort):
                # The matching signal has already selected its original failure.
                if self.outcome.signal.group is not self: self.fail(self.outcome.signal)
        else:
            self.pending.discard(task)
            if not self._internal_cancel(task, task.runner.error): self.fail(task.runner.error)

    def spawn(self, task):
        evaluator = self.owner.evaluator
        owned(self, evaluator)
        current = self.owner.current.task if self.owner.current else None
        if self.state not in ('active', 'joining') or (current is not self.body and (current is None or current.group is not self)):
            raise LanguageError('TaskGroup.spawn 仅允许活动作用域的 body 或成员调用', code='task_group_error')
        # Validate the entire cold gather closure before observing, claiming, or
        # starting anything. An owned collector can then cancel only owned peers.
        pending, closure, seen = [task], [], set()
        while pending:
            member = self.owner.validate(pending.pop(), Task)
            if member in seen: continue
            seen.add(member)
            if member.recording_scope != self.recording_scope:
                raise LanguageError('TaskGroup 不能接纳其他录制 scope 的 Task', code='mode_error')
            if member.group is not self:
                if member.group is not None or member.scope_group is not None or member.state != 'created':
                    raise LanguageError('TaskGroup 只接纳尚未启动且未归属其他组的 Task', code='task_group_error')
                closure.append(member)
            if member.kind == 'gather': pending.extend(reversed(member.members))
        for member in closure:
            member.group = self
            member.observed = True
            self.members.append(member); self.pending.add(member)
        self.owner._start(task)
        if closure: self.owner.event('group.member', self)
        return task


def create(evaluator, *, errors='first'):
    if type(errors) is not str or errors not in ('first', 'group'):
        raise LanguageError('task_group errors 需要 first 或 group', code='argument_error')
    scheduler = evaluator.suspension
    if not scheduler.active: raise LanguageError('TaskGroup 需要活动提交周期', code='task_group_error')
    if len(scheduler.groups) >= 10000: raise ResourceLimit('TaskGroup 数量超过 10000')
    group = TaskGroup(scheduler, errors)
    scheduler.groups.append(group)
    scheduler.event('group.created', group)
    return group


def owned(value, evaluator):
    scheduler = evaluator.suspension
    if type(value) is not TaskGroup: raise LanguageError('需要 TaskGroup', code='argument_error')
    if value.owner is not scheduler or value.epoch != scheduler.epoch or not scheduler.active:
        raise LanguageError('TaskGroup 不能跨执行上下文或提交周期使用', code='ownership_error')
    current = scheduler.current.task if scheduler.current else None
    scope = current.recording_scope if current else None
    if value.recording_scope != scope:
        raise LanguageError('TaskGroup 不能跨录制 scope 使用', code='mode_error')
    return value


def describe(group):
    return {'$handle':'TaskGroup', 'id':group.id, 'state':group.state}


def info(evaluator, group):
    return snapshot_info(owned(group, evaluator))


def snapshot_info(group):
    return {'id':group.id, 'state':group.state, 'ownerTaskId':group.owner_task_id,
            'bodyTaskId':group.body.id if group.body else None, 'parentGroupId':group.parent_group_id,
            'memberTaskIds':[task.id for task in group.members[:100]],
            'memberCount':len(group.members), 'membersTruncated':len(group.members)>100,
            'location':dict(group.location)}


def get_member(value, name, evaluator):
    owned(value, evaluator)
    if name != 'spawn': raise LanguageError('TaskGroup 成员不存在 '+name, code='attribute_error')
    return Native('TaskGroup.spawn', value.spawn, evaluator, spec_id='TaskGroup.spawn', captures=(value,))

