import collections
import threading
import unittest

from chaos.language import Evaluator, LanguageError


class Mailbox:
    def __init__(self, inputs=()):
        self.pending = collections.deque(inputs)
        self.revision = 0
        self.events = []
        self.received = []
        self.calls = []
        self.closed = False
        self.lock = threading.Lock()

    def handle(self, operation, payload, capabilities, team_scope):
        with self.lock:
            self.calls.append((operation, capabilities, team_scope))
            if operation == 'receive':
                if not self.pending:
                    return {'received': False, 'message': None, 'closed': self.closed}
                self.revision += 1
                item = self.pending.popleft()
                self.received.append(item)
                return {'received': True, 'closed': False, 'message': {
                    'runId': 'test-run', 'messageId': 'guide-' + str(self.revision),
                    'revision': self.revision, 'input': item, 'status': 'received'}}
            if operation == 'progress':
                self.events.append(payload)
                return {'runId': 'test-run', 'kind': payload['kind'], 'eventId': len(self.events)}
            raise AssertionError(operation)


class GuidanceNative(unittest.TestCase):
    def test_both_operations_are_cold_until_await(self):
        box = Mailbox([42])
        self.assertEqual(Evaluator(guidance_handler=box.handle).run(
            '[type_of(guidance_receive()),type_of(progress_emit("plan",{summary:"later"}))]'), ['Task', 'Task'])
        self.assertEqual(box.calls, [])

    def test_fifo_null_is_distinct_from_timeout(self):
        box = Mailbox([None, 42])
        result = Evaluator(guidance_handler=box.handle).run(
            '[await guidance_receive(),await guidance_receive(),await guidance_receive()]')
        self.assertEqual([item['received'] for item in result], [True, True, False])
        self.assertEqual([item['message']['input'] for item in result[:2]], [None, 42])
        self.assertEqual([item['message']['revision'] for item in result[:2]], [1, 2])
        self.assertEqual(result[2], {'received': False, 'message': None, 'closed': False})

    def test_invalid_arguments_have_no_host_actions(self):
        box = Mailbox([42])
        for source in ('guidance_receive(-1)', 'guidance_receive(true)',
                       'progress_emit("thinking",null)', 'progress_emit("result",fn(){42})'):
            with self.subTest(source=source), self.assertRaises(LanguageError) as caught:
                Evaluator(guidance_handler=box.handle).run(source)
            self.assertEqual(caught.exception.code, 'argument_error')
        self.assertEqual(box.calls, [])

    def test_progress_snapshot_and_host_receipt(self):
        box = Mailbox()
        result = Evaluator(guidance_handler=box.handle).run(
            'var items=[1];let t=progress_emit("result",items);items=append(items,2);await t')
        self.assertEqual(box.events, [{'kind': 'result', 'data': [1]}])
        self.assertEqual(result, {'runId': 'test-run', 'kind': 'result', 'eventId': 1})

    def test_missing_host_and_invalid_receive_protocol_fail_at_await(self):
        self.assertEqual(Evaluator().run('try{await guidance_receive()}catch e{e.code}'), 'capability_error')
        handler = lambda *args: {'received': False, 'message': 42, 'closed': False}
        self.assertEqual(Evaluator(guidance_handler=handler).run(
            'try{await guidance_receive()}catch e{e.code}'), 'guidance_error')
