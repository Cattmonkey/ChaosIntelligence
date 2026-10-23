"""Default frame scheduling across real logical holders, with no external I/O."""
import unittest

from chaos.language import Evaluator, Native
from chaos.execution_frames import ExecutionCursor


class ExecutionFrameStreams(unittest.TestCase):
    def evaluator(self):
        evaluator = Evaluator(step_limit=200000)
        self.holders = []
        self.observations = []

        def inspect_frames():
            scheduler = evaluator.suspension
            holder = scheduler.stream.runner if scheduler.stream is not None else scheduler.current
            self.assertIsNotNone(holder)
            self.assertIsInstance(holder.iterator, ExecutionCursor)
            self.holders.append(holder)
            cursors = [task.runner.iterator for task in scheduler.tasks]
            self.assertTrue(all(isinstance(cursor, ExecutionCursor) for cursor in cursors))
            self.observations.append((id(holder), id(holder.iterator)))
            return True

        evaluator.env.bind('inspect_frames', Native('inspect_frames', inspect_frames, evaluator))
        return evaluator

    def assert_released(self, evaluator):
        self.assertFalse(evaluator.suspension.active)
        for holder in self.holders:
            self.assertEqual(holder.capture_frames, [])
        for task in evaluator.suspension.tasks:
            self.assertIsInstance(task.runner.iterator, ExecutionCursor)
            self.assertEqual(task.runner.capture_frames, [])

    def test_concurrent_tasks_have_distinct_cursors_and_shared_cells(self):
        evaluator = self.evaluator()
        value = evaluator.run('var total=0;async fn part(x){await sleep(0);inspect_frames();total+=x;x};let parts=await gather([part(20),part(22)]);[parts,total]')
        self.assertEqual(value, [[20, 22], 42])
        self.assertEqual(len(set(self.observations)), 2)
        self.assert_released(evaluator)

    def test_cold_task_creates_no_body_frames_or_effects_before_await(self):
        evaluator = self.evaluator()
        value = evaluator.run('var entered=0;async fn part()->Int{entered+=1;inspect_frames();42};let t=part();let before=[entered,task_info(t).state];let answer=await t;[before,answer,entered]')
        self.assertEqual(value, [[0, 'created'], 42, 1])
        self.assertEqual(len(self.observations), 1)
        self.assert_released(evaluator)

    def test_sync_and_async_stream_holders_resume_without_replaying_prefix(self):
        for flavor, first, second, close in (
            ('fn*', 'next(s)', 'send(s,42)', 'close(s)'),
            ('async fn*', 'await anext(s)', 'await asend(s,42)', 'await aclose(s)'),
        ):
            with self.subTest(flavor=flavor):
                evaluator = self.evaluator()
                source = 'var entries=0;' + flavor + ' stream(){entries+=1;inspect_frames();let received=yield 20;inspect_frames();yield received};let s=stream();let a=' + first + ';let b=' + second + ';' + close + ';[a.value,b.value,entries]'
                self.assertEqual(evaluator.run(source), [20, 42, 1])
                self.assertEqual(len(self.observations), 2)
                self.assertEqual(len(set(self.observations)), 1)
                self.assert_released(evaluator)

    def test_soft_prompt_transfer_preserves_original_cleanup_and_result_packet(self):
        evaluator = self.evaluator()
        source = 'var events=[];let p=prompt_tag();let result=multiple_value_list(fn(){call_prompt(p,fn(){try{inspect_frames();abort_prompt(p,20,22)}finally{events=append(events,"cleanup")}})});[result,events]'
        self.assertEqual(evaluator.run(source), [[20, 22], ['cleanup']])
        self.assert_released(evaluator)

    def test_child_cancellation_retires_robot_before_parent_continues(self):
        evaluator = self.evaluator()
        operations = []
        lease = {'open': False, 'id': None, 'number': 0}

        def handler(operation, payload):
            operations.append(operation)
            if operation == 'open':
                self.assertFalse(lease['open'])
                lease['number'] += 1
                lease.update(open=True, id='frame-robot-' + str(lease['number']))
                return {'id': lease['id'], 'name': payload['name'], 'generation': 1,
                        'profile': 'frame-fixture/1', 'status': 'open',
                        'actuators': {'axis': {'minimum': -1, 'maximum': 1, 'unit': 'normalized'}},
                        'maxObservationAgeMs': 1000, 'latestObservationId': None}
            self.assertEqual(operation, 'close')
            self.assertEqual(payload['id'], lease['id'])
            lease['open'] = False
            return {'schema': 'chaos.robot-receipt/1', 'id': 'closed-' + str(lease['number']),
                    'robotId': lease['id'], 'name': 'alias', 'generation': 1,
                    'operation': 'close', 'status': 'closed', 'targetVerified': False,
                    'commandId': None, 'observationId': None}

        def force_retire(resource_id):
            self.assertEqual(resource_id, lease['id'])
            self.assertTrue(lease['open'])
            operations.append('force-retire')
            lease['open'] = False

        evaluator.robot_handler = handler
        evaluator.robot_cleanup_handler = force_retire
        source = 'var ready=false;async fn controller(){async with await robot_open("rig") as r{inspect_frames();ready=true;await sleep(10)}};let t=spawn(controller());while !ready{await sleep(0.001)};cancel(t);while task_info(t).state!="cancelled"{await sleep(0.001)};async with await robot_open("alias") as r{inspect_frames();[task_info(t).state,type_of(r)]}'
        self.assertEqual(evaluator.run(source), ['cancelled', 'Robot'])
        self.assertEqual(operations, ['open', 'force-retire', 'open', 'close'])
        self.assertFalse(lease['open'])
        self.assert_released(evaluator)
