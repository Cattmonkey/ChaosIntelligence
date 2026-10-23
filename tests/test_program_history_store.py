"""Immutable Program capture at the same CAS/event write boundary as the project."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Framework, Settings, Workflow
from chaos.program import parse_program
from chaos.ownership import RuntimeLease
from chaos.store import Store, Conflict, NotFound


class ProgramHistoryStoreTests(unittest.TestCase):
    def app(self):
        app=Framework(settings=Settings(':memory:'));self.addCleanup(app.close);return app

    def test_all_file_ir_entry_paths_keep_exact_prior_sources(self):
        app=self.app();snapshots=[]
        project=app.create_project('files',source='import "lib/a.chaos" as A; A.answer()',
            files={'lib/a.chaos':'# original comment\nfn answer(){40};export{answer}'},capabilities=[])
        snapshots.append(app.program_snapshot('files'))
        app.edit_files('files',1,'rename','lib/a.chaos',new_path='lib/b.chaos');snapshots.append(app.program_snapshot('files'))
        app.edit_program('files',2,file='lib/b.chaos',program=parse_program('fn answer(){41};export{answer}','lib/b.chaos'))
        snapshots.append(app.program_snapshot('files'))
        app.edit_files('files',3,'create','extra.chaos',source='42');snapshots.append(app.program_snapshot('files'))
        app.edit_files('files',4,'delete','extra.chaos');snapshots.append(app.program_snapshot('files'))
        project=app.edit_files('files',5,'create','next.chaos',source='43');snapshots.append(app.program_snapshot('files'))
        app.save_project({**project,'entry':'next.chaos','source':'43'},from_view='source');snapshots.append(app.program_snapshot('files'))
        self.assertEqual([i['version']for i in app.program_history('files')['items']],list(range(1,8)))
        for snapshot in snapshots:
            old=app.program_revision('files',snapshot['version'])
            self.assertEqual(old['definition'],snapshot['definition']);self.assertEqual(old['origin']['definitionHash'],snapshot['definitionHash'])
        self.assertIn('lib/a.chaos',app.program_revision('files',1)['definition']['files'])
        self.assertNotIn('lib/a.chaos',app.program_revision('files',2)['definition']['files'])
        self.assertIn('# original comment',app.program_revision('files',2)['definition']['files']['lib/b.chaos'])
        self.assertNotIn('extra.chaos',app.program_revision('files',5)['definition']['files'])
        self.assertEqual(app.program_revision('files',7)['definition']['entry'],'next.chaos')

    def test_graph_save_patch_and_equal_hash_versions_are_retained(self):
        app=self.app();project=app.create_project('graph',graph=Workflow.identity().build(),capabilities=[])
        project['graph']['nodes'][0]['x']=12;project=app.save_project(project,from_view='graph')
        app.patch('graph',2,[{'op':'update_node','id':'input','changes':{'x':33}}],reason='layout')
        page=app.program_history('graph')
        self.assertEqual([r['kind']for r in page['items']],['created','saved','patched'])
        self.assertEqual(len({r['definitionHash']for r in page['items']}),1)
        self.assertEqual(app.get_project('graph')['graph']['nodes'][0]['x'],33)
        self.assertEqual(page['items'][2]['reason'],'layout')

    def test_capture_and_event_failures_rollback_project_history_and_events(self):
        app=self.app();before=app.create_project('atomic',source='1',capabilities=[])
        history=app.program_history('atomic');events=app.events('atomic')['items']
        with patch.object(app._store.history,'capture',side_effect=RuntimeError('history insert')):
            with self.assertRaisesRegex(RuntimeError,'history insert'):app.edit_program('atomic',1,source='2')
            with self.assertRaisesRegex(RuntimeError,'history insert'):app.create_project('new',source='3')
        with self.assertRaises(NotFound):app.get_project('new')
        original=app._store._event
        def broken(project,kind,*args):
            if kind=='graph.saved':raise RuntimeError('event insert')
            return original(project,kind,*args)
        with patch.object(app._store,'_event',broken):
            with self.assertRaisesRegex(RuntimeError,'event insert'):app.edit_program('atomic',1,source='2')
        self.assertEqual(app.get_project('atomic'),before)
        self.assertEqual(app.program_history('atomic'),history);self.assertEqual(app.events('atomic')['items'],events)
        with self.assertRaises(NotFound):app.program_revision('atomic',2)

    def test_invalid_edit_and_stale_cas_never_capture(self):
        app=self.app();app.create_project('invalid',source='1',capabilities=[])
        with self.assertRaises(Exception):app.edit_program('invalid',1,source='let =')
        app.edit_program('invalid',1,source='2')
        with self.assertRaises(Conflict):app.edit_program('invalid',1,source='3')
        self.assertEqual(len(app.program_history('invalid')['items']),2)
        self.assertEqual(app.program_revision('invalid',1)['definition']['files']['main.chaos'],'1')

    def test_history_pagination_project_isolation_and_copied_revision(self):
        app=self.app();app.create_project('pages',source='0',capabilities=[]);app.create_project('other',source='99',capabilities=[])
        for version in range(1,6):app.edit_program('pages',version,source=str(version))
        found=[];after=0
        while True:
            page=app.program_history('pages',after=after,limit=2);found.extend(page['items'])
            self.assertEqual((page['currentVersion'],page['firstAvailableVersion']),(6,1))
            if page['nextCursor']is None:break
            after=page['nextCursor']
        self.assertEqual([v['version']for v in found],list(range(1,7)))
        self.assertEqual(len(app.program_history('other')['items']),1)
        value=app.program_revision('pages',1);value['definition']['files']['main.chaos']='tampered'
        self.assertEqual(app.program_revision('pages',1)['definition']['files']['main.chaos'],'0')
        with self.assertRaises(NotFound):app.program_revision('missing',1)
        for after,limit in ((True,1),(-1,1),(2**63,1),(0,True),(0,0),(0,101)):
            with self.subTest(after=after,limit=limit),self.assertRaises(ValueError):app.program_history('pages',after=after,limit=limit)
        for version in (True,0,-1,2**63,'1'):
            with self.subTest(version=version),self.assertRaises(ValueError):app.program_revision('pages',version)

    def test_migration_seeds_only_current_without_rewriting_existing_rows(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'workspace.sqlite3';store=Store(path)
            project=store.create_project('old','old',{'nodes':[],'edges':[]},'1',capabilities=[])
            for version in range(1,7):project=store.save('old',version,project['graph'],str(version+1),'old',from_view='source')
            store.db.execute('DROP TABLE program_revisions')
            before={name:[tuple(row)for row in store.db.execute('SELECT * FROM '+name)]for name in ('projects','runs','events','module_programs','memory')}
            store.close()
            with RuntimeLeaseContext(path):
                with self.assertRaisesRegex(RuntimeError,'active Chaos runtime'):Store(path)
            reopened=Store(path)
            try:
                for name,rows in before.items():self.assertEqual([tuple(row)for row in reopened.db.execute('SELECT * FROM '+name)],rows)
                page=reopened.history.history('old');self.assertEqual((page['currentVersion'],page['firstAvailableVersion']),(7,7))
                self.assertEqual(len(page['items']),1);self.assertEqual(page['items'][0]['kind'],'migration')
                with self.assertRaises(NotFound):reopened.history.revision('old',6)
                archived=reopened.db.execute('SELECT * FROM program_revisions ORDER BY project_id,version').fetchall();archived=[tuple(row)for row in archived]
            finally:reopened.close()
            again=Store(path)
            try:self.assertEqual([tuple(row)for row in again.db.execute('SELECT * FROM program_revisions ORDER BY project_id,version')],archived)
            finally:again.close()

    def test_two_connections_one_cas_winner_and_single_revision(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'db';a=Store(path);b=Store(path);project=a.create_project('race','Race',{'nodes':[],'edges':[]},'0',capabilities=[])
            gate=threading.Barrier(2)
            def save(store,source):
                gate.wait(2)
                try:return store.save('race',1,project['graph'],source,'Race',from_view='source')['version']
                except Conflict:return 'conflict'
            try:
                with concurrent.futures.ThreadPoolExecutor(2)as pool:
                    results=list(pool.map(lambda pair:save(*pair),((a,'1'),(b,'2'))))
                self.assertCountEqual(results,[2,'conflict'])
                self.assertEqual([r['version']for r in a.history.history('race')['items']],[1,2])
                self.assertEqual(a.history.revision('race',2)['definition']['files']['main.chaos'],a.project('race')['source'])
            finally:a.close();b.close()

    def test_metadata_page_uses_one_snapshot_during_other_connection_commit(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'db';a=Store(path);b=Store(path)
            project=a.create_project('read_race','Race',{'nodes':[],'edges':[]},'0',capabilities=[])
            enter=threading.Event();finished=threading.Event();errors=[]
            def write():
                try:
                    self.assertTrue(enter.wait(2));b.save('read_race',1,project['graph'],'1','Race',from_view='source')
                except BaseException as error:errors.append(error)
                finally:finished.set()
            def trace(sql):
                if sql.startswith('SELECT MIN(version)'):
                    enter.set();self.assertTrue(finished.wait(2))
            thread=threading.Thread(target=write);thread.start();a.db.set_trace_callback(trace)
            try:
                page=a.history.history('read_race')
                self.assertEqual(page['currentVersion'],1);self.assertEqual([r['version']for r in page['items']],[1])
                a.db.set_trace_callback(None)
                self.assertEqual(a.history.history('read_race')['currentVersion'],2)
                self.assertEqual(errors,[])
            finally:
                a.db.set_trace_callback(None);enter.set();thread.join(2);a.close();b.close()

    def test_original_dialects_are_captured_without_history_escalation_data(self):
        app=self.app();project=app.create_project('dialect',source='input[0]',files={'unused.chaos':'input[1]'},capabilities=['text.stats'])
        app.save_project({**project,'sourceVersions':{'main.chaos':1,'unused.chaos':2}},from_view='source')
        old=app.program_revision('dialect',2)
        self.assertEqual(old['definition']['languageVersions'],{'main.chaos':1,'unused.chaos':2})
        self.assertEqual(set(old['definition']),{'kind','entry','files','languageVersions'})
        self.assertNotIn('capabilities',json.dumps(old));self.assertNotIn('settings',json.dumps(old))
        app.edit_program('dialect',2,source='input[-1]')
        self.assertEqual(app.program_revision('dialect',2),old)
        self.assertEqual(app.program_revision('dialect',3)['definition']['languageVersions']['main.chaos'],2)


class RuntimeLeaseContext:
    def __init__(self,path):self.path=path
    def __enter__(self):self.lease=RuntimeLease(self.path);return self.lease
    def __exit__(self,*args):self.lease.close()
