"""Task lists are copies captured on the interpreter's real pause boundary."""
import threading
import time
import unittest
from chaos import Framework, Settings
from chaos.debugger import DebugSession, options
from chaos.language import Parser

SOURCE = '''async fn leaf(x: Int)->Int {
  let answer=x+1;
  answer
}
async fn parent() {
  let first=leaf(40);
  let second=leaf(41);
  let joined=gather([first,second,first]);
  await joined
}
await parent()'''


class TaskDebugger(unittest.TestCase):
    def paused(self, get):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            state = get()
            if state['status'] == 'paused': return state
            time.sleep(.005)
        self.fail('debugger did not pause')

    def test_real_parent_gather_and_frame_metadata_survive_read_only_inspection(self):
        with Framework(':memory:', settings=Settings(':memory:')) as app:
            project = app.create_project('task-debug', source=SOURCE)
            run = app.start_debug(project['id'], 1, pause_on_entry=False,
                                  breakpoints=[{'file':'main.chaos','line':2}])
            state = self.paused(run.debugger)
            snapshot = state['taskSnapshot']
            self.assertFalse(snapshot['truncated'])
            self.assertEqual(snapshot['total'], 4)
            tasks = {item['id']:item for item in snapshot['tasks']}
            current = tasks[state['frames'][0]['taskId']]
            joined = next(item for item in tasks.values() if item['kind'] == 'gather')
            self.assertEqual(joined['memberCount'], 3)
            self.assertFalse(joined['membersTruncated'])
            self.assertEqual(joined['memberTaskIds'][0], joined['memberTaskIds'][2])
            self.assertIn(current['id'], joined['memberTaskIds'])
            self.assertEqual(current['parentTaskId'], joined['parentTaskId'])
            self.assertIn(joined['parentTaskId'], tasks)
            self.assertEqual(joined['phase'], 'joining')
            first_id = snapshot['tasks'][0]['id']
            snapshot['tasks'][0]['id'] = 'client-mutation'
            self.assertEqual(run.debugger()['taskSnapshot']['tasks'][0]['id'], first_id)
            run.debug_command('breakpoints', breakpoints=[])
            run.debug_command('continue', pause_id=state['pauseId'])
            self.assertEqual(run.result(4), [41,42,41])
            self.assertEqual(run.debugger()['taskSnapshot'], {'tasks':[],'total':0,'truncated':False})

    def test_checkpoint_copies_only_at_pause_and_keeps_three_argument_compatibility(self):
        project = {'id':'pure-debug','version':1,'entry':'main.chaos','files':{'main.chaos':'42'}}
        session = DebugSession('run', project, options({'pauseOnEntry':True}, project['files']),
                               threading.Event(), lambda *args: None)
        self.addCleanup(lambda: session.finish('cancelled'))
        node = Parser('42', 'main.chaos', ranges=True).program()[1][0]
        supplied = {'tasks':[{'id':'task-1','memberTaskIds':['task-2']}], 'total':2, 'truncated':True}
        failures = []
        def pause():
            try: session.checkpoint(node, None, [], task_snapshot=supplied)
            except BaseException as exc: failures.append(exc)
        thread = threading.Thread(target=pause, daemon=True)
        thread.start()
        state = self.paused(session.snapshot)
        supplied['tasks'][0]['memberTaskIds'].append('task-3')
        self.assertEqual(session.snapshot()['taskSnapshot']['tasks'][0]['memberTaskIds'], ['task-2'])
        session.command('continue', pause_id=state['pauseId']);thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(failures, [])
        self.assertEqual(session.snapshot()['taskSnapshot'], {'tasks':[],'total':0,'truncated':False})
        # No pause is requested, so neither legacy nor metadata-aware calls store it.
        self.assertEqual(session.checkpoint(node, None, []), 0)
        self.assertEqual(session.checkpoint(node, None, [], task_snapshot=supplied), 0)
        self.assertEqual(session.snapshot()['taskSnapshot']['tasks'], [])
