"""Nonblocking guidance registrations in the original cooperative scheduler."""
from dataclasses import dataclass
import time

from .language import LanguageError, ExecutionControl


@dataclass(eq=False)
class GuidanceWait:
    owner: object
    epoch: int
    timeout: object
    deadline: object = None


def receive(evaluator, timeout=0):
    from .task_waits import _seconds
    scheduler = evaluator.suspension
    if not scheduler.active:
        raise LanguageError('Guidance needs an active execution epoch', code='ownership_error')
    timeout = None if timeout is None else _seconds(timeout, 'guidance timeout')
    request = GuidanceWait(scheduler, scheduler.epoch, timeout)
    def operation():
        return (yield request)
    task = scheduler.task(operation(), kind='guidance', capture_roots=(request,))
    task.guidance_wait = request
    task.guidance_operation = True
    return task


class Waits:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.active = {}
        self.clock = time.monotonic

    def begin(self):
        self.active.clear()

    def dispatch(self, runner, request):
        from .guidance_values import recording
        task = runner.task
        if (task is None or task.runner is not runner or task.guidance_wait is not request
                or request.owner is not self.scheduler or request.epoch != self.scheduler.epoch):
            raise LanguageError('Invalid native guidance wait', code='ownership_error')
        self.scheduler._recording_access(task)
        recording(self.scheduler.evaluator, task)
        request.deadline = None if request.timeout is None else self.clock() + request.timeout
        self.active[task] = request
        runner.waiting, runner.state = request, 'guidance_wait'
        self._check(task, self.clock())

    def cleanup(self, task):
        request = getattr(task, 'guidance_wait', None)
        if request is None:
            return
        self.active.pop(task, None)
        request.deadline = None
        task.runner.waiting = None

    def complete(self, task, result=None, error=None):
        from .suspension import TERMINAL
        if task.state in TERMINAL:
            return
        task.runner.iterator.close()
        state = 'cancelled' if isinstance(error, ExecutionControl) else ('failed' if error is not None else 'completed')
        self.scheduler._finish_runner(task.runner, state, result, error, notify=False)

    def cancel(self, task, error):
        self.complete(task, error=error)
        self.scheduler._flush_terminals()

    def _check(self, task, now):
        request = self.active.get(task)
        if request is None:
            return
        scheduler = self.scheduler
        if task.cancel_event.is_set():
            self.complete(task, error=scheduler.cancelled_error(task))
            return
        if task.interrupt is not None:
            self.complete(task, error=task.interrupt)
            return
        try:
            from .guidance_values import response, data
            # Polling also occurs during implicit resource finalization, where
            # the scheduler's ordinary idle checkpoint is intentionally absent.
            # Check only the global budget; a different restored Runner's local
            # cancellation must never become this registration's failure.
            current = scheduler.current
            try:
                scheduler.current = None
                scheduler.evaluator.tick()
            finally:
                scheduler.current = current
            handler = scheduler.evaluator.guidance_handler
            if handler is None:
                raise LanguageError('Current run has no guidance host', code='capability_error')
            # Host input is copied data. Authority belongs to this registration,
            # never to whichever different Runner was most recently restored.
            result = response(handler('receive', {}, task.capabilities,
                                      data(task.team_scope) if task.team_scope is not None else None))
        except (LanguageError, ExecutionControl) as error:
            self.complete(task, error=error)
            return
        except Exception as error:
            self.complete(task, error=LanguageError(str(error), code='effect_error'))
            return
        except BaseException as error:
            self.complete(task, error=error)
            return
        if result['received'] or result['closed']:
            self.complete(task, result)
        elif request.deadline is not None and now >= request.deadline:
            self.complete(task, {'received': False, 'message': None, 'closed': False})

    def poll(self):
        scheduler = self.scheduler
        notifying = scheduler.notifying_terminals
        scheduler.notifying_terminals = True
        try:
            now = self.clock()
            for task in tuple(self.active):
                self._check(task, now)
        finally:
            scheduler.notifying_terminals = notifying
        scheduler._flush_terminals()

    def delay(self):
        if not self.active:
            return None
        deadlines = [request.deadline for request in self.active.values() if request.deadline is not None]
        return min(.01, max(0.0, min(deadlines) - self.clock())) if deadlines else .01

    def finish(self):
        for task in tuple(self.active):
            self.cleanup(task)
        self.active.clear()
