"""Actual Engine/Store adoption, language history, IDE and platform write paths."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaos import Framework, Settings, Client
from chaos.examples import initial_graph
from chaos.ide_agent import IDEAgentService
from chaos.language import LanguageError
from chaos.runtime import Context, Cancelled
from chaos.store import Conflict
from tests.test_ide_agent import FakeClient


class ProgramHistoryIntegrationTests(unittest.TestCase):
    def app(self,**kwargs):
        app=Framework(settings=kwargs.pop('settings',Settings(':memory:')),**kwargs)
        self.addCleanup(app.close);return app

    def context(self,app,project):return Context(app._engine,None,app.get_project(project),threading.Event())

    def test_restore_retests_creates_new_version_and_preserves_current_authority(self):
        app=self.app();app.create_project('restore',name='Old',source='input*input',capabilities=['program.adopt','memory.put'])
        archived=app.program_revision('restore',1)
        project=app.edit_program('restore',1,source='input*2')
        app.save_project({**project,'name':'Current','capabilities':['program.adopt']},from_view='source')
        app._store.memory_put('restore','state',{'current':True})
        current=app.program_snapshot('restore');before=app.program_history('restore')
        with self.assertRaises(LanguageError):app.adopt_program('restore',3,archived['definition'],expected_hash=current['definitionHash'],cases=[{'input':3,'expect':6}])
        self.assertEqual(app.program_history('restore'),before)
        result=app.adopt_program('restore',3,archived['definition'],expected_hash=current['definitionHash'],cases=[{'input':3,'expect':9}],reason='Restore tested v1')
        self.assertEqual(result['receipt']['version'],4)
        self.assertEqual(app.get_project('restore')['capabilities'],['program.adopt']);self.assertEqual(app.get_project('restore')['name'],'Current')
        self.assertEqual(app._store.memory_get('restore','state'),{'current':True})
        self.assertEqual(app.program_revision('restore',1),archived)
        self.assertEqual(app.program_revision('restore',4)['definition'],archived['definition'])
        self.assertEqual(app.program_revision('restore',4)['metadata']['kind'],'adopted')
        self.assertEqual(app.start('restore',4,3,mode='source').result(),9)
        with self.assertRaises(Conflict):app.adopt_program('restore',3,archived['definition'],expected_hash=current['definitionHash'],cases=[{'input':3,'expect':9}])

    def test_language_current_baseline_restores_history_while_lexical_code_stays_fixed(self):
        app=self.app();app.create_project('language',source='input*input',capabilities=['program.adopt'])
        app.edit_program('language',1,source='''fn existing(x){x*2};let old=await program_revision(1);
            let receipt=await program_adopt(current_program(),old,[{input:3,expect:9}],"restore");
            [existing(3),receipt.version,(await program_history()).currentVersion]''')
        handle=app.start('language',2,None,mode='source');self.assertEqual(handle.result(),[6,3,3])
        self.assertEqual(handle.get()['snapshot']['version'],2)
        item=app.program_revision('language',3)['metadata'];self.assertEqual(item['runId'],handle.id)
        self.assertEqual(app.start('language',3,3,mode='source').result(),9)

    def test_historical_origin_cannot_replace_running_adoption_baseline(self):
        app=self.app();app.create_project('old_base',source='input*input',capabilities=['program.adopt'])
        app.edit_program('old_base',1,source='let old=await program_revision(1);await program_adopt(old,old,[{input:3,expect:9}])')
        result=app.start('old_base',2,None,mode='source').wait()
        self.assertEqual(result['status'],'failed');self.assertIn('base differs',result['error'])
        self.assertEqual(app.get_project('old_base')['version'],2)
        project=app.get_project('old_base');app.save_project({**project,'capabilities':[]},from_view='source')
        ctx=self.context(app,'old_base')
        with self.assertRaisesRegex(LanguageError,'does not grant'):
            ctx.program_operation('adopt',{'definition':app.program_revision('old_base',1)['definition'],'cases':[{'input':3,'expect':9}],'base':ctx.program_origin(),'reason':''})
        self.assertEqual(app.get_project('old_base')['version'],3)

    def test_history_read_budget_cancellation_strict_payload_and_recording_poison(self):
        app=self.app();app.create_project('reads',source='42',capabilities=[])
        ctx=self.context(app,'reads');ctx.services['programCalls']=64;ctx.program_depth=4
        self.assertEqual(ctx.program_operation('history',{'after':0,'limit':50})['currentVersion'],1)
        self.assertEqual(ctx.program_operation('revision',{'version':1})['origin']['projectId'],'reads')
        self.assertEqual(ctx.services['programCalls'],64)
        with self.assertRaises(LanguageError):ctx.program_operation('revision',{'version':1,'projectId':'other'})
        ctx.cancel.set()
        with self.assertRaises(Cancelled):ctx.program_operation('history',{'after':0,'limit':50})
        ctx=self.context(app,'reads');scope=ctx.mode_host().handle('recording.begin',{})['scopeId'];ctx.recording_scope=scope
        with self.assertRaises(LanguageError):ctx.program_operation('revision',{'version':1})
        with self.assertRaises(LanguageError):ctx.mode_host().handle('recording.finish',{'scopeId':scope,'parameters':[]})
        ctx.close_services('failed')

    def test_pure_probe_has_no_history_effect_handler(self):
        app=self.app();app.create_project('pure',source='0',capabilities=[])
        candidate={'kind':'program','entry':'main.chaos','files':{'main.chaos':'await program_revision(1)'},'languageVersions':{'main.chaos':2}}
        ctx=self.context(app,'pure');report=ctx.program_operation('probe',{'definition':candidate,'cases':[{'input':None,'expect':None}]})
        self.assertFalse(report['passed']);self.assertIn('Program',report['checks'][0]['error'])
        self.assertEqual(len(app.program_history('pure')['items']),1)

    def test_large_revision_uses_owned_program_without_expanding_plain_json_limit(self):
        app=self.app();files={name+'.chaos':'#'+('x'*75000) for name in ('one','two','three','four')}
        app.create_project('large',source='42',files=files,capabilities=[])
        app.edit_program('large',1,source='let old=await program_revision(1);[type_of(old),length(program_info(old).files["one.chaos"])]')
        self.assertEqual(app.start('large',2,None,mode='source').result(),['Program',75001])
        self.assertGreater(len(json.dumps(app.program_revision('large',1)['definition'])),250000)
        page=app.program_history('large');self.assertNotIn('files',page['items'][0])

    def test_platform_optimizer_captures_exact_canonical_result_once(self):
        app=self.app();graph=initial_graph();graph['nodes'][0]['config'].update(provider='model',policy='model-plan')
        app.create_project('platform',graph=graph)
        proposal={'reason':'move input','operations':[{'op':'update_node','id':'input','changes':{'x':77}}]}
        with patch.object(Context,'model_agent',return_value=proposal):result=app.optimize('platform',1)
        self.assertTrue(result['changed']);self.assertEqual(result['project']['version'],2)
        self.assertEqual(app.program_revision('platform',2)['definition'],app.program_snapshot('platform')['definition'])
        self.assertEqual([r['kind']for r in app.program_history('platform')['items']],['created','patched'])

    def test_ide_structured_proposal_apply_captures_same_store_history(self):
        owner=SimpleNamespace(requests=[]);script=[[('draft.write',{'path':'main.chaos','source':'42'})],[('draft.validate',{})],'Ready']
        settings=Settings(':memory:',client_factory=lambda config:FakeClient(owner,config,script))
        settings.update({'providers':{'text':{'baseURL':'https://model.invalid/v1','apiKey':'HISTORY_FIXTURE_SECRET'}},'runtime':{'timeout':5}},1)
        app=self.app(settings=settings);app.create_project('ide',source='0',capabilities=[])
        with tempfile.TemporaryDirectory()as folder:
            service=IDEAgentService(app,Path(folder)/'state')
            try:
                job=service.start('ide',1,'Return42');deadline=time.monotonic()+5
                while True:
                    result=service.get('ide',job['id'])
                    if result['status']not in ('queued','running'):break
                    self.assertLess(time.monotonic(),deadline);time.sleep(.01)
                self.assertEqual(result['status'],'completed',result)
                self.assertEqual(service.apply('ide',job['id'],1)['version'],2)
                self.assertEqual(service.apply('ide',job['id'],1)['version'],2)
                self.assertEqual([r['version']for r in app.program_history('ide')['items']],[1,2])
                self.assertEqual(app.program_revision('ide',1)['definition']['files']['main.chaos'],'0')
                self.assertEqual(app.program_revision('ide',2)['definition']['files']['main.chaos'],'42')
            finally:service.close()

    def test_sdk_client_routes_and_range_validation(self):
        client=Client('http://127.0.0.1:1')
        with patch.object(client,'_request',return_value={})as request:
            client.program_history('project',after=2,limit=3)
            request.assert_called_with('GET','/projects/project/program-history',query={'after':2,'limit':3})
            client.program_revision('project',2);request.assert_called_with('GET','/projects/project/program-revisions/2')
            for value in (True,0,-1,2**63):
                with self.subTest(value=value),self.assertRaises(ValueError):client.program_revision('project',value)
