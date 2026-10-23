"""Epoch-owned FIFO channels, driven solely by the AST scheduler.

Communication commits finish the native operation Task in the same scheduler
turn. Cancellation can remove a pending operation; it cannot retract a delivery.
No user iterator, descriptor, callback or worker runs during queue maintenance.
"""
from .tuple_values import TupleValue
from .set_values import SetValue
from collections import deque, OrderedDict
from dataclasses import dataclass
from itertools import islice

from .language import LanguageError, ResourceLimit


class Channel:
    def __init__(self, scheduler, capacity):
        self.owner, self.epoch = scheduler, scheduler.epoch
        self.id = 'channel-' + str(scheduler.next_id())
        self.location = dict(scheduler.evaluator.current_span)
        self.capacity, self.state = capacity, 'open'
        current = scheduler.current.task if scheduler.current else None
        self.recording_scope = current.recording_scope if current else None
        self.buffer, self.senders, self.receivers = deque(), OrderedDict(), OrderedDict()


@dataclass
class Send:
    channel: object
    message: object


@dataclass
class Receive:
    channel: object


class Message:
    def __init__(self, scheduler, value, nodes, chars):
        self.scheduler, self.value = scheduler, value
        self.nodes, self.chars, self.released = nodes, chars, False

    def release(self):
        if self.released: return
        self.released = True
        self.scheduler.channel_slots -= 1
        self.scheduler.channel_nodes -= self.nodes
        self.scheduler.channel_chars -= self.chars
        self.value = None


_CURRENT = object()


def owned(value, evaluator, *, scope=_CURRENT):
    scheduler = evaluator.suspension
    if type(value) is not Channel: raise LanguageError('需要 Channel', code='argument_error')
    if value.owner is not scheduler or value.epoch != scheduler.epoch or not scheduler.active:
        raise LanguageError('Channel 不能跨执行上下文或提交周期使用', code='ownership_error')
    if scope is _CURRENT:
        current = scheduler.current.task if scheduler.current else None
        scope = current.recording_scope if current else None
    if value.recording_scope != scope:
        raise LanguageError('Channel 不能跨录制 scope 使用', code='mode_error')
    return value


def describe(channel):
    return {'$handle':'Channel', 'id':channel.id, 'state':channel.state}


def snapshot_info(channel):
    return {'id':channel.id, 'state':channel.state, 'capacity':channel.capacity,
            'buffered':len(channel.buffer), 'waitingSenders':len(channel.senders),
            'waitingReceivers':len(channel.receivers),
            'sendTaskIds':[task.id for task in islice(channel.senders, 100)],
            'receiveTaskIds':[task.id for task in islice(channel.receivers, 100)],
            'sendersTruncated':len(channel.senders)>100,
            'receiversTruncated':len(channel.receivers)>100,
            'location':dict(channel.location)}


def info(evaluator, channel):
    return snapshot_info(owned(channel, evaluator))


def create(evaluator, capacity=0):
    scheduler = evaluator.suspension
    if type(capacity) is not int or capacity < 0:
        raise LanguageError('Channel capacity 必须是非负 Int', code='argument_error')
    if capacity > 10000: raise ResourceLimit('Channel capacity 超过 10000')
    if not scheduler.active: raise LanguageError('Channel 需要活动执行周期', code='ownership_error')
    if len(scheduler.channels) >= 10000: raise ResourceLimit('Channel 数量超过 10000')
    channel = Channel(scheduler, capacity)
    scheduler.channels.append(channel)
    scheduler.event('channel.created', channel)
    return channel


def validate_payload(evaluator, value, *, scope=_CURRENT):
    """Revalidate live ownership without copying, getters, or reservation."""
    from .language import Function, Native, Agent
    from .objects import OBJECT_VALUES, owned as object_owned
    from .suspension import Task, Generator
    from .iteration import HANDLES
    from .task_groups import TaskGroup, owned as group_owned
    from .cancellation import CancelShield
    from .computer_values import VALUES as COMPUTER_VALUES, owned as computer_owned
    from .session_values import AgentSession, owned as session_owned
    from .process_values import Process, owned as process_owned
    from .program_values import Program, owned as program_owned
    from .team_values import Team, owned as team_owned
    from .modes import ModeValue, owned as mode_owned
    scheduler = evaluator.suspension
    pending, nodes, chars = [value], 0, 0
    if scope is _CURRENT:
        current = scheduler.current.task if scheduler.current else None
        scope = current.recording_scope if current else None
    from .channel_select import ChannelCase, owned as case_owned
    while pending:
        item = pending.pop(); nodes += 1
        if isinstance(item, str): chars += len(item)
        elif type(item) is SetValue:
            from .set_runtime import owned as set_owned
            set_owned(item,evaluator);pending.extend(item)
        elif isinstance(item, (list, TupleValue)): pending.extend(item)
        elif isinstance(item, dict):
            nodes += len(item)
            chars += sum(len(key) for key in item)
            pending.extend(item.values())
        elif isinstance(item, (Function, Native, Agent)):
            if item.owner not in (None, evaluator):
                raise LanguageError('Channel 消息 callable 不能跨执行上下文', code='ownership_error')
        elif isinstance(item, OBJECT_VALUES): object_owned(item, evaluator)
        elif isinstance(item, (Task, Generator) + HANDLES):
            if item.owner is not scheduler or item.epoch != scheduler.epoch or not scheduler.active:
                raise LanguageError('Channel 消息句柄不能跨执行上下文或周期', code='ownership_error')
            if item.recording_scope != scope:
                raise LanguageError('Channel 消息不能跨录制 scope', code='mode_error')
        elif type(item) is Channel: owned(item, evaluator, scope=scope)
        elif type(item) is ChannelCase: case_owned(item, evaluator, scope=scope)
        elif type(item) is CancelShield:
            if item.owner is not scheduler or item.epoch != scheduler.epoch or not scheduler.active:
                raise LanguageError('Channel 消息 CancelShield 不能跨执行上下文或周期', code='ownership_error')
            if item.recording_scope != scope:
                raise LanguageError('Channel 消息 CancelShield 不能跨录制 scope', code='mode_error')
            from .agent_tools import access
            access(item, evaluator)
        elif type(item) is TaskGroup:
            if item.owner is not scheduler or item.epoch != scheduler.epoch or not scheduler.active:
                raise LanguageError('Channel 消息 TaskGroup 不能跨执行上下文或周期', code='ownership_error')
            if item.recording_scope != scope:
                raise LanguageError('Channel 消息 TaskGroup 不能跨录制 scope', code='mode_error')
        elif type(item) in COMPUTER_VALUES: computer_owned(item, evaluator)
        elif type(item) is AgentSession: session_owned(item, evaluator)
        elif type(item) is Process: process_owned(item, evaluator)
        elif type(item) is Program: program_owned(item, evaluator)
        elif type(item) is Team: team_owned(item, evaluator)
        elif isinstance(item, ModeValue): mode_owned(item, evaluator)
    return nodes, chars


def _message(evaluator, value):
    scheduler = evaluator.suspension
    copied = evaluator.copy_value(value)
    nodes, chars = validate_payload(evaluator, copied)
    if (scheduler.channel_slots >= 10000 or scheduler.channel_nodes + nodes > 100000 or
            scheduler.channel_chars + chars > 1000000):
        raise ResourceLimit('Channel 未交付消息超过共享资源限制 (10000 slots/100000 nodes/1000000 chars)')
    scheduler.channel_slots += 1; scheduler.channel_nodes += nodes; scheduler.channel_chars += chars
    return Message(scheduler, copied, nodes, chars)


def send(evaluator, channel, value):
    channel = owned(channel, evaluator)
    message = _message(evaluator, value)
    def operation(): return (yield Send(channel, message))
    try:
        task = evaluator.suspension.task(operation(), kind='channel_send', channel=channel)
        task.channel_message = message
        return task
    except BaseException:
        message.release()
        raise


def receive(evaluator, channel, *, cursor=None, locked=False):
    channel = owned(channel, evaluator)
    def operation(): return (yield Receive(channel))
    task = evaluator.suspension.task(operation(), kind='channel_receive', channel=channel)
    task.channel_cursor, task.channel_locked = cursor, locked
    return task


def _prepare(task, result=None, error=None):
    """Remove and commit before terminal callbacks may cancel another member."""
    channel = task.channel
    channel.senders.pop(task, None); channel.receivers.pop(task, None)
    _dirty(channel)
    task.channel_message = None
    task.runner.waiting = None
    cursor = task.channel_cursor
    if cursor is not None and getattr(cursor, 'channel_operation', None) is task:
        cursor.channel_operation = None
        if not task.channel_locked: cursor.busy = False
        cursor.state = ('suspended' if error else ('completed' if result['done'] else 'suspended'))
        if result and result['done']: cursor.result = result['value']
    # These are private single-yield native iterators, never user code.
    task.runner.iterator.close()
    task.runner.state = task.state = 'failed' if error else 'completed'
    task.runner.result, task.runner.error = result, error


def _notify_batch(scheduler, tasks):
    from .suspension import TaskCancelled
    for task in tasks:
        if task is None: continue
        state = 'cancelled' if isinstance(task.runner.error, TaskCancelled) else task.state
        scheduler._finish_runner(task.runner, state, task.runner.result, task.runner.error, notify=False)
    scheduler._flush_terminals()


def _notify(scheduler, task):
    _notify_batch(scheduler, (task,))


def _complete(scheduler, task, result=None, error=None):
    _prepare(task, result, error)
    _notify(scheduler, task)


def _owner(entry):
    from .channel_select import Offer
    return entry.task if isinstance(entry, Offer) else entry


def _prepare_entry(entry, result=None, error=None):
    from .channel_select import Offer, prepare
    if isinstance(entry, Offer):
        prepare(entry.task, entry.index, result, error)
        return entry.task
    _prepare(entry, result, error)
    return entry


def _pair(channel):
    """Oldest compatible offers; a selector cannot rendezvous with itself."""
    if not channel.senders or not channel.receivers: return None
    sender, receiver = next(iter(channel.senders)), next(iter(channel.receivers))
    if _owner(sender) is not _owner(receiver): return sender, receiver
    # The head selection may offer both directions. Bypass only its own
    # incompatible offers, never an older compatible independent participant.
    for other in channel.receivers:
        if _owner(other) is not _owner(sender): return sender, other
    for other in channel.senders:
        if _owner(other) is not _owner(receiver): return other, receiver
    return None


def _pair_for(entry, channel):
    own = channel.senders if entry in channel.senders else channel.receivers
    other = channel.receivers if entry in channel.senders else channel.senders
    for counterpart in other:
        if _owner(counterpart) is _owner(entry): continue
        for earlier in own:
            if earlier is entry: break
            if _owner(earlier) is not _owner(counterpart): return None
        return (entry, counterpart) if entry in channel.senders else (counterpart, entry)
    return None


def _initial_offer(offer):
    channel = offer.channel
    if offer.op == 'send' and channel.state == 'closed': return ('error', offer)
    if offer.op == 'receive' and channel.receivers:
        if next(iter(channel.receivers)) is offer:
            if channel.buffer: return ('buffer', offer)
            if channel.state == 'closed': return ('eof', offer)
    pair = _pair_for(offer, channel) if channel.state == 'open' and not channel.buffer else None
    if pair is not None: return ('pair', *pair)
    if (offer.op == 'send' and channel.state == 'open' and channel.senders and
            next(iter(channel.senders)) is offer and len(channel.buffer)<channel.capacity):
        return ('store', offer)
    return None


def _commit_action(channel, action):
    scheduler = channel.owner
    kind, entry = action[0], action[1]
    if kind == 'error':
        message = channel.senders[entry]; message.release()
        task = _prepare_entry(entry, error=LanguageError('Channel 已关闭', code='channel_closed'))
        _notify(scheduler, task)
        return
    if kind == 'eof':
        _notify(scheduler, _prepare_entry(entry, {'done':True, 'value':None}))
        return
    if kind == 'store':
        message = channel.senders[entry]
        try: validate_payload(scheduler.evaluator, message.value, scope=channel.recording_scope)
        except LanguageError as error:
            message.release(); _notify(scheduler, _prepare_entry(entry, error=error)); return
        channel.buffer.append(message)
        _notify(scheduler, _prepare_entry(entry))
        return
    sender, receiver = (entry, action[2]) if kind == 'pair' else (None, entry)
    message = channel.senders[sender] if sender is not None else channel.buffer.popleft()
    # All compatibility and payload checks precede either token's commit. Both
    # choices lose their other offers before any observer can execute.
    try: validate_payload(scheduler.evaluator, message.value, scope=channel.recording_scope)
    except LanguageError as error:
        message.release()
        sending = _prepare_entry(sender, error=error) if sender is not None else None
        receiving = _prepare_entry(receiver, error=error)
    else:
        result = {'done':False, 'value':message.value}
        message.release()
        sending = _prepare_entry(sender) if sender is not None else None
        receiving = _prepare_entry(receiver, result)
    _notify_batch(scheduler, (sending, receiving))


def _dirty(channel):
    scheduler = channel.owner
    if channel not in scheduler.channel_dirty_set:
        scheduler.channel_dirty_set.add(channel)
        scheduler.channel_dirty.append(channel)


def _pump_many(scheduler, channels):
    for channel in channels: _dirty(channel)
    if scheduler.channel_pumping: return
    notifying = scheduler.notifying_terminals
    scheduler.notifying_terminals, scheduler.channel_pumping = True, True
    try:
        while scheduler.channel_dirty:
            channel = scheduler.channel_dirty.popleft()
            scheduler.channel_dirty_set.discard(channel)
            _pump_commits(channel)
    finally:
        scheduler.notifying_terminals, scheduler.channel_pumping = notifying, False
    scheduler._flush_terminals()


def _pump(channel): _pump_many(channel.owner, (channel,))


def _pump_commits(channel):
    while True:
        if channel.state == 'closed' and channel.senders:
            action = ('error', next(iter(channel.senders)))
        elif channel.receivers and channel.buffer:
            action = ('buffer', next(iter(channel.receivers)))
        else:
            pair = _pair(channel) if channel.state == 'open' else None
            if pair is not None: action = ('pair', *pair)
            elif channel.state == 'open' and channel.senders and len(channel.buffer)<channel.capacity:
                action = ('store', next(iter(channel.senders)))
            elif channel.state == 'closed' and channel.receivers and not channel.buffer:
                action = ('eof', next(iter(channel.receivers)))
            else: return
        _commit_action(channel, action)


def dispatch(scheduler, runner, request):
    task = runner.task
    if task is None or task.runner is not runner or task.channel is not request.channel:
        raise LanguageError('Channel 挂起请求需要原生操作 Task')
    channel = owned(request.channel, scheduler.evaluator)
    if isinstance(request, Send): validate_payload(scheduler.evaluator, request.message.value)
    cursor = task.channel_cursor
    if cursor is not None:
        scheduler.validate(cursor, type(cursor))
        if cursor.busy and not task.channel_locked:
            raise LanguageError('迭代器不能重入', code='argument_error')
        if cursor.state in ('completed', 'cancelled', 'failed', 'closed'):
            _complete(scheduler, task, {'done':True, 'value':cursor.result}); return
        cursor.busy, cursor.state, cursor.channel_operation = True, 'running', task
    runner.waiting, runner.state = request, task.kind
    if isinstance(request, Send):
        if channel.state == 'closed':
            request.message.release()
            _complete(scheduler, task, error=LanguageError('Channel 已关闭', code='channel_closed'))
            return
        channel.senders[task] = request.message
    else: channel.receivers[task] = None
    _pump(channel)


def cancel_task(scheduler, task, error):
    message = task.channel_message
    if message is not None: message.release()
    notifying = scheduler.notifying_terminals
    scheduler.notifying_terminals = True
    try:
        _complete(scheduler, task, error=error)
        _pump_many(scheduler, ())
    finally: scheduler.notifying_terminals = notifying
    scheduler._flush_terminals()


def cleanup_task(task):
    # Binding/access errors can happen before dispatch. Release eager captures.
    if task.selection is not None:
        from .channel_select import cleanup
        cleanup(task)
    if task.channel is None: return
    task.channel.senders.pop(task, None); task.channel.receivers.pop(task, None)
    if task.channel_message is not None:
        task.channel_message.release(); task.channel_message = None


def close(evaluator, channel):
    channel = owned(channel, evaluator)
    if channel.state == 'closed': return False
    channel.state = 'closed'
    scheduler = channel.owner
    notifying = scheduler.notifying_terminals
    # Close is one native commit: settle all pending operations before observer
    # callbacks can fail. This includes buffered deliveries and every EOF waiter.
    scheduler.notifying_terminals = True
    try:
        _pump(channel)
    finally:
        scheduler.notifying_terminals = notifying
    scheduler._flush_terminals()
    scheduler.event('channel.closed', channel)
    return True


def finish(scheduler):
    for channel in scheduler.channels:
        for message in channel.buffer: message.release()
        channel.buffer.clear(); channel.senders.clear(); channel.receivers.clear()
