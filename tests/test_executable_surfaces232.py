"""SDK, actual loopback HTTP and terminal entries share persistent image runs."""
import copy
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Registry, Settings
from chaos.client import ValidationError, ConflictError
from chaos.executable_image import compile_executable
from chaos.server import make_server
from test_executable231 import no_frontend
from test_run_terminal230 import (PtySession, RunPty, environment, wait_file,
                                  result_envelopes, queued, rows)
import test_run_terminal230 as terminal

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/_executable_terminal232.py'


class ExecutableHTTP232(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);work=Path(self.temp.name)
        settings=Settings(':memory:');settings.update({'providers':{name:{'clearKey':True,'baseURL':'http://127.0.0.1:1'} for name in settings.public()['providers']},'runtime':{'continuousEnabled':False,'codeEnabled':False,'browserEnabled':False,'workspace':str(work/'workspace')}},version=settings.public()['version'])
        self.entered=threading.Event();self.release=threading.Event();self.effects=[];registry=Registry()
        @registry.tool('fixture.http232',effect='pure')
        def hold(value,ctx):
            self.entered.set()
            if not self.release.wait(8):raise AssertionError('HTTP fixture not released')
            ctx.checkpoint();self.effects.append(value);return value
        self.app=Framework(':memory:',registry=registry,settings=settings);self.addCleanup(self.app.close)
        self.project=self.app.create_project('http-image',source='let authority_only=999;authority_only',capabilities=['fixture.http232'])
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=work/'control',update_home=work/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port));self.denied=[]
        original=socket.socket.connect;original_ex=socket.socket.connect_ex;port=self.server.server_port
        def admit(address):
            if not(type(address)is tuple and address[0]=='127.0.0.1' and address[1]==port):self.denied.append(repr(address));raise AssertionError('Unexpected HTTP fixture network')
        def connect(sock,address):admit(address);return original(sock,address)
        def connect_ex(sock,address):admit(address);return original_ex(sock,address)
        self.net=patch.object(socket.socket,'connect',connect);self.net_ex=patch.object(socket.socket,'connect_ex',connect_ex)
        self.net.start();self.net_ex.start();self.addCleanup(self.net.stop);self.addCleanup(self.net_ex.stop)
    def close_server(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(3)
        self.assertFalse(self.thread.is_alive());self.assertEqual(self.denied,[])

    def test_client_start_same_id_watch_guidance_receipt_and_actual_frozen_snapshot(self):
        image=compile_executable('await progress_emit("progress",{summary:"image"});tool("fixture.http232",40);let g=await guidance_receive(null);[input,g.message.input]')
        before=copy.deepcopy(self.project)
        with no_frontend():run=self.client.start_executable(before['id'],before['version'],image,42)
        try:
            self.assertTrue(self.entered.wait(3));receipt=run.guide({'text':'continue'},message_id='http-image')
            self.assertEqual(receipt,run.guide({'text':'continue'},message_id='http-image'))
            self.release.set();self.assertEqual(run.result(10),[42,{'text':'continue'}])
        finally:self.release.set()
        record=run.get();self.assertEqual(record['mode'],'executable');self.assertEqual(record['snapshot']['execution']['image'],image)
        events=self.client.events(before['id'],run_id=run.id,limit=300)['items']
        self.assertEqual(list(self.client.watch(before['id'],run_id=run.id,timeout=2)),events)
        self.assertEqual(sum(e['type']=='guidance.queued' for e in events),1);self.assertTrue(all(e['run_id']==run.id for e in events))
        self.assertEqual(self.effects,[40]);self.assertEqual(self.app.get_project(before['id']),before)

    def test_HTTP_executable_mode_auto_debug_and_image_ambiguity_fail_before_admission(self):
        image=compile_executable('42');p=self.project;before=self.app.runs(p['id']);cursor=self.app.events(p['id'])['lastEventId']
        base={'projectId':p['id'],'version':p['version'],'executable':image}
        for extra in ({'mode':'source'},{'mode':'graph'},{'autoOptimize':True},{'debug':True},{'debug':{}},{'executable':None}):
            with self.subTest(extra=extra),self.assertRaises(ValidationError):self.client._request('POST','/runs',dict(base,**extra))
        with self.assertRaises(ConflictError):self.client.start_executable(p['id'],p['version']+1,image,None)
        self.assertEqual(self.app.runs(p['id']),before);self.assertEqual(self.app.events(p['id'])['lastEventId'],cursor);self.assertEqual(self.effects,[])

    def test_HTTP_cancel_and_original_source_start_remain_distinct(self):
        image=compile_executable('await sleep(30)');p=self.project
        run=self.client.start_executable(p['id'],p['version'],image,None);self.assertTrue(run.cancel());self.assertEqual(run.wait(8)['status'],'cancelled')
        source=self.client.start(p['id'],p['version'],None,mode='source');self.assertEqual(source.result(8),999)
        self.assertNotIn('execution',source.get()['snapshot'])

    def test_OpenAPI_executable_admission_is_documented_without_new_authority_field(self):
        schema=self.client._request('GET','/openapi.json');start=schema['components']['schemas']['StartRun']
        self.assertIn('executable',start['properties']);self.assertEqual(schema['paths']['/runs']['post']['operationId'],'startRun')
        for forbidden in ('capabilities','registry','provider','source'):
            self.assertNotIn(forbidden,start['properties'])


def prepare(work,source):
    image=compile_executable(source);file=work/'image.json';file.write_text(json.dumps(image))
    with Framework(str(work/'runtime.sqlite'),settings=Settings(':memory:')) as app:
        p=app.get_project('workspace');p.update(source='let authority_only=999;authority_only',capabilities=['fixture.block','fixture.effect'])
        saved=app.save_project(p,from_view='source')
    return file,saved,image


def executable_command(work,source,flags=(),filename=None):
    file=work/'image.json'
    return [sys.executable,'-B',str(FIXTURE),str(work),'executable','start',str(file) if filename is None else filename,
            '--db',str(work/'runtime.sqlite'),'--config',str(work/'settings.json'),*flags]


class ExecutableCLI232(unittest.TestCase):
    def audit(self,work,status='completed',interactive=False):
        audit=json.loads((work/'audit.json').read_text());self.assertTrue(audit['appsClosed']);self.assertEqual(audit['activeRuns'],0);self.assertEqual(audit['activeOperations'],0)
        self.assertEqual([r['status'] for r in audit['runs']],[status]);self.assertEqual(audit['counters']['saved'],0)
        if interactive:
            self.assertEqual(len(audit['consoleWorkers']),2);self.assertTrue(all(t['joined'] and not t['alive'] for t in audit['consoleWorkers']))
        return audit
    def project_unchanged(self,work,before):
        with Framework(str(work/'runtime.sqlite'),settings=Settings(':memory:')) as app:self.assertEqual(app.get_project('workspace'),before)
    def test_CLI_default_result_and_stream_use_original_handle_without_saving_source(self):
        for flags in ([],['--stream']):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as directory:
                work=Path(directory);_,before,image=prepare(work,'40+2')
                result=subprocess.run(executable_command(work,None,flags),cwd=work,env=environment(work),stdin=subprocess.DEVNULL,text=True,capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr);audit=self.audit(work)
                if flags:
                    events=[json.loads(line) for line in result.stdout.splitlines()]
                    self.assertEqual(events[:-1],audit['events']);self.assertEqual(events[-1],{'type':'run.result','runId':audit['runs'][0]['id'],'output':42})
                else:self.assertEqual(result.stdout,'42\n')
                self.project_unchanged(work,before)
    def test_CLI_stream_flushes_actual_persisted_event_before_held_effect_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);_,before,_=prepare(work,terminal.HELD)
            child=subprocess.Popen(executable_command(work,None,['--stream']),cwd=work,env=environment(work),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                wait_file(work/'entered.json');prefix=bytearray();deadline=time.monotonic()+5
                while b'bot.progress' not in prefix and time.monotonic()<deadline:
                    if select.select([child.stdout],[],[],.1)[0]:prefix.extend(os.read(child.stdout.fileno(),65536))
                self.assertIn(b'bot.progress',prefix);self.assertIsNone(child.poll());self.assertFalse((work/'release').exists())
                (work/'release').touch();tail,err=child.communicate(timeout=10);self.assertEqual(child.returncode,0,err)
                actual=rows(bytes(prefix)+tail);audit=self.audit(work)
                self.assertEqual(actual[:-1],audit['events']);self.assertEqual(actual[-1],{'type':'run.result','runId':audit['runs'][0]['id'],'output':42})
                self.project_unchanged(work,before)
            finally:
                (work/'release').touch()
                if child.poll() is None:child.kill();child.communicate(timeout=3)
    @unittest.skipUnless(os.name=='posix','Actual POSIX terminal required')
    def test_CLI_interactive_guidance_changes_real_Bot_decision_once(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);_,before,_=prepare(work,terminal.BOT)
            with patch.object(terminal,'command',executable_command):tty=RunPty(work,None)
            self.addCleanup(tty.close);tty.read_until('guide> ');tty.wait_for(lambda:(work/'entered.json').exists())
            tty.write('/detach\r');tty.write('验证 👋\r');tty.read_until('"receipt"');self.assertEqual(queued(work),1);(work/'release').touch()
            self.assertEqual(tty.finished(),0);audit=self.audit(work,interactive=True)
            self.assertEqual(audit['providerCalls'],['held']);self.assertEqual(audit['effects'],[{'text':'验证 👋'}])
            self.assertEqual(result_envelopes(bytes(tty.data)),[{'type':'run.result','runId':audit['runs'][0]['id'],'output':[{'text':'验证 👋'},2]}]);self.project_unchanged(work,before)
    @unittest.skipUnless(os.name=='posix','Actual POSIX terminal required')
    def test_CLI_cancel_and_control_keys_cancel_drain_same_local_image_run(self):
        for terminator in ('\x03','\x04','/cancel\r'):
            with self.subTest(terminator=repr(terminator)),tempfile.TemporaryDirectory() as directory:
                work=Path(directory);_,before,_=prepare(work,terminal.HELD)
                with patch.object(terminal,'command',executable_command):tty=RunPty(work,None)
                self.addCleanup(tty.close);tty.read_until('guide> ');tty.wait_for(lambda:(work/'entered.json').exists());tty.write(terminator)
                self.assertEqual(tty.finished(),130);audit=self.audit(work,status='cancelled',interactive=True)
                self.assertTrue(audit['gateExited']);self.assertEqual(audit['effects'],[])
                self.assertEqual(result_envelopes(bytes(tty.data),'run.exit'),[{'type':'run.exit','runId':audit['runs'][0]['id'],'status':'cancelled'}]);self.project_unchanged(work,before)
    def test_CLI_invalid_terminal_and_stdin_image_fail_before_Framework_construction(self):
        for file in ('image.json','-'):
            with self.subTest(file=file),tempfile.TemporaryDirectory() as directory:
                work=Path(directory);prepare(work,'tool("fixture.effect",1)')
                result=subprocess.run(executable_command(work,None,['--interactive'],str(work/file) if file!='-' else '-'),cwd=work,env=environment(work),input='',text=True,capture_output=True,timeout=10)
                self.assertEqual(result.returncode,1);audit=json.loads((work/'audit.json').read_text())
                self.assertEqual(audit['counters'],{'constructed':0,'saved':0,'started':0});self.assertEqual(audit['runs'],[]);self.assertEqual(audit['effects'],[])

    @unittest.skipUnless(shutil.which('node'),'Node.js required for actual snapshot JavaScript')
    def test_web_snapshot_handler_displays_executable_identity_before_authority_source(self):
        script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8'),start=source.indexOf('function showSourceSnapshot('),end=source.indexOf("\n$('#viewSnapshot').onclick",start);
assert(start>=0&&end>start);const handlerEnd=source.indexOf('\n',end+1);const calls=[],controls=[];let graphCalls=0,removed=0,shown=0;
function element(tag){return {tag,dataset:{},children:[],value:'',append(child){this.children.push(child);if(tag==='select'&&child.selected)this.value=child.value},setAttribute(){},before(child){controls.push(child)},querySelector(){return {remove(){removed++}}},showModal(){shown++}}}
const pre=element('pre'),dialog=element('dialog'),button={},nodes={'#snapshotSource':pre,'#snapshotDialog':dialog,'#viewSnapshot':button};
const context=vm.createContext({window:{ChaosCode:{update(...args){calls.push(args)}}},document:{createElement:element,createTextNode:x=>x},$:id=>nodes[id],runSnapshot:null,runVersion:7,snapshotView:false,view:'graph',syncState(){graphCalls++},renderGraph(){graphCalls++},renderInspector(){graphCalls++},fit(){graphCalls++}});
vm.runInContext(source.slice(start,end)+source.slice(end+1,handlerEnd),context);
const image={id:'authority',version:7,authoring:'workflow',source:'DO_NOT_DISPLAY_HOST_SOURCE',files:{'main.chaos':'DO_NOT_DISPLAY_HOST_SOURCE'},entry:'main.chaos',execution:{kind:'executable',imageHash:'a'.repeat(64),entry:'actual.chaos',image:{objects:[{module:'actual.chaos',objectHash:'b'.repeat(64),languageVersion:2}]}}};
context.runSnapshot=image;button.onclick();assert.equal(shown,1);assert.equal(graphCalls,0);assert.equal(controls.length,0);assert.equal(calls[0][2].language,'json');assert(!calls[0][1].includes('DO_NOT_DISPLAY'));
const metadata=JSON.parse(calls[0][1]);assert.equal(metadata.imageHash,image.execution.imageHash);assert.equal(metadata.authorityProject,'authority');assert.equal(metadata.authorityVersion,7);assert.equal(metadata.modules[0].module,'actual.chaos');assert(!('source'in metadata));assert(!('files'in metadata));
context.runSnapshot={id:'authority',authoring:'source',source:'42',entry:'main.chaos',files:{'main.chaos':'42','lib.chaos':'fn helper(){41}'},program:{languageVersion:2}};button.onclick();assert.equal(shown,2);assert.equal(graphCalls,0);assert.equal(calls[1][1],'42');assert.equal(calls[1][2].language,'chaos');assert.equal(controls.length,1);
const select=controls[0].children.find(x=>x&&x.tag==='select');assert(select);select.value='lib.chaos';select.onchange();assert.equal(calls[2][1],'fn helper(){41}');assert.equal(calls[2][2].file,'lib.chaos');assert.equal(removed,2);console.log(JSON.stringify({passed:true,actualFunction:true,imageIdentity:true,sourceModules:true}));'''
        result=subprocess.run([shutil.which('node'),'-e',script,str(ROOT/'web/sync.js')],cwd=ROOT,text=True,capture_output=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(json.loads(result.stdout)['passed'])
