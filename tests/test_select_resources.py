"""Independent atomic-select lifecycle tests; synchronization is causal."""
import threading
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled, TERMINAL


class SelectResourceTests(unittest.TestCase):
    def evaluator(self, effect=None, **options):
        if effect is None:
            return Evaluator(timeout=8, **options)
        return Evaluator(timeout=8, effect_handler=effect,
                         tool_handler=lambda name, value: effect(
                             'tool', {'name': name, 'value': value}, threading.Event()), **options)

    def assert_quiescent(self, evaluator):
        scheduler = evaluator.suspension
        self.assertFalse(scheduler.active)
        self.assertTrue(all(t.state in TERMINAL for t in scheduler.tasks))
        self.assertEqual((scheduler.channel_slots, scheduler.channel_nodes,
                          scheduler.channel_chars), (0, 0, 0))
        self.assertEqual(scheduler.select_registrations, 0)
        self.assertFalse(scheduler.channel_dirty)
        self.assertFalse(scheduler.channel_dirty_set)
        self.assertFalse(scheduler.task_waits.timers)
        self.assertFalse(scheduler.task_waits.deadlines)
        self.assertTrue(all(not c.senders and not c.receivers and not c.buffer
                            for c in scheduler.channels))
        self.assertFalse([t.name for t in threading.enumerate()
                          if t.name.startswith('chaos-effect') and t.is_alive()])

    def test_two_selectors_claim_both_tokens_and_remove_all_losers(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let a=channel();let b=channel();
let first=spawn(channel_select([send_case(a,41),receive_case(b)]));
while channel_info(a).waitingSenders!=1{1};
let second=await channel_select([receive_case(a),send_case(b,99)]);
let one=await first;[one.index,one.op,second.index,second.value,
channel_info(a).waitingSenders,channel_info(a).waitingReceivers,
channel_info(b).waitingSenders,channel_info(b).waitingReceivers,cancel(first)]''')
        self.assertEqual(result, [0, 'send', 0, 41, 0, 0, 0, 0, False])
        self.assert_quiescent(evaluator)

    def test_same_selection_cannot_rendezvous_with_itself(self):
        evaluator = self.evaluator()
        result = evaluator.run('''let c=channel();
let r=await channel_select([send_case(c,41),receive_case(c)],timeout=0);
[r.index,r.op,r.done,r.value,r.timedOut,
channel_info(c).waitingSenders,channel_info(c).waitingReceivers]''')
        self.assertEqual(result, [None, None, False, None, True, 0, 0])
        self.assert_quiescent(evaluator)

    def test_self_pair_at_heads_does_not_block_an_external_peer(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel();
let s=spawn(channel_select([send_case(c,41),receive_case(c)]));
while channel_info(c).waitingSenders!=1 || channel_info(c).waitingReceivers!=1{1};
let received=await channel_receive(c);let selected=await s;
[received.value,selected.index,channel_info(c).waitingSenders,channel_info(c).waitingReceivers]''')
        self.assertEqual(result, [41, 0, 0, 0])
        self.assert_quiescent(evaluator)

    def test_duplicate_receive_cases_consume_only_one_buffer_item(self):
        evaluator = self.evaluator()
        result = evaluator.run('''let c=channel(2);await channel_send(c,41);await channel_send(c,42);
let branch=receive_case(c);let r=await channel_select([branch,branch]);
[r.index,r.value,channel_info(c).buffered,(await channel_receive(c)).value]''')
        self.assertEqual(result, [0, 41, 1, 42])
        self.assert_quiescent(evaluator)

    def test_fifo_includes_ordinary_operations_and_select_offers(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel();let older=spawn(channel_receive(c));
while channel_info(c).waitingReceivers!=1{1};
let selected=spawn(channel_select([receive_case(c)]));
while channel_info(c).waitingReceivers!=2{1};
await channel_send(c,41);await channel_send(c,42);
[(await older).value,(await selected).value]''')
        self.assertEqual(result, [41, 42])
        self.assert_quiescent(evaluator)

    def test_winner_retraction_leaves_other_channel_fifo_usable(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let a=channel();let b=channel();
let selection=spawn(channel_select([receive_case(a),receive_case(b)]));
while channel_info(b).waitingReceivers!=1{1};
let peer=spawn(channel_receive(b));while channel_info(b).waitingReceivers!=2{1};
await channel_send(a,41);await channel_send(b,42);
[(await selection).value,(await peer).value,channel_info(b).waitingReceivers]''')
        self.assertEqual(result, [41, 42, 0])
        self.assert_quiescent(evaluator)

    def test_cancel_retracts_all_offers_synchronously_without_phantom_send(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let a=channel();let b=channel();
let selection=spawn(channel_select([send_case(a,99),receive_case(b)]));
while channel_info(a).waitingSenders!=1{1};let cancelled=cancel(selection);
let before=[task_info(selection).state,channel_info(a).waitingSenders,channel_info(b).waitingReceivers];
let receiver=spawn(channel_receive(a));await channel_send(a,42);
[cancelled,before,(await receiver).value]''')
        self.assertEqual(result, [True, ['cancelled', 0, 0], 42])
        self.assert_quiescent(evaluator)

    def test_group_abort_cancels_inline_and_same_creator_cold_select(self):
        for expression in ('await channel_select(branches)', 'let s=channel_select(branches);await s'):
            with self.subTest(expression=expression):
                evaluator = self.evaluator(step_limit=100000)
                source = '''let a=channel();let b=channel();
let branches=[send_case(a,99),receive_case(b)];
async fn bad(){while channel_info(a).waitingSenders!=1{1};throw "first"}
let code=try{async with task_group() as g{g.spawn(bad());EXPRESSION}}catch e{e.value};
[code,channel_info(a).waitingSenders,channel_info(b).waitingReceivers]'''
                self.assertEqual(evaluator.run(source.replace('EXPRESSION', expression)), ['first', 0, 0])
                self.assert_quiescent(evaluator)

    def test_group_abort_detaches_borrowed_select_without_cancelling_it(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel();let s=spawn(channel_select([receive_case(c)]));
async fn bad(){while channel_info(c).waitingReceivers!=1{1};throw "first"}
let code=try{async with task_group() as g{g.spawn(bad());await s}}catch e{e.value};
let before=[task_info(s).state,task_info(s).cancellationRequested,channel_info(c).waitingReceivers];
await channel_send(c,42);[code,before,(await s).value]''')
        self.assertEqual(result, ['first', ['running', False, 1], 42])
        self.assert_quiescent(evaluator)

    def test_closed_branch_is_selected_by_index_and_buffer_is_preserved(self):
        evaluator = self.evaluator()
        result = evaluator.run('''let closed=channel();channel_close(closed);
let ready=channel(1);await channel_send(ready,42);
let code=try{await channel_select([send_case(closed,99),receive_case(ready)]);null}catch e{e.code};
let result=await channel_select([receive_case(ready),send_case(closed,99)]);
[code,result.index,result.value,(await channel_select([receive_case(closed)])).done]''')
        self.assertEqual(result, ['channel_closed', 0, 42, True])
        self.assert_quiescent(evaluator)

    def test_deadlock_retracts_select_before_outside_catch(self):
        evaluator = self.evaluator()
        result = evaluator.run('''let a=channel();let b=channel();
let code=try{await channel_select([send_case(a,99),receive_case(b)]);null}catch e{e.code};
[code,channel_info(a).waitingSenders,channel_info(b).waitingReceivers]''')
        self.assertEqual(result, ['task_deadlock', 0, 0])
        self.assert_quiescent(evaluator)

    def test_native_timeout_clock_uses_no_worker_and_releases_offers(self):
        evaluator = self.evaluator()
        now, waits = [100.0], []
        def idle(seconds):
            waits.append(seconds);now[0] += seconds
        evaluator.suspension.task_waits.clock = lambda: now[0]
        evaluator.suspension.task_waits.idle_wait = idle
        with patch('chaos.suspension.ThreadPoolExecutor', side_effect=AssertionError('select allocated worker')):
            result = evaluator.run('''let c=channel();let r=await channel_select([receive_case(c)],timeout=0.025);
[r.timedOut,channel_info(c).waitingReceivers]''')
        self.assertEqual(result, [True, 0]);self.assertTrue(waits)
        self.assertGreaterEqual(now[0], 100.025)
        self.assert_quiescent(evaluator)

    def test_trace_failure_cannot_half_commit_two_selectors(self):
        for error_type in (LanguageError, ResourceLimit, KeyboardInterrupt):
            with self.subTest(error=error_type.__name__):
                failure, observed = error_type('terminal observer fixture'), []
                evaluator = None
                def trace(payload):
                    if payload['event'] == 'task.completed' and payload.get('kind') == 'channel_select' and not observed:
                        observed.append([(t.state, t.terminal_order) for t in evaluator.suspension.tasks
                                         if t.kind == 'channel_select'])
                        raise failure
                evaluator = self.evaluator(trace_handler=trace, step_limit=100000)
                with self.assertRaises(error_type) as caught:
                    evaluator.run('''let a=channel();let b=channel();
let s=spawn(channel_select([send_case(a,41),receive_case(b)]));
while channel_info(a).waitingSenders!=1{1};
await channel_select([receive_case(a),send_case(b,99)]);await s''')
                self.assertIs(caught.exception, failure)
                self.assertEqual(len(observed), 1)
                self.assertEqual(len(observed[0]), 2)
                self.assertTrue(all(state == 'completed' and order > 0 for state, order in observed[0]))
                self.assert_quiescent(evaluator)

    def test_close_completes_all_selected_sends_before_observer_failure(self):
        for error_type in (LanguageError, ResourceLimit):
            with self.subTest(error=error_type.__name__):
                failure, observed = error_type('close observer fixture'), []
                evaluator = None
                def trace(payload):
                    if payload['event'] == 'task.failed' and payload.get('kind') == 'channel_select' and not observed:
                        c = evaluator.suspension.channels[0]
                        observed.append((len(c.senders), [t.state for t in evaluator.suspension.tasks if t.kind == 'channel_select']))
                        raise failure
                evaluator = self.evaluator(trace_handler=trace, step_limit=100000)
                with self.assertRaises(error_type) as caught:
                    evaluator.run('''let c=channel();let a=spawn(channel_select([send_case(c,41)]));
let b=spawn(channel_select([send_case(c,42)]));while channel_info(c).waitingSenders!=2{1};
channel_close(c);await a;await b''')
                self.assertIs(caught.exception, failure)
                self.assertEqual(observed, [(0, ['failed', 'failed'])])
                self.assert_quiescent(evaluator)

    def test_group_late_hard_host_failure_preserves_identity_and_skips_cleanup(self):
        for error_type in (ResourceLimit, ExecutionControl, KeyboardInterrupt, SystemExit):
            with self.subTest(error=error_type.__name__):
                entered, queued, ended = (threading.Event() for _ in range(3))
                hard, cleanup = error_type('late select fixture'), []
                def effect(kind, payload, cancel):
                    name = payload['name']
                    if name == 'host':
                        entered.set()
                        try:
                            self.assertTrue(cancel.wait(3));raise hard
                        finally:
                            ended.set()
                    if name == 'ready':
                        self.assertTrue(entered.wait(3));queued.set();return None
                    if name == 'cleanup':
                        cleanup.append(True);return None
                    self.assertTrue(queued.wait(3));raise LanguageError('first ordinary', code='select_fixture')
                evaluator = self.evaluator(effect, step_limit=1000000)
                with self.assertRaises(error_type) as caught:
                    evaluator.run('''let c=channel();async fn host(){tool("host",null)}
async fn bad(){tool("bad",null)}try{async with task_group() as g{
g.spawn(host());g.spawn(bad());g.spawn(channel_select([receive_case(c)]));
while channel_info(c).waitingReceivers!=1{1};tool("ready",null);while true{1}
}}finally{tool("cleanup",null)}''')
                self.assertIs(caught.exception, hard);self.assertTrue(ended.is_set())
                self.assertEqual(cleanup, []);self.assert_quiescent(evaluator)

    def test_public_group_member_cancel_is_hard_and_retracts_select(self):
        evaluator = self.evaluator(step_limit=100000)
        with self.assertRaises(TaskCancelled):
            evaluator.run('''let c=channel();try{async with task_group() as g{
let s=g.spawn(channel_select([receive_case(c)]));while channel_info(c).waitingReceivers!=1{1};
cancel(s);42}}catch e{"cannot catch"}''')
        self.assert_quiescent(evaluator)

    def test_case_and_cold_select_both_count_shared_message_quota(self):
        evaluator = self.evaluator()
        with self.assertRaises(ResourceLimit):
            evaluator.run('''let c=channel();let branch=send_case(c,input);
let a=channel_select([branch]);let b=channel_select([branch]);let d=channel_select([branch]);
let e=channel_select([branch]);channel_select([branch])''', {'input': 'x' * 200000})
        self.assert_quiescent(evaluator)
        self.assertEqual(evaluator.run('let c=channel(1);(await channel_select([send_case(c,42)])).op'), 'send')
        self.assert_quiescent(evaluator)

    def test_reused_case_cancel_and_delivery_release_only_operation_quota(self):
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel(1);let branch=send_case(c,input);
for i in range(12){let cold=channel_select([branch]);cancel(cold);
await channel_select([branch]);await channel_receive(c)};42''', {'input': 'x' * 200000})
        self.assertEqual(result, 42);self.assert_quiescent(evaluator)

    def test_case_template_quota_is_shared_across_channels_until_epoch_end(self):
        evaluator = self.evaluator()
        with self.assertRaises(ResourceLimit):
            evaluator.run('for i in range(6){send_case(channel(),input)}', {'input': 'x' * 200000})
        self.assert_quiescent(evaluator)

    def test_foreign_epoch_case_and_json_description_are_not_authority(self):
        evaluator = self.evaluator()
        branch = evaluator.run('receive_case(channel())')
        for owner in (evaluator, self.evaluator()):
            with self.subTest(same_owner=owner is evaluator):
                with self.assertRaises(LanguageError):
                    owner.run('channel_select([input],timeout=0)', {'input': branch})
                self.assert_quiescent(owner)
        with self.assertRaises(LanguageError):
            self.evaluator().run('channel_select([{"$handle":"ChannelCase","op":"receive"}],timeout=0)')

    def test_failed_case_preflight_never_reserves_partial_message_captures(self):
        foreign = self.evaluator().run('receive_case(channel())')
        evaluator = self.evaluator(step_limit=100000)
        result = evaluator.run('''let c=channel();let good=send_case(c,input);var failures=0;
for i in range(8){try{channel_select([good,foreign])}catch e{failures=failures+1}};
var jobs=[];for i in range(4){jobs=jobs+[channel_select([good])]};
for job in jobs{cancel(job)};failures''', {'input': 'x' * 200000, 'foreign': foreign})
        self.assertEqual(result, 8);self.assert_quiescent(evaluator)

    def test_receive_case_count_is_bounded_without_message_payloads(self):
        evaluator = self.evaluator(step_limit=1000000)
        with self.assertRaises(ResourceLimit):
            evaluator.run('let c=channel();for i in range(10001){receive_case(c)}')
        self.assert_quiescent(evaluator)

    def test_cold_select_reference_quota_is_global_even_for_one_reused_case(self):
        evaluator = self.evaluator(step_limit=1000000)
        with self.assertRaises(ResourceLimit):
            evaluator.run('''let branch=receive_case(channel());
let cases=map(fn(n){branch},range(6000));
let first=channel_select(cases);channel_select(cases)''')
        self.assert_quiescent(evaluator)

    def test_budget_exhaustion_retracts_select_without_user_cleanup(self):
        cleanup = []
        evaluator = self.evaluator(tool_handler=lambda name, value: cleanup.append(name), step_limit=2000)
        with self.assertRaises(ResourceLimit):
            evaluator.run('''let a=channel();let b=channel();try{async with task_group() as g{
g.spawn(channel_select([send_case(a,99),receive_case(b)]));
while channel_info(a).waitingSenders!=1{1};while true{1}
}}finally{tool("cleanup",null)}''')
        self.assertEqual(cleanup, []);self.assert_quiescent(evaluator)

    def test_global_cancel_retracts_select_and_drains_actual_host_worker(self):
        entered, queued, ended, cancelled = (threading.Event() for _ in range(4))
        def controller():
            if queued.wait(3): cancelled.set()
        thread = threading.Thread(target=controller);thread.start()
        def effect(kind, payload, cancel):
            if payload['name'] == 'host':
                entered.set()
                try:
                    self.assertTrue(cancel.wait(3));raise TaskCancelled('cooperative host')
                finally:
                    ended.set()
            self.assertTrue(entered.wait(3));queued.set()
        evaluator = self.evaluator(effect, cancelled=cancelled.is_set, step_limit=1000000)
        try:
            with self.assertRaises((ResourceLimit, ExecutionControl)):
                evaluator.run('''let c=channel();async fn host(){tool("host",null)}
async with task_group() as g{g.spawn(host());g.spawn(channel_select([receive_case(c)]));
while channel_info(c).waitingReceivers!=1{1};tool("ready",null);while true{1}}''')
        finally:
            queued.set();cancelled.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertTrue(ended.is_set())
        self.assert_quiescent(evaluator)

    def test_host_case_metadata_does_not_expose_or_recreate_send_template(self):
        payloads = []
        def effect(kind, payload, cancel):
            payloads.append(payload['value']);return payload['value']
        evaluator = self.evaluator(effect)
        result = evaluator.run('''let c=channel(1);let branch=send_case(c,"private message");
let described=tool("inspect",{nested:[branch]});
let code=try{channel_select([described.nested[0]],timeout=0);null}catch e{e.code};
await channel_select([branch]);[code,(await channel_receive(c)).value]''')
        self.assertIsInstance(result[0], str);self.assertEqual(result[1], 'private message')
        self.assertEqual(payloads[0]['nested'][0]['$handle'], 'ChannelCase')
        self.assertNotIn('private message', repr(payloads));self.assert_quiescent(evaluator)


if __name__ == '__main__':
    unittest.main()
