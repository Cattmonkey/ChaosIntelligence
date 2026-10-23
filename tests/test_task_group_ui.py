"""Group cards render bounded metadata produced by the real scheduler."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.language import Evaluator

ROOT=Path(__file__).resolve().parent.parent


class TaskGroupUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_real_group_metadata_truncation_frame_link_and_resume(self):
        snapshots=[];evaluator=Evaluator()
        def hook(*args):
            snapshot=evaluator.suspension.snapshot()
            if snapshot['groups'] and snapshot['groups'][0]['memberCount']==101:snapshots.append(snapshot)
        evaluator.debug_hook=hook
        evaluator.run('let groups=map(fn(n){task_group()},range(105));async fn work(n){n};async with groups[0] as group {let tasks=map(fn(n){group.spawn(work(n))},range(101));task_group_info(group)}')
        self.assertTrue(snapshots)
        fixture=snapshots[-1];self.assertEqual(fixture['groupTotal'],105);self.assertTrue(fixture['groupsTruncated'])
        self.assertEqual(fixture['groups'][0]['memberCount'],101);self.assertTrue(fixture['groups'][0]['membersTruncated'])
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/task_group_controls.cjs')],input=json.dumps(fixture),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(json.loads(result.stdout)['status'],'passed')
