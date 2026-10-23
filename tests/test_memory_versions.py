"""Versioned memory uses real SQLite transactions, not simulated cache hits."""
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos.ownership import RuntimeLease
from chaos.store import Store, MAX_MEMORY_REVISION


def make_store(path=':memory:', *, project=True):
    store=Store(path)
    if project:store.create_project('p','P',{'nodes':[],'edges':[]},'null',capabilities=[],from_view='source')
    return store


class MemoryVersions(unittest.TestCase):
    def setUp(self):
        self.store=make_store();self.addCleanup(self.store.close)

    def test_missing_legacy_zero_and_stored_null_are_distinct_copied_cells(self):
        self.assertEqual(self.store.memory_read('p','key'),{'key':'key','exists':False,'value':None,'revision':0})
        self.store.db.execute('INSERT INTO memory VALUES(?,?,?)',('p','key','null'))
        self.assertEqual(self.store.memory_read('p','key'),{'key':'key','exists':True,'value':None,'revision':0})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_revisions').fetchone()[0],0)
        response=self.store.memory_compare_exchange('p','key',0,{'items':[42]})
        self.assertEqual(set(response),{'key','exists','value','revision','swapped'})
        self.assertTrue(response['swapped']);self.assertEqual(response['revision'],1)
        response['value']['items'].append(99)
        cell=self.store.memory_read('p','key');cell['value']['items'].append(100)
        self.assertEqual(self.store.memory_get('p','key'),{'items':[42]})

    def test_legacy_put_same_value_advances_version_and_conflict_is_read_only(self):
        value={'count':1};self.assertEqual(self.store.memory_put('p','counter',value),value)
        value['count']=9;self.assertEqual(self.store.memory_get('p','counter'),{'count':1})
        before=self.store.memory_read('p','counter');self.assertEqual(before['revision'],1)
        self.store.memory_put('p','counter',{'count':1})
        stale=self.store.memory_compare_exchange('p','counter',before['revision'],{'count':99})
        self.assertEqual(stale,{'key':'counter','exists':True,'value':{'count':1},'revision':2,'swapped':False})
        missing=self.store.memory_compare_exchange('p','missing',7,True)
        self.assertEqual(missing,{'key':'missing','exists':False,'value':None,'revision':0,'swapped':False})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_revisions').fetchone()[0],1)
        self.assertEqual(self.store.memory_compare_exchange('p','counter',2,{'count':2})['revision'],3)

    def test_new_arguments_and_json_are_validated_before_lock_or_write(self):
        class NoLock:
            def __enter__(self):raise AssertionError('invalid input reached lock')
            def __exit__(self,*args):return False
        bad_revisions=(True,False,-1,1.0,'0',MAX_MEMORY_REVISION+1,None)
        with patch.object(self.store,'lock',NoLock()):
            for revision in bad_revisions:
                with self.subTest(revision=revision),self.assertRaises(ValueError):
                    self.store.memory_compare_exchange('p','key',revision,42)
            for key in ('','x'*129,True,None):
                with self.subTest(key=key),self.assertRaises(ValueError):self.store.memory_read('p',key)
            for value in (float('nan'),{'bad':object()},'x'*100001,['x'*99999]):
                with self.subTest(value_type=type(value)),self.assertRaises(ValueError):
                    self.store.memory_compare_exchange('p','key',0,value)
        self.assertEqual(self.store.memory_read('p','key')['exists'],False)

    def test_two_real_connections_same_revision_have_exactly_one_winner(self):
        with tempfile.TemporaryDirectory(prefix='chaos-memory-cas-')as tmp,ExitStack()as stack:
            path=Path(tmp)/'state.sqlite';one=make_store(path);stack.callback(one.close)
            two=make_store(path,project=False);stack.callback(two.close)
            barrier=threading.Barrier(3);answers=[];errors=[]
            def compare(store,value):
                try:barrier.wait(3);answers.append(store.memory_compare_exchange('p','key',0,value))
                except BaseException as error:errors.append(error)
            workers=[threading.Thread(target=compare,args=(one,11)),threading.Thread(target=compare,args=(two,22))]
            for worker in workers:worker.start()
            barrier.wait(3)
            for worker in workers:worker.join(4);self.assertFalse(worker.is_alive())
            self.assertEqual(errors,[]);self.assertCountEqual([r['swapped']for r in answers],[True,False])
            self.assertEqual([r['revision']for r in answers],[1,1])
            self.assertEqual(answers[0]['value'],answers[1]['value'])
            self.assertEqual(one.memory_read('p','key'),two.memory_read('p','key'))

    def test_second_table_failure_rolls_back_both_cas_and_legacy_put(self):
        self.store.memory_put('p','key',{'original':1});before=self.store.memory_read('p','key')
        self.store.db.execute("CREATE TRIGGER reject_memory_revision BEFORE INSERT ON memory_revisions BEGIN SELECT RAISE(ABORT,'fixture revision failure'); END")
        for operation in (lambda:self.store.memory_compare_exchange('p','key',1,{'changed':2}),
                          lambda:self.store.memory_put('p','key',{'changed':3}),
                          lambda:self.store.memory_compare_exchange('p','new',0,42)):
            with self.assertRaises(sqlite3.IntegrityError):operation()
            self.assertEqual(self.store.memory_read('p','key'),before)
            self.assertFalse(self.store.memory_read('p','new')['exists'])
        self.store.db.execute('DROP TRIGGER reject_memory_revision')
        self.assertTrue(self.store.memory_compare_exchange('p','key',1,{'good':1})['swapped'])

    def test_maximum_revision_rejects_matching_write_but_allows_conflict_read(self):
        self.store.memory_put('p','key','kept')
        self.store.db.execute('UPDATE memory_revisions SET revision=?',(MAX_MEMORY_REVISION,))
        before=self.store.memory_read('p','key')
        self.assertEqual(self.store.memory_compare_exchange('p','key',0,'bad'),dict(before,swapped=False))
        for operation in (lambda:self.store.memory_compare_exchange('p','key',MAX_MEMORY_REVISION,'bad'),
                          lambda:self.store.memory_put('p','key','bad')):
            with self.assertRaises(ValueError):operation()
            self.assertEqual(self.store.memory_read('p','key'),before)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute('UPDATE memory_revisions SET revision=?',(MAX_MEMORY_REVISION+1,))

    def test_versions_persist_across_real_close_reopen_and_projects_are_isolated(self):
        with tempfile.TemporaryDirectory(prefix='chaos-memory-reopen-')as tmp:
            path=Path(tmp)/'state.sqlite';store=make_store(path)
            store.create_project('q','Q',{'nodes':[],'edges':[]},'null',capabilities=[],from_view='source')
            one=store.memory_compare_exchange('p','same',0,None)
            store.memory_put('q','same',{'other':True});store.close()
            store=make_store(path,project=False)
            try:
                self.assertEqual(store.memory_read('p','same'),{k:v for k,v in one.items()if k!='swapped'})
                self.assertEqual(store.memory_read('q','same')['value'],{'other':True})
                self.assertFalse(store.memory_compare_exchange('p','same',0,99)['swapped'])
                self.assertEqual(store.memory_compare_exchange('p','same',1,42)['revision'],2)
                self.assertEqual(store.memory_read('q','same')['revision'],1)
            finally:store.close()

    def test_lazy_revision_table_migration_preserves_legacy_schema_rows_and_requires_lease(self):
        with tempfile.TemporaryDirectory(prefix='chaos-memory-migration-')as tmp:
            path=Path(tmp)/'state.sqlite';store=make_store(path)
            store.db.execute('DROP TABLE memory_revisions')
            store.db.execute('INSERT INTO memory VALUES(?,?,?)',('p','legacy',json.dumps({'old':[1,None]})))
            schema=store.db.execute("SELECT sql FROM sqlite_master WHERE name='memory'").fetchone()[0]
            rows=[tuple(row)for row in store.db.execute('SELECT * FROM memory ORDER BY project_id,key')]
            store.close();lease=RuntimeLease(path)
            try:
                with self.assertRaises(RuntimeError):Store(path)
            finally:lease.close()
            store=make_store(path,project=False)
            try:
                self.assertEqual(store.db.execute("SELECT sql FROM sqlite_master WHERE name='memory'").fetchone()[0],schema)
                self.assertEqual([tuple(row)for row in store.db.execute('SELECT * FROM memory ORDER BY project_id,key')],rows)
                self.assertEqual(store.db.execute('SELECT count(*) FROM memory_revisions').fetchone()[0],0)
                self.assertEqual(store.memory_read('p','legacy')['revision'],0)
                self.assertTrue(store.memory_read('p','legacy')['exists'])
                self.assertTrue(store.memory_compare_exchange('p','legacy',0,42)['swapped'])
            finally:store.close()

    def test_read_value_and_revision_share_one_snapshot_while_another_connection_writes(self):
        with tempfile.TemporaryDirectory(prefix='chaos-memory-snapshot-')as tmp,ExitStack()as stack:
            path=Path(tmp)/'state.sqlite';writer=make_store(path);stack.callback(writer.close)
            reader=make_store(path,project=False);stack.callback(reader.close)
            first=threading.Event();read_first=threading.Event();done=threading.Event();errors=[]
            def update():
                try:
                    writer.memory_put('p','key',1);first.set()
                    if not read_first.wait(3):raise AssertionError('reader never observed first committed cell')
                    for value in range(2,41):writer.memory_put('p','key',value)
                except BaseException as error:errors.append(error)
                finally:done.set()
            worker=threading.Thread(target=update);worker.start()
            try:
                self.assertTrue(first.wait(3));self.assertEqual(reader.memory_read('p','key')['value'],1);read_first.set()
                for _ in range(1000):
                    cell=reader.memory_read('p','key');self.assertEqual(cell['value'],cell['revision'])
                    if done.is_set():break
            finally:read_first.set();worker.join(4)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(reader.memory_read('p','key'),{'key':'key','exists':True,'value':40,'revision':40})


if __name__=='__main__':unittest.main()
