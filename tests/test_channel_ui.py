"""The channel panel consumes real capped scheduler metadata without messages."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from chaos.language import Evaluator

ROOT=Path(__file__).resolve().parent.parent

class ChannelUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is unavailable')
    def test_real_channel_snapshot_caps_and_read_only_resume(self):
        snapshots=[];evaluator=Evaluator()
        def hook(*args):
            snapshot=evaluator.suspension.snapshot()
            if snapshot['channels'] and snapshot['channels'][0]['waitingSenders']==101:snapshots.append(snapshot)
        evaluator.debug_hook=hook
        evaluator.run('let channels=map(fn(n){channel()},range(105));let ch=channels[0];let jobs=map(fn(n){spawn(channel_send(ch,"hidden-payload"))},range(101));while channel_info(ch).waitingSenders<101{null};let marker=channel_info(ch);channel_close(ch);try{await gather(jobs)}catch e{null}')
        self.assertTrue(snapshots);snapshot=snapshots[-1]
        self.assertEqual(snapshot['channelTotal'],105);self.assertTrue(snapshot['channelsTruncated'])
        self.assertEqual(snapshot['channels'][0]['waitingSenders'],101);self.assertTrue(snapshot['channels'][0]['sendersTruncated'])
        self.assertNotIn('hidden-payload',str(snapshot))
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/channel_controls.cjs')],input=json.dumps(snapshot),text=True,capture_output=True,cwd=ROOT,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(json.loads(result.stdout)['status'],'passed')
