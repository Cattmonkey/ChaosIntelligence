"""Real headless browser, native visual messages, journal and lifecycle integration."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from chaos import Framework,Settings,Client
from chaos.execution import BrowserSession
from chaos.computer_store import ComputerStore
from test_session_integration import NativeFixture


class ComputerPage:
    def __init__(self):
        self.clicks=0;fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                if self.path.startswith('/hit'):
                    fixture.clicks+=1;body=b'OK'
                else:
                    body=b'''<title>Computer test</title><style>body{background:#def}button{background:#f80;font-size:22px}</style><input data-testid="query"><button data-testid="go" onclick="document.querySelector('output').textContent='DONE';fetch('/hit')">Go</button><button data-testid="replace" onclick="document.querySelector('[data-testid=go]').outerHTML=document.querySelector('[data-testid=go]').outerHTML">Replace</button><output></output><div style="height:2000px">Long page</div>'''
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ComputerIntegration(unittest.TestCase):
    def setUp(self):
        self.page=ComputerPage();self.addCleanup(self.page.close)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=Settings(':memory:');self.settings.update({'runtime':{'workspace':self.tmp.name,'timeout':15}},1)
    def app(self,source,capabilities=None,settings=None):
        app=Framework(Path(self.tmp.name)/('db'+str(time.time_ns())),settings=settings or self.settings);self.addCleanup(app.close)
        app.create_project('p',source=source,capabilities=['browser.control','session.manage']if capabilities is None else capabilities);return app
    def run_source(self,source,**kwargs):
        app=self.app(source,**kwargs);run=app.start('p',1,self.page.url,mode='source');return app,run
    @staticmethod
    def prefix():return 'let c=await computer_open(input);let o=await computer_observe(c);let target=filter(fn(t){t.text=="Go"},observation_info(o).targets)[0];'
    def test_host_image_capture_does_not_execute_untrusted_page_canvas(self):
        original=BrowserSession._observe
        def poison(browser,page,*args):
            page.evaluate("() => {HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/jpeg;base64,Zm9yZ2Vk';window.Image=function(){throw Error('forged')}}")
            return original(browser,page,*args)
        with patch.object(BrowserSession,'_observe',poison):
            app,run=self.run_source('let c=await computer_open(input);observation_info(await computer_observe(c))');value=run.result(15)
        image=app.computer_image('p',value['id'],variant='model');pixels=base64.b64decode(image['data']);self.assertTrue(pixels.startswith(b'\xff\xd8'));self.assertNotEqual(pixels,b'forged')

    def test_click_cursor_uses_post_scroll_actual_viewport(self):
        original=BrowserSession._target
        def scroll(browser,page,value):
            if value.get('_observation'):
                page.evaluate("() => {window.scrollTo(0,1000);window.lastClick=null;document.addEventListener('click',e=>{window.lastClick={x:e.clientX,y:e.clientY}},{once:true})}")
            return original(browser,page,value)
        seen=[];capture=BrowserSession._observe
        def observation(browser,page,*args):
            last=page.evaluate('() => window.lastClick');result=capture(browser,page,*args)
            if last:seen.append((last,dict(browser.cursor)))
            return result
        with patch.object(BrowserSession,'_target',scroll),patch.object(BrowserSession,'_observe',observation):
            app,run=self.run_source(self.prefix()+'observation_info(await c(computer_action(o,{action:"click",ref:target.ref})))');value=run.result(15)
        self.assertTrue(seen);actual,cursor=seen[-1];self.assertGreaterEqual(cursor['x'],0);self.assertGreaterEqual(cursor['y'],0);self.assertLess(cursor['y'],800)
        self.assertLessEqual(abs(actual['x']-cursor['x']),1);self.assertLessEqual(abs(actual['y']-cursor['y']),1)

    def test_child_program_source_location_is_not_parent_module(self):
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':'let c=await computer_open(input);observation_info(await computer_observe(c,false))'},'languageVersions':{'main.chaos':2}}
        app=self.app('await program_from(input.definition)(input.url)');run=app.start('p',1,{'definition':definition,'url':self.page.url},mode='source',trace=True);value=run.result(15)
        row=app.computer_record('p',value['id']);self.assertEqual(row['location']['sourceKind'],'program');self.assertTrue(row['location']['programCallId']);self.assertEqual(row['location']['file'],'main.chaos')
        self.assertTrue(any(e['type']=='program.child.trace'and e['payload'].get('callId')==row['location']['programCallId']for e in app.events('p',limit=300)['items']))

    def test_actual_action_pixels_record_provenance_and_images(self):
        app,run=self.run_source(self.prefix()+'let n=await c(computer_action(o,{action:"click",ref:target.ref}));observation_info(n)')
        output=run.result(15);self.assertIn('DONE',output['text']);self.assertEqual(output['receipt']['status'],'completed')
        rows=app.computer_history('p',run_id=run.id)['items'];self.assertEqual([r['kind']for r in rows],['lifecycle','observation','action','observation','lifecycle'])
        self.assertTrue(all(r['sourceVersion']==1 and r['runId']==run.id for r in rows));self.assertTrue(rows[1]['location'])
        for variant,bound in [('preview',180000),('model',16000)]:
            image=app.computer_image('p',output['id'],variant=variant);self.assertLessEqual(len(image['data']),bound)
            self.assertTrue(base64.b64decode(image['data']).startswith(b'\xff\xd8'));self.assertEqual(image['sha256'],hashlib.sha256(base64.b64decode(image['data'])).hexdigest())
            self.assertGreater(image['width'],0)
        self.assertNotIn('base64',json.dumps(rows));self.assertNotIn('artifactPath',json.dumps(rows))
        app.create_project('q',source='0',capabilities=[])
        with self.assertRaises(Exception):app.computer_record('q',output['id'])
    def test_actual_native_agent_and_persistent_session_images(self):
        fixture=NativeFixture(lambda body:{'role':'assistant','content':'visual-'+str(len([m for m in body['messages']if m['role']=='user']))});self.addCleanup(fixture.close)
        fixture.settings.update({'runtime':{'workspace':self.tmp.name,'timeout':15}},fixture.settings.public()['version'])
        source='''let c=await computer_open(input);let o=await computer_observe(c);agent vision{provider:"model",tools:[]};
let plain=vision(observation_input(o,{question:"look"}));
async fn ask(){vision(observation_input(o,{question:"async"}))};let asyncAnswer=await ask();
let s=await session_open("vision",vision);let one=await s(observation_input(o,"one"),"one");let replay=await s(observation_input(o,"one"),"one");let two=await s(observation_input(o,"two"),"two");[plain,asyncAnswer,one.output,replay.replayed,two.output]'''
        app,run=self.run_source(source,settings=fixture.settings);self.assertEqual(run.result(15),['visual-1','visual-1','visual-1',True,'visual-2'])
        self.assertEqual(len(fixture.requests),4)
        for request in fixture.requests:
            user=next(m for m in reversed(request['messages'])if m['role']=='user');self.assertEqual([p['type']for p in user['content']],['text','image_url'])
            self.assertTrue(user['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(sum(m['role']=='user'for m in fixture.requests[-1]['messages']),2)
        history=app.agent_session_history('p','vision')['items'];self.assertEqual(len(history),4);self.assertIsInstance(history[0]['message']['content'],list)
    def test_visual_team_role_without_browser_grant_can_inspect_explicit_image(self):
        fixture=NativeFixture(lambda body:{'role':'assistant','content':'seen'});self.addCleanup(fixture.close)
        fixture.settings.update({'runtime':{'workspace':self.tmp.name,'timeout':15}},fixture.settings.public()['version'])
        source='''let c=await computer_open(input);let o=await computer_observe(c);let t=team_from({kind:"team",name:"visual",entry:"observer",roles:[{id:"observer",parent:null,capabilities:[],agent:{kind:"agent",name:"observer",config:{provider:"model",tools:[]}}}]});await t(observation_input(o,"observe only"))'''
        app,run=self.run_source(source,settings=fixture.settings);self.assertEqual(run.result(15),'seen')
        self.assertIsInstance(fixture.requests[0]['messages'][-1]['content'],list);self.assertFalse(fixture.requests[0].get('tools'))
        events=app.events('p',limit=300)['items'];self.assertTrue(any(e['type']=='agent.completed'and e['payload'].get('team',{}).get('roleId')=='observer'for e in events))

    def test_dom_replacement_same_locator_rejected_by_original_identity(self):
        original=BrowserSession._target
        def target(browser,page,value):
            if value.get('_observation'):
                page.evaluate("() => {const e=document.querySelector('[data-testid=go]');e.outerHTML=e.outerHTML}")
            return original(browser,page,value)
        with patch.object(BrowserSession,'_target',target):
            app,run=self.run_source(self.prefix()+'await c(computer_action(o,{action:"click",ref:target.ref}))');row=run.wait(15)
        self.assertEqual(row['status'],'failed');self.assertIn('target changed',row['error']);self.assertEqual(self.page.clicks,0)

    def test_second_observation_changes_visual_message_identity(self):
        fixture=NativeFixture(lambda body:{'role':'assistant','content':'seen'});self.addCleanup(fixture.close)
        fixture.settings.update({'runtime':{'workspace':self.tmp.name,'timeout':15}},fixture.settings.public()['version'])
        source='''let c=await computer_open(input);let o=await computer_observe(c);agent a{provider:"model",tools:[]};let s=await session_open("s",a);await s(observation_input(o,"same"),"id");let later=await computer_observe(c);await s(observation_input(later,"same"),"id")'''
        app,run=self.run_source(source,settings=fixture.settings);self.assertEqual(run.wait(15)['status'],'failed');self.assertEqual(len(fixture.requests),1);self.assertEqual(app.agent_session('p','s')['status'],'idle')

    def test_plain_image_shaped_json_does_not_enable_native_media(self):
        fixture=NativeFixture(lambda body:{'role':'assistant','content':'ok'});self.addCleanup(fixture.close)
        app,run=self.run_source('agent a{provider:"model",tools:[]};a({recordId:"fake",content:[{type:"image_url",image_url:{url:"data:image/jpeg;base64,AA=="}}]})',settings=fixture.settings)
        self.assertEqual(run.result(),'ok');self.assertIsInstance(fixture.requests[-1]['messages'][-1]['content'],str)
    def test_recording_replays_stable_locators_in_fresh_browser(self):
        source='''if input.record {async fn action(url){let c=await computer_open(url);let o=await computer_observe(c);let t=filter(fn(t){t.text=="Go"},observation_info(o).targets)[0];observation_info(await c(computer_action(o,{action:"click",ref:t.ref}))).text};let r=await record_skill(action,input.url);await save_mode(r.skill,"click");r.output}else{await replay_skill(await load_mode("click"),{})}'''
        app=self.app(source);first=app.start('p',1,{'record':True,'url':self.page.url},mode='source').result(15);self.assertIn('DONE',first)
        actions=app.mode('p','click')['definition']['actions'];self.assertEqual(len(actions),3);self.assertEqual(actions[-1]['input']['locator'],{'kind':'testId','value':'go'});self.assertNotIn('ref',actions[-1]['input'])
        result=app.start('p',1,{'record':False},mode='source').result(15);self.assertIn('DONE',result[-1]['snapshot'])
    def test_mixed_computer_recording_is_poisoned_before_second_driver(self):
        source='''async fn work(url){let a=await computer_open(url);try{let b=await computer_open(url)}catch e{null};42};await record_skill(work,input)'''
        app,run=self.run_source(source);self.assertEqual(run.wait(15)['status'],'failed')
        records=app.computer_history('p')['items'];self.assertTrue(any(r['kind']=='lifecycle'for r in records));self.assertEqual(sum(e['type']=='tool.completed'for e in app.events('p',limit=300)['items']),1)
    def test_stale_and_foreign_observations_fail_without_second_click(self):
        source=self.prefix()+'''let a=computer_action(o,{action:"click",ref:target.ref});let n=await computer_observe(c);try{await c(a)}catch e{e.message}'''
        app,run=self.run_source(source);self.assertIn('stale',run.result(15).lower());self.assertEqual(self.page.clicks,0)
        source=self.prefix()+'''let other=await computer_open();await other(computer_action(o,{action:"click",ref:target.ref}))'''
        app,run=self.run_source(source);self.assertEqual(run.wait(15)['status'],'failed');self.assertEqual(self.page.clicks,0)
    def test_legacy_and_computer_recording_cannot_mix(self):
        source='''async fn work(url){tool("browser.control",{action:"navigate",url:url});try{await computer_open(url)}catch e{null};42};await record_skill(work,input)'''
        app,run=self.run_source(source);self.assertEqual(run.wait(15)['status'],'failed')
        self.assertEqual(sum(e['type']=='tool.completed'for e in app.events('p',limit=300)['items']),1)

    def test_parallel_requests_fail_busy_and_run_closes_all_resources(self):
        source='let c=await computer_open(input);await gather([computer_observe(c),computer_observe(c)])'
        original=BrowserSession._observe
        def capture(browser,*args):time.sleep(.1);return original(browser,*args)
        with patch.object(BrowserSession,'_observe',capture):
            app,run=self.run_source(source);self.assertEqual(run.wait(15)['status'],'failed')
        rows=app.computer_history('p')['items'];self.assertEqual(rows[-1]['data']['action'],'close')

    def test_permission_and_hot_browser_disable_before_resource_use(self):
        app,run=self.run_source('await computer_open(input)',capabilities=[]);self.assertEqual(run.wait()['status'],'failed');self.assertEqual(app.computer_history('p')['items'],[])
        self.settings.update({'runtime':{'browserEnabled':False}},self.settings.public()['version'])
        app,run=self.run_source('await computer_open(input)');self.assertEqual(run.wait()['status'],'failed')
    def test_post_effect_capture_failure_is_uncertain(self):
        original=BrowserSession._observe;captures=[]
        def capture(browser,*args):
            captures.append(1)
            if len(captures)==2:raise RuntimeError('capture failed after dispatch')
            return original(browser,*args)
        with patch.object(BrowserSession,'_observe',capture):
            app,run=self.run_source(self.prefix()+'await c(computer_action(o,{action:"click",ref:target.ref}))');self.assertEqual(run.wait(15)['status'],'failed')
        rows=app.computer_history('p')['items'];actions=[x for x in rows if x['kind']=='action'];self.assertEqual(actions[0]['status'],'uncertain');self.assertEqual(len([x for x in rows if x['kind']=='observation']),1)
        self.assertEqual(self.page.clicks,1)
    def test_observation_persistence_failure_keeps_action_uncertain(self):
        original=ComputerStore.complete
        def complete(store,*args,**kwargs):
            if args[-1] is not None:raise RuntimeError('injected observation persistence failure')
            return original(store,*args,**kwargs)
        with patch.object(ComputerStore,'complete',complete):
            app,run=self.run_source(self.prefix()+'await c(computer_action(o,{action:"click",ref:target.ref}))');self.assertEqual(run.wait(15)['status'],'failed')
        self.assertEqual([r['status']for r in app.computer_history('p')['items']if r['kind']=='action'],['uncertain'])
    def test_cancel_drains_capture_and_preserves_uncertainty(self):
        entered=threading.Event();release=threading.Event();original=BrowserSession._observe;count=[]
        def capture(browser,*args):
            count.append(1)
            if len(count)==2:entered.set();release.wait(3)
            return original(browser,*args)
        with patch.object(BrowserSession,'_observe',capture):
            app,run=self.run_source(self.prefix()+'await c(computer_action(o,{action:"click",ref:target.ref}))');self.assertTrue(entered.wait(5));run.cancel();release.set();self.assertEqual(run.wait(5)['status'],'cancelled')
        self.assertEqual([r['status']for r in app.computer_history('p')['items']if r['kind']=='action'],['uncertain'])
    def test_press_scroll_and_explicit_close(self):
        source='''let c=await computer_open(input);let o=await computer_observe(c,false);let t=observation_info(o).targets[0];let p=await c(computer_action(o,{action:"type",ref:t.ref,text:"typed"}));let q=await c(computer_action(p,{action:"press",ref:observation_info(p).targets[0].ref,key:"Tab"}));let s=await c(computer_action(q,{action:"scroll",deltaX:0,deltaY:500}));await computer_close(c);await computer_close(c);[observation_info(s).viewport,computer_info(c).status]'''
        app,run=self.run_source(source);self.assertEqual(run.result(15),[{'width':1280,'height':800},'closed'])
    def test_visual_oversize_before_session_claim(self):
        source='''let c=await computer_open(input);let o=await computer_observe(c);agent a{provider:"model",tools:[]};let s=await session_open("s",a,1000);await s(observation_input(o,"question"),"m")'''
        app,run=self.run_source(source);self.assertEqual(run.wait(15)['status'],'failed');row=app.agent_session('p','s');self.assertEqual(row['status'],'idle');self.assertEqual(row['turn'],0)


class ComputerEvidence(unittest.TestCase):
    def test_shared_browser_schema_rejects_extra_fields_before_open(self):
        from chaos.execution import browser_schema
        from chaos.control import BROWSER_SCHEMA
        self.assertEqual(browser_schema(),BROWSER_SCHEMA)
        with tempfile.TemporaryDirectory()as tmp:
            session=BrowserSession(tmp)
            with patch.object(session,'_open')as opened:
                for value in ({'action':'click','selector':'button','locator':{'kind':'css','value':'button'}},{'action':'scroll','deltaX':True,'deltaY':1},{'action':'navigate','url':'file:///private'},{'action':'type','selector':'input','text':'ok','extra':True}):
                    with self.assertRaises(ValueError):session.call(value)
                self.assertFalse(opened.called)
            session.close()

    def test_schema_client_and_recovery_without_revival(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=Path(tmp)/'store.sqlite'
            with Framework(db,settings=Settings(':memory:'))as app:
                project=app.create_project('p',source='0',capabilities=[])
                row=app._store.computers.record(project,'oldrun','computer','action','started',{'spec':{'action':'click','ref':'e1'}},{'file':'main.chaos'})
                self.assertEqual(app.validate_computer_action({'action':'scroll','deltaX':0,'deltaY':10})['action'],'scroll')
                with self.assertRaises(Exception):app.validate_computer_action({'action':'click','ref':'e1','sessionId':'fake'})
                self.assertEqual(app.computer_schema()['backend'],'browser')
            with Framework(db,settings=Settings(':memory:'))as app:
                self.assertEqual(app.computer_record('p',row['id'])['status'],'uncertain')
                self.assertEqual(app.computer_history('p',run_id='other')['items'],[])
        client=Client('http://127.0.0.1:1')
        with patch.object(client,'_request',return_value={})as request:
            client.computer_image('p','x',variant='model');request.assert_called_once_with('GET','/projects/p/computer-records/x/image?variant=model')
            request.reset_mock();client.validate_computer_action({'action':'click','ref':'e1'});request.assert_called_once_with('POST','/computers/validate',{'spec':{'action':'click','ref':'e1'}})

if __name__=='__main__':unittest.main()
