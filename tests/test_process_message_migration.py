"""Only three nullable columns; bounded, atomic, privately certified backfill."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from chaos.ownership import RuntimeLease
from chaos.process_store import initialize, MESSAGE_LINK_COLUMNS
from chaos.store import Store
from tests.test_process_message_core import fixture, send, complete


OLD_COLUMNS=('seq','project_id','key','message_id','input','input_hash','status')


def schema(db):
    return [tuple(row)for row in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]


def old_messages(db):
    return [tuple(row)for row in db.execute('SELECT '+','.join(OLD_COLUMNS)+' FROM process_messages ORDER BY seq')]


def downgrade(db):
    rows=old_messages(db)
    db.execute('DROP TABLE process_messages')
    db.execute('''CREATE TABLE process_messages (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, key TEXT,
        message_id TEXT, input TEXT, input_hash TEXT, status TEXT,
        UNIQUE(project_id,key,message_id))''')
    db.execute('CREATE INDEX process_messages_fifo ON process_messages(project_id,key,status,seq)')
    db.executemany('INSERT INTO process_messages VALUES(?,?,?,?,?,?,?)',rows)


class ProcessMessageMigration(unittest.TestCase):
    def legacy(self,path):
        store=fixture(path);send(store,'one',42);complete(store,'one',42)
        send(store,'two',99);claim=store.processes.claim('p','dot','run-two')
        store.transition_run('run-two','failed',{},None,'original failure',_process=claim)
        row=store.processes.get('p','dot')
        store.processes.reconcile('p','dot',row['revision'],7,'legacy evidence deliberately not in event',False,{'process.manage'})
        event=store.db.execute("SELECT reconciled_event_id FROM process_messages WHERE message_id='two'").fetchone()[0]
        payload=json.loads(store.db.execute('SELECT payload FROM events WHERE id=?',(event,)).fetchone()[0]);payload.pop('evidence')
        store.db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(payload),event))
        # Latest Process document retains evidence but is not a per-message certificate.
        downgrade(store.db);before=old_messages(store.db);store.close();return before

    def test_only_three_nullable_columns_and_original_rows_schema_objects_survive(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'old.sqlite3';before=self.legacy(path)
            db=sqlite3.connect(path);db.execute('CREATE INDEX fixture_message_hash ON process_messages(input_hash)')
            db.execute("CREATE TRIGGER fixture_message_guard BEFORE DELETE ON process_messages BEGIN SELECT RAISE(ABORT,'retained'); END")
            db.commit();old_schema=schema(db);db.close()
            store=Store(path)
            try:
                info=list(store.db.execute('PRAGMA table_info(process_messages)'))
                self.assertEqual([row[1]for row in info],list(OLD_COLUMNS)+list(MESSAGE_LINK_COLUMNS))
                self.assertEqual([(row[2],row[3],row[4])for row in info[-3:]],[('TEXT',0,None),('INTEGER',0,None),('INTEGER',0,None)])
                self.assertEqual(old_messages(store.db),before)
                after=schema(store.db)
                self.assertEqual([row for row in after if row[1]!='process_messages'],[row for row in old_schema if row[1]!='process_messages'])
                self.assertEqual(store.processes.message('p','dot','one')['run']['output']['output'],42)
                outcome=store.processes.message('p','dot','two')
                self.assertEqual(outcome['run']['status'],'failed');self.assertEqual(outcome['mailboxStatus'],'consumed')
                self.assertIsNone(outcome['reconciliation']['evidence'])
                self.assertEqual(list(store.db.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE '_chaos_message_%'")),[])
            finally:store.close()

    def test_no_private_claim_provenance_and_forged_run_input_remain_unavailable(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'old.sqlite3';store=fixture(path);send(store);complete(store)
            event=store.db.execute("SELECT claim_event_id FROM process_messages WHERE message_id='one'").fetchone()[0]
            store.db.execute('DELETE FROM process_event_lineage WHERE event_id=?',(event,))
            store.create_run('fake',store.project('p'),'source',{'process':{'key':'dot','messageId':'one'}})
            store.event('p','process.turn.queued',{'key':'dot','messageId':'one','kind':'message','runId':'fake','version':1,'turn':2},'fake')
            downgrade(store.db);before=old_messages(store.db);store.close()
            reopened=Store(path)
            try:
                result=reopened.processes.message('p','dot','one')
                self.assertEqual(result['linkStatus'],'unavailable');self.assertIsNone(result['run'])
                self.assertEqual(old_messages(reopened.db),before)
            finally:reopened.close()

    def test_multiple_host_claims_for_same_message_are_not_arbitrarily_selected(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'old.sqlite3';store=fixture(path);send(store);complete(store)
            event=store.db.execute("SELECT claim_event_id FROM process_messages WHERE message_id='one'").fetchone()[0]
            payload=json.loads(store.db.execute('SELECT payload FROM events WHERE id=?',(event,)).fetchone()[0])
            with store.write():store.processes._write(store.processes._read('p','dot'),'process.turn.queued','run-one',payload)
            downgrade(store.db);store.close();reopened=Store(path)
            try:self.assertEqual(reopened.processes.message('p','dot','one')['linkStatus'],'unavailable')
            finally:reopened.close()

    def test_legacy_pending_recovery_reconciliation_keeps_unavailable_certificate_empty(self):
        for missing in ('private-event','ambiguous-event'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory()as folder:
                path=Path(folder)/'old.sqlite3';store=fixture(path);send(store)
                store.processes.claim('p','dot','pending-one')
                event=store.db.execute("SELECT claim_event_id FROM process_messages WHERE message_id='one'").fetchone()[0]
                if missing=='private-event':
                    store.db.execute('DELETE FROM process_event_lineage WHERE event_id=?',(event,))
                else:
                    payload=json.loads(store.db.execute('SELECT payload FROM events WHERE id=?',(event,)).fetchone()[0])
                    with store.write():store.processes._write(store.processes._read('p','dot'),'process.turn.queued','pending-one',payload)
                downgrade(store.db);store.close();reopened=Store(path)
                try:
                    self.assertEqual(reopened.processes.message('p','dot','one')['linkStatus'],'unavailable')
                    reopened.recover();row=reopened.processes.get('p','dot')
                    self.assertEqual(reopened.run('pending-one')['status'],'interrupted')
                    reopened.processes.reconcile('p','dot',row['revision'],8,'explicit legacy inspection',False,{'process.manage'})
                    result=reopened.processes.message('p','dot','one')
                    self.assertEqual(result['mailboxStatus'],'consumed');self.assertEqual(result['linkStatus'],'unavailable')
                    self.assertIsNone(result['run']);self.assertIsNone(result['reconciliation'])
                    self.assertEqual(tuple(reopened.db.execute("SELECT run_id,claim_event_id,reconciled_event_id FROM process_messages WHERE message_id='one'").fetchone()),(None,None,None))
                    self.assertTrue(reopened.db.execute("SELECT e.id FROM events e JOIN process_event_lineage p ON p.event_id=e.id WHERE e.type='process.reconciled' AND e.run_id='pending-one'").fetchone())
                finally:reopened.close()

    def test_migration_runtime_lease_blocks_live_owner_before_any_ddl(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'old.sqlite3';self.legacy(path)
            db=sqlite3.connect(path);before=schema(db);rows=old_messages(db);db.close()
            lease=RuntimeLease(path)
            try:
                with self.assertRaisesRegex(RuntimeError,'active Chaos runtime'):Store(path)
                db=sqlite3.connect(path)
                try:self.assertEqual(schema(db),before);self.assertEqual(old_messages(db),rows)
                finally:db.close()
            finally:lease.close()

    def test_third_column_and_backfill_faults_roll_back_all_three_alters(self):
        for fault in ('third-alter','backfill'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory()as folder:
                path=Path(folder)/'old.sqlite3';self.legacy(path)
                db=sqlite3.connect(path,isolation_level=None);before=schema(db);rows=old_messages(db)
                class Adapter:
                    alters=0
                    def execute(self,sql,*args):
                        if sql.startswith('ALTER TABLE process_messages'):
                            self.alters+=1
                            if fault=='third-alter' and self.alters==3:raise sqlite3.OperationalError('third alter fault')
                        return db.execute(sql,*args)
                try:
                    if fault=='third-alter':
                        with self.assertRaisesRegex(sqlite3.OperationalError,'third alter fault'):initialize(Adapter())
                    else:
                        with patch('chaos.process_store._migrate_message_links',side_effect=RuntimeError('backfill fault')):
                            with self.assertRaisesRegex(RuntimeError,'backfill fault'):initialize(Adapter())
                    self.assertEqual(schema(db),before);self.assertEqual(old_messages(db),rows)
                    self.assertEqual([row[1]for row in db.execute('PRAGMA table_info(process_messages)')],list(OLD_COLUMNS))
                finally:db.close()

    def test_reopen_is_idempotent_without_a_migration_lease_or_second_backfill(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'old.sqlite3';self.legacy(path);store=Store(path)
            result=store.processes.message('p','dot','one');before=schema(store.db)
            messages=[tuple(row)for row in store.db.execute('SELECT * FROM process_messages ORDER BY seq')];store.close()
            with patch('chaos.store.RuntimeLease',side_effect=AssertionError('unnecessary lease')),patch('chaos.process_store._migrate_message_links',side_effect=AssertionError('repeated backfill')):
                store=Store(path)
            try:
                self.assertEqual(schema(store.db),before)
                self.assertEqual([tuple(row)for row in store.db.execute('SELECT * FROM process_messages ORDER BY seq')],messages)
                self.assertEqual(store.processes.message('p','dot','one'),result)
            finally:store.close()

    def test_history_iterators_use_bounded_fetchmany_not_fetchall(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'old.sqlite3';self.legacy(path);db=sqlite3.connect(path,isolation_level=None);batches=[]
            class Cursor:
                def __init__(self,real):self.real=real;self.description=real.description
                def fetchmany(self,n):batches.append(n);return self.real.fetchmany(n)
                def fetchall(self):raise AssertionError('unbounded history read')
            class Adapter:
                def execute(self,sql,*args):
                    cursor=db.execute(sql,*args)
                    return Cursor(cursor)if 'FROM process_event_lineage pe JOIN events e'in sql or 'FROM _chaos_message_claims c JOIN process_messages m'in sql else cursor
            try:
                initialize(Adapter());self.assertTrue(batches);self.assertEqual(set(batches),{128})
            finally:db.close()
