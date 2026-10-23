"""Native groups and their scope tasks share the exact paused interpreter view."""
import threading
import time
import unittest
from chaos import Framework, Settings
from chaos.debugger import preview
from chaos.language import Evaluator

SOURCE='''async fn child(value:Int)->Int {
  let answer=value+1;
  answer
}
async with task_group() as group {
  let first=group.spawn(child(40));
  let second=group.spawn(child(41));
  [await first,await second]
}'''


class TaskGroupDebugger(unittest.TestCase):
    def test_paused_groups_scope_tasks_and_exact_continue(self):
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('task-group-debug',source=SOURCE)
            run=app.start_debug(project['id'],1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}])
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                state=run.debugger()
                if state['status']=='paused':break
                time.sleep(.005)
            self.assertEqual(state['status'],'paused')
            snapshot=state['taskSnapshot'];self.assertEqual(snapshot['groupTotal'],1);self.assertFalse(snapshot['groupsTruncated'])
            group=snapshot['groups'][0];tasks={row['id']:row for row in snapshot['tasks']}
            active=tasks[state['frames'][0]['taskId']]
            self.assertEqual(active['groupId'],group['id']);self.assertIn(active['id'],group['memberTaskIds'])
            self.assertEqual(tasks[group['bodyTaskId']]['kind'],'scope');self.assertNotIn(group['bodyTaskId'],group['memberTaskIds'])
            self.assertEqual(tasks[group['bodyTaskId']]['groupId'],group['id']);self.assertIsNone(group['ownerTaskId'])
            original=list(group['memberTaskIds']);group['memberTaskIds'].append('client mutation')
            self.assertEqual(run.debugger()['taskSnapshot']['groups'][0]['memberTaskIds'],original)
            run.debug_command('breakpoints',breakpoints=[]);run.debug_command('continue',pause_id=state['pauseId'])
            self.assertEqual(run.result(4),[41,42]);self.assertFalse(run.debugger()['taskSnapshot'].get('groups',[]))

    def test_group_local_preview_does_not_expose_live_scheduler_or_execute_methods(self):
        evaluator=Evaluator();seen=[]
        def hook(node,env,frames):
            try:group=env.get('group')
            except Exception:return
            seen.append(preview(group))
        evaluator.debug_hook=hook
        self.assertEqual(evaluator.run('async with task_group() as group {task_group_info(group).state}'),'active')
        value=next(row for row in seen if row.get('type')=='TaskGroup')
        self.assertEqual(set(value),{'type','id','state','ownerTaskId','bodyTaskId','parentGroupId','memberTaskIds','memberCount','membersTruncated'})
        self.assertNotIn('owner',value);self.assertNotIn('body',value)
        self.assertFalse(any(t.name.startswith('chaos-effect') and t.is_alive() for t in threading.enumerate()))
