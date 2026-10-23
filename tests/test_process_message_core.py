"""Exact ordinary mailbox outcomes follow host-certified actual run state."""
import json
import unittest
from unittest.mock import patch

from chaos.process_store import MESSAGE_OUTCOME_SQL
from chaos.store import Store, Conflict, NotFound
from tests.test_process_integration import definition


def fixture(path=':memory:'):
    store=Store(path)
    store.create_project('p','Messages',{'nodes':[],'edges':[]},'42',
        files={'dot.chaos':'fn step(c){process_step(c.state,c.input)};export {step}'},capabilities=['process.manage'])
    store.processes.create('p',1,'dot',definition(),0,{'process.manage'})
    claim=store.processes.claim('p','dot','bootstrap')
    store.transition_run('bootstrap','completed',{}, {'state':0,'output':'start','done':False},_process=claim)
    return store


def send(store,ident='one',value=42):
    return store.processes.send('p','dot',value,ident,{'process.manage'},lambda x:x)


def complete(store,ident='one',output=42):
    claim=store.processes.claim('p','dot','run-'+ident)
    store.transition_run('run-'+ident,'completed',{}, {'state':claim['pending']['turn'],'output':output,'done':False},_process=claim)
    return claim


class ProcessMessageCore(unittest.TestCase):
    def app(self):
        store=fixture();self.addCleanup(store.close);return store

    def test_queued_projection_is_one_read_without_any_mutation(self):
        store=self.app();send(store);before=store.db.total_changes;sql=[]
        store.db.set_trace_callback(sql.append)
        result=store.processes.message('p','dot','one')
        store.db.set_trace_callback(None)
        self.assertEqual(result,{'key':'dot','messageId':'one','mailboxStatus':'queued','linkStatus':'unclaimed','run':None,'reconciliation':None})
        self.assertEqual(store.db.total_changes,before)
        self.assertEqual(len(sql),1);self.assertTrue(sql[0].startswith('SELECT '))

    def test_claimed_completed_and_old_output_remain_exact_after_later_turns(self):
        store=self.app();send(store);claim=store.processes.claim('p','dot','run-one')
        queued=store.processes.message('p','dot','one')
        self.assertEqual((queued['mailboxStatus'],queued['linkStatus'],queued['run']['status']),('claimed','linked','queued'))
        store.transition_run('run-one','running',{})
        self.assertEqual(store.processes.message('p','dot','one')['run']['status'],'running')
        original={'state':1,'output':{'answer':42},'done':False}
        store.transition_run('run-one','completed',{},original,_process=claim)
        first=store.processes.message('p','dot','one')
        self.assertEqual(first['run']['output'],original)
        send(store,'two',99);complete(store,'two',99)
        self.assertEqual(store.processes.message('p','dot','one'),first)
        self.assertEqual(send(store,'one',42)['status'],'consumed')
        self.assertEqual(store.processes.message('p','dot','one'),first)
        with self.assertRaises(Conflict):send(store,'one','changed')
        self.assertEqual(set(first),{'key','messageId','mailboxStatus','linkStatus','run','reconciliation'})
        self.assertEqual(set(first['run']),{'id','version','status','output','error','created','updated'})

    def test_failed_cancelled_interrupted_remain_unsuccessful_after_reconciliation(self):
        for status in ('failed','cancelled','interrupted'):
            with self.subTest(status=status):
                store=self.app();send(store);claim=store.processes.claim('p','dot','run-one')
                store.transition_run('run-one',status,{},None,'actual '+status,_process=claim)
                before=store.processes.message('p','dot','one')
                self.assertEqual(before['mailboxStatus'],'claimed');self.assertEqual(before['run']['status'],status)
                self.assertIsNone(before['reconciliation'])
                row=store.processes.get('p','dot')
                store.processes.reconcile('p','dot',row['revision'],7,'explicit evidence',False,{'process.manage'})
                result=store.processes.message('p','dot','one')
                self.assertEqual(result['mailboxStatus'],'consumed');self.assertEqual(result['run'],before['run'])
                self.assertEqual(result['reconciliation']['evidence'],'explicit evidence')
                self.assertEqual(result['reconciliation']['runId'],'run-one')
                self.assertEqual(set(result['reconciliation']),{'eventId','runId','evidence','at'})

    def test_restart_retains_exact_claim_without_replaying(self):
        store=self.app();send(store);store.processes.claim('p','dot','run-one')
        store.recover()
        result=store.processes.message('p','dot','one')
        self.assertEqual(result['run']['status'],'interrupted');self.assertEqual(result['mailboxStatus'],'claimed')
        self.assertIsNone(result['reconciliation'])
        self.assertEqual(store.processes.get('p','dot')['status'],'needs_reconciliation')

    def test_guidance_namespaces_and_missing_addresses_are_not_ordinary_messages(self):
        store=self.app()
        store.processes.guide('p','dot','guide','same',{'process.manage'},lambda x:x)
        for project,key,ident in [('p','dot','same'),('p','dot','missing'),('other','dot','same'),('p','other','same')]:
            with self.subTest(project=project,key=key,ident=ident),self.assertRaises(NotFound):
                store.processes.message(project,key,ident)

    def test_ids_are_strict_before_any_sql_and_none_is_not_generated(self):
        store=self.app();sql=[];store.db.set_trace_callback(sql.append)
        for identity in (None,True,1,[],{},'', 'x'*129,'has space'):
            with self.subTest(identity=identity),self.assertRaises(ValueError):store.processes.message('p','dot',identity)
        for project in (None,True,'x'*65):
            with self.assertRaises(ValueError):store.processes.message(project,'dot','one')
        store.db.set_trace_callback(None);self.assertEqual(sql,[])

    def test_partial_wrong_scope_and_forged_run_input_certificates_fail_closed(self):
        store=self.app();send(store);project=store.project('p')
        store.create_run('forged',project,'source',{'process':{'key':'dot','messageId':'one'}})
        for run_id,event_id in [('forged',None),('forged',1),('missing',1)]:
            with self.subTest(run_id=run_id,event_id=event_id):
                store.db.execute("UPDATE process_messages SET status='claimed',run_id=?,claim_event_id=? WHERE message_id='one'",(run_id,event_id))
                with self.assertRaises(Conflict):store.processes.message('p','dot','one')

    def test_real_certificate_requires_claim_event_payload_and_private_lineage(self):
        store=self.app();send(store);complete(store)
        event=store.db.execute("SELECT claim_event_id FROM process_messages WHERE message_id='one'").fetchone()[0]
        original=store.db.execute('SELECT payload FROM events WHERE id=?',(event,)).fetchone()[0]
        for key,value in [('messageId','wrong'),('key','wrong'),('runId','wrong'),('kind','timer'),('version',99),('turn',True)]:
            payload=json.loads(original);payload[key]=value
            store.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),event))
            with self.subTest(key=key),self.assertRaises(Conflict):store.processes.message('p','dot','one')
        store.db.execute('UPDATE events SET payload=? WHERE id=?',(original,event))
        store.db.execute('DELETE FROM process_event_lineage WHERE event_id=?',(event,))
        with self.assertRaises(Conflict):store.processes.message('p','dot','one')

    def test_queued_message_with_a_certificate_cannot_be_replayed(self):
        store=self.app();send(store);complete(store)
        store.db.execute("UPDATE process_messages SET status='queued' WHERE message_id='one'")
        before=store.db.total_changes
        with self.assertRaises(Conflict):store.processes.claim('p','dot','must-not-exist')
        with self.assertRaises(Conflict):store.processes.message('p','dot','one')
        with self.assertRaises(NotFound):store.run('must-not-exist')
        self.assertEqual(store.db.total_changes,before)

    def test_claim_failure_rolls_back_run_message_and_certificate_atomically(self):
        store=self.app();send(store);original=store._event
        def fail(project,kind,*args):
            if kind=='process.turn.queued':raise RuntimeError('claim event fault')
            return original(project,kind,*args)
        with patch.object(store,'_event',side_effect=fail),self.assertRaisesRegex(RuntimeError,'claim event fault'):
            store.processes.claim('p','dot','never')
        self.assertEqual(store.processes.message('p','dot','one')['linkStatus'],'unclaimed')
        self.assertIsNone(store.processes.get('p','dot')['pending'])
        with self.assertRaises(NotFound):store.run('never')

    def test_reconciliation_event_failure_preserves_pending_claim_and_no_fake_ack(self):
        store=self.app();send(store);claim=store.processes.claim('p','dot','run-one')
        store.transition_run('run-one','failed',{},None,'fail',_process=claim)
        row=store.processes.get('p','dot');before=store.processes.message('p','dot','one');original=store._event
        def fail(project,kind,*args):
            if kind=='process.reconciled':raise RuntimeError('reconcile fault')
            return original(project,kind,*args)
        with patch.object(store,'_event',side_effect=fail),self.assertRaisesRegex(RuntimeError,'reconcile fault'):
            store.processes.reconcile('p','dot',row['revision'],9,'proof',False,{'process.manage'})
        self.assertEqual(store.processes.message('p','dot','one'),before)
        self.assertEqual(store.processes.get('p','dot')['pending'],row['pending'])

    def test_query_plan_uses_message_unique_index_and_pk_joins(self):
        store=self.app();send(store);complete(store)
        plan=[row[3]for row in store.db.execute('EXPLAIN QUERY PLAN '+MESSAGE_OUTCOME_SQL,('p','dot','one'))]
        self.assertTrue(any('SEARCH m USING INDEX sqlite_autoindex_process_messages' in row for row in plan),plan)
        self.assertFalse(any(row.startswith('SCAN ')for row in plan),plan)
