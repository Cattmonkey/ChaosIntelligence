"""Single-thread AST scheduler with bounded, isolated host-effect workers.

All iterator/Env mutations happen in the calling interpreter thread. Workers only
receive copied effect payloads and a task cancellation event. Finishing an epoch
waits for every worker: an uncooperative host callback can delay shutdown, but
cannot silently outlive a completed run.
"""

from .exception_classes import raised as raised_value
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
import threading

from .language import LanguageError, ResourceLimit, ExecutionControl, Raised


class TaskCancelled(ExecutionControl):
    def __init__(self, message, *, _cancel_event=None):
        super().__init__(message)
        self._cancel_event = _cancel_event


def hard_error(error):
    """Control failures, including host interrupts, outrank ordinary failures."""
    from .task_groups import GroupAbort
    from .control_prompts import NonlocalTransfer
    if isinstance(error, (GroupAbort,NonlocalTransfer)): return False
    return (isinstance(error, (ResourceLimit, ExecutionControl)) or
            error is not None and not isinstance(error, Exception) and
            not isinstance(error, GeneratorClose))


def error_priority(error):
    if isinstance(error, TaskCancelled) and error._cancel_event is not None: return 2
    if hard_error(error): return 3
    return 1 if error is not None else 0


class GeneratorClose(BaseException):
    pass


@dataclass
class Drain:
    """Private operation barrier; control never bypasses its actual completion."""
    task: object


@dataclass
class Await:
    task: object


class Join:
    """One gather's private, scheduler-owned completion barrier."""
    def __init__(self, tasks):
        self.tasks = tasks
        self.members = list(dict.fromkeys(tasks))
        self.pending = set()
        self.error = None
        self.runner = None
        self.cancelling = False


@dataclass
class Yield:
    value: object


@dataclass
class Effect:
    kind: str
    payload: dict


CHECKPOINT = object()
TERMINAL = ('completed', 'failed', 'cancelled', 'closed')


class Runner:
    def __init__(self, iterator, frames=(), depth=0, span=None, task=None):
        self.iterator, self.frames, self.depth = iterator, list(frames), depth
        self.capture_frames = []
        self.span, self.task = dict(span or {}), task
        self.value, self.error, self.result = None, None, None
        self.state, self.waiting, self.future = 'ready', None, None
        self.await_owned = None
        self.effect_kind = None
        self.effect_event = None
        self.deferred_error = None
        self.started = False
        self.execution_retired = False
        self.stream = None
        self.decimal_state = None
        self.condition_state = None
        self.control_state = None
        self.context_state = None
        self.handled_error = None
        self.error_context = None
        self.capabilities = getattr(task, 'capabilities', None)
        self.team_scope = getattr(task, 'team_scope', None)
        self.bot_guard = getattr(task,'bot_guard',None)


class Task:
    def __init__(self, scheduler, iterator):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.capture_roots = ()
        self.id = 'task-' + str(scheduler.next_id())
        self.location = dict(scheduler.evaluator.current_span)
        self.cancel_event = threading.Event()
        self.capabilities = scheduler.current.capabilities if scheduler.current else None
        self.team_scope = scheduler.current.team_scope if scheduler.current else None
        self.bot_guard = getattr(scheduler.current,'bot_guard',None) if scheduler.current else None
        from .execution_frames import ensure_cursor
        self.runner = Runner(ensure_cursor(scheduler.evaluator,iterator), task=self)
        from .decimal_values import active_state
        self.runner.decimal_state=active_state(scheduler).child()
        from .conditions import DynamicState
        self.runner.condition_state=DynamicState(scheduler)
        from .control_prompts import ControlState
        self.runner.control_state=ControlState(scheduler)
        from .dynamic_contexts import active_state as context_state
        self.runner.context_state=context_state(scheduler).child()
        self.state = 'created'
        self.observed = False
        self.cancel_sources = set()
        self.cancel_shields, self.deferred_interrupt = [], None
        self.joins = []
        self.completion_waits = {}
        self.native_wait = None
        self.guidance_wait = None
        self.guidance_operation = False
        self.selection = None
        self.terminal_order = 0
        self.kind, self.members = 'task', ()
        self.group, self.scope_group, self.interrupt = None, None, None
        self.channel, self.channel_message, self.channel_cursor = None, None, None
        self.channel_locked = False
        parent = scheduler.current.task if scheduler.current else None
        self.parent_task_id = parent.id if parent else None
        self.recording_scope = parent.recording_scope if parent else None


class Generator:
    def __init__(self, scheduler, iterator):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.capture_roots = ()
        self.id = 'generator-' + str(scheduler.next_id())
        self.location = dict(scheduler.evaluator.current_span)
        self.capabilities = scheduler.current.capabilities if scheduler.current else None
        self.team_scope = scheduler.current.team_scope if scheduler.current else None
        self.bot_guard = getattr(scheduler.current,'bot_guard',None) if scheduler.current else None
        from .execution_frames import ensure_cursor
        self.runner = Runner(ensure_cursor(scheduler.evaluator,iterator))
        self.runner.bot_guard = self.bot_guard
        from .decimal_values import active_state
        self.runner.decimal_state=active_state(scheduler).child()
        from .conditions import DynamicState
        self.runner.condition_state=DynamicState(scheduler)
        from .control_prompts import ControlState
        self.runner.control_state=ControlState(scheduler)
        from .dynamic_contexts import active_state as context_state
        self.runner.context_state=context_state(scheduler).child()
        self.runner.capabilities = self.capabilities
        self.runner.team_scope = self.team_scope
        self.state, self.busy, self.result = 'created', False, None
        self.context_manager = None
        parent = scheduler.current.task if scheduler.current else None
        self.recording_scope = parent.recording_scope if parent else None


class AsyncGenerator(Generator):
    """A stream resumed only through cooperative Task operations."""
    pass


class Scheduler:
    def __init__(self, evaluator):
        self.evaluator = evaluator
        self.epoch, self.sequence = 0, 0
        self.current, self.pool, self.stream = None, None, None
        self.ready, self.tasks, self.generators, self.waiters = deque(), [], [], []
        self.active, self.finishing = False, False
        self.groups = []
        self.condition_scopes=[]
        self.cancel_shields = []
        self.resource_stacks, self.stack_entries = [], 0
        self.channels = []
        self.channel_cases = []
        self.select_registrations = 0
        self.channel_dirty, self.channel_dirty_set, self.channel_pumping = deque(), set(), False
        self.channel_slots = self.channel_nodes = self.channel_chars = 0
        self.event_count = 0
        self.compute_operations = 0
        self.completion_sequence, self.hard_failure = 0, None
        self.cancel_queue, self.cancelling_joins = deque(), False
        self.terminal_queue, self.notifying_terminals = deque(), False
        from .iteration import Iteration
        self.iteration = Iteration(self)
        from .task_waits import Waits
        self.task_waits = Waits(self)
        from .guidance_waits import Waits as GuidanceWaits
        self.guidance_waits = GuidanceWaits(self)

    def next_id(self):
        self.sequence += 1
        return self.sequence

    def begin(self):
        if self.active: self.finish()
        self.epoch += 1
        self.active = True
        from .decimal_values import DecimalState
        self.decimal_state=DecimalState(self)
        from .conditions import DynamicState
        self.condition_state=DynamicState(self);self.condition_scopes.clear()
        from .control_prompts import ControlState
        self.control_state=ControlState(self);self.control_scopes=[]
        from .dynamic_contexts import State
        self.context_values=[];self.context_slots=0
        self.context_state=State(self)
        self.event_count = 0
        self.compute_operations = 0
        self.completion_sequence, self.hard_failure = 0, None
        self.cancel_queue.clear(); self.cancelling_joins = False
        self.terminal_queue.clear(); self.notifying_terminals = False
        self.ready.clear(); self.tasks.clear(); self.generators.clear(); self.waiters.clear()
        self.groups.clear(); self.channels.clear(); self.channel_cases.clear()
        self.cancel_shields.clear()
        self.resource_stacks.clear(); self.stack_entries = 0
        self.select_registrations = 0
        self.channel_dirty.clear(); self.channel_dirty_set.clear(); self.channel_pumping = False
        self.channel_slots = self.channel_nodes = self.channel_chars = 0
        self.iteration.begin()
        self.task_waits.begin()
        self.guidance_waits.begin()

    def event(self, name, handle):
        callback = self.evaluator.trace_handler
        if not callback: return
        self.event_count += 1
        if self.event_count > 1000:
            if self.event_count == 1001:
                callback({'event':'lifecycle.truncated', 'limit':1000})
            return
        from .task_groups import TaskGroup, snapshot_info
        from .cancellation import CancelShield, snapshot_info as shield_info
        if type(handle) is CancelShield:
            callback({'event':name, 'shieldId':handle.id, **shield_info(handle)})
            return
        from .channels import Channel, snapshot_info as channel_info
        if type(handle) is Channel:
            callback({'event':name, 'channelId':handle.id, **channel_info(handle)})
            return
        metadata = self._info(handle) if isinstance(handle, Task) else {}
        if type(handle) is TaskGroup:
            callback({'event':name, 'groupId':handle.id, **snapshot_info(handle)})
            return
        callback({'event':name, 'taskId':handle.id if isinstance(handle,Task) else None,
                  'generatorId':handle.id if isinstance(handle,Generator) else None,
                  'location':dict(handle.location), 'state':handle.state,
                  **{key:metadata[key] for key in ('kind','parentTaskId','memberTaskIds','memberCount','membersTruncated','phase','groupId','channelId','timing','selection','cancellationRequested','cancellationDeferred','shieldDepth') if key in metadata}})

    @staticmethod
    def _info(task):
        from .channel_select import selection_info
        from .cancellation import requested, deferred
        return {'id':task.id, 'state':task.state, 'phase':task.runner.state,
                'kind':task.kind, 'parentTaskId':task.parent_task_id,
                'channelId':task.channel.id if task.channel else None,
                'groupId':(task.scope_group or task.group).id if (task.scope_group or task.group) else None,
                'memberTaskIds':[member.id for member in task.members[:100]],
                'memberCount':len(task.members), 'membersTruncated':len(task.members)>100,
                'cancellationRequested':requested(task),
                'cancellationDeferred':deferred(task), 'shieldDepth':len(task.cancel_shields),
                'observed':task.observed, 'location':dict(task.location),
                'timing':task.owner.task_waits.timing(task), 'selection':selection_info(task)}

    def info(self, task):
        self.validate(task, Task)
        self._recording_access(task)
        return self._info(task)

    def snapshot(self, limit=100):
        limit = max(0, min(100, limit))
        tasks = [task for task in self.tasks if task.owner is self and task.epoch == self.epoch]
        from .task_groups import snapshot_info
        from .channels import snapshot_info as channel_info
        from .cancellation import snapshot_info as shield_info
        return {'tasks':[self._info(task) for task in tasks[:limit]],
                'total':len(tasks), 'truncated':len(tasks)>limit,
                'groups':[snapshot_info(group) for group in self.groups[:limit]],
                'groupTotal':len(self.groups), 'groupsTruncated':len(self.groups)>limit,
                'channels':[channel_info(channel) for channel in self.channels[:limit]],
                'channelTotal':len(self.channels), 'channelsTruncated':len(self.channels)>limit,
                'shields':[shield_info(shield) for shield in self.cancel_shields[:limit]],
                'shieldTotal':len(self.cancel_shields), 'shieldsTruncated':len(self.cancel_shields)>limit}

    def check_interrupt(self):
        task = self.current.task if self.current else None
        if task is not None and task.interrupt is not None:
            error, task.interrupt = task.interrupt, None
            raise error

    def interrupt(self, task, error):
        if task.state in TERMINAL: return
        from .task_groups import GroupAbort
        if task.cancel_shields and not task.cancel_event.is_set() and isinstance(error, GroupAbort):
            if task.deferred_interrupt is None:
                task.deferred_interrupt = error
                self.event('shield.deferred', task.cancel_shields[-1])
            return
        if task.interrupt is None or error_priority(error) > error_priority(task.interrupt):
            task.interrupt = error
        from .task_groups import GroupRun
        # A nested native driver shares the logical Task. Interrupt its actual
        # waiting effect/scope, never abandon a live worker or unrelated Await.
        for runner in list(self.waiters):
            if runner.task is not task: continue
            if runner.effect_event is not None: runner.effect_event.set()
            if isinstance(runner.waiting, GroupRun): runner.waiting.group.fail(error)

    def _group_start(self, runner, request):
        from .task_groups import owned
        group = owned(request.group, self.evaluator)
        if group.state != 'new': raise LanguageError('TaskGroup 只能进入一次', code='task_group_error')
        group.runner, group.state = runner, 'active'
        parent = runner.task
        group.owner_task_id = parent.id if parent else None
        parent_group = (parent.scope_group or parent.group) if parent else None
        group.parent_group_id = parent_group.id if parent_group else None
        body = self.task(request.iterator, kind='scope', scope_group=group)
        body.scope_group, body.observed = group, True
        body.runner.frames = list(self.evaluator.frames)
        body.runner.depth = self.evaluator.depth
        body.runner.span = dict(self.evaluator.current_span)
        body.runner.handled_error = self.evaluator.handled_error
        body.runner.error_context = self.evaluator.error_context
        group.body = body
        runner.waiting, runner.state = request, 'joining'
        self.waiters.append(runner); self._start(body)
        self.event('group.entered', group)

    @staticmethod
    def cancelled_error(task):
        return TaskCancelled('任务已取消', _cancel_event=task.cancel_event)

    @staticmethod
    def _local_cancel(error, task):
        return isinstance(error, TaskCancelled) and error._cancel_event is task.cancel_event

    def validate(self, handle, expected):
        if not isinstance(handle, expected) or expected is Generator and isinstance(handle, AsyncGenerator):
            raise LanguageError('需要 '+expected.__name__, code='argument_error')
        if handle.owner is not self or handle.epoch != self.epoch or not self.active:
            raise LanguageError('Task/Generator 不能跨执行上下文或提交周期使用')
        from .agent_tools import access
        access(handle, self.evaluator)
        from .bot_runtime import check_handle
        check_handle(self.evaluator,handle)
        if not isinstance(handle, Task):
            self._recording_access(handle)
            current = self.current.task if self.current is not None else None
            scope = current.recording_scope if current is not None else None
            if handle.recording_scope is not None and handle.recording_scope != scope and not self.finishing:
                raise LanguageError('录制 stream 不能在其他 scope 消费', code='mode_error')
        return handle

    def task(self, iterator, *, kind='task', members=(), scope_group=None, channel=None, native_wait=None, selection=None, _compute_operation=False, capture_roots=()):
        if len(self.tasks) >= 10000: raise ResourceLimit('任务数量超过 10000')
        task = Task(self, iterator)
        task.capture_roots = tuple(capture_roots)
        if kind=='scope':
            from .decimal_values import active_state
            task.runner.decimal_state=active_state(self)
            from .conditions import active_state as recovery_state
            task.runner.condition_state=recovery_state(self)
            from .control_prompts import active_state as control_state
            task.runner.control_state=control_state(self)
            from .dynamic_contexts import active_state as context_state
            task.runner.context_state=context_state(self)
        task.kind, task.members = kind, tuple(members)
        task.compute_operation = _compute_operation
        task.scope_group, task.channel = scope_group, channel
        task.native_wait = native_wait
        task.selection = selection
        if selection is not None: selection.task = task
        if scope_group is not None: scope_group.body = task
        self.tasks.append(task)
        self.event('task.created', task)
        return task

    def generator(self, iterator, asynchronous=False, *, capture_roots=()):
        if len(self.generators) >= 10000: raise ResourceLimit('生成器数量超过 10000')
        handle = (AsyncGenerator if asynchronous else Generator)(self, iterator); self.generators.append(handle)
        handle.capture_roots = tuple(capture_roots)
        self.event('generator.created', handle)
        return handle

    def _recording_access(self, task):
        from .agent_tools import access
        access(task, self.evaluator)
        current = self.current.task if self.current is not None else None
        scope = current.recording_scope if current is not None else None
        if isinstance(task, Task) and (task.channel is not None or task.native_wait is not None or task.selection is not None or getattr(task, 'stack_operation', None) is not None or getattr(task, 'agent_operation', False) or getattr(task, 'compute_operation', False) or getattr(task, 'ui_operation', False) or getattr(task, 'device_operation', False) or getattr(task, 'robot_operation', False) or getattr(task, 'guidance_operation', False)) and task.recording_scope != scope:
            raise LanguageError('原生操作 Task 不能跨录制 scope 使用', code='mode_error')
        if current is not None and current.recording_scope is not None and task.recording_scope != current.recording_scope:
            raise LanguageError('录制不能控制来自其他 scope 的 Task/stream', code='mode_error')

    def spawn(self, task):
        task = self.validate(task, Task)
        self._recording_access(task)
        return self._start(task)

    def _start(self, task):
        if task.state == 'created':
            from .bot_runtime import active_decision,check_handle
            check_handle(self.evaluator,task)
            decision=active_decision(self.evaluator)
            if decision is not None and task.group is None and task.scope_group is None:
                current=self.current.task if self.current else None
                group=(current.scope_group or current.group) if current is not None else None
                group=group or decision.group
                if group is None or group.state not in ('active','joining'):
                    raise LanguageError('Bot child work needs its active structured scope',code='bot_state_error')
                return group.spawn(task)
            task.state = 'running'; self.ready.append(task.runner)
            self.event('task.started', task)
        return task

    def cancel(self, task):
        task = self.validate(task, Task)
        self._recording_access(task)
        return self._cancel(task, 'explicit')

    def _cancel(self, task, source):
        if task.state in TERMINAL: return False
        newly_requested = source not in task.cancel_sources
        task.cancel_sources.add(source)
        if task.cancel_shields and not task.cancel_event.is_set() and source not in ('global', 'epoch'):
            if newly_requested: self.event('shield.deferred', task.cancel_shields[-1])
            return True
        task.cancel_event.set()
        from .task_groups import GroupRun
        for runner in list(self.waiters):
            if runner.task is not task: continue
            if runner.effect_event is not None: runner.effect_event.set()
            if isinstance(runner.waiting, GroupRun): runner.waiting.group.fail(self.cancelled_error(task))
        self._start(task)
        if task.selection is not None:
            from .channel_select import cancel
            cancel(self, task, self.cancelled_error(task))
            return True
        if task.guidance_wait is not None:
            self.guidance_waits.cancel(task, self.cancelled_error(task))
            return True
        if task.native_wait is not None:
            self.task_waits.cancel(task, self.cancelled_error(task))
            return True
        if task.channel is not None:
            from .channels import cancel_task
            cancel_task(self, task, self.cancelled_error(task))
            return True
        # Propagate into nested barriers before looking for runnable waiters.
        # An iterative queue handles deeply nested gathers without Python stack
        # recursion and never traverses an ordinary Task's unrelated Await.
        self.cancel_queue.append(task)
        if not self.cancelling_joins:
            self.cancelling_joins = True
            try:
                while self.cancel_queue:
                    cancelled = self.cancel_queue.popleft()
                    if isinstance(cancelled.runner.waiting, Join):
                        self._join_error(cancelled.runner.waiting, self.cancelled_error(cancelled))
            finally:
                self.cancelling_joins = False
        return True

    def gather(self, tasks):
        if not isinstance(tasks, list): raise LanguageError('gather 需要 Task 列表')
        if len(tasks) > 10000: raise ResourceLimit('gather 超过 10000 项')
        tasks = list(tasks)
        for task in tasks: self.validate(task, Task)
        def collect():
            return (yield Join(tasks))
        return self.task(collect(), kind='gather', members=tasks)

    def _join_error(self, join, error):
        if error is None: return
        if error_priority(error) > error_priority(join.error):
            join.error = error
        if not join.cancelling:
            join.cancelling = True
            for member in join.members: self._cancel(member, join)

    def _join_finished(self, join, task):
        join.pending.discard(task)
        error = task.runner.error
        # A cancellation is ignorable only when this exact join requested it.
        # Another gather, explicit cancel, or a naked host control error is hard.
        if self._local_cancel(error, task) and task.cancel_sources == {join}: return
        if (task.group is not None and join.runner is not None and join.runner.task is not None and
                join.runner.task.group is task.group and task.group._internal_cancel(task, error)): return
        self._join_error(join, error)

    def _join_start(self, runner, join):
        # No observation or start occurs until every member passes preflight.
        for task in join.members:
            self.validate(task, Task)
            self._recording_access(task)
            if task is runner.task: raise LanguageError('Task 不能等待自身')
        # Reject an existing dependency back to this collector before adoption.
        # In particular, gather([its_caller]) must deliver an ordinary deadlock
        # error to that caller rather than cancelling the caller as a peer.
        pending, visited = list(join.members), set()
        while pending:
            task = pending.pop()
            if task is runner.task:
                raise LanguageError('Task 等待形成循环，无法继续', code='task_deadlock')
            if task in visited: continue
            visited.add(task)
            waiting = task.runner.waiting
            if isinstance(waiting, Task): pending.append(waiting)
            elif isinstance(waiting, Join): pending.extend(waiting.members)
        join.runner = runner
        join.pending = {task for task in join.members if task.state not in TERMINAL}
        for task in join.members:
            task.observed = True
            task.joins.append(join)
        for task in sorted((task for task in join.members if task.state in TERMINAL), key=lambda task:task.terminal_order):
            self._join_finished(join, task)
        for task in join.members: self._start(task)
        runner.waiting, runner.state = join, 'joining'
        self.waiters.append(runner)

    def effect(self, kind, payload):
        return Effect(kind, payload)

    @staticmethod
    def _worker(effect, cancel_event, handler, fallback):
        # Inputs are data and host callbacks, never an Evaluator or Env.
        if effect.kind == 'gpu':
            if '_bot_guard' in effect.payload:
                if handler is None:raise LanguageError('Guarded GPU needs Bot admission host',code='capability_error')
                return handler(effect.kind,effect.payload,cancel_event)
            from .gpu_compute import run
            return run(effect.payload,cancel_event)
        if effect.kind == 'program':
            p = effect.payload
            result = handler(effect.kind,p,cancel_event) if handler else fallback(p['operation'],p['payload'])
            if p['operation'] == 'revision':
                from .program_values import _RevisionPayload
                return _RevisionPayload.capture(result)
            if p['operation'] == 'adopt':
                if type(result) is not dict or 'receipt' not in result:
                    raise LanguageError('Program 采用宿主必须返回 receipt', code='program_error')
                # Extract before _wake's ordinary JSON budget: a persisted bundle
                # may be larger than the language's generic result limit.
                return result['receipt']
            return result
        if handler: return handler(effect.kind, effect.payload, cancel_event)
        if effect.kind == 'guidance':
            p = effect.payload
            caps = frozenset(p['_agent_caps']) if '_agent_caps' in p else None
            return fallback(p['operation'], p['payload'], caps, p.get('_team'))
        p = effect.payload
        if effect.kind in ('mode','process','session','computer','device','robot'): return fallback(p['operation'], p['payload'])
        if effect.kind == 'tool': return fallback(p['name'], p['value'])
        if effect.kind == 'agent':
            if '_observed' in p: return fallback('agent', p)
            return fallback(p['name'], p['config'], p['value'])
        return fallback(p['graph'], p['value'])

    def _restore(self, runner):
        e = self.evaluator
        e.frames, e.depth, e.current_span, e.handled_error, e.error_context = runner.frames, runner.depth, runner.span, runner.handled_error, runner.error_context
        self.stream = runner.stream

    def _save(self, runner):
        e = self.evaluator
        runner.frames, runner.depth, runner.span, runner.handled_error, runner.error_context = e.frames, e.depth, e.current_span, e.handled_error, e.error_context
        runner.stream = self.stream

    def _retire_execution(self, handle):
        from .execution_retirement import retire_execution
        return retire_execution(self, handle)

    def _finish_runner(self, runner, state, result=None, error=None, *, notify=True):
        if runner.task and runner.task.runner is runner and runner.task.terminal_order:
            return
        runner.state, runner.result, runner.error = state, result, error
        if runner.task and runner.task.runner is runner:
            runner.task.state = state
            from .channels import cleanup_task
            cleanup_task(runner.task)
            self.task_waits.cleanup(runner.task)
            self.guidance_waits.cleanup(runner.task)
            self.completion_sequence += 1
            runner.task.terminal_order = self.completion_sequence
            if error_priority(error) == 3 and self.hard_failure is None:
                self.hard_failure = error
            # Channel cancellation commits immediately. Its observers can
            # cancel more native operations through overlapping joins; enqueue
            # notifications so that this causal cascade cannot grow the Python
            # stack. The outermost call drains every notification before return.
            self.terminal_queue.append(runner.task)
            if notify: self._flush_terminals()

    def _flush_terminals(self):
        if self.notifying_terminals: return
        self.notifying_terminals = True
        notification_error = None
        retired = []
        try:
            while self.terminal_queue:
                task = self.terminal_queue.popleft()
                retired.append(task)
                group = task.scope_group or task.group
                notices = [(group.finished, (task,))] if group is not None else []
                notices.extend((self._join_finished, (join, task)) for join in list(task.joins))
                notices.append((self.task_waits.member_finished, (task,)))
                notices.append((self.event, ('task.'+task.state, task)))
                for callback, args in notices:
                    try:
                        callback(*args)
                    except BaseException as failure:
                        # A trace callback is a real host boundary. It may
                        # fail, but cannot strand already committed notices
                        # or replace a stronger control error with a peer's
                        # cancellation. Raise only after the queue is empty.
                        if error_priority(failure) > error_priority(notification_error):
                            notification_error = failure
                        if error_priority(failure) == 3 and self.hard_failure is None:
                            self.hard_failure = failure
        finally:
            self.notifying_terminals = False
            for task in retired: self._retire_execution(task)
        if notification_error is not None: raise notification_error

    def _step(self, runner):
        e = self.evaluator
        old, saved, saved_stream = self.current, (e.frames, e.depth, e.current_span, e.handled_error, e.error_context), self.stream
        self.current = runner; self._restore(runner)
        suspended = False
        try:
            if runner.task and (runner.task.cancel_event.is_set() or runner.task.interrupt is not None):
                group = runner.task.group or runner.task.scope_group
                if group is not None:
                    from .errors import observe
                    try: observe(e, runner.error)
                    except BaseException as observed: runner.error = observed
                    group.remember_failure(runner.error)
            if runner.task and runner.task.cancel_event.is_set() and error_priority(runner.error) < 2:
                runner.error = self.cancelled_error(runner.task)
            if (runner.task and runner.task.interrupt is not None and
                    (error_priority(runner.error) < 2 or error_priority(runner.task.interrupt) > error_priority(runner.error))):
                runner.error, runner.task.interrupt = runner.task.interrupt, None
            error, value = runner.error, runner.value
            runner.error = runner.value = None
            runner.started = True
            request = runner.iterator.throw(error) if error is not None else runner.iterator.send(value)
            suspended = True
            from .task_groups import GroupRun
            from .channels import Send, Receive, dispatch
            from .task_waits import Sleep, Wait
            from .channel_select import Selection, dispatch as dispatch_select
            from .guidance_waits import GuidanceWait
            if isinstance(request, GuidanceWait):
                self.guidance_waits.dispatch(runner, request)
            elif isinstance(request, Selection):
                dispatch_select(self, runner, request)
            elif isinstance(request, (Sleep, Wait)):
                self.task_waits.dispatch(runner, request)
            elif isinstance(request, (Send, Receive)):
                dispatch(self, runner, request)
            elif isinstance(request, GroupRun):
                self._group_start(runner, request)
            elif isinstance(request, Join):
                self._join_start(runner, request)
            elif isinstance(request, Drain):
                target = self.validate(request.task, Task)
                self._recording_access(target)
                if target is runner.task: raise LanguageError('Task 不能等待自身')
                target.observed = True
                self._start(target)
                runner.waiting, runner.state = request, 'joining'
                self.waiters.append(runner)
            elif isinstance(request, Await):
                target = self.validate(request.task, Task)
                self._recording_access(target)
                if target is runner.task: raise LanguageError('Task 不能等待自身')
                # Cold native operations created by this logical Task are
                # owned by this wait. Already started or foreign-created Tasks
                # remain borrowed, exactly like ordinary Await targets.
                parent_id = runner.task.id if runner.task else None
                runner.await_owned = (target if (target.channel is not None or target.native_wait is not None or target.selection is not None or getattr(target,'compute_operation',False) or getattr(target,'ui_operation',False) or getattr(target,'device_operation',False) or getattr(target,'robot_operation',False) or getattr(target,'guidance_operation',False)) and
                                      target.state == 'created' and target.parent_task_id == parent_id else None)
                target.observed = True
                self.spawn(target)
                runner.waiting, runner.state = target, 'waiting'
                self.waiters.append(runner)
            elif isinstance(request, Effect):
                if runner.task is None: raise LanguageError('异步效果必须位于 Task 内')
                if self.pool is None: self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='chaos-effect')
                fallback = {'tool':e.tool_handler, 'agent':e.agent_handler, 'workflow':e.workflow_handler, 'mode':e.mode_handler, 'program':e.program_handler, 'process':e.process_handler, 'session':e.session_handler, 'computer':e.computer_handler, 'device':e.device_handler, 'robot':e.robot_handler, 'guidance':e.guidance_handler, 'agent_transition':None,'gpu':None,'ui':None}[request.kind]
                if request.kind == 'agent' and '_observed' in request.payload:
                    # The legacy three-argument callback cannot carry image
                    # identity or Team authority. Preserve the private envelope
                    # through the same dedicated handler used by sync Agents.
                    fallback = e.computer_handler
                from .bot_runtime import current_guard
                guard=current_guard(e)
                if guard is not None:
                    if e.effect_handler is None:
                        raise LanguageError('Guarded Bot effects require the run admission host',code='capability_error')
                    request=Effect(request.kind,{**request.payload,'_bot_guard':guard})
                # Provenance is scheduler-owned metadata, never copied from a
                # user tool argument or model ToolCall input.
                if request.kind!='gpu' and runner.task.recording_scope is not None:
                    request = Effect(request.kind, {**request.payload, '_mode':{'recordingId':runner.task.recording_scope}})
                if request.kind!='gpu' and runner.team_scope is not None and '_team' not in request.payload:
                    request = Effect(request.kind, {**request.payload, '_team':runner.team_scope})
                if request.kind!='gpu' and runner.capabilities is not None:
                    request = Effect(request.kind, {**request.payload, '_agent_caps':sorted(runner.capabilities)})
                runner.effect_kind = request.kind
                # A scope interrupt cancels this one effect without poisoning
                # the body's Task event; ordinary finally can still run effects.
                closing_stack = any(stack.operation_task is runner.task and stack.operation_kind in ('close', 'aclose')
                                    for stack in self.resource_stacks)
                runner.effect_event = threading.Event() if runner.task.scope_group is not None or closing_stack else runner.task.cancel_event
                if runner.task.cancel_event.is_set() or runner.task.interrupt is not None: runner.effect_event.set()
                runner.future = self.pool.submit(self._worker, request, runner.effect_event, None if request.kind=='gpu' and '_bot_guard' not in request.payload else e.effect_handler, fallback)
                runner.state = 'effect'; self.waiters.append(runner)
            elif isinstance(request, Yield):
                from .cancellation import check_yield
                check_yield(self, self.stream)
                if runner.task and runner.task.scope_group is not None:
                    raise LanguageError('TaskGroup 作用域不能跨越 yield', code='task_group_error')
                runner.state, runner.result = 'yielded', request.value
            elif request is CHECKPOINT:
                runner.state = 'ready'
            else:
                raise LanguageError('无效的挂起请求')
        except StopIteration as stop:
            from .task_groups import ScopeOutcome, GroupAbort
            outcome = stop.value
            if runner.task and runner.task.scope_group is not None and isinstance(outcome, ScopeOutcome):
                failure = outcome.error or (outcome.signal if isinstance(outcome.signal, GroupAbort) else None)
                from .errors import observe
                try: observe(e, failure)
                except BaseException as observed:
                    failure = observed; outcome = ScopeOutcome(error=observed)
                state = 'cancelled' if isinstance(failure, (ExecutionControl, GroupAbort)) else ('failed' if failure else 'completed')
                self._finish_runner(runner, state, outcome, failure)
            else:
                self._finish_runner(runner, 'completed', outcome)
        except (TaskCancelled, ExecutionControl) as error:
            if runner.task and runner.task.runner is runner and runner.task.terminal_order: raise
            self._finish_runner(runner, 'cancelled', error=error)
        except GeneratorClose:
            if runner.task and runner.task.runner is runner and runner.task.terminal_order: raise
            self._finish_runner(runner, 'closed')
        except BaseException as error:
            if runner.task and runner.task.runner is runner and runner.task.terminal_order: raise
            from .errors import observe
            try: observe(e, error)
            except BaseException as observed: error = observed
            if suspended:
                # Protocol/binding failures occur at the suspension expression,
                # so deliver them back through its catch/finally scopes.
                runner.error, runner.state = error, 'ready'
            else:
                self._finish_runner(runner, 'failed', error=error)
        finally:
            self._save(runner)
            self.current = old
            e.frames, e.depth, e.current_span, e.handled_error, e.error_context = saved
            self.stream = saved_stream
            if runner.task is not None and runner.task.runner is runner:
                self._retire_execution(runner.task)

    def _observe_failure(self, runner, error):
        """Attribute completed host effects to their logical task before drain."""
        if error is None: return None
        from .errors import observe
        e = self.evaluator
        saved = (e.frames, e.depth, e.current_span, e.handled_error, e.error_context, self.stream, self.current)
        self.current = runner; self._restore(runner)
        try:
            return observe(e, error)
        except BaseException as failure:
            return failure
        finally:
            e.frames, e.depth, e.current_span, e.handled_error, e.error_context, self.stream, self.current = saved

    def _wake(self):
        self.task_waits.poll()
        self.guidance_waits.poll()
        from .task_groups import GroupRun, GroupAbort, ScopeOutcome
        for runner in list(self.waiters):
            if isinstance(runner.waiting, Drain):
                target = runner.waiting.task
                if target.state not in TERMINAL: continue
                runner.value, runner.error = target.runner.result, target.runner.error
                if error_priority(runner.deferred_error) > error_priority(runner.error): runner.error = runner.deferred_error
                runner.deferred_error = None
                runner.waiting = None
                self.waiters.remove(runner); runner.state = 'ready'; self.ready.append(runner)
                continue
            if isinstance(runner.waiting, GroupRun):
                group = runner.waiting.group
                if runner.task and runner.task.cancel_event.is_set(): group.fail(self.cancelled_error(runner.task))
                elif runner.task and runner.task.interrupt is not None: group.fail(runner.task.interrupt)
                if group.body.state not in TERMINAL or group.pending: continue
                group.state = 'closed'
                self.event('group.closed', group)
                runner.value = group.outcome or ScopeOutcome()
                runner.error, runner.waiting = group.final_error(), None
                self.waiters.remove(runner); runner.state = 'ready'; self.ready.append(runner)
                continue
            if isinstance(runner.waiting, Join):
                join = runner.waiting
                if runner.task and runner.task.cancel_event.is_set():
                    self._join_error(join, self.cancelled_error(runner.task))
                if join.pending: continue
                for task in join.members:
                    if join in task.joins: task.joins.remove(join)
                from .multiple_values import primary,prepare
                runner.value = [primary(prepare(self.evaluator,task.runner.result,ceiling=runner.capabilities),self.evaluator) for task in join.tasks] if join.error is None else None
                runner.error, runner.waiting = join.error, None
                self.waiters.remove(runner); runner.state = 'ready'; self.ready.append(runner)
                continue
            # Effects must actually finish, including after a cancellation request.
            if runner.future is not None:
                if not runner.future.done(): continue
                try:
                    result = runner.future.result()
                    from .program_values import _RevisionPayload
                    # Only Program revision uses this internal data-only envelope.
                    # No generic JSON marker or other host effect gains a bypass.
                    if runner.effect_kind == 'program' and type(result) is _RevisionPayload:
                        runner.value = result
                    else: runner.value = self.evaluator.copy_value(result)
                except (LanguageError, ExecutionControl) as error:
                    # The established host effect API receives this event and
                    # may acknowledge it with TaskCancelled(message). Attribute
                    # only that exact signal to its actual cancelled worker;
                    # generic control failures and subclasses retain identity
                    # and hard-error priority regardless of the event's state.
                    if (type(error) is TaskCancelled and error._cancel_event is None and
                            runner.effect_event is not None and runner.effect_event.is_set()):
                        error._cancel_event = runner.effect_event
                    runner.error = error
                except Exception as error:
                    code = {'tool':'tool_error', 'agent':'provider_error'}.get(runner.effect_kind, 'effect_error')
                    runner.error = LanguageError(str(error), code=code)
                except BaseException as error:
                    # Host interrupts retain their identity and unwind the AST
                    # through the suspended expression, including nested drivers.
                    runner.error = error
                runner.error = self._observe_failure(runner, runner.error)
                if error_priority(runner.deferred_error) > error_priority(runner.error):
                    group = (runner.task.group or runner.task.scope_group) if runner.task else None
                    if group is not None: group.remember_failure(runner.error)
                    runner.error = runner.deferred_error
                runner.deferred_error = None
                # Preserve genuine late host failures. Only acknowledgement of
                # this effect's exact scoped cancellation becomes the pending
                # private lexical interrupt, which runs ordinary cleanup once.
                if (runner.task and runner.task.interrupt is not None and
                        isinstance(runner.error, TaskCancelled) and
                        runner.error._cancel_event is runner.effect_event and
                        not runner.task.cancel_event.is_set()):
                    runner.error = None
                runner.future = None
                runner.effect_event = None
            else:
                target = runner.waiting
                if runner.task and runner.task.cancel_event.is_set():
                    if runner.await_owned is not None: self._cancel(runner.await_owned, runner)
                    runner.error = self.cancelled_error(runner.task)
                elif runner.task and runner.task.interrupt is not None:
                    if runner.await_owned is not None: self._cancel(runner.await_owned, runner)
                    runner.error = None
                elif target.state not in TERMINAL:
                    continue
                else:
                    runner.value, runner.error = target.runner.result, target.runner.error
                runner.waiting = None
                runner.await_owned = None
            self.waiters.remove(runner); runner.state = 'ready'; self.ready.append(runner)

    def _run_until(self, runner, allow_yield=False, check_budget=True):
        if runner.state == 'yielded': runner.state = 'ready'
        self.ready.append(runner)
        while runner.state not in TERMINAL and not (allow_yield and runner.state == 'yielded'):
            self._wake()
            if self.ready:
                current = self.ready.popleft()
                if current.state != 'ready': continue
                self._step(current)
                if current.state == 'ready': self.ready.append(current)
                elif current.state == 'yielded' and (current is not runner or not allow_yield):
                    self._finish_runner(current, 'failed', error=LanguageError('yield 仅可用于生成器'))
            else:
                futures = [r.future for r in self.waiters if r.future is not None]
                timer_delay = self.task_waits.delay()
                guidance_delay = self.guidance_waits.delay()
                if guidance_delay is not None:
                    timer_delay = guidance_delay if timer_delay is None else min(timer_delay, guidance_delay)
                if not futures and timer_delay is None:
                    # A caller awaiting a gather must receive deadlock through
                    # that gather's completion barrier, after its members stop.
                    # Follow only this caller's dependency chain: an unrelated
                    # gather must not be cancelled to break another task's cycle.
                    waiting, visited = runner, set()
                    while waiting not in visited:
                        visited.add(waiting)
                        from .task_groups import GroupRun
                        if isinstance(waiting.waiting, GroupRun):
                            waiting.waiting.group.fail(LanguageError('Task 等待形成循环，无法继续', code='task_deadlock'))
                            break
                        if isinstance(waiting.waiting, Join):
                            join = waiting.waiting
                            self._join_error(join, LanguageError('Task 等待形成循环，无法继续', code='task_deadlock'))
                            break
                        from .channel_select import Selection, cancel as cancel_select
                        if isinstance(waiting.waiting, Selection):
                            cancel_select(self, waiting.task, LanguageError('Channel select 无法继续', code='task_deadlock'))
                            self._wake()
                            break
                        from .task_waits import Sleep, Wait
                        if isinstance(waiting.waiting, (Sleep, Wait)):
                            self.task_waits.cancel(waiting.task, LanguageError('Task 等待无法继续', code='task_deadlock'))
                            self._wake()
                            break
                        from .channels import Send, Receive, cancel_task
                        if isinstance(waiting.waiting, (Send, Receive)):
                            cancel_task(self, waiting.task, LanguageError('Channel 等待无法继续', code='task_deadlock'))
                            self._wake()
                            break
                        if not isinstance(waiting.waiting, Task): break
                        waiting = waiting.waiting.runner
                    else:
                        waiting = None
                    if self.ready: continue
                    if waiting is not None and isinstance(waiting.waiting, (Join, GroupRun)):
                        self._wake()
                        if self.ready: continue
                    if runner in self.waiters: self.waiters.remove(runner)
                    runner.waiting = None
                    runner.error = LanguageError('Task 等待形成循环，无法继续', code='task_deadlock')
                    runner.state = 'ready'; self.ready.append(runner)
                    continue
                delay = min(0.01, timer_delay) if timer_delay is not None else 0.01
                if futures: wait(futures, timeout=delay, return_when=FIRST_COMPLETED)
                else: self.task_waits.idle_wait(delay)
                if not self.finishing and check_budget:
                    # No AST is executing here. A reentrant driver may leave its
                    # caller in self.current; delivering that caller's local
                    # interrupt here would abandon the real suspended iterator.
                    # Only global budget/cancellation checks belong to idle time.
                    logical_current, self.current = self.current, None
                    try:
                        self.evaluator.tick()
                    except (ResourceLimit, ExecutionControl) as error:
                        for task in self.tasks:
                            if task.state not in TERMINAL: self._cancel(task, 'global')
                        raise error
                    finally:
                        self.current = logical_current
        if runner.error is not None: raise runner.error
        return runner.result

    def drive(self, iterator):
        if not self.active: self.begin()
        e = self.evaluator
        task = self.current.task if self.current else None
        from .execution_frames import ensure_cursor
        runner = Runner(ensure_cursor(e,iterator), e.frames, e.depth, e.current_span, task)
        capture_parent=self.stream.runner if self.stream is not None else self.current
        if capture_parent is not None:runner.capture_frames=capture_parent.capture_frames
        from .decimal_values import active_state
        runner.decimal_state=active_state(self)
        from .conditions import active_state as recovery_state
        runner.condition_state=recovery_state(self)
        from .control_prompts import active_state as control_state
        runner.control_state=control_state(self)
        from .dynamic_contexts import active_state as context_state
        runner.context_state=context_state(self)
        runner.handled_error = e.handled_error
        runner.error_context = e.error_context
        runner.capabilities = self.current.capabilities if self.current else None
        runner.team_scope = self.current.team_scope if self.current else None
        runner.bot_guard = getattr(self.current,'bot_guard',None) if self.current else None
        # A reentrant native callback belongs to the same logical task, but must
        # never overwrite its parent's task lifecycle status.
        try:
            return self._run_until(runner)
        except (ResourceLimit, ExecutionControl) as error:
            from .task_groups import GroupRun
            scoped = task is not None and (task.scope_group is not None or task.group is not None or task.cancel_shields)
            if runner.state not in TERMINAL and (scoped or isinstance(runner.waiting, GroupRun)):
                # Scope control must cross its real worker/barrier. In particular,
                # a reentrant native driver cannot drop a future that may still
                # reveal a higher-priority host failure after cancellation.
                if isinstance(runner.waiting, GroupRun):
                    runner.waiting.group.fail(error)
                elif isinstance(runner.waiting, Drain):
                    runner.deferred_error = error
                elif runner.future is not None:
                    runner.deferred_error = error
                    if runner.effect_event is not None: runner.effect_event.set()
                else:
                    if runner in self.waiters: self.waiters.remove(runner)
                    runner.waiting = None
                    runner.error, runner.state = error, 'ready'
                return self._run_until(runner, check_budget=False)
            # A hard error from the nested scheduler's idle budget check occurs
            # outside iterator.send(). Deliver it into this suspended driver so
            # stream locks and logical frame scopes unwind in their own context.
            if runner.state not in TERMINAL:
                if runner in self.waiters: self.waiters.remove(runner)
                self.ready = deque(item for item in self.ready if item is not runner)
                runner.waiting = runner.future = None
                runner.error, runner.state = error, 'ready'
                self._step(runner)
            raise

    def _abort_stream(self, handle):
        # Resuming a suspended AST must restore its own logical frames, even
        # during force-close after a cleanup illegally yields.
        e = self.evaluator
        already_current = self.stream is handle
        saved = (e.frames, e.depth, e.current_span, e.handled_error, e.error_context, self.stream)
        if not already_current:
            self._restore(handle.runner); self.stream = handle
        iterator = handle.runner.iterator
        if getattr(handle.runner, 'execution_retired', False):
            if not already_current:
                e.frames, e.depth, e.current_span, e.handled_error, e.error_context, self.stream = saved
            return
        try:
            if handle.runner.started: iterator.throw(TaskCancelled('生成器已终止'))
        except BaseException:
            pass
        finally:
            try: iterator.close()
            except BaseException: pass
            if not already_current:
                self._save(handle.runner)
                e.frames, e.depth, e.current_span, e.handled_error, e.error_context, self.stream = saved
                self._retire_execution(handle)

    def _s_resume(self,handle,value=None,error=None,locked=False,context=None):
        return self.iteration._frame_request('resume',(handle,value,error,locked,context))

    def next(self, handle):
        return self.send(handle, None)

    def send(self, handle, value):
        return self.drive(self.iteration.step(handle, value))

    def throw_into(self, handle, value):
        return self.drive(self.iteration.step(handle, error=raised_value(self.evaluator, self.evaluator.copy_value(value))))

    def _s_close(self,handle,context=None):
        return self.iteration._frame_request('stream_close',(handle,context))

    def close(self, handle):
        return self.drive(self.iteration.close(handle))

    def collect(self, handle):
        return self.drive(self.iteration.collect(handle))

    def anext(self, handle):
        return self.asend(handle, None)

    def asend(self, handle, value):
        handle = self.iteration.direct(handle, True)
        from .channels import Channel, receive
        if isinstance(getattr(handle, 'target', None), Channel):
            if value is not None: raise LanguageError('Channel 迭代器只接受 null', code='argument_error')
            return receive(self.evaluator, handle.target, cursor=handle)
        return self.task(self.iteration.step(handle, value, asynchronous=True), capture_roots=(handle,value))

    def athrow(self, handle, value):
        handle = self.iteration.direct(handle, True)
        return self.task(self.iteration.step(handle, error=raised_value(self.evaluator, self.evaluator.copy_value(value)), asynchronous=True), capture_roots=(handle,value))

    def aclose(self, handle):
        handle = self.iteration.direct(handle, True)
        return self.task(self.iteration.close(handle, True), capture_roots=(handle,))

    def acollect(self, handle):
        return self.task(self.iteration.collect(handle, True), capture_roots=(handle,))

    def delegate(self, items, asynchronous=False):
        return (yield from self.iteration.delegate(items, asynchronous))

    def _drain_tasks(self):
        for task in self.tasks:
            if task.state not in TERMINAL: self._cancel(task, 'epoch')
        # Consume every future through _wake: merely waiting and dropping its
        # result loses late host control failures. No cancelled AST receives a
        # new budget or runs user finally hooks while this barrier is drained.
        while any(task.state not in TERMINAL for task in self.tasks):
            self._wake()
            if self.ready:
                runner = self.ready.popleft()
                if runner.state != 'ready': continue
                self._step(runner)
                if runner.state == 'ready': self.ready.append(runner)
                continue
            futures = [runner.future for runner in self.waiters if runner.future is not None]
            if futures:
                wait(futures, return_when=FIRST_COMPLETED)
                continue
            # A cycle consisting only of joins has no worker left to drain.
            # Break it with cancellation, then let normal terminal notification
            # release the remaining barriers.
            pending = next((task for task in self.tasks if task.state not in TERMINAL), None)
            if pending is None: break
            runner = pending.runner
            if runner in self.waiters: self.waiters.remove(runner)
            if isinstance(runner.waiting, Join):
                for member in runner.waiting.members:
                    if runner.waiting in member.joins: member.joins.remove(runner.waiting)
            runner.waiting = None
            runner.error, runner.state = self.cancelled_error(pending), 'ready'
            self.ready.append(runner)
        self.ready.clear(); self.waiters.clear()

    def finish(self, original=None):
        if not self.active: return
        hard = hard_error(original)
        pending_error = None
        pending_error_from_stack = False
        self.finishing = True
        try:
            self._drain_tasks()
            # A worker may reveal a hard error only when epoch cancellation
            # wakes it. That failure forbids subsequent language cleanup too.
            hard = hard or self.hard_failure is not None
            # Cleanup may register more generators or user adapters. Process each
            # once in this epoch, under the same step/deadline/quantity bounds.
            gi, ii = 0, 0
            from .iteration import AsyncIterator
            while (gi < len(self.generators) or ii < len(self.iteration.handles) or
                   any(stack.state != 'closed' for stack in self.resource_stacks)):
                if gi < len(self.generators):
                    handle = self.generators[gi]; gi += 1
                    if handle.state in TERMINAL: continue
                    if handle.context_manager is not None:
                        # The lexical owner may itself be a later-created
                        # suspended generator. Let its close unwind the manager
                        # in lexical order, rather than registry creation order.
                        if hard or handle.context_manager.state == 'created':
                            from .contexts import abandon
                            abandon(self.evaluator, handle.context_manager)
                        continue
                    try:
                        if hard: self._abort_stream(handle)
                        else:
                            handle.runner.task = None
                            if isinstance(handle, AsyncGenerator):
                                task = self.aclose(handle)
                                def closing(): return (yield Await(task))
                                self.drive(closing())
                            else: self.close(handle)
                    except (GeneratorClose, StopIteration): pass
                    except (ResourceLimit, ExecutionControl) as error:
                        hard = True
                        if pending_error is None: pending_error = error
                    except BaseException as error:
                        if hard_error(error): hard = True
                        if pending_error is None: pending_error = error
                    finally:
                        self._abort_stream(handle); handle.state = 'closed'
                        self._retire_execution(handle)
                elif ii < len(self.iteration.handles):
                    handle = self.iteration.handles[ii]; ii += 1
                    if handle.close_attempted: continue
                    if hard:
                        handle.close_attempted, handle.busy, handle.state = True, False, 'closed'
                        continue
                    try:
                        if isinstance(handle, AsyncIterator):
                            task = self.aclose(handle)
                            def closing(): return (yield Await(task))
                            self.drive(closing())
                        else: self.drive(self.iteration.close(handle))
                    except (ResourceLimit, ExecutionControl) as error:
                        hard = True
                        if pending_error is None: pending_error = error
                    except BaseException as error:
                        if hard_error(error): hard = True
                        if pending_error is None: pending_error = error
                else:
                    from .resource_stacks import unwind, abandon as abandon_stack
                    stack = next((item for item in reversed(self.resource_stacks)
                                  if item.state != 'closed' and not item.context_active), None)
                    if stack is None:
                        # Any surviving lexical owner was hard-aborted. Acquired
                        # child stacks normally close through their root's LIFO.
                        for item in self.resource_stacks: abandon_stack(self.evaluator, item)
                        continue
                    try:
                        if hard: abandon_stack(self.evaluator, stack)
                        else: self.drive(unwind(self.evaluator, stack, finalizing=True, managed=True))
                    except BaseException as error:
                        if hard_error(error): hard = True
                        if pending_error is None or error_priority(error) > error_priority(pending_error):
                            pending_error = error
                            pending_error_from_stack = True
            if hard: self.iteration.hard_close()
            # A hard-aborted owner never executes user exit hooks. Any reserved
            # streams still present now receive bookkeeping-only teardown.
            from .contexts import abandon
            for handle in self.generators:
                if handle.context_manager is not None and handle.context_manager.state != 'closed':
                    abandon(self.evaluator, handle.context_manager)
            # Cleanup can itself create tasks. They belong to this same scope.
            self._drain_tasks()
            for task in self.tasks:
                if task.state == 'failed' and not task.observed and pending_error is None:
                    pending_error = task.runner.error
        finally:
            if self.pool is not None: self.pool.shutdown(wait=True, cancel_futures=True)
            from .resource_stacks import abandon as abandon_stack
            for stack in self.resource_stacks: abandon_stack(self.evaluator, stack)
            from .channels import finish as finish_channels
            finish_channels(self)
            from .channel_select import finish as finish_select
            finish_select(self)
            self.task_waits.finish()
            self.guidance_waits.finish()
            from .conditions import finish as finish_recovery
            finish_recovery(self)
            from .dynamic_contexts import finish as finish_contexts
            finish_contexts(self)
            self.active = False; self.finishing = False; self.pool = None
            self.ready.clear(); self.waiters.clear(); self.current = None
        if self.hard_failure is not None and error_priority(original) < 3: raise self.hard_failure
        # Preserve existing generator/adapter finalizer precedence. Dynamic
        # stacks additionally retain their hard cleanup failures across a pending
        # ordinary run error, with the selected failure's provenance kept here.
        if pending_error is not None and (original is None or pending_error_from_stack and
                                           error_priority(pending_error) > error_priority(original)):
            raise pending_error
