"""Epoch-local monotonic timers and non-owning Task completion observers.

These requests never occupy an effect worker. A wait owns only its registration;
its members remain borrowed, including after timeout or cancellation.
"""
from dataclasses import dataclass
import heapq
import math
import threading
import time

from .language import LanguageError, ResourceLimit


@dataclass(eq=False)
class Sleep:
    seconds: float
    deadline: object = None


@dataclass(eq=False)
class Wait:
    tasks: tuple
    until: str
    timeout: object
    deadline: object = None
    reserved: bool = True


def _seconds(value, name):
    if type(value) not in (int, float):
        raise LanguageError(name+' 需要有限非负 Number', code='argument_error')
    try: number = float(value)
    except (ValueError, OverflowError): number = float('inf')
    if not math.isfinite(number) or number < 0:
        raise LanguageError(name+' 需要有限非负 Number', code='argument_error')
    return number


def _scope(scheduler):
    current = scheduler.current.task if scheduler.current else None
    return current.recording_scope if current else None


def _validate(scheduler, tasks, scope):
    from .suspension import Task
    for task in tasks:
        scheduler.validate(task, Task)
        if task.recording_scope != scope:
            raise LanguageError('task_wait 不能观察其他录制 scope 的 Task', code='mode_error')


def sleep(evaluator, seconds):
    scheduler = evaluator.suspension
    request = Sleep(_seconds(seconds, 'sleep seconds'))
    def operation(): return (yield request)
    return scheduler.task(operation(), kind='sleep', native_wait=request)


def wait(evaluator, tasks, until='first', timeout=None):
    scheduler = evaluator.suspension
    if type(tasks) is not list or type(until) is not str or until not in ('first','all','error'):
        raise LanguageError('task_wait 需要 Task 列表及 first/all/error 模式', code='argument_error')
    if len(tasks) > 10000: raise ResourceLimit('task_wait 超过 10000 项')
    timeout = None if timeout is None else _seconds(timeout, 'task_wait timeout')
    _validate(scheduler, tasks, _scope(scheduler))
    members = tuple(dict.fromkeys(tasks))
    engine = scheduler.task_waits
    if engine.references + len(members) > 10000:
        raise ResourceLimit('task_wait 未完成成员引用总数超过 10000')
    request = Wait(members, until, timeout)
    def operation(): return (yield request)
    engine.references += len(members)
    try:
        return scheduler.task(operation(), kind='wait', members=members, native_wait=request)
    except BaseException:
        # task.created is a host callback; if it failed after registration, the
        # epoch still owns that Task and its reservation until normal cleanup.
        if not any(task.native_wait is request for task in scheduler.tasks):
            engine.references -= len(members)
            request.reserved = False
        raise


class Waits:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.clock = time.monotonic
        self.idle_wait = threading.Event().wait
        self.active, self.timers, self.deadlines = {}, [], {}
        self.sequence = self.references = 0

    def begin(self):
        self.active.clear(); self.timers.clear(); self.deadlines.clear()
        self.sequence = self.references = 0

    def timing(self, task):
        request = task.native_wait or task.selection
        if request is None: return None
        from .suspension import TERMINAL
        remaining = (max(0.0, request.deadline-self.clock())
                     if task in self.deadlines and task.state not in TERMINAL and request.deadline is not None else None)
        return {'seconds':request.seconds if isinstance(request, Sleep) else None,
                'until':request.until if isinstance(request, Wait) else None,
                'timeoutSeconds':request.timeout if not isinstance(request, Sleep) else None,
                'remainingSeconds':remaining}

    def dispatch(self, runner, request):
        task = runner.task
        if task is None or task.runner is not runner or task.native_wait is not request:
            raise LanguageError('无效的 native wait 请求', code='argument_error')
        self.scheduler._recording_access(task)
        if isinstance(request, Wait):
            _validate(self.scheduler, request.tasks, task.recording_scope)
        delay = request.seconds if isinstance(request, Sleep) else request.timeout
        request.deadline = None if delay is None else self.arm(task, delay)
        self.active[task] = request
        runner.waiting = request
        runner.state = 'sleeping' if isinstance(request, Sleep) else 'waiting_tasks'
        if isinstance(request, Wait):
            for member in request.tasks: member.completion_waits[task] = None
            for member in request.tasks: self.scheduler._start(member)
            self._check(task, self.clock())

    def cleanup(self, task):
        request = task.native_wait
        if request is None: return
        self.active.pop(task, None)
        if isinstance(request, Wait):
            for member in request.tasks: member.completion_waits.pop(task, None)
            if request.reserved:
                self.references -= len(request.tasks)
                request.reserved = False
        task.runner.waiting = None
        request.deadline = None
        self.disarm(task)

    def arm(self, task, seconds):
        deadline = self.clock()+seconds
        self.deadlines[task] = deadline
        self.sequence += 1
        heapq.heappush(self.timers, (deadline, self.sequence, task))
        return deadline

    def disarm(self, task):
        self.deadlines.pop(task, None)
        self._prune()
        # Heap tombstones are bounded independently of timer duration. Avoid
        # quadratic deletion when an epoch cancels thousands of timers.
        if len(self.timers) > 2*len(self.deadlines)+64 or not self.deadlines:
            self.timers[:] = [item for item in self.timers if item[2] in self.deadlines]
            heapq.heapify(self.timers)

    def complete(self, task, result=None, error=None):
        from .suspension import TERMINAL, ExecutionControl
        if task.state in TERMINAL: return
        # These iterators contain no user AST or finally code. Commit before
        # callbacks, just as Channel operations do, so cancellation cannot undo
        # a completion snapshot or revive an expired timer.
        task.runner.iterator.close()
        state = 'cancelled' if isinstance(error, ExecutionControl) else ('failed' if error is not None else 'completed')
        self.scheduler._finish_runner(task.runner, state, result, error, notify=False)

    def cancel(self, task, error):
        self.complete(task, error=error)
        self.scheduler._flush_terminals()

    def _check(self, task, now):
        from .suspension import TERMINAL, error_priority
        request = self.active.get(task)
        if request is None: return
        if isinstance(request, Sleep):
            if now >= request.deadline: self.complete(task)
            return
        # Hard execution controls are never ordinary completion data. Surface
        # the earliest committed hard identity at this boundary, without
        # consuming member errors or acquiring cancellation ownership.
        hard = [member for member in request.tasks if member.state in TERMINAL and
                error_priority(member.runner.error) == 3]
        if hard:
            self.complete(task, error=min(hard, key=lambda member:member.terminal_order).runner.error)
            return
        try: _validate(self.scheduler, request.tasks, task.recording_scope)
        except LanguageError as error:
            self.complete(task, error=error)
            return
        done = [member for member in request.tasks if member.state in TERMINAL]
        satisfied = (not request.tasks or len(done) == len(request.tasks) or
                     request.until == 'first' and bool(done) or
                     request.until == 'error' and any(member.runner.error is not None for member in done))
        expired = request.deadline is not None and now >= request.deadline
        if satisfied or expired:
            self.complete(task, {'done':done,
                                'pending':[member for member in request.tasks if member.state not in TERMINAL],
                                'timedOut':not satisfied})

    def member_finished(self, member):
        for task in tuple(member.completion_waits): self._check(task, self.clock())

    def poll(self):
        now = self.clock()
        notifying = self.scheduler.notifying_terminals
        self.scheduler.notifying_terminals = True
        try:
            # Scope membership can change while record_skill runs. The shared
            # reference quota bounds this sweep. Settle all due native timers
            # before any observer can fail or cancel a simultaneous completion.
            for task in tuple(self.active): self._check(task, now)
            from .channel_select import timeout
            while self.timers and self.timers[0][0] <= now:
                _, _, task = heapq.heappop(self.timers)
                if task in self.deadlines and task.selection is not None:
                    timeout(self.scheduler, task)
                self._prune()
        finally: self.scheduler.notifying_terminals = notifying
        self.scheduler._flush_terminals()

    def _prune(self):
        while self.timers and self.timers[0][2] not in self.deadlines:
            heapq.heappop(self.timers)

    def delay(self):
        self._prune()
        return max(0.0, self.timers[0][0]-self.clock()) if self.timers else None

    def finish(self):
        for task in self.scheduler.tasks: self.cleanup(task)
        self.active.clear(); self.timers.clear(); self.deadlines.clear()
        self.references = 0
