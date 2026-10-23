"""Cold current-run guidance and explicit public progress operations."""
import json
from .language import LanguageError
from .extensions import json_copy

KINDS = ('progress', 'plan', 'result')


def data(value, max_chars=40000, max_nodes=10000, max_depth=64):
    try:
        copied = json_copy(value, max_chars=max_chars, max_nodes=max_nodes, max_depth=max_depth)
        if len(json.dumps(copied, ensure_ascii=False, allow_nan=False, separators=(',', ':'))) > max_chars:
            raise ValueError('Serialized JSON exceeds ' + str(max_chars) + ' characters')
        return copied
    except (ValueError, TypeError, OverflowError) as error:
        raise LanguageError('Guidance/progress needs bounded JSON: ' + str(error), code='argument_error') from None


def response(value):
    value = data(value, 50000)
    if (type(value) is not dict or set(value) != {'received', 'message', 'closed'}
            or type(value['received']) is not bool or type(value['closed']) is not bool):
        raise LanguageError('Invalid guidance receive response', code='guidance_error')
    message = value['message']
    if value['received']:
        if (type(message) is not dict or set(message) not in ({'runId', 'messageId', 'revision', 'input', 'status'}, {'runId', 'messageId', 'revision', 'input', 'status', 'target'})
                or type(message['runId']) is not str or not message['runId']
                or type(message['messageId']) is not str or not 1 <= len(message['messageId']) <= 128
                or type(message['revision']) is not int or message['revision'] < 1
                or message['status'] != 'received'):
            raise LanguageError('Invalid received guidance message', code='guidance_error')
        if 'target' in message:
            from .guidance_targets import validate_target
            try:validate_target(message['target'])
            except ValueError as error:raise LanguageError(str(error),code='guidance_error') from None
    elif message is not None:
        raise LanguageError('Empty guidance response must have null message', code='guidance_error')
    return value


def progress_response(value, kind):
    value = data(value)
    if (type(value) is not dict or set(value) != {'runId', 'kind', 'eventId'}
            or type(value['runId']) is not str or not value['runId'] or value['kind'] != kind
            or type(value['eventId']) is not int or value['eventId'] < 1):
        raise LanguageError('Invalid progress receipt', code='guidance_error')
    return value


def recording(evaluator, task):
    if task.recording_scope is not None:
        raise LanguageError('Guidance/progress cannot run inside a Skill recording', code='mode_error')


def progress(evaluator, kind, payload):
    from .suspension import Effect
    if type(kind) is not str or kind not in KINDS:
        raise LanguageError('progress kind must be progress, plan or result', code='argument_error')
    copied = data(payload, 8000, 2048, 32)
    packet = {'kind': kind, 'data': copied}
    scheduler = evaluator.suspension
    if not scheduler.active:
        raise LanguageError('Guidance/progress needs an active execution epoch', code='ownership_error')
    def operation():
        task = scheduler.current.task
        recording(evaluator, task)
        if evaluator.guidance_handler is None:
            raise LanguageError('Current run has no guidance host', code='capability_error')
        result = yield Effect('guidance', {'operation': 'progress', 'payload': packet})
        return progress_response(result, kind)
    task = scheduler.task(operation(), kind='progress', capture_roots=(packet,))
    task.guidance_operation = True
    return task


def builtins(evaluator):
    from .guidance_waits import receive
    return {'guidance_receive': lambda timeout=0: receive(evaluator, timeout),
            'progress_emit': lambda kind, payload: progress(evaluator, kind, payload)}
