"""Durable claims, failure fencing and cross-run driver ownership."""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from chaos import Framework, Settings
from chaos.computer_pool import ComputerPool
from chaos.language import LanguageError


class Driver:
    def __init__(self,workspace,timeout=15,storage_state=None):
        self.state=copy.deepcopy(storage_state);self.closed=False
        self.entered=None;self.proceed=None;self.fail=False
    def checkpoint_state(self):
        if self.entered:self.entered.set();self.proceed.wait(3)
        if self.fail:raise RuntimeError('checkpoint fixture failure')
        return {'storage':copy.deepcopy(self.state),'url':'about:blank'}
    def close(self,timeout=None):self.closed=True


class ContinuityResources(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Framework(Path(self.tmp.name)/'state.sqlite3');self.addCleanup(self.app.close)
        self.app.create_project('p',source='0',capabilities=['browser.control'])
        self.store=self.app._store.computers;self.pool=self.app._engine.computer_pool
        self.ctx=SimpleNamespace(project=self.app.get_project('p'),run_id='run-a')
        self.config={'workspace':self.tmp.name,'timeout':3}
        self.patch=patch('chaos.computer_pool.BrowserBroker',Driver);self.patch.start();self.addCleanup(self.patch.stop)
    def acquire(self,key='desk',ctx=None):return self.pool.acquire(ctx or self.ctx,key,self.config)

    def test_same_run_distinct_context_cannot_take_lease_and_old_claim_is_fenced(self):
        first=self.acquire();other=SimpleNamespace(project=self.ctx.project,run_id='run-a')
        with self.assertRaisesRegex(LanguageError,'already leased'):self.acquire(ctx=other)
        self.pool.release(first);second=self.acquire(ctx=other)
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(first['stableSessionId'],second['stableSessionId'])
        self.assertEqual(second['generation'],first['generation']+1);self.assertTrue(second['continued'])
        with self.assertRaisesRegex(LanguageError,'stale'):self.pool.validate(first)
        self.pool.release(second,close=True)

    def test_project_scoped_keys_and_metadata_never_expose_storage_or_lease(self):
        self.app.create_project('q',source='0',capabilities=['browser.control'])
        first=self.acquire();second=self.acquire(ctx=SimpleNamespace(project=self.app.get_project('q'),run_id='run-q'))
        first['broker'].state={'cookies':[{'name':'fixture','value':'private-fixture-value'}],'origins':[]}
        self.assertNotEqual(first['stableSessionId'],second['stableSessionId'])
        self.pool.release(first);self.pool.release(second)
        wire=json.dumps(self.store.sessions('p'))
        self.assertNotIn('private-fixture-value',wire);self.assertNotIn(first['claim']['token'],wire);self.assertNotIn('storage',wire)
        self.assertEqual(len(self.store.sessions('p')['items']),1)
        self.assertEqual(self.store.get_session('p','desk')['driverState'],'attached')

    def test_cleanup_releases_only_after_actual_checkpoint_and_does_not_block_other_keys(self):
        first=self.acquire();entered=threading.Event();proceed=threading.Event()
        first['broker'].entered=entered;first['broker'].proceed=proceed;errors=[]
        def release():
            try:self.pool.release(first)
            except BaseException as e:errors.append(e)
        thread=threading.Thread(target=release);thread.start()
        try:
            self.assertTrue(entered.wait(1));self.assertEqual(self.store.get_session('p','desk')['status'],'leased')
            with self.assertRaisesRegex(LanguageError,'already leased'):self.acquire()
            second=self.acquire('unrelated');self.pool.release(second,close=True)
            self.assertTrue(thread.is_alive())
        finally:proceed.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(self.store.get_session('p','desk')['status'],'idle')

    def test_checkpoint_failure_closes_driver_retains_last_state_and_requires_reconcile(self):
        first=self.acquire();first['broker'].fail=True
        with self.assertRaisesRegex(RuntimeError,'checkpoint fixture'):self.pool.release(first)
        self.assertTrue(first['broker'].closed)
        row=self.store.get_session('p','desk');self.assertEqual(row['status'],'detached');self.assertTrue(row['requiresReconcile'])
        second=self.acquire();self.assertFalse(second['continued']);self.assertTrue(second['requiresReconcile'])
        self.pool.release(second,close=True)

    def test_failed_uncertainty_write_stays_fenced_in_memory_and_at_release(self):
        first=self.acquire()
        with patch.object(self.store,'uncertain_session',side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):self.pool.uncertain(first,None,'effect failed')
        self.pool.validate(first);self.assertTrue(first['requiresReconcile'])
        self.pool.release(first,close=True)
        self.assertTrue(self.store.get_session('p','desk')['requiresReconcile'])

    def test_release_persistence_failure_retains_exclusive_claim_until_retry(self):
        first=self.acquire()
        with patch.object(self.store,'release_session',side_effect=RuntimeError('disk failed')):
            with self.assertRaises(RuntimeError):self.pool.release(first,close=True)
        self.assertEqual(self.store.get_session('p','desk')['status'],'leased')
        with self.assertRaises(LanguageError):self.acquire()
        self.pool.release(first,close=True)
        self.assertEqual(self.store.get_session('p','desk')['status'],'detached')

    def test_capacity_never_evicts_idle_dom_and_explicit_close_frees_slot(self):
        with patch.object(self.pool,'MAX_DRIVERS',2):
            a=self.acquire('a');b=self.acquire('b');self.pool.release(a);self.pool.release(b)
            with self.assertRaisesRegex(LanguageError,'capacity'):self.acquire('c')
            self.assertFalse(a['broker'].closed);self.assertFalse(b['broker'].closed)
            reconnect=self.acquire('a');self.assertTrue(reconnect['continued']);self.pool.release(reconnect,close=True)
            c=self.acquire('c');self.pool.release(c,close=True)

    def test_restart_fences_abandoned_claims_and_marks_started_actions_uncertain_once(self):
        claim=self.store.claim_session('p','crashed','old-run')
        row=self.store.record(self.ctx.project,'old-run','old-handle','action','started',
            {'stableSessionId':claim['id'],'sessionKey':'crashed','generation':claim['generation'],'spec':{'action':'navigate'}},{})
        self.app._store.recover()
        status=self.store.get_session('p','crashed');self.assertEqual(status['status'],'detached');self.assertTrue(status['requiresReconcile'])
        self.assertGreater(status['generation'],claim['generation'])
        with self.assertRaises(LanguageError):self.store.validate_lease(claim)
        fresh=self.store.claim_session('p','crashed','new-run');self.store.reconcile_session(fresh,'new-observation','Checked actual page')
        self.store.release_session(fresh);self.app._store.recover()
        self.assertFalse(self.store.get_session('p','crashed')['requiresReconcile'])
        self.assertEqual(self.store.get('p',row['id'])['status'],'uncertain')

    def test_identity_limit_key_validation_and_pagination(self):
        for key in ['', '../escape', 'a'*129, True]:
            with self.assertRaises((ValueError,LanguageError)):self.store.claim_session('p',key,'r')
        for n in range(64):
            claim=self.store.claim_session('p','key'+str(n),'r');self.store.release_session(claim)
        with self.assertRaisesRegex(LanguageError,'64'):self.store.claim_session('p','overflow','r')
        page=self.store.sessions('p',0,50);self.assertEqual(len(page['items']),50);self.assertEqual(page['after'],50);self.assertTrue(page['hasMore'])
        tail=self.store.sessions('p',page['after'],50);self.assertEqual(len(tail['items']),14);self.assertIsNone(tail['after']);self.assertFalse(tail['hasMore'])
        claim=self.store.claim_session('p','key0','r');self.store.release_session(claim)

    def test_old_released_resource_cannot_close_new_generation(self):
        first=self.acquire();self.pool.release(first)
        second=self.acquire();self.pool.release(second)
        self.pool.close_idle(first)
        self.assertFalse(second['broker'].closed)
        self.assertEqual(self.store.get_session('p','desk')['status'],'idle')
        self.pool.close_idle(second)
        self.assertTrue(second['broker'].closed)
        self.assertEqual(self.store.get_session('p','desk')['status'],'detached')

    def test_engine_close_timeout_retains_database_ownership_until_driver_stops(self):
        resource=self.acquire();self.pool.release(resource)
        broker=resource['broker'];original=broker.close;timeouts=[]
        def delayed(timeout=None):
            timeouts.append(timeout)
            raise TimeoutError('driver still closing')
        with patch.object(broker,'close',delayed):
            with self.assertRaises(TimeoutError):self.app.close(timeout=.05)
        self.assertTrue(self.app._store.runtime_owned)
        self.assertEqual(self.store.get_session('p','desk')['status'],'idle')
        self.assertLessEqual(timeouts[0],.05)
        self.app.close();self.assertTrue(broker.closed)
        with Framework(Path(self.tmp.name)/'state.sqlite3') as reopened:
            self.assertEqual(reopened._store.computers.get_session('p','desk')['status'],'detached')

    def test_accepted_cancel_after_normal_release_retires_idle_driver_before_terminal(self):
        from chaos.runtime import Context
        project=self.app.get_project('p');project['source']='computer_info(await computer_connect("desk"))'
        project['files'][project['entry']]=project['source'];self.app.save_project(project,from_view='source')
        released=threading.Event();proceed=threading.Event();original=Context.close_services
        def finish(context,outcome):
            original(context,outcome)
            if outcome=='completed':released.set();proceed.wait(3)
        with patch.object(Context,'close_services',finish):
            handle=self.app.start('p',self.app.get_project('p')['version'],None,mode='source')
            try:
                self.assertTrue(released.wait(2));self.assertEqual(self.store.get_session('p','desk')['status'],'idle')
                self.assertTrue(handle.cancel())
            finally:proceed.set()
            self.assertEqual(handle.wait(5)['status'],'cancelled')
        self.assertEqual(self.store.get_session('p','desk')['status'],'detached')
        self.assertEqual(self.pool.entries,{})

    def test_shutdown_uses_last_release_checkpoint_without_reading_live_storage(self):
        resource=self.acquire()
        resource['broker'].state={'cookies':[{'name':'fixture','value':'committed'}],'origins':[]}
        self.pool.release(resource)
        resource['broker'].state={'cookies':[{'name':'fixture','value':'after-release'}],'origins':[]}
        resource['broker'].fail=True
        self.app.close()
        self.assertTrue(resource['broker'].closed)
        with Framework(Path(self.tmp.name)/'state.sqlite3') as reopened:
            claim=reopened._store.computers.claim_session('p','desk','next-run')
            self.assertEqual(claim['storage']['cookies'][0]['value'],'committed')
            self.assertFalse(claim['requiresReconcile'])
            reopened._store.computers.release_session(claim)

    def test_engine_close_detaches_idle_brokers_before_unlocking_database(self):
        resource=self.acquire();self.pool.release(resource)
        self.app.close();self.assertTrue(resource['broker'].closed)
        with Framework(Path(self.tmp.name)/'state.sqlite3') as reopened:
            row=reopened._store.computers.get_session('p','desk')
            self.assertEqual(row['status'],'detached');self.assertEqual(row['driverState'],'detached')
            self.assertFalse(row['requiresReconcile'])
