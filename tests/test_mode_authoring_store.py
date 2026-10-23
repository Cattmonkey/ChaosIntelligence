"""Authoring and runtime loads share exact historical definitions and evidence."""
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
from chaos.modes import definition_hash
from chaos.settings import Settings
from chaos.store import Conflict, NotFound

PLAN={'kind':'plan','name':'work','steps':[{'id':'one','call':'work','needs':[]}]}
GOAL={'kind':'goal','objective':'reach42','planner':'planner','verify':'verify','max_iterations':2}
SKILL={'kind':'skill','actions':[{'tool':'text.normalize','input':' hi '}],'parameters':[]}


class ModeAuthoringStoreTests(unittest.TestCase):
    def setUp(self):
        self.app=Framework(settings=Settings(':memory:'))
        self.project=self.app.create_project('authoring',source='null',capabilities=[])
        self.host=ModeHost(self.app._store,self.project,'host_run')

    def tearDown(self):
        self.host.close();self.app.close()

    def events(self):return self.app.events('authoring')['items']

    def capture(self, complete=True):
        scope=self.host.handle('recording.begin',{})['scopeId']
        token=self.host.tool_begin(scope,'text.normalize',' hi ')
        self.host.tool_finish(token,'hi')
        if complete:return self.host.handle('recording.finish',{'scopeId':scope,'parameters':[]})['artifact']
        self.host.handle('recording.abort',{'scopeId':scope})
        return self.app.mode('authoring','recording_'+scope)

    def test_validate_is_canonical_pure_and_value_sources_import(self):
        before=self.events()
        for definition in (PLAN,GOAL,SKILL):
            with self.subTest(kind=definition['kind']):
                supplied=copy.deepcopy(definition)
                row=self.app.validate_mode(supplied,name='authored')
                self.assertEqual(row['definition'],definition)
                self.assertEqual(row['definitionHash'],definition_hash(definition))
                self.assertEqual(Framework.evaluate('import "value.chaos" as M;mode_info(M.authored)',files={'value.chaos':row['source']}),definition)
                supplied.clear();self.assertEqual(row['definition'],definition)
        self.assertEqual(self.events(),before)
        self.assertEqual(self.app.modes('authoring')['items'],[])

    def test_invalid_definition_name_and_hash_normalization(self):
        first=self.app.validate_mode(PLAN)
        reordered={'steps':[{'call':'work','id':'one'}],'name':'work','kind':'plan'}
        self.assertEqual(self.app.validate_mode(reordered)['definitionHash'],first['definitionHash'])
        for definition in ({**PLAN,'origin':{}},{**PLAN,'steps':[{'id':'a','call':'x','needs':['a']}]},{**SKILL,'parameters':[{'name':'x','step':0,'path':['absent']}]}):
            with self.subTest(definition=definition),self.assertRaises((ValueError,LanguageError)):self.app.validate_mode(definition)
        for name in ('x;tool("memory.put",42)','fn','bad.name',''):
            with self.subTest(name=name),self.assertRaises((ValueError,LanguageError)):self.app.validate_mode(PLAN,name=name)
        with self.assertRaises(ValueError):self.app.validate_mode({**SKILL,'actions':[{'tool':'text.normalize','input':'x'*100001}]})

    def test_source_resolves_one_revision_and_default_formats(self):
        first=self.app.save_mode('authoring','p',PLAN)
        self.app.save_mode('authoring','p',{**PLAN,'name':'new'},revision=first['revision'])
        source=self.app.mode_source('authoring','p',revision=1)
        self.assertEqual({k:source[k]for k in ('key','revision','format','definitionHash')},{'key':'p','revision':1,'format':'value','definitionHash':definition_hash(PLAN)})
        self.assertEqual(Framework.evaluate('import "p.chaos" as P;mode_info(P.saved_mode)',files={'p.chaos':source['source']}),PLAN)
        self.app.save_mode('authoring','g',GOAL);self.assertEqual(self.app.mode_source('authoring','g')['format'],'value')
        self.app.save_mode('authoring','s',SKILL)
        self.assertEqual(self.app.mode_source('authoring','s')['format'],'function')
        self.assertIn('fn recorded(',self.app.mode_source('authoring','s')['source'])
        self.assertIn('let explicit =',self.app.mode_source('authoring','s',format='value',name='explicit')['source'])
        for kwargs in ({'format':'unknown'},{'format':'function'},{'revision':True},{'name':'bad;name'}):
            with self.subTest(kwargs=kwargs),self.assertRaises((ValueError,LanguageError)):self.app.mode_source('authoring','p',**kwargs)
        with self.assertRaises(NotFound):self.app.mode_source('authoring','p',revision=999)

    def test_load_origin_matches_exact_historical_row_and_event(self):
        self.app.save_mode('authoring','p',PLAN)
        self.app.save_mode('authoring','p',{**PLAN,'name':'new'},revision=1)
        row=self.host.handle('artifact.load',{'key':'p','revision':1})
        origin={'projectId':'authoring','key':'p','revision':1,'definitionHash':definition_hash(PLAN)}
        self.assertEqual(row['origin'],origin);self.assertEqual(row['definition'],PLAN)
        event=self.events()[-1];self.assertEqual(event['type'],'mode.loaded');self.assertEqual(event['run_id'],'host_run')
        self.assertEqual(event['payload'],{**origin,'kind':'plan','sourceHash':self.host.source_hash})
        self.assertNotIn('definition',event['payload']);self.assertNotIn('lease',json.dumps(event))
        row['origin']['revision']=99
        self.assertEqual(self.host.handle('artifact.load',{'key':'p'})['origin']['revision'],2)
        self.assertEqual(self.host.handle('artifact.load',{'key':'p','revision':None})['origin']['revision'],2)

    def test_inspection_and_export_do_not_emit_runtime_load_evidence(self):
        self.app.save_mode('authoring','p',PLAN);before=self.events()
        self.app.mode('authoring','p');self.app.mode_source('authoring','p');self.app.mode_history('authoring','p')
        self.assertEqual(self.events(),before)
        self.assertNotIn('origin',self.app.mode('authoring','p'))

    def test_load_event_failure_rolls_back_and_does_not_return_value(self):
        self.app.save_mode('authoring','p',PLAN);before=self.events();original=self.app._store._event
        def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('evidence unavailable')
        with patch.object(self.app._store,'_event',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'evidence unavailable'):self.host.handle('artifact.load',{'key':'p'})
        self.assertEqual(self.events(),before)
        self.assertEqual(self.app.mode('authoring','p')['revision'],1)

    def test_latest_load_and_event_are_atomic_against_concurrent_save(self):
        self.app.save_mode('authoring','p',PLAN);original=self.app._store._event
        started=threading.Event();done=threading.Event();errors=[];threads=[]
        def update():
            started.set()
            try:self.app.save_mode('authoring','p',{**PLAN,'name':'new'},revision=1)
            except BaseException as error:errors.append(error)
            finally:done.set()
        def emit(project,kind,payload,run_id=None):
            if kind=='mode.loaded':
                t=threading.Thread(target=update);threads.append(t);t.start()
                self.assertTrue(started.wait(1));self.assertFalse(done.wait(.05))
            return original(project,kind,payload,run_id)
        with patch.object(self.app._store,'_event',side_effect=emit):row=self.host.handle('artifact.load',{'key':'p'})
        for thread in threads:thread.join(2);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(row['origin']['revision'],1)
        self.assertEqual(next(e for e in self.events()if e['type']=='mode.loaded')['payload']['revision'],1)
        self.assertEqual(self.app.mode('authoring','p')['revision'],2)

    def test_load_rejects_invalid_and_missing_revisions_without_events(self):
        self.app.save_mode('authoring','p',PLAN);before=self.events()
        for revision in (True,False,0,-1,1.0,'1'):
            with self.subTest(revision=revision),self.assertRaises(ValueError):self.host.handle('artifact.load',{'key':'p','revision':revision})
        for payload in ({'key':'missing'},{'key':'p','revision':999}):
            with self.subTest(payload=payload),self.assertRaises(NotFound):self.host.handle('artifact.load',payload)
        self.assertEqual(self.events(),before)

    def test_captured_skill_change_rejected_and_authored_clone_has_no_receipts(self):
        capture=self.capture();key=capture['key'];changed={**capture['definition'],'actions':[{'tool':'text.normalize','input':'changed'}]}
        with self.assertRaises(Conflict):self.app.save_mode('authoring',key,changed,revision=capture['revision'])
        same=self.app.save_mode('authoring',key,capture['definition'],revision=capture['revision'])
        self.assertEqual(same['checkpoint'],capture['checkpoint']);self.assertEqual(same['status'],'ready')
        self.assertEqual(self.app.mode('authoring',key,revision=capture['revision']),capture)
        clone=self.app.save_mode('authoring','authored',changed)
        self.assertIsNone(clone['checkpoint']);self.assertIsNone(clone['sourceHash']);self.assertEqual(clone['status'],'ready')
        self.assertNotIn('receipts',json.dumps(clone))
        with self.assertRaises((LanguageError,ValueError)):self.app.save_mode('authoring','fake',{**changed,'checkpoint':capture['checkpoint']})

    def test_incomplete_capture_never_loads_exports_or_promotes(self):
        capture=self.capture(complete=False);key=capture['key'];revisions=[r['revision']for r in self.app.mode_history('authoring',key)['items']]
        same=self.app.save_mode('authoring',key,capture['definition'],revision=capture['revision'])
        self.assertEqual(same['status'],'incomplete');self.assertEqual(same['checkpoint'],capture['checkpoint'])
        for revision in [None,*revisions,same['revision']]:
            with self.subTest(revision=revision),self.assertRaises(LanguageError):self.host.handle('artifact.load',{'key':key,'revision':revision})
            for format in ('value','function',None):
                with self.subTest(revision=revision,format=format),self.assertRaises(ValueError):self.app.mode_source('authoring',key,revision=revision,format=format)
        with self.assertRaises(Conflict):self.app.save_mode('authoring',key,{**SKILL,'parameters':[{'name':'x','step':0,'path':[]}]},revision=same['revision'])
        self.assertEqual([e for e in self.events()if e['type']=='mode.loaded'],[])

    def test_ready_capture_cannot_load_its_incomplete_historical_snapshot(self):
        capture=self.capture();self.assertEqual(self.host.handle('artifact.load',{'key':capture['key']})['status'],'ready')
        with self.assertRaises(LanguageError):self.host.handle('artifact.load',{'key':capture['key'],'revision':capture['revision']-1})

    def test_language_same_definition_cas_preserves_original_recording_run(self):
        source='''if input==null {await record_skill(fn(x){tool("text.normalize",x)}," original ")}
else {let value=await load_mode(input.key,revision=input.revision);await save_mode(value,input.key,revision=input.revision)}'''
        project=self.app.create_project('provenance',source=source,capabilities=['text.normalize'])
        first=self.app.start('provenance',1,None,mode='source');first.result()
        captured=self.app.modes('provenance')['items'][0]
        second=self.app.start('provenance',1,{'key':captured['key'],'revision':captured['revision']},mode='source')
        saved=second.result();self.assertEqual(saved['revision'],captured['revision']+1)
        for field in ('checkpoint','sourceHash','runId'):self.assertEqual(saved[field],captured[field])
        self.assertEqual(saved['runId'],first.id);self.assertNotEqual(saved['runId'],second.id)
        events=[e for e in self.app.events('provenance',run_id=second.id)['items']if e['type']=='mode.saved']
        self.assertEqual(len(events),1);self.assertEqual(events[0]['run_id'],second.id)

    def test_project_origins_and_histories_are_isolated(self):
        self.app.save_mode('authoring','p',PLAN);other=self.app.create_project('other',source='null')
        self.app.save_mode('other','p',{**PLAN,'name':'other'})
        host=ModeHost(self.app._store,other,'other_run')
        try:row=host.handle('artifact.load',{'key':'p','revision':1})
        finally:host.close()
        self.assertEqual(row['origin']['projectId'],'other');self.assertEqual(row['definition']['name'],'other')
        self.assertEqual([e for e in self.events()if e['type']=='mode.loaded'],[])

    def test_client_uses_shared_validate_and_source_routes(self):
        client=Client();calls=[];client._request=lambda *args,**kwargs:calls.append((args,kwargs))or{}
        client.validate_mode(PLAN,name='p');client.mode_source('a b','p',revision=2,format='value',name='selected')
        self.assertEqual(calls[0],(('POST','/modes/validate',{'definition':PLAN,'name':'p'}),{}))
        self.assertEqual(calls[1],(('GET','/projects/a%20b/modes/p/source'),{'query':{'revision':2,'format':'value','name':'selected'}}))

    def test_real_language_load_has_persisted_origin_and_no_definition_in_evidence(self):
        source='let p=await load_mode("p",revision=1);[mode_info(p),mode_metadata(p)]'
        project=self.app.create_project('language',source=source,capabilities=[])
        self.app.save_mode('language','p',PLAN);self.app.save_mode('language','p',{**PLAN,'name':'new'},revision=1)
        run=self.app.start('language',project['version'],None,mode='source');result=run.result()
        self.assertEqual(result[0],PLAN);self.assertEqual(result[1]['origin']['revision'],1)
        events=[e for e in self.app.events('language',run_id=run.id)['items']if e['type']=='mode.loaded']
        self.assertEqual(len(events),1);self.assertEqual(events[0]['payload']['definitionHash'],definition_hash(PLAN))
        self.assertNotIn('definition',events[0]['payload'])

    def test_runtime_load_evidence_failure_prevents_subsequent_effect(self):
        project=self.app.create_project('event_fail',source='let p=await load_mode("p");tool("text.normalize","must not run")',capabilities=['text.normalize'])
        self.app.save_mode('event_fail','p',PLAN);original=self.app._store._event
        def event(project,kind,payload,run_id=None):
            if kind=='mode.loaded':raise RuntimeError('audit unavailable')
            return original(project,kind,payload,run_id)
        with patch.object(self.app._store,'_event',side_effect=event):run=self.app.start('event_fail',1,None,mode='source');result=run.wait()
        self.assertEqual(result['status'],'failed');self.assertIn('audit unavailable',result['error'])
        self.assertFalse(any(e['type']=='tool.started'for e in self.app.events('event_fail',run_id=run.id)['items']))

    def test_historical_definition_and_evidence_survive_reopen(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'modes.sqlite3'
            with Framework(path,settings=Settings(':memory:'))as app:
                project=app.create_project('p',source='null');app.save_mode('p','plan',PLAN);app.save_mode('p','plan',{**PLAN,'name':'new'},revision=1)
                host=ModeHost(app._store,project,'load_run')
                try:loaded=host.handle('artifact.load',{'key':'plan','revision':1})
                finally:host.close()
            with Framework(path,settings=Settings(':memory:'))as app:
                self.assertEqual(app.mode('p','plan',revision=1)['definition'],PLAN)
                event=next(e for e in app.events('p')['items']if e['type']=='mode.loaded')
                self.assertEqual(event['payload']['definitionHash'],loaded['origin']['definitionHash'])


if __name__=='__main__':unittest.main()
