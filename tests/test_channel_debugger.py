"""Only bounded channel metadata crosses a true paused interpreter checkpoint."""
import time
import unittest
from chaos import Framework,Settings
from chaos.debugger import preview
from chaos.language import Evaluator

SOURCE='''async fn run() {
  let ch=channel();
  async with task_group() as group {
    let pending=group.spawn(channel_send(ch,{secret:"channel-payload"}));
    while channel_info(ch).waitingSenders==0 { null }
    let metadata=channel_info(ch);
    let first=await channel_receive(ch);
    channel_close(ch);
    [first,channel_info(ch).state]
  }
}
await run()'''

class ChannelDebugger(unittest.TestCase):
    def test_real_blocked_sender_snapshot_readonly_continue(self):
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('channel-debug',source=SOURCE)
            run=app.start_debug(project['id'],1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':6}])
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                state=run.debugger()
                if state['status']=='paused':break
                time.sleep(.005)
            self.assertEqual(state['status'],'paused')
            snapshot=state['taskSnapshot'];self.assertEqual(snapshot['channelTotal'],1);self.assertFalse(snapshot['channelsTruncated'])
            channel=snapshot['channels'][0];self.assertEqual(channel['state'],'open');self.assertEqual(channel['capacity'],0)
            self.assertEqual(channel['waitingSenders'],1);self.assertEqual(channel['waitingReceivers'],0);self.assertEqual(channel['buffered'],0)
            task=next(row for row in snapshot['tasks'] if row['id']==channel['sendTaskIds'][0])
            self.assertEqual(task['kind'],'channel_send');self.assertEqual(task['phase'],'channel_send');self.assertEqual(task['channelId'],channel['id'])
            self.assertNotIn('channel-payload',str(snapshot));self.assertNotIn('buffer',channel)
            channel['sendTaskIds'].append('client mutation');channel['state']='fake'
            self.assertEqual(run.debugger()['taskSnapshot']['channels'][0]['state'],'open')
            self.assertEqual(run.debugger()['taskSnapshot']['channels'][0]['sendTaskIds'],[task['id']])
            local=next(row['value']for frame in state['frames']for row in frame['locals']if row['name']=='ch')
            self.assertEqual(local['type'],'Channel');self.assertNotIn('channel-payload',str(local))
            run.debug_command('breakpoints',breakpoints=[]);run.debug_command('continue',pause_id=state['pauseId'])
            self.assertEqual(run.result(4),[{'done':False,'value':{'secret':'channel-payload'}},'closed'])
            self.assertFalse(run.debugger()['taskSnapshot'].get('channels',[]))

    def test_native_preview_excludes_messages_and_live_runtime(self):
        seen=[];evaluator=Evaluator()
        def hook(node,env,frames):
            try:channel=env.get('ch')
            except Exception:return
            seen.append(preview(channel))
        evaluator.debug_hook=hook
        self.assertEqual(evaluator.run('let ch=channel(1);await channel_send(ch,"private-buffered-value");channel_info(ch).buffered'),1)
        value=next(item for item in reversed(seen)if item.get('type')=='Channel' and item.get('buffered')==1)
        self.assertEqual(set(value),{'type','id','state','capacity','buffered','waitingSenders','waitingReceivers'})
        self.assertNotIn('private-buffered-value',str(seen));self.assertNotIn('owner',value)
