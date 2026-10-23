"""Independent Channel cancellation tests with causal host-effect handshakes."""
import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled, TERMINAL


class ChannelResourceTests(unittest.TestCase):
    def evaluator(self, effect=None, **options):
        if effect is None:
            return Evaluator(timeout=8, **options)
        return Evaluator(timeout=8, effect_handler=effect,
                         tool_handler=lambda name, value: effect(
                             'tool', {'name': name, 'value': value}, threading.Event()), **options)

    def assert_quiescent(self, evaluator):
        self.assertFalse(evaluator.suspension.active)
        self.assertTrue(all(task.state in TERMINAL for task in evaluator.suspension.tasks))
        self.assertFalse([thread.name for thread in threading.enumerate()
                          if thread.name.startswith('chaos-effect') and thread.is_alive()])

    def member_failure(self, sending):
        failed = threading.Event()

        def effect(kind, payload, cancel):
            failed.set()
            raise LanguageError('causal member failure', code='channel_fixture')

        operation = 'channel_send(c,99)' if sending else 'channel_receive(c)'
        field = 'waitingSenders' if sending else 'waitingReceivers'
        source = '''let c=channel();
async fn bad(){while channel_info(c).FIELD!=1{1};tool("fail",null)}
var pending=null;let code=try{async with task_group() as g{
 pending=g.spawn(OP);g.spawn(bad());while true{1}
}}catch e{e.code};
let before=channel_info(c);let receiver=spawn(channel_receive(c));
await channel_send(c,42);let item=await receiver;channel_close(c);
[code,before.state,before.waitingSenders,before.waitingReceivers,
 task_info(pending).state,item,await channel_receive(c)]'''.replace('FIELD', field).replace('OP', operation)
        evaluator = self.evaluator(effect, step_limit=1000000)
        self.assertEqual(evaluator.run(source), ['channel_fixture', 'open', 0, 0, 'cancelled',
                                              {'done': False, 'value': 42}, {'done': True, 'value': None}])
        self.assertTrue(failed.is_set())
        self.assert_quiescent(evaluator)

    def test_group_failure_retracts_send_without_closing_shared_channel(self):
        self.member_failure(True)

    def test_group_failure_retracts_receive_without_losing_later_value(self):
        self.member_failure(False)

    def test_group_interrupt_cancels_implicit_async_for_receive(self):
        source = '''let c=channel();
async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "first"}
let error=try{async with task_group() as g{
 g.spawn(bad());async for item in c{throw "unexpected delivery"}
}}catch e{e.value};
let pending=channel_info(c).waitingReceivers;let next=spawn(channel_receive(c));
await channel_send(c,41);[error,pending,(await next).value,channel_info(c).state]'''
        evaluator = self.evaluator(step_limit=100000)
        self.assertEqual(evaluator.run(source), ['first', 0, 41, 'open'])
        self.assert_quiescent(evaluator)

    def test_cancelled_acollect_retracts_its_internal_receive(self):
        source = '''let c=channel();var collector=null;
async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "first"}
let error=try{async with task_group() as g{
 collector=g.spawn(acollect(c));g.spawn(bad())
}}catch e{e.value};
let pending=channel_info(c).waitingReceivers;let next=spawn(channel_receive(c));
await channel_send(c,42);[error,pending,task_info(collector).state,(await next).value]'''
        evaluator = self.evaluator(step_limit=100000)
        self.assertEqual(evaluator.run(source), ['first', 0, 'cancelled', 42])
        self.assert_quiescent(evaluator)

    def test_interrupted_body_owns_inline_and_same_creator_cold_receive(self):
        for prepare, waiting in (('', 'await channel_receive(c)'),
                                 ('operation=channel_receive(c);', 'await operation')):
            with self.subTest(precreated=bool(prepare)):
                source = '''let c=channel();var operation=null;
async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "first"}
let error=try{async with task_group() as g{
 PREPARE g.spawn(bad());WAITING
}}catch e{e.value};
let before=channel_info(c).waitingReceivers;let next=spawn(channel_receive(c));
await channel_send(c,42);[error,before,(await next).value]'''.replace('PREPARE', prepare).replace('WAITING', waiting)
                evaluator = self.evaluator(step_limit=100000)
                self.assertEqual(evaluator.run(source), ['first', 0, 42])
                self.assert_quiescent(evaluator)

    def test_interrupted_body_does_not_cancel_started_external_receive(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel();let external=spawn(channel_receive(c));
async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "first"}
let error=try{async with task_group() as g{g.spawn(bad());await external}}catch e{e.value};
let before=[channel_info(c).waitingReceivers,task_info(external).cancellationRequested];
await channel_send(c,42);[error,before,(await external).value]''')
        self.assertEqual(result, ['first', [1, False], 42])
        self.assert_quiescent(evaluator)

    def test_reentrant_native_driver_retracts_channel_operations(self):
        entered, ended = threading.Event(), threading.Event()

        def effect(kind, payload, cancel):
            if payload['name'] == 'body':
                entered.set()
                try:
                    self.assertTrue(cancel.wait(3))
                    return 0
                finally:
                    ended.set()
            self.assertTrue(entered.wait(3))
            raise LanguageError('reentrant child failure', code='channel_fixture')

        evaluator = self.evaluator(effect)
        result = evaluator.run('''let c=channel();
fn* items(){tool("body",null);yield 1};let stream=items();
async fn bad(){while channel_info(c).waitingSenders!=1{1};tool("bad",null)}
let error=try{async with task_group() as g{
 g.spawn(channel_send(c,99));g.spawn(bad());map(fn(n){next(stream)},[0])
}}catch e{e.code};let before=channel_info(c).waitingSenders;
let receiver=spawn(channel_receive(c));await channel_send(c,42);
[error,before,(await receiver).value]''')
        self.assertEqual(result, ['channel_fixture', 0, 42])
        self.assertTrue(ended.is_set())
        self.assert_quiescent(evaluator)

    def test_operation_commit_precedes_producer_host_ack_and_late_cancel(self):
        acknowledged = threading.Event()
        payloads = []

        def effect(kind, payload, cancel):
            payloads.append(payload['value'])
            self.assertFalse(cancel.is_set())
            acknowledged.set()
            return None

        source = '''let c=channel();let receiver=spawn(channel_receive(c));
var sender=null;
async fn produce(){sender=channel_send(c,41);await sender;
 tool("ack",[task_info(sender).state,task_info(receiver).state]);42}
let producer=spawn(produce());let result=await producer;
[result,cancel(sender),cancel(receiver),await sender,await receiver]'''
        evaluator = self.evaluator(effect)
        self.assertEqual(evaluator.run(source), [42, False, False, None, {'done': False, 'value': 41}])
        self.assertEqual(payloads, [['completed', 'completed']])
        self.assertTrue(acknowledged.is_set())
        self.assert_quiescent(evaluator)

    def test_channel_close_does_not_cancel_unrelated_producer_host_effect(self):
        entered, release = threading.Event(), threading.Event()
        cancellation = []

        def effect(kind, payload, cancel):
            if payload['name'] == 'producer':
                entered.set()
                self.assertTrue(release.wait(3))
                cancellation.append(cancel.is_set())
                return 41
            if payload['name'] == 'ready':
                self.assertTrue(entered.wait(3))
            else:
                release.set()
            return None

        source = '''let c=channel();
async fn produce(){await channel_send(c,tool("producer",null))}
let producer=spawn(produce());tool("ready",null);channel_close(c);tool("release",null);
let code=try{await producer;null}catch e{e.code};
[code,channel_info(c).state,channel_info(c).waitingSenders,await channel_receive(c)]'''
        evaluator = self.evaluator(effect)
        try:
            result = evaluator.run(source)
        finally:
            release.set()
        self.assertIsInstance(result[0], str)
        self.assertEqual(result[1:], ['closed', 0, {'done': True, 'value': None}])
        self.assertEqual(cancellation, [False])
        self.assert_quiescent(evaluator)

    def test_late_host_hard_error_survives_group_channel_cancellation(self):
        for error_type in (ResourceLimit, ExecutionControl, KeyboardInterrupt, SystemExit):
            with self.subTest(error=error_type.__name__):
                entered, queued, ended = threading.Event(), threading.Event(), threading.Event()
                hard, cleanup = error_type('late channel peer'), []

                def effect(kind, payload, cancel):
                    name = payload['name']
                    if name == 'host':
                        entered.set()
                        try:
                            self.assertTrue(cancel.wait(3))
                            raise hard
                        finally:
                            ended.set()
                    if name == 'ready':
                        self.assertTrue(entered.wait(3))
                        queued.set()
                        return None
                    if name == 'cleanup':
                        cleanup.append(True)
                        return None
                    self.assertTrue(queued.wait(3))
                    raise LanguageError('first ordinary error', code='channel_fixture')

                source = '''let c=channel();async fn host(){tool("host",null)}
async fn bad(){tool("bad",null)}
try{async with task_group() as g{
 g.spawn(channel_receive(c));g.spawn(host());g.spawn(bad());
 while channel_info(c).waitingReceivers!=1{1};
 tool("ready",null);while true{1}
}}finally{tool("cleanup",null)}'''
                evaluator = self.evaluator(effect, step_limit=1000000)
                with self.assertRaises(error_type) as result:
                    evaluator.run(source)
                self.assertIs(result.exception, hard)
                self.assertTrue(ended.is_set())
                self.assertEqual(cleanup, [])
                self.assert_quiescent(evaluator)

    def test_public_member_cancel_remains_hard_control(self):
        evaluator = self.evaluator(step_limit=100000)
        with self.assertRaises(TaskCancelled):
            evaluator.run('''let c=channel();
try{async with task_group() as g{
 let receiver=g.spawn(channel_receive(c));while channel_info(c).waitingReceivers!=1{1};
 cancel(receiver);42
}}catch e{"must not catch"}''')
        self.assert_quiescent(evaluator)

    def test_budget_exhaustion_drains_channel_waits_without_user_finally(self):
        cleanup = []
        evaluator = self.evaluator(lambda kind, payload, cancel: cleanup.append(payload), step_limit=2000)
        with self.assertRaises(ResourceLimit):
            evaluator.run('''let c=channel();try{async with task_group() as g{
 g.spawn(channel_receive(c));while channel_info(c).waitingReceivers!=1{1};while true{1}
}}finally{tool("cleanup",null)}''')
        self.assertEqual(cleanup, [])
        self.assert_quiescent(evaluator)

    def test_global_cancel_drains_channel_and_real_host_before_return(self):
        entered, queued, ended, cancel_run = (threading.Event() for _ in range(4))

        def controller():
            if queued.wait(3):
                cancel_run.set()

        thread = threading.Thread(target=controller)
        thread.start()

        def effect(kind, payload, cancel):
            if payload['name'] == 'host':
                entered.set()
                try:
                    self.assertTrue(cancel.wait(3))
                    raise TaskCancelled('cooperative host')
                finally:
                    ended.set()
            self.assertTrue(entered.wait(3))
            queued.set()
            return None

        evaluator = self.evaluator(effect, cancelled=cancel_run.is_set, step_limit=1000000)
        try:
            with self.assertRaises((ResourceLimit, ExecutionControl)):
                evaluator.run('''let c=channel();async fn host(){tool("host",null)}
async with task_group() as g{
 g.spawn(host());g.spawn(channel_receive(c));while channel_info(c).waitingReceivers!=1{1};
 tool("ready",null);while true{1}
}''')
        finally:
            queued.set()
            cancel_run.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(ended.is_set())
        self.assert_quiescent(evaluator)

    def test_deadlocked_group_retracts_waiters_before_outside_catch(self):
        evaluator = self.evaluator()
        result = evaluator.run('''let c=channel();let error=try{
async with task_group() as g{g.spawn(channel_send(c,99))}
}catch e{e.code};let before=channel_info(c);
let next=spawn(channel_receive(c));await channel_send(c,42);
[error,before.waitingSenders,before.waitingReceivers,(await next).value]''')
        self.assertEqual(result, ['task_deadlock', 0, 0, 42])
        self.assert_quiescent(evaluator)

    def test_closed_handles_still_validate_owner_epoch_and_json_forgery(self):
        owner = self.evaluator()
        handle = owner.run('let c=channel();channel_close(c);c')
        for evaluator in (owner, self.evaluator()):
            for expression in ('channel_info(input)', 'channel_close(input)',
                               'channel_send(input,1)', 'channel_receive(input)',
                               'await anext(input)', 'await acollect(input)'):
                with self.subTest(expression=expression, same_owner=evaluator is owner):
                    with self.assertRaises(LanguageError):
                        evaluator.run(expression, {'input': handle})
        with self.assertRaises(LanguageError):
            self.evaluator().run('channel_receive({"$handle":"Channel","id":"channel-1","state":"open"})')

    def test_host_receives_only_opaque_metadata_not_channel_buffer(self):
        payloads = []

        def effect(kind, payload, cancel):
            payloads.append(payload['value'])
            return payload['value']

        evaluator = self.evaluator(effect)
        result = evaluator.run('''let c=channel(1);await channel_send(c,"private queued value");
let described=tool("inspect",{nested:[c]});
let code=try{channel_close(described.nested[0]);null}catch e{e.code};
[code,(await channel_receive(c)).value]''')
        self.assertIsInstance(result[0], str)
        self.assertEqual(result[1], 'private queued value')
        self.assertEqual(payloads[0]['nested'][0]['$handle'], 'Channel')
        self.assertNotIn('private queued value', repr(payloads))
        self.assert_quiescent(evaluator)

    def test_message_character_quota_is_shared_across_channels_and_cold_tasks(self):
        evaluator = self.evaluator()
        with self.assertRaises(ResourceLimit):
            evaluator.run('for i in range(6){channel_send(channel(1),input)}', {'input': 'x' * 200000})
        self.assert_quiescent(evaluator)
        # A new epoch starts with its own quota; the previous failed epoch
        # cannot poison later local execution.
        self.assertIsNone(evaluator.run('let c=channel(1);await channel_send(c,input)', {'input': 'x' * 200000}))
        self.assert_quiescent(evaluator)

    def test_message_node_quota_is_shared_across_distinct_channels(self):
        evaluator = self.evaluator(step_limit=100000)
        with self.assertRaises(ResourceLimit):
            evaluator.run('for i in range(11){channel_send(channel(1),input)}', {'input': [0] * 9999})
        self.assert_quiescent(evaluator)

    def test_cold_cancel_and_delivery_release_live_message_quota(self):
        source = '''for round in range(3){
 let c=channel(1);var cold=[];
 for i in range(5){cold=cold+[channel_send(c,input)]};
 for task in cold{cancel(task)};
 for i in range(6){await channel_send(c,input);await channel_receive(c)};
 channel_close(c)
};42'''
        evaluator = self.evaluator(step_limit=100000)
        self.assertEqual(evaluator.run(source, {'input': 'x' * 200000}), 42)
        self.assert_quiescent(evaluator)

    def test_close_rejection_releases_pending_send_message_quota(self):
        source = '''for round in range(3){
 let c=channel();var jobs=[];
 for i in range(5){jobs=jobs+[spawn(channel_send(c,input))]};
 while channel_info(c).waitingSenders!=5{1};channel_close(c);
 for task in jobs{try{await task}catch e{null}}
};42'''
        evaluator = self.evaluator(step_limit=100000)
        self.assertEqual(evaluator.run(source, {'input': 'x' * 200000}), 42)
        self.assert_quiescent(evaluator)

    def test_failed_payload_preflight_never_reserves_message_budget(self):
        foreign = self.evaluator().run('channel()')
        source = '''let c=channel();var failures=0;
for i in range(8){try{channel_send(c,[input,foreign])}catch e{failures=failures+1}};
var jobs=[];for i in range(5){jobs=jobs+[channel_send(c,input)]};
for task in jobs{cancel(task)};failures'''
        evaluator = self.evaluator(step_limit=100000)
        self.assertEqual(evaluator.run(source, {'input': 'x' * 200000, 'foreign': foreign}), 8)
        self.assert_quiescent(evaluator)


if __name__ == '__main__':
    unittest.main()
