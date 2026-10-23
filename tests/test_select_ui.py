"""Selection cards consume actual bounded snapshots without touching queues."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.language import Evaluator

ROOT=Path(__file__).resolve().parent.parent

class SelectUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_real_selection_snapshot_cancel_and_safe_ui(self):
        snapshots=[];completed=[];evaluator=Evaluator()
        def checkpoint(*args):
            snapshot=evaluator.suspension.snapshot()
            if any(t['phase']=='channel_select'for t in snapshot['tasks']):snapshots.append(snapshot)
            if any(t.get('selection',{}).get('index')==0 for t in snapshot['tasks']if t.get('selection')):completed.append(snapshot)
        evaluator.debug_hook=checkpoint
        output=evaluator.run('let left=channel();let right=channel();let choice=spawn(channel_select([receive_case(left),receive_case(right)],timeout=30));while task_info(choice).phase!="channel_select"{null};let before=task_info(choice);let stopped=cancel(choice);let buffered=channel(1);await channel_send(buffered,42);let winner=await channel_select([receive_case(buffered)]);let won=winner.index;[stopped,channel_info(left).waitingReceivers,channel_info(right).waitingReceivers,task_info(choice).state,won]')
        self.assertEqual(output,[True,0,0,'cancelled',0]);self.assertTrue(snapshots);self.assertTrue(completed)
        snapshot=snapshots[0];task=next(t for t in snapshot['tasks']if t['kind']=='channel_select');self.assertEqual(task['selection']['caseCount'],2);self.assertEqual(task['memberTaskIds'],[])
        self.assertEqual([item['index']for item in task['selection']['cases']],[0,1]);self.assertEqual(task['timing']['timeoutSeconds'],30)
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/select_controls.cjs')],input=json.dumps({'pending':snapshot,'completed':completed[-1]}),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(json.loads(result.stdout)['status'],'passed')

if __name__=='__main__':unittest.main()
