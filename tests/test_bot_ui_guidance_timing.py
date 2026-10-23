"""Actual host phase barriers; a running Run is not necessarily an active Bot."""
from contextlib import contextmanager
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Framework, Registry, Settings
from chaos.runtime import Context


@contextmanager
def hold_bot_plan_until_guide():
    original=Context.guidance_operation
    observed=[]
    def operation(context,kind,payload,capabilities=None,team_scope=None):
        receipt=original(context,kind,payload,capabilities,team_scope)
        if kind=='progress' and payload.get('kind')=='plan':
            deadline=time.monotonic()+8
            while True:
                context.budget.tick()
                with context.engine.idle:
                    box=context.engine.guidance.get(context.run_id)
                    if context.run_id not in context.engine.active or box is None:
                        raise AssertionError('Planning barrier lost its actual active Run')
                    if box['revision']>=1:
                        observed.append((context.run_id,receipt['eventId'],box['revision']))
                        break
                if time.monotonic()>=deadline:
                    raise AssertionError('Actual UI guidance did not enter the planning mailbox')
                time.sleep(.005)
        return receipt
    with patch.object(Context,'guidance_operation',operation):
        yield
        if not observed:raise AssertionError('The real planning progress barrier was not exercised')


class BotGuidanceTiming(unittest.TestCase):
    def test_closed_bot_in_active_run_keeps_late_guidance_as_generic_queued_input(self):
        entered=threading.Event();release=threading.Event();registry=Registry()
        @registry.tool('fixture.after_bot')
        def after_bot(value,context):
            entered.set();deadline=time.monotonic()+8
            while not release.wait(.005):
                context.checkpoint()
                if time.monotonic()>deadline:raise AssertionError('Tail fixture was not released')
            return value
        source=('let worker=bot("goal",fn(s){"old proposal"},fn(p,s){p},'
                'fn(s){{done:true,context:null,output:s.evidence[0]}});'
                'let answer=await worker();tool("fixture.after_bot",null);answer')
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('timing',source=source,capabilities=['fixture.after_bot'])
            run=app.start('timing',1,None,mode='source')
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(run.get()['status'],'running')
                self.assertEqual(app.guidance_scopes(run.id),{'runId':run.id,'active':True,'scopes':[]})
                receipt=run.guide({'text':'late input'},'late')
                self.assertEqual(receipt['status'],'queued')
                release.set();actual=run.result(5)
                self.assertEqual(actual['status'],'achieved');self.assertEqual(actual['output'],'old proposal')
                events=app.events('timing',run_id=run.id,limit=300)['items']
                closed=next(e for e in events if e['type']=='bot.scope.closed')
                queued=next(e for e in events if e['type']=='guidance.queued')
                self.assertLess(closed['id'],queued['id'])
                self.assertFalse(any(e['type']in('guidance.received','bot.context.applied') for e in events))
                self.assertEqual(queued['payload']['messageId'],'late')
            finally:
                release.set();run.cancel()
