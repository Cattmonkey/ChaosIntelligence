"""Lexical task-local cancellation deferral; hard run control always bypasses it."""
from .language import Env, LanguageError, ResourceLimit
from .suspension import error_priority


class CancelShield:
    def __init__(self, scheduler):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.id = 'shield-' + str(scheduler.next_id())
        self.state, self.location = 'new', dict(scheduler.evaluator.current_span)
        current = scheduler.current
        self.capabilities = current.capabilities if current else None
        self.recording_scope = current.task.recording_scope if current and current.task else None
        self.task, self.stream, self.depth = None, None, 0
        self.requested = False


def requested(task):
    return bool(task.cancel_event.is_set() or task.cancel_sources or task.deferred_interrupt is not None)


def deferred(task):
    return bool(task.cancel_shields and not task.cancel_event.is_set() and requested(task))


def create(evaluator):
    scheduler = evaluator.suspension
    if not scheduler.active:
        raise LanguageError('CancelShield 需要活动执行周期', code='ownership_error')
    if len(scheduler.cancel_shields) >= 10000:
        raise ResourceLimit('CancelShield 数量超过 10000')
    value = CancelShield(scheduler)
    scheduler.cancel_shields.append(value)
    scheduler.event('shield.created', value)
    return value


def owned(value, evaluator):
    scheduler = evaluator.suspension
    if type(value) is not CancelShield:
        raise LanguageError('需要 CancelShield', code='argument_error')
    if value.owner is not scheduler or value.epoch != scheduler.epoch or not scheduler.active:
        raise LanguageError('CancelShield 不能跨解释器或执行周期使用', code='ownership_error')
    from .agent_tools import access
    access(value, evaluator)
    task = scheduler.current.task if scheduler.current else None
    if value.recording_scope != (task.recording_scope if task else None):
        raise LanguageError('CancelShield 不能跨录制 scope 使用', code='mode_error')
    return value


def describe(value):
    return {'$handle':'CancelShield', 'id':value.id, 'state':value.state}


def snapshot_info(value):
    task = value.task
    active = value.state == 'active'
    return {'id':value.id, 'state':value.state, 'taskId':task.id if task else None,
            'location':dict(value.location), 'depth':value.depth if active else 0,
            'cancellationRequested':requested(task) if active else value.requested,
            'cancellationDeferred':deferred(task) if active else False}


def info(evaluator, value):
    return snapshot_info(owned(value, evaluator))


def check_yield(scheduler, stream):
    task = scheduler.current.task if scheduler.current else None
    if task is not None and any(value.stream is stream for value in task.cancel_shields):
        raise LanguageError('CancelShield 作用域不能跨越 yield', code='cancel_shield_error')

