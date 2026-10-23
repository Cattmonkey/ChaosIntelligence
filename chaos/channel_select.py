"""One native choice commits one channel operation; cases are reusable values."""
from dataclasses import dataclass

from .language import LanguageError, ResourceLimit

_CURRENT = object()


class ChannelCase:
    def __init__(self, scheduler, channel, op, message=None):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.id = 'case-' + str(scheduler.next_id())
        self.channel, self.op, self.message = channel, op, message
        self.recording_scope = channel.recording_scope
        self.location = dict(scheduler.evaluator.current_span)


def owned(value, evaluator, *, scope=_CURRENT):
    scheduler = evaluator.suspension
    if type(value) is not ChannelCase:
        raise LanguageError('需要 ChannelCase', code='argument_error')
    if value.owner is not scheduler or value.epoch != scheduler.epoch or not scheduler.active:
        raise LanguageError('ChannelCase 不能跨执行上下文或提交周期', code='ownership_error')
    if scope is _CURRENT:
        current = scheduler.current.task if scheduler.current else None
        scope = current.recording_scope if current else None
    if value.recording_scope != scope:
        raise LanguageError('ChannelCase 不能跨录制 scope', code='mode_error')
    return value


def describe(value):
    return {'$handle':'ChannelCase', 'id':value.id, 'op':value.op, 'channelId':value.channel.id}


def case_info(evaluator, value):
    value = owned(value, evaluator)
    return {'id':value.id, 'op':value.op, 'channelId':value.channel.id, 'location':dict(value.location)}


def _case(evaluator, channel, op, value=None):
    from .channels import owned as channel_owned, _message
    channel = channel_owned(channel, evaluator)
    scheduler = evaluator.suspension
    if len(scheduler.channel_cases) >= 10000:
        raise ResourceLimit('ChannelCase 数量超过 10000')
    message = _message(evaluator, value) if op == 'send' else None
    case = ChannelCase(scheduler, channel, op, message)
    scheduler.channel_cases.append(case)
    return case


def send_case(evaluator, channel, value): return _case(evaluator, channel, 'send', value)
def receive_case(evaluator, channel): return _case(evaluator, channel, 'receive')


@dataclass(eq=False)
class Offer:
    task: object
    index: int
    channel: object
    op: str
    message: object = None

    @property
    def id(self): return self.task.id


class Selection:
    def __init__(self, cases, messages, timeout):
        self.cases, self.messages, self.timeout = cases, messages, timeout
        self.task, self.deadline, self.index = None, None, None
        self.offers, self.pending, self.reserved = [], False, True


def selection_info(task):
    selection = task.selection
    if selection is None: return None
    return {'caseCount':len(selection.cases),
            'cases':[{'index':i, 'op':case.op, 'channelId':case.channel.id}
                     for i,case in enumerate(selection.cases[:100])],
            'casesTruncated':len(selection.cases)>100, 'index':selection.index}


def select(evaluator, cases, timeout=None):
    from .channels import _message, validate_payload
    from .task_waits import _seconds
    scheduler = evaluator.suspension
    if type(cases) is not list or not cases:
        raise LanguageError('channel_select 需要非空 ChannelCase 列表', code='argument_error')
    if len(cases)>10000 or scheduler.select_registrations+len(cases)>10000:
        raise ResourceLimit('channel_select 未完成分支总数超过 10000')
    timeout = None if timeout is None else _seconds(timeout, 'channel_select timeout')
    cases = tuple(cases)
    for case in cases:
        owned(case, evaluator)
        if case.message is not None: validate_payload(evaluator, case.message.value)
    messages = {}
    try:
        for i,case in enumerate(cases):
            if case.op == 'send': messages[i] = _message(evaluator, case.message.value)
    except BaseException:
        for message in messages.values(): message.release()
        raise
    request = Selection(cases, messages, timeout)
    scheduler.select_registrations += len(cases)
    def operation(): return (yield request)
    try:
        return scheduler.task(operation(), kind='channel_select', selection=request)
    except BaseException:
        if request.task is None: _detach(scheduler, request)
        raise


def _detach(scheduler, selection, winning_send=None):
    selection.pending = False
    for offer in selection.offers:
        offer.channel.senders.pop(offer, None)
        offer.channel.receivers.pop(offer, None)
        from .channels import _dirty
        _dirty(offer.channel)
    selection.offers.clear()
    for i,message in selection.messages.items():
        if i != winning_send: message.release()
    selection.messages.clear()
    if selection.reserved:
        scheduler.select_registrations -= len(selection.cases)
        selection.reserved = False
    if selection.task is not None: scheduler.task_waits.disarm(selection.task)
    selection.deadline = None


def prepare(task, index=None, result=None, error=None, timed_out=False):
    from .suspension import TERMINAL, TaskCancelled
    if task.state in TERMINAL: return
    selection = task.selection
    selection.index = index
    op = selection.cases[index].op if index is not None else None
    winning_send = index if op == 'send' and error is None else None
    _detach(task.owner, selection, winning_send)
    task.runner.waiting = None
    task.runner.iterator.close()
    task.state = task.runner.state = 'cancelled' if isinstance(error, TaskCancelled) else ('failed' if error else 'completed')
    task.runner.error = error
    task.runner.result = None if error else {'index':index, 'op':op,
        'done':result['done'] if op == 'receive' else False,
        'value':result['value'] if op == 'receive' else None, 'timedOut':timed_out}


def cleanup(task):
    if task.selection is not None: _detach(task.owner, task.selection)


def cancel(scheduler, task, error):
    from .channels import _notify, _pump_many
    prepare(task, error=error)
    notifying = scheduler.notifying_terminals
    scheduler.notifying_terminals = True
    try:
        _notify(scheduler, task)
        _pump_many(scheduler, ())
    finally: scheduler.notifying_terminals = notifying
    scheduler._flush_terminals()


def timeout(scheduler, task):
    from .channels import _notify, _pump_many
    if not task.selection.pending: return
    prepare(task, timed_out=True)
    notifying = scheduler.notifying_terminals
    scheduler.notifying_terminals = True
    try:
        _notify(scheduler, task)
        _pump_many(scheduler, ())
    finally: scheduler.notifying_terminals = notifying
    scheduler._flush_terminals()


def dispatch(scheduler, runner, request):
    from .channels import owned as channel_owned, validate_payload, _initial_offer, _commit_action, _dirty, _pump_many
    task = runner.task
    if task is None or task.runner is not runner or task.selection is not request:
        raise LanguageError('无效的 Channel select 请求', code='argument_error')
    scheduler._recording_access(task)
    # Every case is checked before any queue registration or payload delivery.
    for i,case in enumerate(request.cases):
        owned(case, scheduler.evaluator)
        channel_owned(case.channel, scheduler.evaluator)
        if i in request.messages: validate_payload(scheduler.evaluator, request.messages[i].value)
    request.pending = True
    runner.waiting, runner.state = request, 'channel_select'
    for i,case in enumerate(request.cases):
        offer = Offer(task, i, case.channel, case.op, request.messages.get(i))
        request.offers.append(offer)
        if case.op == 'send': case.channel.senders[offer] = offer.message
        else: case.channel.receivers[offer] = None
        _dirty(case.channel)
    if request.timeout is not None:
        request.deadline = scheduler.task_waits.arm(task, request.timeout)
    notifying = scheduler.notifying_terminals
    scheduler.notifying_terminals = True
    try:
        # Initial readiness has explicit input-order bias. A rendezvous claims
        # its pending peer's corresponding offer in this same native commit.
        seen = set()
        for offer in tuple(request.offers):
            # Later cases in the same queue direction cannot overtake this
            # choice's first offer. Probe each channel/direction once, keeping
            # initial readiness linear even for 10000 duplicate alternatives.
            key = (offer.channel, offer.op)
            if key in seen: continue
            seen.add(key)
            action = _initial_offer(offer)
            if action is not None:
                _commit_action(offer.channel, action)
                break
        if request.pending and request.timeout == 0:
            timeout(scheduler, task)
        _pump_many(scheduler, ())
    finally: scheduler.notifying_terminals = notifying
    scheduler._flush_terminals()


def finish(scheduler):
    for case in scheduler.channel_cases:
        if case.message is not None: case.message.release()
    for task in scheduler.tasks: cleanup(task)
    scheduler.channel_dirty.clear(); scheduler.channel_dirty_set.clear()
