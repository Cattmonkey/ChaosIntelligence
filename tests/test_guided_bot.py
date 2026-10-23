"""Real language controller, host guidance and observable stream share one run."""
from pathlib import Path
import threading
import time
import unittest
from chaos import Framework, Registry, ToolSpec, Settings
from chaos.store import Conflict

CONTROLLER=Path(__file__).resolve().parents[1].joinpath('examples/guided-bot.chaos').read_text()

class GuidedBotTests(unittest.TestCase):
    def setup_run(self,source,registry=None):
        app=Framework(registry=registry,settings=Settings(':memory:'))
        self.addCleanup(app.close)
        project=app.create_project('bot',source=source,files={'bot.chaos':CONTROLLER},capabilities=['hold','act'] if registry else [])
        return app,project

    def test_guidance_during_cognition_discards_stale_proposal(self):
        entered=threading.Event();release=threading.Event();calls=[];effects=[]
        registry=Registry()
        def hold(value,ctx):
            calls.append(value)
            if len(calls)==1:
                entered.set()
                while not release.wait(.01):ctx.checkpoint()
            return 'old' if not value else value[-1]['input']['text']
        registry.register_tool(ToolSpec('hold',hold))
        registry.register_tool(ToolSpec('act',lambda value,ctx:effects.append(value) or value))
        source='''import "bot.chaos" as B;
async fn planner(state){tool("hold",state.guidance)}
async fn execute(proposal,state){tool("act",proposal)}
async fn verify(state){{done:true,output:state.evidence}}
await B.guided_bot("long goal",planner,execute,verify)'''
        app,project=self.setup_run(source,registry)
        run=app.start('bot',1,None,mode='source')
        self.assertTrue(entered.wait(3))
        receipt=run.guide({'text':'new direction'},message_id='guide-one')
        self.assertEqual(receipt['status'],'queued')
        self.assertEqual(run.guide({'text':'new direction'},message_id='guide-one'),receipt)
        with self.assertRaises(Conflict):run.guide({'text':'different'},message_id='guide-one')
        release.set();result=run.result(10)
        self.assertEqual(effects,['new direction']);self.assertEqual(len(calls),2)
        self.assertEqual(result['output'],['new direction'])
        self.assertEqual(result['state']['guidance'][0]['revision'],1)
        events=app.events('bot',run_id=run.id,limit=300)['items']
        self.assertEqual(sum(e['type']=='guidance.queued' for e in events),1)
        self.assertEqual(sum(e['type']=='guidance.received' for e in events),1)
        kinds=[e['payload']['kind'] for e in events if e['type']=='bot.progress']
        self.assertIn('plan',kinds);self.assertIn('progress',kinds);self.assertIn('result',kinds)
        with self.assertRaises(Conflict):run.guide('too late')

    def test_null_fifo_copy_and_progress_cannot_spoof_receipts(self):
        app,project=self.setup_run('let a=await guidance_receive(null);let b=await guidance_receive(null);[a.message.input,b.message.input]')
        run=app.start('bot',1,None,mode='source');body={'x':[1]}
        run.guide(None,message_id='null');run.guide(body,message_id='copy');body['x'].append(2)
        self.assertEqual(run.result(5),[None,{'x':[1]}])
        bad=app.create_project('bad',source='try{await progress_emit("guidance.received",{})}catch e{e.code}',capabilities=[])
        self.assertEqual(app.start('bad',1,None,mode='source').result(5),'argument_error')

    def test_waiting_cancellation_and_late_submit(self):
        app,project=self.setup_run('await guidance_receive(null)');run=app.start('bot',1,None,mode='source')
        self.assertTrue(run.cancel());self.assertEqual(run.wait(5)['status'],'cancelled')
        self.assertNotIn(run.id,app._engine.guidance)
        with self.assertRaises(Conflict):run.guide({'text':'late'})

    def test_numeric_heavy_guidance_and_progress_bounds_are_serialized(self):
        app,project=self.setup_run('await guidance_receive(null)');run=app.start('bot',1,None,mode='source')
        with self.assertRaises(ValueError):run.guide([10**4000]*11,message_id='oversize')
        self.assertFalse(any(e['type']=='guidance.queued' for e in app.events('bot',run_id=run.id)['items']))
        run.cancel();run.wait(5)
        bad=app.create_project('progress',source='try{await progress_emit("result",input)}catch e{e.code}',capabilities=[])
        self.assertEqual(app.start('progress',1,[10**4000]*3,mode='source').result(5),'argument_error')
        self.assertFalse(any(e['type']=='bot.progress' for e in app.events('progress')['items']))
