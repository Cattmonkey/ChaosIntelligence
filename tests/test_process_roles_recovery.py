"""Real SQLite reopen, transaction acknowledgement and one-shot role wakeups."""
import json
from pathlib import Path
import tempfile
import unittest

from chaos import Framework, Settings
from chaos.language import LanguageError
from chaos.store import Conflict
from test_process_roles_core import fixture,guide,claim,open_pair,DEFINITION


class ProcessRoleRecovery(unittest.TestCase):
    def test_queued_namespace_fifo_and_receipt_survive_real_close_reopen(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'state.sqlite3';app=fixture(self,path)
            a=guide(app,'left',{'n':1},'a');b=guide(app,'left',{'n':2},'b');row=app.get_process('p','worker')
            app.control_process('p','worker',row['revision'],'pause')
            before=[tuple(r)for r in app._store.db.execute('SELECT * FROM process_role_messages ORDER BY seq')]
            app.close();reopened=Framework(path,settings=Settings(':memory:'));self.addCleanup(reopened.close);reopened._engine.pause_dispatch(True)
            self.assertEqual([tuple(r)for r in reopened._store.db.execute('SELECT * FROM process_role_messages ORDER BY seq')],before)
            self.assertEqual(guide(reopened,'left',{'n':1},'a'),a);self.assertEqual(guide(reopened,'left',{'n':2},'b'),b)
            self.assertEqual(reopened._store.processes.role_registry('p','worker')['roles'][0]['pendingCount'],2)

    def test_received_and_adopted_survive_restart_without_auto_replay(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'state.sqlite3';app=fixture(self,path)
            first=guide(app,'left',1,'received');second=guide(app,'left',2,'adopted');current=claim(app);unused,child=open_pair(app,current)
            store=app._store.processes
            store.bot_operation(current,'receive',{**child,'messageId':'received','revision':first['revision']},lambda v:v)
            store.bot_operation(current,'receive',{**child,'messageId':'adopted','revision':second['revision']},lambda v:v)
            store.bot_operation(current,'adopt',{**child,'messageId':'adopted','revision':second['revision']},lambda v:v)
            before=[tuple(r)for r in app._store.db.execute('SELECT * FROM process_role_messages ORDER BY seq')]
            app.close();reopened=Framework(path,settings=Settings(':memory:'));self.addCleanup(reopened.close);reopened._engine.pause_dispatch(True)
            row=reopened.get_process('p','worker');self.assertEqual(row['status'],'needs_reconciliation');self.assertEqual(row['turn'],0)
            self.assertEqual(row['state'],{'kept':True});self.assertEqual([tuple(r)for r in reopened._store.db.execute('SELECT * FROM process_role_messages ORDER BY seq')],before)
            self.assertEqual([r['status']for r in reopened._store.db.execute('SELECT status FROM process_role_messages ORDER BY seq')],['guide_received','guide_adopted'])
            self.assertEqual(row['pending']['roleGuideAdopted'],[{'roleId':child['roleId'],'messageId':'adopted','revision':second['revision']}])
            reconciled=reopened._store.processes.reconcile('p','worker',row['revision'],{'verified':True},'manually verified uncertain turn',True,{'process.manage'})
            self.assertIsNone(reconciled['pending']);self.assertEqual(reconciled['state'],{'verified':True})
            rows=reopened._store.db.execute('SELECT status,input,offered FROM process_role_messages ORDER BY seq').fetchall()
            self.assertEqual(rows[0]['status'],'guide_received');self.assertEqual(rows[0]['offered'],0)
            self.assertEqual(rows[1]['status'],'guide_consumed');self.assertIsNone(json.loads(rows[1]['input'])['input'])
            self.assertEqual(guide(reopened,'left',2,'adopted')['status'],'applied')

    def test_offered_unhandled_input_does_not_busyloop_and_resume_reoffers(self):
        app=fixture(self);guide(app,'left',42,'unhandled');current=claim(app)
        self.assertEqual(app._store.db.execute('SELECT offered FROM process_role_messages').fetchone()[0],1)
        with app._store.write():app._store.processes.finish_in_transaction(current,'completed',{'state':{'turn':1},'output':None,'done':False},None)
        self.assertEqual(app._store.processes.candidates(),[])
        self.assertIsNone(claim(app,'must-not-start'))
        before=app.get_process('p','worker')
        with self.assertRaises(Conflict):app._store.processes.configure('p','worker',before['revision'],{**before['definition'],'maxTurns':1},{'process.manage'})
        self.assertEqual(app.get_process('p','worker'),before)
        row=app.get_process('p','worker');app.control_process('p','worker',row['revision'],'pause');row=app.get_process('p','worker')
        app.control_process('p','worker',row['revision'],'resume')
        self.assertEqual(app._store.processes.candidates(),[('p','worker')])
        later=claim(app,'turn-two');box=app._store.processes.role_box(later,'left')
        self.assertEqual([m['receipt']['messageId']for m in box['messages']],['unhandled'])
        self.assertNotEqual(later['pending']['lease'],current['pending']['lease'])

    def test_late_input_remains_unoffered_for_next_turn_and_terminal_refuses_loss(self):
        app=fixture(self);current=claim(app);guide(app,'right',42,'late')
        with app._store.write():app._store.processes.finish_in_transaction(current,'completed',{'state':{},'output':None,'done':False},None)
        self.assertEqual(app._store.processes.candidates(),[('p','worker')])
        later=claim(app,'turn-two');before=app.get_process('p','worker')
        with self.assertRaises(Conflict),app._store.write():
            app._store.processes.finish_in_transaction(later,'completed',{'state':{},'output':42,'done':True},None)
        self.assertEqual(app.get_process('p','worker'),before)
        self.assertEqual(app._store.db.execute('SELECT status FROM process_role_messages').fetchone()[0],'guide_queued')

    def test_only_root_ack_consumes_qualified_role_adoption_and_child_cannot_replace_checkpoint(self):
        app=fixture(self);receipt=guide(app,'left',42,'m');current=claim(app);root,child=open_pair(app,current);store=app._store.processes
        for operation in ('receive','adopt'):store.bot_operation(current,operation,{**child,'messageId':'m','revision':receipt['revision']},lambda v:v)
        checkpoint={'goal':'child'}
        store.bot_operation(current,'commit',{**child,'terminal':True,'done':True,'snapshot':checkpoint,'output':42},lambda v:v)
        work=app.get_process('p','worker')['pending']['botWork']
        self.assertIsNone(work['checkpoint']);self.assertEqual(work['checkpointRevision'],0)
        self.assertEqual(app._store.db.execute('SELECT status FROM process_role_messages').fetchone()[0],'guide_adopted')
        store.bot_operation(current,'close',{**child,'success':True},lambda v:v)
        output={'state':{'goal':'root'},'output':42,'done':True}
        store.bot_operation(current,'commit',{**root,'terminal':True,'done':True,'snapshot':output['state'],'output':42},lambda v:v)
        self.assertTrue(all(not r['addressable']for r in store.role_registry('p','worker')['roles']))
        with self.assertRaises(Conflict):guide(app,'left',43,'after-terminal-checkpoint')
        store.bot_operation(current,'close',{**root,'success':True},lambda v:v)
        with app._store.write():store.finish_in_transaction(current,'completed',output,None)
        self.assertEqual(app.get_process('p','worker')['state'],{'goal':'root'})
        self.assertEqual(app._store.db.execute('SELECT status FROM process_role_messages').fetchone()[0],'guide_consumed')

    def test_additive_table_preserves_original_message_schema_and_rejects_retired_target(self):
        app=fixture(self);columns=[r['name']for r in app._store.db.execute('PRAGMA table_info(process_messages)')]
        self.assertEqual(columns,['seq','project_id','key','message_id','input','input_hash','status','run_id','claim_event_id','reconciled_event_id'])
        self.assertEqual([r['name']for r in app._store.db.execute('PRAGMA table_info(process_role_messages)')],
                         ['seq','project_id','key','role_id','message_id','revision','input','input_hash','status','offered'])
        before=app.get_process('p','worker');target=before['botRoles']['left']['target']
        app._store.processes.configure('p','worker',before['revision'],{**DEFINITION,'roles':['right']},{'process.manage'})
        with self.assertRaises(Conflict):app._store.processes.guide('p','worker',1,'stale',{'process.manage'},lambda v:v,target=target)
        self.assertEqual(app._store.db.execute('SELECT count(*) FROM process_role_messages').fetchone()[0],0)
