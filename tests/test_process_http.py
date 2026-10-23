"""Public durable language Process transport, CLI and shared state semantics."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from chaos import Framework,Client,Settings
from chaos.client import APIError,ValidationError,ConflictError
from chaos.server import make_server


def definition(**changes):
    return { 'kind':'process','name':'counter','module':'counter.chaos','export':'step','intervalSeconds':None,'maxTurns':100,**changes}


class ProcessHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.client=Client(self.origin)
        self.project=self.client.create_project('persistent',source='42',files={'counter.chaos':'fn step(ctx){process_step({count:ctx.state.count+1},ctx.programVersion,false)};export{step};'},capabilities=['process.manage'])
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def wait(self,key='counter',turn=1,status='waiting'):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            item=self.client.get_process('persistent',key)
            if item['turn']>=turn and item['status']==status:return item
            if item['status']=='needs_reconciliation':self.fail(str(item))
            time.sleep(.01)
        self.fail('Process did not reach expected state: '+str(item))
    def create(self,key='counter',**changes):
        return self.client.create_process('persistent',1,key,definition(**changes),state={'count':0})
    def cli(self,*args):
        result=subprocess.run([sys.executable,'-m','chaos','process',*args,'--server',self.origin],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)

    def test_validation_schema_export_roundtrip_is_pure(self):
        value=definition();before=self.client.events('persistent')['lastEventId']
        checked=self.client.validate_process(value,name='持久计数器')
        self.assertEqual(checked,self.app.validate_process(value,name='持久计数器'))
        self.assertEqual(Framework.evaluate('import "p.chaos" as P;process_info(P.持久计数器)',files={'p.chaos':checked['source']}),value)
        self.assertEqual(self.client.process_schema(),self.app.process_schema())
        self.assertEqual(self.client.events('persistent')['lastEventId'],before)
        with self.assertRaises(ValidationError):self.client.validate_process({**value,'capabilities':['code.execute']})
        with self.assertRaises(ValidationError):self.client._request('POST','/processes/validate',{'definition':value,'origin':'forged'})
        with self.assertRaises(ValidationError):self.client.validate_process(value,name='process_from')

    def test_actual_export_turn_mailbox_and_idempotent_transport(self):
        self.create();first=self.wait();self.assertEqual(first['state'],{'count':1});self.assertEqual(first['output'],1)
        receipt=self.client.send_process('persistent','counter',{'request':42},message_id='first-message')
        second=self.wait(turn=2);self.assertEqual(second['state'],{'count':2})
        again=self.client.send_process('persistent','counter',{'request':42},message_id='first-message')
        self.assertEqual(again['messageId'],receipt['messageId'])
        with self.assertRaises(ConflictError):self.client.send_process('persistent','counter',{'request':43},message_id='first-message')
        self.assertEqual(self.client.get_process('persistent','counter')['turn'],2)
        run=self.client.get_run(second['lastRunId']);self.assertEqual(run['status'],'completed');self.assertEqual(run['version'],1)
        self.assertEqual(run['snapshot']['source'],'42');self.assertEqual(self.client.get_project('persistent')['source'],'42')
        self.assertTrue(any(e['type']=='process.turn.completed'for e in self.client.events('persistent')['items']))

    def test_revision_controls_configuration_and_latest_project_version(self):
        self.create();item=self.wait();paused=self.client.control_process('persistent','counter',item['revision'],'pause')
        self.assertEqual(paused['status'],'paused')
        with self.assertRaises(ConflictError):self.client.control_process('persistent','counter',item['revision'],'resume')
        changed=self.client.configure_process('persistent','counter',paused['revision'],definition(name='new name'))
        self.assertEqual(changed['definition']['name'],'new name');self.assertEqual(changed['state'],{'count':1})
        self.client.edit_program('persistent',1,file='counter.chaos',source='fn step(ctx){process_step({count:ctx.state.count+10},ctx.programVersion,false)};export{step};')
        self.client.send_process('persistent','counter',None,message_id='after-edit')
        current=self.client.get_process('persistent','counter')
        self.client.control_process('persistent','counter',current['revision'],'resume')
        final=self.wait(turn=2);self.assertEqual(final['state'],{'count':11});self.assertEqual(final['output'],2)
        stopped=self.client.control_process('persistent','counter',final['revision'],'stop');self.assertEqual(stopped['status'],'stopped')

    def test_authority_project_scope_and_closed_transport(self):
        self.client.create_project('denied',source='null',files={'counter.chaos':'fn step(x){process_step(null)};export{step};'},capabilities=[])
        with self.assertRaises(APIError):self.client.create_process('denied',1,'counter',definition())
        with self.assertRaises(ConflictError):self.client.create_process('persistent',99,'counter',definition())
        self.create();self.wait()
        for suffix,body in [('messages',{'input':None,'projectId':'denied'}),('control',{'revision':1,'action':'pause','capabilities':[]}),('configure',{'revision':1,'definition':definition(),'state':None}),('reconcile',{'revision':1,'state':None,'passed':True})]:
            with self.subTest(suffix=suffix),self.assertRaises(ValidationError):
                self.client._request('POST','/projects/persistent/processes/counter/'+suffix,body)
        with self.assertRaises(APIError):self.client.get_process('denied','counter')
        self.assertEqual(self.client.processes('persistent')['items'][0]['key'],'counter')
        self.assertEqual(self.client.processes('denied')['items'],[])

    def test_failed_turn_needs_explicit_reconciliation_not_resume(self):
        self.client.edit_program('persistent',1,file='counter.chaos',source='fn step(ctx){throw "intentional failure"};export{step};')
        self.client.create_process('persistent',2,'bad',definition(),state={'count':5})
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            item=self.client.get_process('persistent','bad')
            if item['status']=='needs_reconciliation':break
            time.sleep(.01)
        self.assertEqual(item['status'],'needs_reconciliation');self.assertEqual(item['state'],{'count':5});self.assertIsNotNone(item['pending'])
        with self.assertRaises(APIError):self.client.control_process('persistent','bad',item['revision'],'resume')
        settled=self.client.reconcile_process('persistent','bad',item['revision'],{'count':5},evidence='Inspected failed run; retain checkpoint',resume=False)
        self.assertIsNone(settled['pending']);self.assertIn(settled['status'],('paused','stopped'))
        with self.assertRaises(ConflictError):self.client.reconcile_process('persistent','bad',item['revision'],{'count':99})
        self.assertEqual(self.client.get_process('persistent','bad')['state'],{'count':5})

    def test_cli_controls_use_same_service_and_exact_json_values(self):
        file=Path(self.temp.name)/'process.json';file.write_text(json.dumps(definition()))
        created=self.cli('create','persistent','cli','--version','1','--definition-file',str(file),'--state-json','{"count":9007199254740993}')
        self.assertEqual(created['key'],'cli');item=self.wait('cli')
        self.assertEqual(item['state']['count'],9007199254740994)
        queried=self.cli('get','persistent','cli');self.assertEqual(queried['state'],item['state'])
        self.assertEqual(self.cli('list','persistent')['items'][0]['key'],'cli')
        self.cli('send','persistent','cli','--input-json','{"id":9007199254740995}','--message-id','from-cli')
        item=self.wait('cli',2);paused=self.cli('pause','persistent','cli','--revision',str(item['revision']))
        self.assertEqual(paused['status'],'paused')

    def test_openapi_preserves_old_paths_and_has_precise_new_contract(self):
        spec=self.client._request('GET','/openapi.json');schemas=spec['components']['schemas'];paths=spec['paths']
        self.assertEqual(schemas['ProcessDefinition'],self.client.process_schema())
        for name in ('CreateProcess','ProcessMessage','ProcessConfigure','ProcessControl','ProcessReconcile'):self.assertFalse(schemas[name]['additionalProperties'])
        self.assertEqual(paths['/projects/{projectId}/processes/{key}/messages']['post']['operationId'],'sendProcess')
        self.assertEqual(paths['/projects/{projectId}/program-adopt']['post']['operationId'],'adoptProgram')
        self.assertEqual(paths['/projects/{projectId}/program']['get']['operationId'],'getProgram')
        for path in ('/processes.js','/processes.css'):
            import urllib.request
            with urllib.request.urlopen(self.origin+path)as response:self.assertEqual(response.status,200)
