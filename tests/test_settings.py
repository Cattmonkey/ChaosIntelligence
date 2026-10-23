import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest

from chaos.settings import Settings, model_identity, validate_url
from chaos.store import Conflict

class FakeClient:
    def __init__(self, ids, hook=None):
        self.models = self
        self.ids = ids
        self.hook = hook
        self.closed = False
    def list(self):
        if self.hook: self.hook()
        return [NS(id=x,created=1,owned_by='fixture') for x in self.ids]
    def close(self): self.closed=True

class SettingsContracts(unittest.TestCase):
    def test_secret_storage_public_preservation_and_explicit_clear(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'config.json'; s=Settings(p)
            secret='fictional-long-secret-a1b2'
            s.update({'providers':{'text':{'apiKey':secret}}},1)
            self.assertNotIn(secret,json.dumps(s.public()))
            self.assertEqual(os.stat(p).st_mode&0o777,0o600)
            public=s.public(); public['providers']['text']['apiKey']=''
            s.update(public)
            self.assertEqual(s.resolve()['apiKey'],secret)
            self.assertEqual(Settings(p).resolve()['apiKey'],secret)
            s.update({'providers':{'text':{'clearKey':True}}},s.public()['version'])
            self.assertFalse(s.public()['providers']['text']['hasKey'])
    def test_bad_urls_rejected_without_changing_state(self):
        s=Settings(':memory:')
        for value in ['https://user:secret@example.com/v1','https://example.com/v1?k=secret','http://example.com/v1','file:///tmp/x']:
            with self.assertRaises(ValueError):s.update({'providers':{'text':{'baseURL':value}}},1)
            self.assertEqual(s.public()['version'],1)
    def test_model_catalog_rank_and_fallback(self):
        client=FakeClient(['gpt-6.9-sol','gpt-6.10-sol','gpt-6-astra','gpt-5.9-astra','unrelated-gpt-90-sol'])
        s=Settings(':memory:',client_factory=lambda config:client)
        s.update({'providers':{'text':{'apiKey':'fixture-secret'}}},1)
        s.discover('text')
        self.assertTrue(client.closed)
        self.assertEqual(s.resolve(role='executor')['model'],'gpt-6.10-sol')
        self.assertEqual(s.resolve(role='planner')['model'],'gpt-6-astra')
        s.report_failure('text','gpt-6.10-sol')
        self.assertEqual(s.resolve(role='executor')['model'],'gpt-6.9-sol')
        s.report_success('text','gpt-6.10-sol')
        self.assertEqual(s.resolve(role='executor')['model'],'gpt-6.10-sol')
    def test_agent_override_is_specific(self):
        s=Settings(':memory:')
        s.update({'providers':{'text':{'apiKey':'shared-secret'}},'agents':{'solver':{'baseURL':'http://localhost:9999/v1','apiKey':'private-agent-key','model':'private-v3','backend':'codex'}}},1)
        route=s.resolve('solver')
        self.assertEqual(route['apiKey'],'private-agent-key')
        self.assertEqual(route['model'],'private-v3')
        self.assertEqual(route['candidates'],['private-v3'])
        self.assertEqual(route['baseURL'],'http://localhost:9999/v1')
        self.assertEqual(route['backend'],'codex')
        self.assertEqual(s.resolve('unconfigured')['apiKey'],'shared-secret')
    def test_stale_writers_conflict(self):
        s=Settings(':memory:'); s.update({'runtime':{'maxSteps':20}},1)
        with self.assertRaises(Conflict):s.update({'runtime':{'maxSteps':30}},1)
        self.assertEqual(s.runtime()['maxSteps'],20)
    def test_discovery_does_not_overwrite_concurrent_provider_change(self):
        s=None
        def changed():s.update({'providers':{'text':{'baseURL':'http://localhost:9876/v1'}}},s.public()['version'])
        client=FakeClient(['irrelevant-model'],changed)
        s=Settings(':memory:',client_factory=lambda config:client)
        s.update({'providers':{'text':{'apiKey':'fixture-secret'}}},1)
        with self.assertRaises(Conflict):s.discover('text')
        self.assertEqual(s.public()['providers']['text']['models'],[])
        self.assertTrue(client.closed)
    def test_discovery_transport_details_not_exposed(self):
        class Broken(FakeClient):
            def list(self):raise RuntimeError('sensitive credential raw-body')
        s=Settings(':memory:',client_factory=lambda config:Broken([]))
        s.update({'providers':{'text':{'apiKey':'fixture-secret'}}},1)
        with self.assertRaises(RuntimeError) as result:s.discover('text')
        self.assertNotIn('sensitive',str(result.exception))
    def test_redacts_nested_values(self):
        s=Settings(':memory:');s.update({'providers':{'text':{'apiKey':'fictional-key-value'}}},1)
        value=s.redact({'a':['prefix fictional-key-value suffix',{'authorization':'unknown-key'}],'API_KEY':'unknown'})
        self.assertEqual(value,{'a':['prefix [redacted] suffix',{'authorization':'[redacted]'}],'API_KEY':'[redacted]'})

if __name__=='__main__':unittest.main()
