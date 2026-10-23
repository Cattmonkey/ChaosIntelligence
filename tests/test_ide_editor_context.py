"""Real IDE service/HTTP flow with only fixture model responses and temporary data."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.ide_agent import IDEAgentService
from chaos.language import LanguageError
from chaos.ide_context import editor_snapshot, semantic_context
from chaos.openapi import specification
from chaos.server import make_server
from chaos.store import Conflict
from test_ide_agent import FakeClient


def snapshot(files=None, *, active='main.chaos', anchor=0, head=0):
    files = files or {'main.chaos': '40'}
    return {'files': dict(files), 'entry': 'main.chaos', 'languageVersions': {p: 2 for p in files},
            'activeFile': active, 'selection': {'anchor': anchor, 'head': head}}


class EditorContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.scripts, self.clients, self.requests = [], [], []
        def factory(config):
            client = FakeClient(self, config, self.scripts.pop(0)); self.clients.append(client); return client
        self.settings = Settings(':memory:', client_factory=factory)
        self.settings.update({'providers': {'text': {'baseURL': 'https://fixture.invalid/v1', 'apiKey': 'editor-context-fixture-secret'}},
                              'runtime': {'timeout': 5}}, 1)
        self.app = Framework(settings=self.settings)
        self.app.create_project('project', source='input')
        self.service = IDEAgentService(self.app, self.root/'ide')

    def tearDown(self):
        self.service.close(); self.app.close(); self.temp.cleanup()

    def start(self, script, context=None, session=None):
        self.scripts.append(script)
        return self.service.start('project', 1, 'Review current editor draft', session, editor_context=context)

    def wait(self, job):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            result = self.service.get('project', job['id'])
            if result['status'] not in ('queued','running'): return result
            time.sleep(.005)
        self.fail('Fixture assistant did not finish')

    def test_unsaved_multifile_context_real_analysis_tools_and_atomic_apply(self):
        library = 'let emoji="😀";fn answer()->Int{40};export{answer}'
        files = {'main.chaos': 'import "lib.chaos" as M;M.answer()', 'lib.chaos': library}
        pos = len(library[:library.index('answer')].encode('utf-16-le'))//2
        context = snapshot(files, active='lib.chaos', anchor=pos+6, head=pos)
        def inspect_context(request):
            supplied = json.loads(request['messages'][1]['content'])['editorContext']
            self.assertEqual(supplied['selectedText'], 'answer')
            self.assertEqual(supplied['selection'], context['selection'])
            self.assertTrue(supplied['analysis']['complete'])
            self.assertTrue(any(s['name']=='answer' and s['kind']=='function' for s in supplied['analysis']['symbols']))
            self.assertEqual(supplied['analysis']['diagnostics'], [])
            return [('draft.read', {'path':'lib.chaos'})]
        def edit(request):
            result = json.loads([m for m in request['messages'] if m['role']=='tool'][-1]['content'])
            self.assertEqual(result['source'], library)
            return [('draft.write', {'path':'lib.chaos','source':library.replace('{40}', '{42}')})]
        result = self.wait(self.start([inspect_context, edit, [('draft.evaluate',{})], 'Ready to review'], context))
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['checks'][0]['output'], 42)
        self.assertEqual(result['changes'], [{'path':'lib.chaos','before':library,'after':library.replace('{40}','{42}') }])
        self.assertEqual(self.app.get_project('project')['source'], 'input')
        saved = self.service.apply('project', result['id'], 1, editor_context=context)
        self.assertEqual(saved['version'], 2); self.assertEqual(self.app.start('project',2,None,mode='source').result(),42)
        self.assertEqual(saved['capabilities'], self.app.get_project('project')['capabilities'])

    def test_snapshot_detaches_caller_and_response_before_worker(self):
        context = snapshot(); frozen = copy.deepcopy(context)
        self.service.manager.pause_dispatch(True)
        job = self.start([[('draft.write',{'path':'main.chaos','source':'42'})], 'done'], context)
        context['files']['main.chaos']='99'; context['selection']['head']=1
        job['editorContext']['fileHashes']['main.chaos']='forged'
        self.service.manager.pause_dispatch(False)
        result = self.wait(job)
        self.assertEqual(result['changes'][0]['before'],'40')
        self.assertNotEqual(result['editorContext']['fileHashes']['main.chaos'],'forged')
        self.assertEqual(self.service.apply('project',job['id'],1,editor_context=frozen)['source'],'42')

    def test_exact_schema_utf16_paths_versions_and_keys_rejected_before_queue(self):
        base = snapshot({'main.chaos':'"😀"'})
        invalid = []
        for position in (True, -1, 2, 5, 1.5):
            item=copy.deepcopy(base);item['selection']['head']=position;invalid.append(item)
        item=copy.deepcopy(base);item['capabilities']=['device.control'];invalid.append(item)
        item=copy.deepcopy(base);item['languageVersions']={};invalid.append(item)
        item=copy.deepcopy(base);item['languageVersions']['main.chaos']=True;invalid.append(item)
        item=copy.deepcopy(base);item['entry']='missing.chaos';invalid.append(item)
        item=snapshot({'../outside.chaos':'42'});invalid.append(item)
        item=copy.deepcopy(base);item['files']['main.chaos']='"editor-context-fixture-secret"';invalid.append(item)
        for context in invalid:
            with self.subTest(context_keys=list(context)):
                with self.assertRaises((ValueError, LanguageError)):
                    self.service.start('project',1,'invalid',editor_context=context)
        self.assertEqual(self.service.manager.list(),[]);self.assertEqual(self.requests,[])
        self.assertEqual(self.service._db.execute('SELECT count(*) FROM ide_sessions').fetchone()[0],0)

    def test_hash_covers_bundle_entry_and_dialects_not_cursor_or_active_file(self):
        context=snapshot({'main.chaos':'40','lib.chaos':'42'})
        _, expected=editor_snapshot(context,'project',1)
        moved=copy.deepcopy(context);moved['activeFile']='lib.chaos';moved['selection']={'anchor':2,'head':0}
        self.assertEqual(editor_snapshot(moved,'project',1)[1]['draftHash'],expected['draftHash'])
        for field, value in [('files',{'main.chaos':'41','lib.chaos':'42'}),('entry','lib.chaos'),('languageVersions',{'main.chaos':1,'lib.chaos':2})]:
            altered=copy.deepcopy(context);altered[field]=value
            self.assertNotEqual(editor_snapshot(altered,'project',1)[1]['draftHash'],expected['draftHash'])
        self.assertNotEqual(editor_snapshot(context,'other',1)[1]['draftHash'],expected['draftHash'])
        self.assertNotEqual(editor_snapshot(context,'project',2)[1]['draftHash'],expected['draftHash'])

    def test_incomplete_source_analysis_remains_inspectable_and_repairable(self):
        context=snapshot({'main.chaos':'async fn use(d:Device,a:DeviceAction){await d('},head=46)
        context['selection']['head']=len(context['files']['main.chaos'])
        def inspect(request):
            analysis=json.loads(request['messages'][1]['content'])['editorContext']['analysis']
            self.assertFalse(analysis['complete']);self.assertTrue(analysis['diagnostics'])
            self.assertEqual(analysis['signatureHelp']['parameters'][0]['type'],'DeviceAction')
            return [('draft.write',{'path':'main.chaos','source':'42'})]
        job=self.wait(self.start([inspect,'repaired'],context))
        self.assertEqual(job['status'],'completed',job);self.assertTrue(job['validation']['ok'])
        self.assertEqual(self.app.get_project('project')['version'],1)

    def test_contextual_apply_requires_unchanged_draft_without_consuming_proposal(self):
        context=snapshot();job=self.wait(self.start([[('draft.write',{'path':'main.chaos','source':'42'})],'done'],context))
        with self.assertRaises(Conflict):self.service.apply('project',job['id'],1)
        with self.assertRaises(Conflict):self.service.apply('project',job['id'],1,editor_context=snapshot({'main.chaos':'41'}))
        self.assertIsNone(self.service.get('project',job['id'])['appliedVersion'])
        self.assertEqual(self.app.get_project('project')['source'],'input')
        moved=copy.deepcopy(context);moved['selection']={'anchor':2,'head':0}
        self.assertEqual(self.service.apply('project',job['id'],1,editor_context=moved)['source'],'42')
        self.assertEqual(self.service.apply('project',job['id'],1,editor_context=context)['version'],2)

    def test_project_write_between_check_and_save_loses_cas_and_preserves_other_writer(self):
        context=snapshot();job=self.wait(self.start([[('draft.write',{'path':'main.chaos','source':'42'})],'done'],context))
        save=self.app.save_project
        def competing(project, **options):
            self.app.edit_program('project',1,source='99')
            return save(project,**options)
        with patch.object(self.app,'save_project',side_effect=competing):
            with self.assertRaises(Conflict):self.service.apply('project',job['id'],1,editor_context=context)
        self.assertEqual(self.app.get_project('project')['source'],'99')
        self.assertIsNone(self.service.get('project',job['id'])['appliedVersion'])

    def test_same_context_followup_keeps_proposal_changed_context_restarts(self):
        context=snapshot();first=self.wait(self.start([[('draft.write',{'path':'main.chaos','source':'41'})],'first'],context))
        def expect(value):
            def inspect(request):
                tool=json.loads([m for m in request['messages'] if m['role']=='tool'][-1]['content'])
                self.assertEqual(tool['source'],value);return 'checked'
            return inspect
        second=self.wait(self.start([[('draft.read',{'path':'main.chaos'})],expect('41')],context,first['sessionId']))
        self.assertEqual(second['changes'][0]['before'],'40')
        third=self.wait(self.start([[('draft.read',{'path':'main.chaos'})],expect('90')],snapshot({'main.chaos':'90'}),first['sessionId']))
        self.assertEqual(third['changes'],[])

    def test_cross_mode_followup_cannot_downgrade_context_guard(self):
        contextual=self.wait(self.start([[('draft.write',{'path':'main.chaos','source':'42'})],'first'],snapshot()))
        legacy=self.wait(self.start(['No edit'],None,contextual['sessionId']))
        self.assertNotIn('editorContext',legacy);self.assertEqual(legacy['changes'],[])
        with self.assertRaises(ValueError):self.service.apply('project',legacy['id'],1)
        old=self.wait(self.start([[('draft.write',{'path':'main.chaos','source':'99'})],'legacy'],None,contextual['sessionId']))
        fresh=self.wait(self.start(['No edit'],snapshot({'main.chaos':'43'}),old['sessionId']))
        self.assertEqual(fresh['changes'],[])

    def test_large_bundle_exceeds_generic_task_budget_but_uses_existing_source_columns(self):
        files={'main.chaos':'42',**{'part'+str(i)+'.chaos':' '*90000+'42' for i in range(4)}}
        context=snapshot(files);self.service.manager.pause_dispatch(True)
        job=self.start(['Reviewed unchanged'],context)
        task=self.service.manager.get(job['id'])['task']
        self.assertLess(len(json.dumps(task)),10000)
        row=self.service._turn('project',job['id'])
        self.assertGreater(len(row['base_files']),250000);self.assertEqual(json.loads(row['base_files']),files)
        self.service.manager.pause_dispatch(False)
        self.assertEqual(self.wait(job)['status'],'completed')

    def test_analysis_summary_is_bounded_and_reports_truncation(self):
        source=';'.join('let value'+str(i)+'='+str(i) for i in range(100))
        files,meta=editor_snapshot(snapshot({'main.chaos':source}),'project',1)
        summary=semantic_context(files,meta)
        self.assertEqual(len(summary['analysis']['symbols']),40)
        self.assertTrue(summary['analysis']['truncated'])
        self.assertLess(len(json.dumps(summary)),100000)

    def test_long_history_and_paths_fit_complete_harness_context_budget(self):
        from chaos.extensions import json_copy
        files={'main.chaos':'42', **{('module'+str(i)+'x'*210+'.chaos'):'42' for i in range(63)}}
        context=snapshot(files); session=None
        for index in range(5):
            self.scripts.append(['reply'*20000])
            job=self.wait(self.service.start('project',1,'p'*20000,session,editor_context=context))
            self.assertEqual(job['status'],'completed',job);session=job['sessionId']
        def inspect(request):
            complete=json.loads(request['messages'][1]['content'])
            json_copy(complete)
            self.assertTrue(complete['historyTruncated'])
            self.assertLessEqual(sum(len(item['user'])+len(item['assistant']) for item in complete['history']),16000)
            self.assertEqual(len(complete['editorContext']['files']),64)
            return 'bounded'
        job=self.wait(self.start([inspect],context,session))
        self.assertEqual(job['status'],'completed',job)

    def test_context_and_required_apply_proof_survive_service_restart(self):
        context=snapshot();job=self.wait(self.start([[('draft.write',{'path':'main.chaos','source':'42'})],'done'],context))
        self.assertTrue(self.service.close());self.service=IDEAgentService(self.app,self.root/'ide')
        self.assertEqual(self.service.get('project',job['id'])['editorContext'],job['editorContext'])
        with self.assertRaises(Conflict):self.service.apply('project',job['id'],1)
        self.assertEqual(self.service.apply('project',job['id'],1,editor_context=context)['source'],'42')

    def test_client_legacy_wire_is_identical_and_context_additive(self):
        client=Client('http://127.0.0.1:1');context=snapshot()
        with patch.object(client,'_request',return_value={}) as request:
            client.start_assistant('project',1,'hello')
            self.assertEqual(request.call_args.args,('POST','/projects/project/assistant',{'version':1,'prompt':'hello','sessionId':None}))
            client.apply_assistant('project','job',1)
            self.assertEqual(request.call_args.args,('POST','/projects/project/assistant/job/apply',{'version':1}))
            client.start_assistant('project',1,'hello',editor_context=context)
            self.assertEqual(request.call_args.args[2]['editorContext'],context)
            client.apply_assistant('project','job',1,editor_context=context)
            self.assertEqual(request.call_args.args[2],{'version':1,'editorContext':context})

    def test_http_routes_client_and_openapi_use_same_snapshot_contract(self):
        server=make_server(port=0,framework=self.app,state_dir=self.root/'http',update_home=self.root/'updates')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=Client('http://127.0.0.1:'+str(server.server_port));context=snapshot()
            self.scripts.append([[('draft.write',{'path':'main.chaos','source':'42'})],'ready'])
            job=client.start_assistant('project',1,'Use unsaved draft',editor_context=context)
            deadline=time.monotonic()+5
            while job['status'] in ('queued','running') and time.monotonic()<deadline:
                time.sleep(.005);job=client.assistant('project',job['id'])
            self.assertEqual(job['status'],'completed',job);self.assertEqual(job['changes'][0]['before'],'40')
            with self.assertRaises(Exception) as caught:client.apply_assistant('project',job['id'],1)
            self.assertEqual(caught.exception.status,409)
            saved=client.apply_assistant('project',job['id'],1,editor_context=context)
            self.assertEqual(saved['source'],'42');self.assertEqual(saved['version'],2)
            schema=specification()['components']['schemas']
            self.assertEqual(set(schema['EditorContext']['required']),set(context))
            self.assertIn('editorContext',schema['AssistantApply']['properties'])
        finally:
            server.shutdown();server.server_close();thread.join(2)
