"""Real browser continuity, fresh authority and cancellation across durable leases."""
import importlib.util
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from chaos import Framework,Settings
from chaos.execution import BrowserSession
from chaos.language import LanguageError

SOURCE='''let c=await computer_connect("desk");var o=await computer_observe(c,false);
if input.url!=null {o=await c(computer_action(o,{action:"navigate",url:input.url}))} else {null};
let result={computer:computer_info(c),observation:observation_info(o)};
if input.close {await computer_close(c)} else {null};result'''
CLICK='''let c=await computer_connect("desk");var o=await computer_observe(c,false);
o=await c(computer_action(o,{action:"navigate",url:input}));
let target=filter(fn(t){t.text=="Go"},observation_info(o).targets)[0];
observation_info(await c(computer_action(o,{action:"click",ref:target.ref})))'''


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ContinuityIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        fixture=self;self.hits=0;self.loads=0
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/hit':fixture.hits+=1;body=b'OK'
                else:
                    fixture.loads+=1
                    body=b'''<title>Durable fixture</title><button onclick="document.querySelector('output').textContent='DONE';fetch('/hit')">Go</button><output></output><script>document.querySelector('output').textContent=localStorage.getItem('fixture')==='yes'&&document.cookie.includes('fixture=yes')?'RESTORED':'EMPTY';localStorage.setItem('fixture','yes');document.cookie='fixture=yes; path=/'</script>'''
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join,3);self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url='http://127.0.0.1:'+str(self.server.server_port);self.db=Path(self.tmp.name)/'state.sqlite3'
        self.settings=Settings(':memory:');self.settings.update({'runtime':{'workspace':self.tmp.name,'timeout':15}},1)
        self.app=Framework(self.db,settings=self.settings);self.addCleanup(self.app.close)
    def project(self,source=SOURCE):self.app.create_project('p',source=source,capabilities=['browser.control'])
    def run_source(self,value):return self.app.start('p',self.app.get_project('p')['version'],value,mode='source')

    def test_close_restart_restores_storage_but_not_dom_or_navigation(self):
        self.project();first=self.run_source({'url':self.url,'close':False}).result(15)
        self.assertIn('EMPTY',first['observation']['text']);self.assertFalse(first['computer']['continued'])
        warm=self.run_source({'url':None,'close':False}).result(15)
        self.assertTrue(warm['computer']['continued']);self.assertEqual(warm['observation']['sessionId'],first['observation']['sessionId'])
        self.app.close();self.app=Framework(self.db,settings=self.settings);self.addCleanup(self.app.close)
        cold=self.run_source({'url':None,'close':False}).result(15)
        self.assertEqual(cold['observation']['url'],'about:blank');self.assertFalse(cold['computer']['continued'])
        self.assertEqual(cold['computer']['stableSessionId'],first['computer']['stableSessionId'])
        self.assertNotEqual(cold['observation']['sessionId'],first['observation']['sessionId'])
        restored=self.run_source({'url':self.url,'close':True}).result(15)
        self.assertIn('RESTORED',restored['observation']['text']);self.assertEqual(self.hits,0)
        self.assertEqual(self.app.computer_session('p','desk')['status'],'detached')

    def test_action_uncertainty_write_failure_blocks_retry_until_fresh_reconcile(self):
        source='''let c=await computer_connect("desk");var o=await computer_observe(c,false);
        o=await c(computer_action(o,{action:"navigate",url:input}));
        let t=filter(fn(t){t.text=="Go"},observation_info(o).targets)[0];
        try{await c(computer_action(o,{action:"click",ref:t.ref}))}catch e{null};
        let fresh=await computer_observe(c,false);
        let blocked=try{await c(computer_action(fresh,{action:"scroll",deltaX:0,deltaY:1}));false}catch e{true};
        let before=computer_info(c);let cleared=await computer_reconcile(c,fresh,"Observed the actual completed page");
        [blocked,before.requiresReconcile,cleared.requiresReconcile]'''
        self.project(source);original=BrowserSession._observe;failed=[False]
        def fail_once(session,page,*args):
            result=original(session,page,*args)
            if 'DONE' in result['snapshot'] and not failed[0]:failed[0]=True;raise RuntimeError('capture fixture failure')
            return result
        with patch.object(BrowserSession,'_observe',fail_once),patch.object(self.app._store.computers,'uncertain_session',side_effect=RuntimeError('uncertain write failed')):
            self.assertEqual(self.run_source(self.url).result(15),[True,True,False])
        self.assertEqual(self.hits,1)
        self.assertFalse(self.app.computer_session('p','desk')['requiresReconcile'])
        self.app.close();self.app=Framework(self.db,settings=self.settings);self.addCleanup(self.app.close)
        self.assertFalse(self.app.computer_session('p','desk')['requiresReconcile'])

    def test_cancel_waits_for_actual_effect_and_closes_driver_before_releasing(self):
        self.project(CLICK);entered=threading.Event();proceed=threading.Event();original=BrowserSession._observe
        def capture(session,page,*args):
            result=original(session,page,*args)
            if 'DONE' in result['snapshot']:entered.set();proceed.wait(5)
            return result
        with patch.object(BrowserSession,'_observe',capture):
            handle=self.run_source(self.url)
            try:
                self.assertTrue(entered.wait(8));self.assertTrue(handle.cancel())
                time.sleep(.05);self.assertNotIn(handle.get()['status'],('cancelled','failed','completed'))
                self.assertEqual(self.app.computer_session('p','desk')['status'],'leased')
                with self.assertRaisesRegex(LanguageError,'leased'):
                    self.app._engine.computer_pool.acquire(SimpleNamespace(project=self.app.get_project('p'),run_id='competitor'),'desk',{'workspace':self.tmp.name,'timeout':3})
            finally:proceed.set()
            self.assertEqual(handle.wait(10)['status'],'cancelled')
        row=self.app.computer_session('p','desk');self.assertEqual(row['status'],'detached');self.assertTrue(row['requiresReconcile'])
        self.assertEqual(self.hits,1);self.assertEqual(self.app._engine.computer_pool.entries,{})

    def test_revoked_grant_rejects_active_driver_operation_but_cleanup_releases(self):
        self.project(CLICK);original=BrowserSession._observe;revoked=[False]
        def revoke(session,page,*args):
            result=original(session,page,*args)
            if page.url==self.url+'/' and not revoked[0]:
                revoked[0]=True
                project=self.app.get_project('p');project['capabilities']=[];self.app.save_project(project)
            return result
        with patch.object(BrowserSession,'_observe',revoke):run=self.run_source(self.url).wait(15)
        self.assertEqual(run['status'],'failed');self.assertIn('revoked',run['error']);self.assertEqual(self.hits,0)
        self.assertEqual(self.app.computer_session('p','desk')['status'],'detached')

    def test_release_then_failure_and_async_scope_failure_close_only_old_idle_generation(self):
        for source in ['let c=await computer_connect("desk");await computer_observe(c,false);await computer_release(c);throw "failure"',
                       'async with await computer_connect("desk") as c {await computer_observe(c,false);throw "failure"}']:
            with self.subTest(source=source):
                if not self.app.projects():self.project(source)
                else:
                    try:project=self.app.get_project('p')
                    except KeyError:self.project(source)
                    else:
                        project['source']=source;project['files'][project['entry']]=source;self.app.save_project(project,from_view='source')
                run=self.run_source(None).wait(15);self.assertEqual(run['status'],'failed',run)
                self.assertEqual(self.app.computer_session('p','desk')['status'],'detached')
                self.assertEqual(self.app._engine.computer_pool.entries,{})
