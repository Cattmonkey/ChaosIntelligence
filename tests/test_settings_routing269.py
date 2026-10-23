"""Call-time compatibility retains one configured host across AI entry points."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from chaos import Framework, Settings
from chaos.ide_agent import IDEAgentService

NAMES=('CHAOS_MODEL_URL','CHAOS_MODEL_KEY','CHAOS_MODEL_NAME')
ENV=dict(zip(NAMES,('https://temporary.invalid/v1/chat/completions','fixture-temporary-secret','temporary-model')))

class Gateway:
    def __init__(self,config):
        self.config=config;self.chat=NS(completions=self);self.models=self;self.closed=False
    def create(self,**kwargs):
        return NS(choices=[NS(message=NS(content=kwargs['model'],tool_calls=[]))])
    def list(self):return [NS(id='persistent-catalog-model',created=1,owned_by='fixture')]
    def close(self):self.closed=True

class SharedSettingsRouting(unittest.TestCase):
    def setUp(self):
        clean=patch.dict(os.environ,dict.fromkeys(NAMES,''));clean.start();self.addCleanup(clean.stop)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.path=self.root/'settings.json';self.configs=[];self.clients=[]
        def factory(config):
            self.configs.append(copy.deepcopy(config));client=Gateway(config);self.clients.append(client);return client
        self.factory=factory;self.settings=Settings(self.path,client_factory=factory)
        self.settings.update({
            'providers':{'text':{'baseURL':'https://persistent.invalid/v1','apiKey':'fixture-persistent-secret','models':[{'id':'gpt-999-sol'}]}},
            'roles':{role:{'model':'persistent-'+role,'autoUpgrade':False} for role in ('planner','executor','reviewer')},
            'runtime':{'maxSteps':6,'maxConcurrent':2,'timeout':30,'codeEnabled':False,'workspace':str(self.root/'tasks')},
        },1)

    def test_late_overlay_shared_by_agents_bound_agents_sessions_and_ide(self):
        initial=self.path.read_bytes()
        programs={
            'ordinary':'import "std/intelligence" as AI;AI.assistant(input)',
            'bound':'import "std/intelligence" as AI;let a=agent_bind(AI.assistant,[agent_tool("twice",fn(x:Int){x*2},input_type="Int",output_type="Int")]);await a(input)',
            'session':'import "std/intelligence" as AI;let s=await session_open("help",AI.assistant);let r=await s(input);r.output',
        }
        with Framework(settings=self.settings) as app:
            for name,source in programs.items():app.create_project(name,source=source,capabilities=['session.manage'] if name=='session' else [])
            app.create_project('ide',source='42');service=IDEAgentService(app,self.root/'ide')
            try:
                # The Framework and IDE already exist when the caller enables env compatibility.
                with patch.dict(os.environ,ENV):
                    for name in programs:self.assertEqual(app.start(name,1,21,mode='source').result(10),'temporary-model')
                    job=service.start('ide',1,'Explain the installed language');deadline=time.monotonic()+5
                    while job['status'] in ('queued','running') and time.monotonic()<deadline:
                        time.sleep(.01);job=service.get('ide',job['id'])
                    self.assertEqual(job['status'],'completed',job.get('error'));self.assertEqual(job['reply'],'temporary-model')
                    self.assertEqual(len(self.configs),4)
                    for config in self.configs:
                        self.assertEqual(config['baseURL'],'https://temporary.invalid/v1')
                        self.assertEqual(config['apiKey'],ENV['CHAOS_MODEL_KEY'])
                        self.assertEqual(config['model'],'temporary-model');self.assertEqual(config['configVersion'],2)
                        self.assertEqual(config['maxConcurrent'],2);self.assertFalse(config['codeEnabled'])
                    self.assertIs(self.settings.client_factory,self.factory);self.assertEqual(self.path.read_bytes(),initial)
                self.assertEqual(app.start('ordinary',1,21,mode='source').result(10),'persistent-executor')
            finally:service.close()
        self.assertTrue(all(client.closed for client in self.clients))

    def test_hot_set_and_unset_do_not_mutate_or_persist_environment(self):
        original=self.path.read_bytes();public=self.settings.public();data=copy.deepcopy(self.settings._data)
        expected_constructed=Settings(self.path)._data
        with patch.dict(os.environ,ENV):
            self.assertEqual(Settings(self.path)._data,expected_constructed)
            for role in ('planner','executor','reviewer'):
                route=self.settings.resolve(role=role);self.assertEqual(route['model'],'temporary-model')
                self.assertEqual(route['candidates'],['temporary-model'])
            os.environ['CHAOS_MODEL_NAME']='next-model'
            self.assertEqual(self.settings.resolve()['model'],'next-model');self.assertEqual(self.settings.public(),public)
            self.assertEqual(self.settings._data,data);self.assertEqual(self.path.read_bytes(),original)
            self.settings.update({'runtime':{'maxSteps':7}},2)
            self.assertNotIn(ENV['CHAOS_MODEL_KEY'],self.path.read_text());self.assertNotIn('temporary.invalid',self.path.read_text())
        route=self.settings.resolve();self.assertEqual(route['model'],'persistent-executor')
        self.assertEqual(route['maxSteps'],7);self.assertEqual(route['configVersion'],3)

    def test_no_url_does_not_activate_and_no_key_does_not_reuse_persisted_secret(self):
        baseline=self.settings.resolve()
        with patch.dict(os.environ,{'CHAOS_MODEL_KEY':ENV['CHAOS_MODEL_KEY'],'CHAOS_MODEL_NAME':'ignored'}):self.assertEqual(self.settings.resolve(),baseline)
        with patch.dict(os.environ,{'CHAOS_MODEL_URL':ENV['CHAOS_MODEL_URL'],'CHAOS_MODEL_NAME':'temporary-model'}):
            route=self.settings.resolve();self.assertEqual(route['apiKey'],'')
            with self.assertRaisesRegex(ValueError,'key is not configured'):self.settings.client(resolved=route)
        self.assertEqual(self.configs,[])

    def test_agent_override_health_and_host_state_survive(self):
        self.settings.update({'agents':{'private':{'baseURL':'https://agent.invalid/v1','apiKey':'fixture-agent-secret','model':'agent-model','backend':'codex'}}},2)
        self.settings.report_failure('text','gpt-999-sol');health=dict(self.settings.health)
        with patch.dict(os.environ,ENV):
            route=self.settings.resolve('private')
            self.assertEqual([route[k] for k in ('baseURL','apiKey','model','backend')],['https://agent.invalid/v1','fixture-agent-secret','agent-model','codex'])
            self.assertEqual(route['candidates'],['agent-model']);self.assertEqual(self.settings.health,health)
            self.assertEqual(self.settings.runtime()['maxSteps'],6);self.assertIs(self.settings.client_factory,self.factory)
        self.settings.update({'roles':{'executor':{'autoUpgrade':True}}},3)
        self.assertEqual(self.settings.resolve()['model'],'persistent-executor')
        self.settings.report_success('text','gpt-999-sol');self.assertEqual(self.settings.resolve()['model'],'gpt-999-sol')

    def test_other_provider_roles_and_specific_agents_keep_their_model_and_connection(self):
        self.settings.update({'roles':{'executor':{'provider':'grok','model':'grok-fixture','autoUpgrade':False}},
            'agents':{'private':{'provider':'grok','model':'grok-agent','autoUpgrade':False}},
            'providers':{'grok':{'apiKey':'fixture-grok-secret','baseURL':'https://grok.invalid/v1'}}},2)
        with patch.dict(os.environ,ENV):
            for agent,role,model in [('', 'executor','grok-fixture'),('private','planner','grok-agent')]:
                route=self.settings.resolve(agent,role)
                self.assertEqual(route['model'],model);self.assertEqual(route['provider'],'grok')
                self.assertEqual(route['baseURL'],'https://grok.invalid/v1');self.assertEqual(route['apiKey'],'fixture-grok-secret')
            self.assertEqual(self.settings.resolve(role='planner')['model'],'temporary-model')

    def test_disabled_native_plugin_cannot_be_bypassed(self):
        self.settings.update({'plugins':[{'id':'native','enabled':False}]},2)
        with Framework(settings=self.settings) as app,patch.dict(os.environ,ENV):
            for name,source in {
                'ordinary':'import "std/intelligence" as AI;AI.assistant(input)',
                'bound':'import "std/intelligence" as AI;let b=agent_bind(AI.assistant,[]);await b(input)',
            }.items():
                app.create_project(name,source=source,capabilities=[]);run=app.start(name,1,'help',mode='source').wait(10)
                self.assertEqual(run['status'],'failed');self.assertIn('Plugin service unavailable: native',run['error'])
            self.assertEqual(self.configs,[])

    def test_invalid_environment_rejected_without_client_or_secret_in_error(self):
        for values in ({'CHAOS_MODEL_URL':'https://user:fixture-temporary-secret@example.invalid/v1'},
                       {'CHAOS_MODEL_URL':'http://external.invalid/v1'},
                       {'CHAOS_MODEL_KEY':'x'*501},{'CHAOS_MODEL_NAME':'x'*201}):
            with self.subTest(fields=sorted(values)),patch.dict(os.environ,{**ENV,**values}):
                with self.assertRaises(ValueError) as error:self.settings.resolve()
                self.assertNotIn(ENV['CHAOS_MODEL_KEY'],str(error.exception))
        self.assertEqual(self.configs,[])

    def test_discovery_uses_persisted_connection_and_not_temporary_catalog(self):
        with patch.dict(os.environ,ENV):
            self.settings.discover('text')
            self.assertEqual(self.configs[-1]['baseURL'],'https://persistent.invalid/v1')
            self.assertEqual(self.configs[-1]['apiKey'],'fixture-persistent-secret')
            self.assertEqual(self.settings.public()['providers']['text']['models'][0]['id'],'persistent-catalog-model')
            self.assertEqual(self.settings.resolve()['candidates'],['temporary-model'])
        self.assertTrue(self.clients[-1].closed)

    def test_temporary_keys_are_redacted_before_resolve_and_after_unset_without_persistence(self):
        with patch.dict(os.environ,ENV):
            self.assertEqual(self.settings.redact({'message':ENV['CHAOS_MODEL_KEY']}),{'message':'[redacted]'})
            self.settings.resolve()
        self.assertEqual(self.settings.redact({'message':ENV['CHAOS_MODEL_KEY']}),{'message':'[redacted]'})
        self.assertNotIn(ENV['CHAOS_MODEL_KEY'],json.dumps(self.settings._data))
        self.assertNotIn(ENV['CHAOS_MODEL_KEY'],self.path.read_text())

if __name__=='__main__':unittest.main()
