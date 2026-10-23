"""Proposal: additive lineage migration fails closed and preserves old SQLite clients.

The 0.109-shaped fixture is constructed in a fresh temporary database by removing
only the two additive provenance tables from an otherwise fully initialized Store.
No production database or external settings file is opened.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from chaos.ownership import RuntimeLease
from chaos.process_store import initialize as initialize_processes
from chaos.store import Store
from tests.test_process_integration import definition


NEW_TABLES = {'process_run_lineage','process_event_lineage'}
NEW_INDEXES = {'process_run_lineage_process','process_event_lineage_process'}


def schema(db):
    return {row[0]:(row[1],row[2],row[3]) for row in db.execute(
        "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_autoindex%' ORDER BY name")}


def rows(db, names):
    return {name:sorted((tuple(row) for row in db.execute('SELECT * FROM "'+name+'"')),key=repr)
            for name in names}


class ProcessStreamMigration(unittest.TestCase):
    def old_fixture(self, path):
        store = Store(path)
        try:
            store.create_project('p','legacy',{'nodes':[],'edges':[]},'42',
                files={'dot.chaos':'fn step(c){process_step(c.state+1)};export {step}'},
                capabilities=['process.manage'])
            store.processes.create('p',1,'alpha',definition(),0,{'process.manage'})
            claim = store.processes.claim('p','alpha','legacy-real-looking')
            store.transition_run('legacy-real-looking','completed',{},
                {'state':1,'output':None,'done':False},_process=claim)
            store.event('p','process.bot.adopt',{'key':'alpha','processKey':'alpha',
                'processOrigin':'lifecycle','messageId':'identical-looking-spoof'},'legacy-real-looking')
            # SQLite supports application-owned indexes and triggers beside all old Store tables.
            store.db.execute('CREATE INDEX fixture_legacy_memory ON memory(project_id,key)')
            store.db.execute("CREATE TRIGGER fixture_legacy_event BEFORE INSERT ON events WHEN NEW.type='fixture.reject' BEGIN SELECT RAISE(ABORT,'old trigger retained'); END")
            store.db.execute('INSERT INTO memory VALUES(?,?,?)',('p','sentinel','{"old":true}'))
            for table in sorted(NEW_TABLES):
                store.db.execute('DROP TABLE '+table)
            return schema(store.db), rows(store.db,[name for name,entry in schema(store.db).items() if entry[0]=='table'])
        finally:
            store.close()

    def test_migration_is_exactly_two_tables_two_indexes_and_has_no_history_backfill(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'old.sqlite3'
            configuration = Path(folder)/'settings.json'
            configuration.write_text('{"externalSentinel":true}')
            configuration_before = hashlib.sha256(configuration.read_bytes()).hexdigest()
            before_schema,before_rows = self.old_fixture(path)
            store = Store(path)
            try:
                after_schema = schema(store.db)
                self.assertEqual(set(after_schema)-set(before_schema),NEW_TABLES|NEW_INDEXES)
                self.assertEqual({name:after_schema[name] for name in before_schema},before_schema)
                self.assertEqual(rows(store.db,before_rows),before_rows)
                self.assertEqual(hashlib.sha256(configuration.read_bytes()).hexdigest(),configuration_before)
                self.assertEqual(store.event_page('p',process_key='alpha'),
                    {'items':[],'nextCursor':None,'lastEventId':0,
                     'historyStartEventId':None,'legacyHistoryIncluded':False})
                legacy_ids = [row['id'] for row in store.events('p',0,300,'legacy-real-looking')]
                self.assertTrue(legacy_ids)
                old_all = [row['id'] for row in store.events('p')]
                original = store.processes.get('p','alpha')
                store.processes.control('p','alpha',original['revision'],'pause',{'process.manage'})
                first = store.event_page('p',process_key='alpha')
                self.assertEqual(len(first['items']),1)
                self.assertEqual(first['items'][0]['type'],'process.pause')
                self.assertEqual(first['items'][0]['processOrigin'],'lifecycle')
                self.assertEqual(first['historyStartEventId'],first['items'][0]['id'])
                self.assertFalse(first['legacyHistoryIncluded'])
                self.assertFalse(set(legacy_ids).intersection(row['id'] for row in first['items']))
                self.assertEqual([row['id'] for row in store.events('p') if row['id']<=old_all[-1]],old_all)
                row = store.processes.get('p','alpha')
                store.processes.control('p','alpha',row['revision'],'resume',{'process.manage'})
                store.processes.send('p','alpha',42,'new-message',{'process.manage'},lambda value:value)
                claim = store.processes.claim('p','alpha','new-certified')
                store.transition_run('new-certified','completed',{},
                    {'state':2,'output':42,'done':False},_process=claim)
                selected = store.event_page('p',limit=300,process_key='alpha')
                self.assertEqual(selected['historyStartEventId'],first['historyStartEventId'])
                self.assertTrue(any(row['type']=='run.completed' and row['run_id']=='new-certified' for row in selected['items']))
                # Process revisions use INSERT OR REPLACE; all previous event lineage must survive.
                self.assertTrue(set(row['id'] for row in first['items']).issubset(row['id'] for row in selected['items']))
                for name in NEW_TABLES:
                    self.assertEqual(list(store.db.execute('PRAGMA foreign_key_list('+name+')')),[])
                certified = rows(store.db,NEW_TABLES)
                backup = Path(folder)/'backup.sqlite3'
                target = sqlite3.connect(backup)
                try: store.db.backup(target)
                finally: target.close()
            finally:
                store.close()
            # An already migrated file does not acquire a new migration lease.
            with patch('chaos.store.RuntimeLease',side_effect=AssertionError('unnecessary migration lease')):
                reopened = Store(path)
            try:
                self.assertEqual(rows(reopened.db,NEW_TABLES),certified)
                self.assertEqual(reopened.event_page('p',limit=300,process_key='alpha'),selected)
            finally:
                reopened.close()
            restored = Store(backup)
            try:
                self.assertEqual(rows(restored.db,NEW_TABLES),certified)
                self.assertEqual(restored.event_page('p',limit=300,process_key='alpha'),selected)
                self.assertTrue(NEW_INDEXES.issubset(schema(restored.db)))
            finally:
                restored.close()

    def test_runtime_lease_prevents_any_lineage_ddl_on_old_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'old.sqlite3'
            before_schema,before_rows = self.old_fixture(path)
            lease = RuntimeLease(path)
            try:
                with self.assertRaisesRegex(RuntimeError,'active Chaos runtime'):
                    Store(path)
                db = sqlite3.connect(path)
                try:
                    self.assertEqual(schema(db),before_schema)
                    self.assertEqual(rows(db,before_rows),before_rows)
                    self.assertFalse(NEW_TABLES.intersection(schema(db)))
                finally:
                    db.close()
            finally:
                lease.close()

    def test_fourth_lineage_ddl_failure_rolls_back_both_tables_and_both_indexes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'old.sqlite3'
            before_schema,before_rows = self.old_fixture(path)
            db = sqlite3.connect(path,isolation_level=None)
            class FourthDDLError:
                calls = 0
                def execute(self,sql,*args):
                    # Observe actual DDL statements; do not replace initialization logic.
                    if sql.lstrip().upper().startswith('CREATE ') and any(name in sql for name in NEW_TABLES|NEW_INDEXES):
                        self.calls += 1
                        if self.calls==4:
                            raise sqlite3.OperationalError('fourth lineage DDL injected')
                    return db.execute(sql,*args)
            adapter = FourthDDLError()
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError,'fourth lineage DDL injected'):
                    initialize_processes(adapter)
                self.assertEqual(adapter.calls,4)
                self.assertEqual(schema(db),before_schema)
                self.assertEqual(rows(db,before_rows),before_rows)
            finally:
                db.close()

    def test_an_old_sqlite_writer_can_still_use_old_columns_but_cannot_certify_events(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'old.sqlite3'
            self.old_fixture(path)
            store = Store(path)
            try:
                # Emulate the 0.109 SQL contract with a separate SQLite connection.
                old = sqlite3.connect(path,isolation_level=None)
                try:
                    columns = [row[1] for row in old.execute('PRAGMA table_info(events)')]
                    self.assertEqual(columns,['id','project_id','run_id','type','payload','created'])
                    original = json.loads(old.execute('SELECT document FROM processes WHERE project_id=? AND key=?',('p','alpha')).fetchone()[0])
                    original['revision'] += 1
                    original['status'] = 'paused'
                    old.execute('INSERT OR REPLACE INTO processes VALUES(?,?,?,?,?)',
                        ('p','alpha',json.dumps(original),'paused',None))
                    inserted = old.execute('INSERT INTO events(project_id,run_id,type,payload,created) VALUES(?,?,?,?,?)',
                        ('p','legacy-real-looking','process.pause',json.dumps({'key':'alpha','revision':original['revision']}),1.0)).lastrowid
                    self.assertEqual(old.execute('SELECT count(*) FROM process_run_lineage').fetchone()[0],0)
                    self.assertEqual(old.execute('SELECT count(*) FROM process_event_lineage').fetchone()[0],0)
                finally:
                    old.close()
                self.assertTrue(any(row['id']==inserted for row in store.events('p',0,300,'legacy-real-looking')))
                self.assertEqual(store.event_page('p',process_key='alpha')['items'],[])
                self.assertEqual(store.processes.get('p','alpha')['status'],'paused')
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
