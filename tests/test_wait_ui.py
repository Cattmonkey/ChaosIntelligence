"""Debugger timing is a frozen actual scheduler snapshot, never a UI clock."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.language import Evaluator

ROOT=Path(__file__).resolve().parent.parent

class WaitUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_actual_sleep_wait_snapshot_render_and_resume(self):
        snapshots=[];evaluator=Evaluator()
        def checkpoint(*args):
            snapshot=evaluator.suspension.snapshot()
            if any(t['phase']=='waiting_tasks'for t in snapshot['tasks']):snapshots.append(snapshot)
        evaluator.debug_hook=checkpoint
        output=evaluator.run('let timer=sleep(30);let waiter=spawn(task_wait([timer],until="all",timeout=20));while task_info(waiter).phase!="waiting_tasks"{null};let before=task_info(timer);let cancelled=cancel(waiter);let borrowed=task_info(timer).state;cancel(timer);[cancelled,borrowed]')
        self.assertEqual(output,[True,'running']);self.assertTrue(snapshots)
        snapshot=snapshots[0]
        self.assertTrue(any(t['phase']=='sleeping'for t in snapshot['tasks']))
        self.assertTrue(any(t['kind']=='wait'and t['memberCount']==1 for t in snapshot['tasks']))
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/wait_controls.cjs')],input=json.dumps(snapshot),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'passed')

if __name__=='__main__':unittest.main()
