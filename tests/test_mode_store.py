"""Same-Store artifacts, CAS leases, explicit recovery and real receipt semantics."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Framework, Client
from chaos.language import LanguageError
from chaos.mode_store import ModeHost
from chaos.settings import Settings
from chaos.store import Conflict, NotFound


PLAN={'kind':'plan','name':'work','steps':[{'id':'one','call':'work','needs':[]}]}
GOAL={'kind':'goal','objective':'reach42','planner':'planner','verify':'verify','max_iterations':2}


class ModeStoreTests(unittest.TestCase):
    def setUp(self):
        self.app=Framework(settings=Settings(':memory:'))
        self.project=self.app.create_project('modes',source='input',capabilities=[])
        self.host=ModeHost(self.app._store,self.project,'run_one')

    def tearDown(self):
        self.host.close();self.app.close()

    def claim(self,key='goal',host=None,definition=None,value=None):
        return (host or self.host).handle('goal.claim',{'key':key,'definition':definition or GOAL,'input':value})

    def commit(self,claim,phase,iteration=None,host=None):
        checkpoint=copy.deepcopy(claim['checkpoint']);checkpoint['phase']=phase
        if iteration is not None:checkpoint['iteration']=iteration
        return (host or self.host).handle('goal.commit',{'key':claim['key'],'lease':claim['lease'],'revision':claim['revision'],'checkpoint':checkpoint})

    def test_artifact_cas_history_and_copied_schema(self):
        definition=copy.deepcopy(PLAN);one=self.app.save_mode('modes','plan',definition)
        definition['steps'][0]['call']='changed'
        self.assertEqual(one['definition'],PLAN);self.assertNotIn('lease',one)
        with self.assertRaises(Conflict):self.app.save_mode('modes','plan',PLAN)
        two=self.app.save_mode('modes','plan',{**PLAN,'name':'updated'},revision=1)
        self.assertEqual(two['revision'],2);self.assertEqual(self.app.mode('modes','plan',revision=1)['definition'],PLAN)
        self.assertEqual([r['revision']for r in self.app.mode_history('modes','plan')['items']],[1,2])
        self.assertEqual(self.app.get_project('modes')['version'],self.project['version'])

    def test_project_isolation_and_cursor(self):
        self.app.create_project('other',source='input')
        for key in ('a','b'):self.app.save_mode('modes',key,PLAN)
        self.app.save_mode('other','a',{**PLAN,'name':'other'})
        page=self.app.modes('modes',limit=1);self.assertEqual(page['nextCursor'],'a')
        self.assertEqual([r['key']for r in self.app.modes('modes',after='a')['items']],['b'])
        self.assertEqual(self.app.mode('other','a')['definition']['name'],'other')
        with self.assertRaises(NotFound):self.app.mode('other','b')

    def test_invalid_definitions_and_versions_leave_no_events(self):
        before=self.app.events('modes')['lastEventId']
        for key,value,revision in (('../bad',PLAN,None),('bad',{**PLAN,'extra':1},None),('bad',PLAN,True),('bad',{'kind':'plan','name':'x','steps':[{'id':'x','call':'x','needs':['x']}]},None)):
            with self.subTest(key=key,value=value),self.assertRaises((ValueError,LanguageError)):self.app.save_mode('modes',key,value,revision=revision)
        self.assertEqual(self.app.modes('modes')['items'],[]);self.assertEqual(self.app.events('modes')['lastEventId'],before)

    def test_concurrent_same_revision_has_one_commit(self):
        self.app.save_mode('modes','plan',PLAN);gate=threading.Barrier(2);answers=[]
        def save(name):
            gate.wait()
            try:self.app.save_mode('modes','plan',{**PLAN,'name':name},revision=1);answers.append('saved')
            except Conflict:answers.append('conflict')
        threads=[threading.Thread(target=save,args=(str(i),))for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(2);self.assertFalse(t.is_alive())
        self.assertCountEqual(answers,['saved','conflict']);self.assertEqual(len(self.app.mode_history('modes','plan')['items']),2)

    def test_event_failure_rolls_back_artifact_and_revision(self):
        with patch.object(self.app._store,'_event',side_effect=RuntimeError('event failure')):
            with self.assertRaises(RuntimeError):self.app.save_mode('modes','plan',PLAN)
        self.assertEqual(self.app.modes('modes')['items'],[])
        self.assertEqual(self.app._store.db.execute('SELECT count(*) FROM mode_revisions').fetchone()[0],0)

    def test_snapshot_contains_modes_at_same_cursor(self):
        one=self.app.save_mode('modes','plan',PLAN);snapshot=self.app.snapshot('modes')
        self.assertEqual(snapshot['modes']['items'],[one]);self.assertEqual(snapshot['eventCursor'],self.app.events('modes')['lastEventId'])

    def test_claim_exclusivity_and_no_public_lease(self):
        claim=self.claim();other=ModeHost(self.app._store,self.project,'run_two')
        try:
            with self.assertRaises(Conflict):self.claim(host=other)
            self.assertNotIn('lease',self.app.mode('modes','goal'))
            self.assertNotIn('lease',self.app.mode_history('modes','goal')['items'][0])
            self.assertNotIn(claim['lease'],json.dumps(self.app.events('modes')))
        finally:other.close()

    def test_source_and_input_identity_are_exact(self):
        claim=self.claim(value=True);self.host.handle('goal.release',{'key':'goal','lease':claim['lease'],'outcome':'paused'})
        with self.assertRaises(Conflict):self.claim(value=1)
        changed={**self.project,'files':{**self.project['files'],'main.chaos':'input // changed'}}
        other=ModeHost(self.app._store,changed,'run_two')
        try:
            with self.assertRaises(Conflict):self.claim(host=other,value=True)
        finally:other.close()

    def test_goal_attempt_is_consumed_before_effect_and_needs_reconciliation(self):
        claim=self.commit(self.claim(),'pending',1)
        self.host.close('failed');artifact=self.app.mode('modes','goal')
        self.assertEqual(artifact['status'],'needs_reconciliation');self.assertEqual(artifact['checkpoint']['iteration'],1)
        other=ModeHost(self.app._store,self.project,'run_two')
        try:
            with self.assertRaises(Conflict):self.claim(host=other)
            ready=self.app.reconcile_goal('modes','goal',artifact['revision'],{'checked':True},evidence='manually checked')
            self.assertEqual(ready['checkpoint']['iteration'],1)
            resumed=self.claim(host=other);resumed=self.commit(resumed,'pending',2,other)
            done=self.commit(resumed,'exhausted',host=other);self.assertEqual(done['status'],'exhausted')
            self.assertIsNone(self.claim(host=other)['lease'])
        finally:other.close()

    def test_invalid_goal_transitions_and_revision_rejected(self):
        claim=self.claim()
        for phase,iteration in [('done',0),('pending',2),('pending',True)]:
            with self.assertRaises(ValueError):self.commit(claim,phase,iteration)
        pending=self.commit(claim,'pending',1)
        with self.assertRaises(Conflict):self.commit(claim,'pending',1)
        with self.assertRaises(ValueError):self.commit(pending,'exhausted')
        self.assertEqual(self.app.mode('modes','goal')['checkpoint']['iteration'],1)

    def test_completed_goal_is_not_reset_by_save_or_claim(self):
        done=self.commit(self.commit(self.claim(),'pending',1),'done')
        self.assertIsNone(done['lease']);self.assertEqual(self.claim()['checkpoint']['phase'],'done')
        with self.assertRaises(Conflict):self.app.save_mode('modes','goal',{**GOAL,'max_iterations':10},revision=done['revision'])

    def test_exhausted_reconciliation_never_refunds_iteration(self):
        definition={**GOAL,'max_iterations':1};claim=self.commit(self.claim(definition=definition),'pending',1)
        self.host.close('cancelled');artifact=self.app.mode('modes','goal')
        result=self.app.reconcile_goal('modes','goal',artifact['revision'],{},evidence='effect verified')
        self.assertEqual(result['status'],'exhausted');self.assertEqual(result['checkpoint']['iteration'],1)

    def test_release_old_token_does_not_clear_new_claim(self):
        old=self.claim();self.host.handle('goal.release',{'key':'goal','lease':old['lease'],'outcome':'paused'})
        other=ModeHost(self.app._store,self.project,'run_two')
        try:
            current=self.claim(host=other);self.host.handle('goal.release',{'key':'goal','lease':old['lease'],'outcome':'failed'})
            self.assertEqual(self.app.mode('modes','goal')['runId'],'run_two')
            self.assertEqual(self.commit(current,'pending',1,other)['checkpoint']['iteration'],1)
        finally:other.close()

    def test_recording_receipts_and_success_definition(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'text.normalize',{'text':'  hi '},'2')
        self.host.tool_finish(token,'hi');sealed=self.host.handle('recording.finish',{'scopeId':scope,'parameters':[{'name':'text','step':0,'path':['text']}]})
        self.assertEqual(sealed['definition']['actions'],[{'tool':'text.normalize','input':{'text':'  hi '}}])
        artifact=sealed['artifact'];self.assertEqual(artifact['status'],'ready')
        receipt=artifact['checkpoint']['receipts'][0];self.assertEqual(receipt['status'],'completed');self.assertEqual(receipt['toolVersion'],'2');self.assertEqual(len(receipt['operationId']),32)
        with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})

    def test_recording_cannot_be_forged_or_used_cross_host(self):
        with self.assertRaises(ValueError):self.host.handle('recording.receipt',{'tool':'secret','input':42})
        scope=self.host.handle('recording.begin',{})['scopeId'];other=ModeHost(self.app._store,self.project,'run_two')
        try:
            with self.assertRaises(ValueError):other.tool_begin(scope,'text.normalize','x')
        finally:other.close()

    def test_caught_overlap_permanently_poisons_scope(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'text.normalize','a')
        with self.assertRaises(LanguageError):self.host.tool_begin(scope,'text.normalize','b')
        self.host.tool_finish(token,'a')
        with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})
        self.assertEqual(self.app.modes('modes',kind='skill')['items'][0]['status'],'incomplete')

    def test_failed_receipt_and_pending_close_never_publish(self):
        for failure in (True,False):
            scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'text.normalize','x')
            if failure:self.host.tool_finish(token,{'error':'failed'},status='failed')
            with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})
        self.host.close('cancelled')
        self.assertTrue(all(r['status']=='incomplete'for r in self.app.modes('modes',kind='skill')['items']))
        self.assertEqual(self.app.modes('modes',kind='skill')['items'][-1]['checkpoint']['pending'],[])

    def test_close_interrupts_pending_receipt_in_already_incomplete_scope(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];self.host.tool_begin(scope,'text.normalize','first')
        with self.assertRaises(LanguageError):self.host.tool_begin(scope,'text.normalize','overlap')
        self.host.close('cancelled')
        artifact=self.app.modes('modes',kind='skill')['items'][0]
        self.assertEqual(artifact['status'],'incomplete');self.assertEqual(artifact['checkpoint']['pending'],[])
        self.assertEqual(artifact['checkpoint']['receipts'][0]['status'],'interrupted')

    def test_redacted_input_requires_explicit_parameter(self):
        secret='AUDIT_FAKE_KNOWN_SECRET'
        host=ModeHost(self.app._store,self.project,'secret_run',lambda value:json.loads(json.dumps(value).replace(secret,'[redacted]')))
        try:
            scope=host.handle('recording.begin',{})['scopeId'];token=host.tool_begin(scope,'text.normalize',{'token':secret})
            host.tool_finish(token,secret)
            with self.assertRaises(LanguageError):host.handle('recording.finish',{'scopeId':scope,'parameters':[]})
            ready=host.handle('recording.finish',{'scopeId':scope,'parameters':[{'name':'token','step':0,'path':['token']}]})
            self.assertNotIn(secret,json.dumps(ready));self.assertNotIn(secret,json.dumps(self.app.mode_history('modes',ready['artifact']['key'])))
        finally:host.close()

    def test_browser_canonical_replay_input_replaces_ephemeral_ref(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'browser.control',{'action':'click','ref':'ephemeral'})
        canonical={'action':'click','locator':{'role':'button','name':'Save'}}
        self.host.tool_finish(token,{'ok':True},replay_input=canonical)
        skill=self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})['definition']
        self.assertEqual(skill['actions'][0]['input'],canonical)

    def test_invalid_parameter_path_does_not_create_ready_skill(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'text.normalize',{'text':'hi'});self.host.tool_finish(token,'hi')
        for params in ([{'name':'x','step':True,'path':[]}],[{'name':'x','step':0,'path':['missing']}],[{'name':'x','step':0,'path':[]},{'name':'y','step':0,'path':['text']}]):
            with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':params})
        self.assertEqual(self.app.modes('modes',kind='skill')['items'][0]['status'],'recording')

    def test_recording_aggregate_budget_retains_incomplete_summary(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'text.normalize','x'*60000)
        with self.assertRaises(LanguageError):self.host.tool_finish(token,'y'*60000)
        record=self.app.modes('modes',kind='skill')['items'][0]
        self.assertEqual(record['status'],'incomplete');self.assertTrue(record['checkpoint']['limitReached'])
        with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})

    def test_oversized_result_cannot_be_sealed_after_caught_error(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'text.normalize','hi');self.host.tool_finish(token,'hi')
        token=self.host.tool_begin(scope,'text.normalize','again')
        with self.assertRaises(ValueError):self.host.tool_finish(token,'x'*100001)
        self.host.tool_finish(token,{'error':'large'},status='failed')
        with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})

    def test_recording_count_limit_is_sticky(self):
        scope=self.host.handle('recording.begin',{})['scopeId']
        for i in range(128):token=self.host.tool_begin(scope,'text.normalize',i);self.host.tool_finish(token,i)
        with self.assertRaises(LanguageError):self.host.tool_begin(scope,'text.normalize',129)
        with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})

    def test_host_closure_is_idempotent(self):
        self.claim();self.host.close();self.host.close()
        self.assertEqual(self.app.mode('modes','goal')['status'],'ready')
        with self.assertRaises(LanguageError):self.host.handle('artifact.load',{'key':'goal'})

    def test_none_lease_cannot_target_a_saved_plan(self):
        artifact=self.host.handle('artifact.save',{'key':'plan','definition':PLAN})
        checkpoint={'iteration':1,'state':None,'results':{},'history':[],'phase':'pending','evidence':None}
        with self.assertRaises(Conflict):self.host.handle('goal.commit',{'key':'plan','lease':None,'revision':artifact['revision'],'checkpoint':checkpoint})
        self.host.handle('goal.release',{'key':'plan','lease':None,'outcome':'failed'})
        self.assertEqual(self.app.mode('modes','plan'),artifact)

    def test_recording_input_admission_failure_poisoned(self):
        scope=self.host.handle('recording.begin',{})['scopeId'];token=self.host.tool_begin(scope,'text.normalize','hi');self.host.tool_finish(token,'hi')
        with self.assertRaises(ValueError):self.host.tool_begin(scope,'text.normalize','x'*100001)
        with self.assertRaises(LanguageError):self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})
        self.assertEqual(self.app.modes('modes',kind='skill')['items'][0]['status'],'incomplete')

    def test_client_methods_use_versioned_project_routes(self):
        client=Client();calls=[]
        client._request=lambda *args,**kwargs:calls.append((args,kwargs)) or {}
        client.modes('p',kind='goal');client.mode('p','key',revision=2);client.save_mode('p','key',PLAN,revision=2);client.mode_history('p','key');client.reconcile_goal('p','key',2,{},evidence='checked')
        self.assertEqual([c[0][0]for c in calls],['GET','GET','PUT','GET','POST'])
        self.assertEqual(calls[1][1]['query'],{'revision':2});self.assertEqual(calls[-1][0][1],'/projects/p/modes/key/reconcile')


class ModeRecoveryTests(unittest.TestCase):
    def test_reopen_preserves_old_data_and_marks_pending_work(self):
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'store.sqlite3'
            with Framework(path,settings=Settings(':memory:'))as app:
                project=app.create_project('p',source='input');app._store.memory_put('p','old',{'kept':True})
                host=ModeHost(app._store,project,'crashed_run');claim=host.handle('goal.claim',{'key':'g','definition':GOAL,'input':None})
                checkpoint={**claim['checkpoint'],'phase':'pending','iteration':1}
                host.handle('goal.commit',{'key':'g','lease':claim['lease'],'revision':claim['revision'],'checkpoint':checkpoint})
                scope=host.handle('recording.begin',{})['scopeId'];host.tool_begin(scope,'text.normalize','pending')
            with Framework(path,settings=Settings(':memory:'))as app:
                self.assertEqual(app._store.memory_get('p','old'),{'kept':True})
                self.assertEqual(app.mode('p','g')['status'],'needs_reconciliation')
                recording=app.modes('p',kind='skill')['items'][0];self.assertEqual(recording['status'],'incomplete')
                self.assertEqual(recording['checkpoint']['receipts'][0]['status'],'interrupted')
                self.assertEqual(recording['checkpoint']['pending'],[])
                self.assertEqual(app.get_project('p')['source'],'input')


if __name__=='__main__':unittest.main()
