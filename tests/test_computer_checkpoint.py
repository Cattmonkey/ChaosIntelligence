"""Private browser checkpointing retains storage, never live page authority."""
import importlib.util
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from chaos.execution import BrowserBroker, BrowserSession, ExecutionError, browser_storage


class CheckpointBrokerTests(unittest.TestCase):
    def test_checkpoint_runs_serially_on_owner_and_copies_restore_input(self):
        owners=[]
        class Session:
            def __init__(self, workspace, timeout, storage_state):
                owners.append(threading.get_ident());self.storage=storage_state
            def call(self, value, checkpoint):
                owners.append(threading.get_ident());return value
            def checkpoint_state(self):
                owners.append(threading.get_ident());return {'storage':self.storage,'url':'about:blank'}
            def close(self):owners.append(threading.get_ident())
        state={'cookies':[],'origins':[]}
        broker=BrowserBroker('.',session_factory=Session,storage_state=state)
        state['origins'].append({'bad':'mutation'})
        try:
            self.assertEqual(broker.call({'a':1}),{'a':1})
            self.assertEqual(broker.checkpoint_state(),{'storage':{'cookies':[],'origins':[]},'url':'about:blank'})
        finally:broker.close()
        self.assertEqual(len(set(owners)),1);self.assertNotEqual(owners[0],threading.get_ident())

    def test_bound_and_close_timeout_do_not_claim_a_live_worker_stopped(self):
        with self.assertRaises(ExecutionError):browser_storage({'cookies':[],'origins':[{'x':'a'*1048576}]})
        with self.assertRaises(ExecutionError):browser_storage({'cookies':[],'origins':[],'extra':42})
        entered,release=threading.Event(),threading.Event();errors=[]
        class Session:
            def __init__(self,*a,**k):pass
            def call(self,value,checkpoint):entered.set();release.wait(3);checkpoint()
            def close(self):pass
        broker=BrowserBroker('.',session_factory=Session)
        def run():
            try:broker.call({})
            except ExecutionError as e:errors.append(e)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(TimeoutError):broker.close(timeout=.01)
            self.assertTrue(thread.is_alive());self.assertTrue(broker._thread.is_alive())
        finally:release.set();thread.join(3);broker.close(timeout=3)
        self.assertFalse(broker._thread.is_alive());self.assertEqual(len(errors),1)

    def test_close_timeout_with_full_queue_keeps_eventual_shutdown_signal(self):
        entered,release=threading.Event(),threading.Event();errors=[];effects=[]
        class Session:
            def __init__(self,*a,**k):pass
            def call(self,value,checkpoint):
                entered.set();release.wait(5);effects.append(value);checkpoint()
            def close(self):pass
        broker=BrowserBroker('.',session_factory=Session)
        def run(value):
            try:broker.call(value)
            except ExecutionError as error:errors.append(error)
        threads=[threading.Thread(target=run,args=(i,))for i in range(129)]
        threads[0].start();self.assertTrue(entered.wait(1))
        try:
            for thread in threads[1:]:thread.start()
            deadline=time.monotonic()+3
            while broker._queue.qsize()!=128 and time.monotonic()<deadline:time.sleep(.005)
            self.assertEqual(broker._queue.qsize(),128)
            start=time.monotonic()
            with self.assertRaises(TimeoutError):broker.close(timeout=.01)
            self.assertLess(time.monotonic()-start,.5)
            self.assertTrue(broker._thread.is_alive())
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:thread.join(3)
            broker.close(timeout=3)
        self.assertEqual(effects,[0]);self.assertEqual(len(errors),129)
        self.assertFalse(any(thread.is_alive()for thread in threads))
        self.assertFalse(broker._thread.is_alive())

    def test_lazy_checkpoint_never_launches_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker=BrowserBroker(tmp)
            try:self.assertEqual(broker.checkpoint_state(),{'storage':{'cookies':[],'origins':[]},'url':'about:blank'})
            finally:broker.close()


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class BrowserCheckpointTests(unittest.TestCase):
    def test_real_storage_restore_and_reference_fence_without_navigation(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'''<title>Checkpoint</title><button data-testid="save" onclick="localStorage.setItem('work','saved');document.cookie='work=saved; Path=/';document.querySelector('output').textContent='UNSAVED DOM'">Save</button><output id="storage"></output><script>document.querySelector('output').textContent='restored:'+localStorage.getItem('work')</script>'''
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
            def log_message(self,*a):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join,3);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url='http://127.0.0.1:'+str(server.server_port)
        with tempfile.TemporaryDirectory() as tmp:
            one=BrowserBroker(tmp)
            try:
                observed=one.call({'action':'navigate','url':url})
                ref=observed['targets'][0]['ref']
                guards={k:observed[k]for k in ('sessionId','controlEpoch','snapshotId')}
                one.call({'action':'click','ref':ref,**guards})
                latest=one.call({'action':'observe','image':False})
                checkpoint=one.checkpoint_state()
                self.assertIn('UNSAVED DOM',latest['snapshot'])
                self.assertEqual(checkpoint['url'],url+'/')
                self.assertEqual(checkpoint['storage']['cookies'][0]['value'],'saved')
                with self.assertRaisesRegex(ExecutionError,'Stale'):
                    one.call({'action':'click','ref':latest['targets'][0]['ref'],**{k:latest[k]for k in guards}})
                current=one.call({'action':'observe','image':False})
                self.assertIn('UNSAVED DOM',current['snapshot']);self.assertEqual(current['sessionId'],latest['sessionId'])
            finally:one.close()
            two=BrowserBroker(tmp,storage_state=checkpoint['storage'])
            try:
                blank=two.call({'action':'observe','image':False})
                self.assertEqual(blank['url'],'about:blank');self.assertNotEqual(blank['sessionId'],latest['sessionId'])
                restored=two.call({'action':'navigate','url':url})
                self.assertIn('restored:saved',restored['snapshot']);self.assertNotIn('UNSAVED DOM',restored['snapshot'])
            finally:two.close()
