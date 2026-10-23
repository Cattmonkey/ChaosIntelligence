import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, Native
from chaos import Framework, Settings
from tests.test_guidance_native import Mailbox


class GuidanceLifecycle(unittest.TestCase):
    def cleanup_probe(self, mode):
        source = r'''
import json,time,sys
from chaos.language import Evaluator,ResourceLimit
mode=sys.argv[1];cancelled=False;seen=[];evaluator=None
def host(operation,payload,capabilities,team_scope):
    global cancelled
    assert operation=='receive' and payload=={}
    seen.append(evaluator.suspension.finishing)
    if mode=='deadline':evaluator.deadline=time.monotonic()-1
    else:cancelled=True
    return {'received':False,'message':None,'closed':False}
evaluator=Evaluator(guidance_handler=host,timeout=5,cancelled=lambda:cancelled)
try:
    evaluator.run('async fn* stream(){try{yield 1}finally{await guidance_receive(null)}};let handle=stream();await anext(handle);42')
except ResourceLimit as error:
    result={'code':error.code,'seen':seen,'active':evaluator.suspension.active,'waitCount':len(evaluator.suspension.guidance_waits.active),'captures':[len(task.runner.capture_frames) for task in evaluator.suspension.tasks],'frames':[len(task.runner.iterator.frames) for task in evaluator.suspension.tasks]}
else:raise AssertionError('Implicit cleanup ignored its real execution budget')
print(json.dumps(result))
'''
        env = {key: value for key, value in os.environ.items() if not key.startswith('CHAOS_MODEL_')
               and not any(word in key.upper() for word in ('API_KEY', 'TOKEN', 'MODEL', 'PROVIDER'))}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory(prefix='chaos-guidance-finalizer-') as temporary:
            env['CHAOS_CONFIG'] = str(Path(temporary) / 'settings.json')
            completed = subprocess.run([sys.executable, '-B', '-c', source, mode],
                                       env=env, capture_output=True, text=True, timeout=3)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result['code'], 'resource_limit')
        self.assertEqual(result['seen'], [True])
        self.assertFalse(result['active'])
        self.assertEqual(result['waitCount'], 0)
        self.assertTrue(all(count == 0 for count in result['captures'] + result['frames']))

    def test_implicit_cleanup_receive_honors_expired_deadline_before_next_host_call(self):
        self.cleanup_probe('deadline')

    def test_implicit_cleanup_receive_honors_global_cancel_before_next_host_call(self):
        self.cleanup_probe('cancel')

    def test_reentrant_poll_does_not_deliver_other_task_cancel_or_change_authority(self):
        from chaos.agent_tools import scope
        from chaos.guidance_waits import receive
        box = Mailbox()
        evaluator = Evaluator(guidance_handler=box.handle)
        restored = []
        def make_wait():
            with scope(evaluator, frozenset({'text.normalize'})):
                return receive(evaluator, None)
        def cancel_and_poll(target):
            current = evaluator.suspension.current
            evaluator.suspension.cancel(target)
            with box.lock:
                box.pending.append(42)
            evaluator.suspension.guidance_waits.poll()
            restored.append(evaluator.suspension.current is current)
        result = evaluator.run('''let pending=make_wait();spawn(pending);await sleep(0);
        var victim=null;async fn other(){cancel_and_poll(victim)};
        victim=other();spawn(victim);await task_wait([victim],"all");
        [(await pending).message.input,task_info(victim).state]''', {
            'make_wait': Native('make_wait', make_wait, evaluator),
            'cancel_and_poll': Native('cancel_and_poll', cancel_and_poll, evaluator)})
        self.assertEqual(result, [42, 'cancelled'])
        self.assertEqual(restored, [True])
        self.assertEqual(box.received, [42])
        self.assertTrue(all(call[1] == frozenset({'text.normalize'}) and call[2] is None
                            for call in box.calls if call[0] == 'receive'))
        self.assertEqual(evaluator.suspension.guidance_waits.active, {})

    def test_wait_handles_cannot_cross_evaluator_or_epoch(self):
        first = Evaluator(guidance_handler=Mailbox().handle)
        task = first.run('guidance_receive(null)')
        for evaluator in (first, Evaluator(guidance_handler=Mailbox().handle)):
            with self.subTest(evaluator=evaluator), self.assertRaises(LanguageError):
                evaluator.run('await input', {'input': task})

    def test_recording_rejects_receive_and_progress_without_consumption(self):
        for operation in ('await guidance_receive()', 'await progress_emit("plan",{})'):
            with self.subTest(operation=operation), Framework(settings=Settings(':memory:')) as app:
                app.create_project('recording', source='''
                let result=try{await record_skill(async fn(x){OPERATION},null)}catch e{e.code};
                [result,(await guidance_receive(null)).message.input]'''.replace('OPERATION', operation), capabilities=[])
                run=app.start('recording',1,None,mode='source')
                app._engine.guide(run.id,42,'record-guide')
                self.assertEqual(run.result(5),['mode_error',42])
                events=app.events('recording',run_id=run.id)['items']
                self.assertEqual(sum(item['type']=='guidance.received' for item in events),1)
                self.assertFalse(any(item['type']=='bot.progress' for item in events))

    def test_hard_budget_failure_drains_registered_wait_and_frames(self):
        box = Mailbox()
        evaluator = Evaluator(guidance_handler=box.handle, step_limit=50)
        with self.assertRaises(ResourceLimit):
            evaluator.run('async fn listen(){await guidance_receive(null)};spawn(listen());while true{1}')
        self.assertEqual(evaluator.suspension.guidance_waits.active, {})
        for task in evaluator.suspension.tasks:
            self.assertEqual(task.runner.capture_frames, [])
            self.assertEqual(task.runner.iterator.frames, [])

    def test_prompt_abort_preserves_next_guidance_and_outer_execution(self):
        box = Mailbox([42])
        evaluator = Evaluator(guidance_handler=box.handle)
        result = evaluator.run('''let tag=prompt_tag();
        let escaped=call_prompt(tag,fn(){abort_prompt(tag,21)});
        [escaped,(await guidance_receive()).message.input]''')
        self.assertEqual(result, [21, 42])
        self.assertEqual(evaluator.suspension.guidance_waits.active, {})
