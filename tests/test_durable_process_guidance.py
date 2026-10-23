"""The original Process inbox is the durable input boundary, not an application queue."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Framework,Settings,Registry,ToolSpec
from chaos.store import Conflict


DEFINITION={'kind':'process','name':'dot','module':'dot.chaos','export':'step','intervalSeconds':None,'maxTurns':10}


class DurableProcessGuidanceTests(unittest.TestCase):
    def setup_app(self,path=':memory:',source='fn step(c){process_step(c.state,c.input,true)};export {step}'):
        app=Framework(path,settings=Settings(':memory:'));self.addCleanup(app.close)
        app._engine.pause_dispatch(True)
        app.create_project('p',source='null',files={'dot.chaos':source},capabilities=['process.manage'])
        app.create_process('p',1,'dot',DEFINITION,state={'n':0})
        return app

    def guide(self,app,value,message_id):
        return app._engine.process_operation('p','guide',{'key':'dot','input':value,'messageId':message_id})

    def wait(self,app,predicate):
        end=time.monotonic()+4
        while time.monotonic()<end:
            row=app.get_process('p','dot')
            if predicate(row):return row
            time.sleep(.01)
        self.fail(str(row))

    def test_default_and_digit_leading_ids_replay(self):
        app=self.setup_app();first=app.send_process('p','dot',42)
        self.assertEqual(app.send_process('p','dot',42,message_id=first['messageId']),first)
        receipt=self.guide(app,{'goal':'动态上下文'},'0-guidance')
        self.assertEqual(self.guide(app,{'goal':'动态上下文'},'0-guidance'),receipt)
        self.assertEqual(receipt['revision'],1)

    def test_purpose_and_payload_conflicts_are_one_identity(self):
        app=self.setup_app();self.guide(app,42,'same')
        with self.assertRaises(Conflict):app.send_process('p','dot',42,message_id='same')
        with self.assertRaises(Conflict):self.guide(app,43,'same')
        app.send_process('p','dot',42,message_id='message')
        with self.assertRaises(Conflict):self.guide(app,42,'message')

    def test_guidance_is_never_ordinary_turn_input(self):
        app=self.setup_app();self.guide(app,{'guide':1},'g');app.send_process('p','dot',{'ordinary':2},message_id='m')
        app._engine.pause_dispatch(False)
        row=self.wait(app,lambda r:r['status']=='completed')
        self.assertIsNone(row['output'])
        item=app._store.db.execute("SELECT status,input FROM process_messages WHERE message_id='g'").fetchone()
        self.assertEqual(item['status'],'guide_queued');self.assertEqual(json.loads(item['input'])['input'],{'guide':1})

    def test_original_receive_commits_ack_with_original_process_step(self):
        app=self.setup_app(source='async fn step(c){let m=await guidance_receive();process_step(c.state,m.message.input,true)};export {step}')
        self.guide(app,{'guide':1},'g');app._engine.pause_dispatch(False)
        row=self.wait(app,lambda r:r['status']=='completed');self.assertEqual(row['output'],{'guide':1})
        item=app._store.db.execute("SELECT status,input FROM process_messages WHERE message_id='g'").fetchone()
        self.assertEqual(item['status'],'guide_consumed');self.assertIsNone(json.loads(item['input'])['input'])
        self.assertEqual(self.guide(app,{'guide':1},'g')['status'],'applied')

    def test_queued_receipts_survive_reopen_with_exact_fifo_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.sqlite3';app=self.setup_app(path)
            a=self.guide(app,{'guide':1},'a');b=self.guide(app,{'guide':2},'b')
            before=[tuple(row) for row in app._store.db.execute('SELECT seq,message_id,input_hash,status,input FROM process_messages ORDER BY seq')]
            app.close()
            reopened=Framework(path,settings=Settings(':memory:'));self.addCleanup(reopened.close);reopened._engine.pause_dispatch(True)
            self.assertEqual(self.guide(reopened,{'guide':1},'a'),a);self.assertEqual(self.guide(reopened,{'guide':2},'b'),b)
            after=[tuple(row) for row in reopened._store.db.execute('SELECT seq,message_id,input_hash,status,input FROM process_messages ORDER BY seq')]
            self.assertEqual(before,after)

    def test_failed_queue_transaction_has_no_receipt_or_revision(self):
        app=self.setup_app();old=app.get_process('p','dot')
        with patch.object(app._store,'_event',side_effect=OSError('journal unavailable')):
            with self.assertRaises(OSError):self.guide(app,{'guide':1},'g')
        self.assertEqual(app.get_process('p','dot'),old)
        self.assertEqual(app._store.db.execute('SELECT count(*) FROM process_messages').fetchone()[0],0)

    def test_guide_bound_and_invalid_ids_do_not_mutate(self):
        app=self.setup_app()
        for value,identity in [('x'*40001,'big'),(1,'space id'),(1,''),(1,False)]:
            with self.subTest(identity=identity),self.assertRaises(ValueError):self.guide(app,value,identity)
        self.assertEqual(app._store.db.execute('SELECT count(*) FROM process_messages').fetchone()[0],0)

    def test_full_sync_and_original_schema_are_retained(self):
        app=self.setup_app()
        self.assertEqual(app._store.db.execute('PRAGMA synchronous').fetchone()[0],2)
        columns=[r['name'] for r in app._store.db.execute('PRAGMA table_info(process_messages)')]
        self.assertEqual(columns,['seq','project_id','key','message_id','input','input_hash','status','run_id','claim_event_id','reconciled_event_id'])

    def test_receive_journal_failure_does_not_ack_or_lose_input(self):
        source='async fn step(c){try{let m=await guidance_receive();process_step(c.state,m.message.input,true)}catch e{process_step(c.state,null,false)}};export {step}'
        app=self.setup_app(source=source);self.guide(app,{'guide':1},'g')
        original=app._store._event
        def fail_received(project_id,kind,payload,run_id=None):
            if kind=='guidance.received':
                app._engine.pause_dispatch(True)
                raise OSError('received journal unavailable')
            return original(project_id,kind,payload,run_id)
        with patch.object(app._store,'_event',side_effect=fail_received):
            app._engine.pause_dispatch(False);self.wait(app,lambda r:r['turn']==1)
        item=app._store.db.execute("SELECT status,input FROM process_messages WHERE message_id='g'").fetchone()
        self.assertEqual(item['status'],'guide_queued');self.assertEqual(json.loads(item['input'])['input'],{'guide':1})
        app._engine.pause_dispatch(False);row=self.wait(app,lambda r:r['status']=='completed')
        self.assertEqual(row['turn'],2);self.assertEqual(row['output'],{'guide':1})

    def test_achieved_checkpoint_seals_guidance_during_real_wrapper_gap(self):
        entered=threading.Event();release=threading.Event();registry=Registry()
        def gate(value,context):
            entered.set();self.assertTrue(release.wait(3));return value
        registry.register_tool(ToolSpec('fixture.gate',gate))
        app=Framework(settings=Settings(':memory:'),registry=registry);self.addCleanup(app.close)
        app._engine.pause_dispatch(True)
        source='async fn step(c){let b=bot("goal",fn(s){1},fn(p,s){p},fn(s){{done:true,context:s.context,output:s.evidence}},c.state,1);let r=await b();tool("fixture.gate",r.state);process_step(r.state,r.output,r.done)};export {step}'
        app.create_project('p',source='null',files={'dot.chaos':source},capabilities=['process.manage','fixture.gate'])
        app.create_process('p',1,'dot',DEFINITION,state={'n':0});self.guide(app,{'guide':1},'g')
        app._engine.pause_dispatch(False)
        try:
            self.assertTrue(entered.wait(3));row=app.get_process('p','dot')
            self.assertIsNone(row['pending']['botWork']['scope']);self.assertTrue(row['pending']['botWork']['checkpoint']['done'])
            with self.assertRaises(Conflict):self.guide(app,{'late':1},'late')
            with self.assertRaises(Conflict):app.guide(row['lastRunId'],{'late':1},message_id='late-run')
            self.assertEqual(self.guide(app,{'guide':1},'g')['status'],'applied')
        finally:release.set()
        self.wait(app,lambda r:r['status']=='completed')

