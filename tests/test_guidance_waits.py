import threading
import unittest

from chaos.language import Evaluator
from tests.test_guidance_native import Mailbox


class GuidanceWaits(unittest.TestCase):
    def test_timed_wait_and_close_use_original_scheduler(self):
        box = Mailbox()
        evaluator = Evaluator(guidance_handler=box.handle)
        self.assertEqual(evaluator.run('await guidance_receive(0.01)'),
                         {'received': False, 'message': None, 'closed': False})
        box.closed = True
        self.assertEqual(evaluator.run('await guidance_receive(null)'),
                         {'received': False, 'message': None, 'closed': True})
        self.assertEqual(evaluator.suspension.guidance_waits.active, {})

    def test_four_parked_listeners_leave_effect_workers_available(self):
        box = Mailbox()
        arrived = threading.Event()
        def effect(kind, payload, cancel):
            self.assertEqual(kind, 'tool')
            with box.lock:
                box.pending.extend([1, 2, 3, 4])
            arrived.set()
            return 42
        evaluator = Evaluator(guidance_handler=box.handle, effect_handler=effect,
                              tool_handler=lambda name, value: None, timeout=5)
        result = evaluator.run('''async fn listen(){(await guidance_receive(null)).message.input}
        let listeners=[spawn(listen()),spawn(listen()),spawn(listen()),spawn(listen())];
        async fn produce(){tool("fixture",null)};let producer=spawn(produce());
        [await gather(listeners),await producer]''')
        self.assertEqual(result, [[1, 2, 3, 4], 42])
        self.assertTrue(arrived.is_set())
        self.assertEqual(evaluator.suspension.guidance_waits.active, {})

    def test_cancelled_cold_receive_does_not_consume(self):
        box = Mailbox([42])
        evaluator = Evaluator(guidance_handler=box.handle)
        self.assertEqual(evaluator.run('let t=guidance_receive(null);cancel(t);(await guidance_receive()).message.input'), 42)
        self.assertEqual(box.received, [42])

    def test_child_cancel_releases_wait_before_parent_continues(self):
        box = Mailbox()
        evaluator = Evaluator(guidance_handler=box.handle)
        result = evaluator.run('''async fn listen(){await guidance_receive(null)};
        let child=spawn(listen());await sleep(0);cancel(child);
        await task_wait([child],"all");[task_info(child).state,41+1]''')
        self.assertEqual(result, ['cancelled', 42])
        self.assertEqual(evaluator.suspension.guidance_waits.active, {})
        self.assertEqual(box.received, [])
