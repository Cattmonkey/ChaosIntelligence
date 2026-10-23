import json
import os
from pathlib import Path
import tempfile
import warnings
import getpass
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chaos.deployment_settings import configure, ConfigurationError
from chaos.project import init_project
from chaos.settings import Settings


class DeploymentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        init_project(self.root)
        self.path = self.root / '.chaos/settings.json'
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def existing(self):
        settings = Settings(self.path)
        settings.update({'providers': {'text': {'apiKey': 'private-fixture-key'},
                        'grok': {'baseURL': 'https://grok.example/v1'}},
                         'agents': {'custom-bot': {'provider': 'grok', 'role': 'reviewer'}},
                         'roles': {'reviewer': {'model': 'custom-reviewer', 'autoUpgrade': False}},
                         'runtime': {'codeEnabled': False},
                         'plugins': [{'id': 'code', 'enabled': False}]}, settings.public()['version'])
        return settings

    def test_first_setup_offline_persists_tiers_without_credentials_or_prompts(self):
        with patch('builtins.input', side_effect=AssertionError('Unexpected input')), \
             patch('chaos.settings.Settings.client', side_effect=AssertionError('Unexpected network')):
            result = configure(self.root)
        settings = Settings(self.path)
        self.assertEqual(set(result['providers']), {'text', 'image1k', 'image4k', 'grok'})
        self.assertEqual(set(result['roles']), {'planner', 'executor', 'reviewer', 'image', 'grok'})
        self.assertTrue(result['created'])
        self.assertEqual(settings.runtime()['workspace'], '.chaos/tasks')
        self.assertEqual(result['advice']['status'], 'disabled')
        self.assertFalse(any(p['configured'] for p in result['providers'].values()))
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_repeated_setup_preserves_settings_byte_exact_and_ignores_unrequested_env(self):
        self.existing()
        before = self.path.read_bytes()
        with patch.dict(os.environ, {'CHAOS_TEXT_KEY': 'ignored-key'}):
            result = configure(self.root)
        self.assertFalse(result['changed'])
        self.assertEqual(before, self.path.read_bytes())

    def test_explicit_environment_patch_preserves_custom_routes_caps_and_plugins(self):
        settings = self.existing()
        before = settings.public()
        events = []
        with patch.dict(os.environ, {'CHAOS_BASE_URL': 'https://shared.example/v1',
                                    'CHAOS_GROK_BASE_URL': 'https://special.example/v1',
                                    'CHAOS_IMAGE_1K_KEY': 'new-image-secret',
                                    'CHAOS_EXECUTOR_MODEL': 'new-executor'}):
            result = configure(self.root, models=True, reporter=lambda *v: events.append(v))
        after = settings.public()
        self.assertEqual(settings.resolve()['apiKey'], 'private-fixture-key')
        self.assertEqual(after['providers']['grok']['baseURL'], 'https://special.example/v1')
        for name in ('agents', 'plugins', 'runtime'):
            self.assertEqual(before[name], after[name])
        self.assertEqual(before['roles']['reviewer'], after['roles']['reviewer'])
        self.assertFalse(after['roles']['executor']['autoUpgrade'])
        self.assertTrue(after['roles']['planner']['autoUpgrade'])
        rendered = json.dumps([result, events])
        self.assertNotIn('private-fixture-key', rendered)
        self.assertNotIn('new-image-secret', rendered)
        self.assertNotIn('keyHint', rendered)

    def test_blank_interactive_input_keeps_keys_urls_and_auto_upgrade(self):
        self.existing()
        before = self.path.read_bytes()
        with patch('builtins.input', return_value=''), \
             patch('chaos.deployment_settings.getpass.getpass', return_value=''):
            result = configure(self.root, models=True, interactive=True)
        self.assertFalse(result['changed'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_and_corrupt_configs_preserved_without_secret_exception(self):
        self.existing()
        before = self.path.read_bytes()
        with patch.dict(os.environ, {'CHAOS_BASE_URL': 'https://private-fixture-key@example.test/v1'}):
            with self.assertRaises(ConfigurationError) as error:
                configure(self.root, models=True)
        self.assertNotIn('private-fixture-key', str(error.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text('{broken-private-fixture-key')
        with self.assertRaises(ConfigurationError):
            configure(self.root)
        self.assertEqual(self.path.read_text(), '{broken-private-fixture-key')

    def test_input_cancellation_does_not_partially_save(self):
        self.existing()
        before = self.path.read_bytes()
        with patch('builtins.input', side_effect=['https://new.example/v1', EOFError()]):
            with self.assertRaises(ConfigurationError):
                configure(self.root, models=True, interactive=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_insecure_terminal_cannot_fall_back_to_visible_key_input(self):
        self.existing()
        before = self.path.read_bytes()
        def unsafe_getpass(*args):
            warnings.warn('Cannot hide input', getpass.GetPassWarning)
            raise AssertionError('Must not continue to visible input')
        with patch('builtins.input', return_value=''), \
             patch('chaos.deployment_settings.getpass.getpass', side_effect=unsafe_getpass):
            with self.assertRaises(ConfigurationError):
                configure(self.root, models=True, interactive=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_config_change_rejects_stale_patch(self):
        self.existing()
        original_update = Settings.update
        concurrent = Settings(self.path)

        def racing_update(settings, changes, version=None):
            original_update(concurrent, {'roles': {'executor': {'model': 'concurrent-model'}}}, version)
            return original_update(settings, changes, version)

        with patch.dict(os.environ, {'CHAOS_TEXT_KEY': 'stale-key'}), \
             patch.object(Settings, 'update', racing_update):
            with self.assertRaises(ConfigurationError):
                configure(self.root, models=True)
        self.assertEqual(Settings(self.path).resolve()['model'], 'concurrent-model')
        self.assertEqual(Settings(self.path).resolve()['apiKey'], 'private-fixture-key')

    @unittest.skipUnless(hasattr(os, 'symlink'), 'Symlinks unavailable')
    def test_symlink_cannot_redirect_config_or_lock(self):
        outside = Path(self.temp.name) / 'outside'
        outside.write_text('unchanged')
        (self.root / '.chaos').mkdir()
        for name in ('settings.json', 'settings.json.lock'):
            path = self.root / '.chaos' / name
            path.symlink_to(outside)
            try:
                with self.assertRaises(ConfigurationError):
                    configure(self.root)
                self.assertEqual(outside.read_text(), 'unchanged')
            finally:
                path.unlink()

    def test_optional_ai_is_advisory_sanitized_and_does_not_mutate_settings(self):
        self.existing()
        before = self.path.read_bytes()
        calls = []
        closed = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='\x1b[31m建议\x1b[0m private-fixture-key\u202e；配置不会自动执行'))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                 close=lambda: closed.append(True))
        with patch.object(Settings, 'client', return_value=client):
            result = configure(self.root, ai=True, environment={
                'system': 'Darwin', 'machine': 'arm64', 'pythonVersion': '3.13',
                'apiKey': 'hidden-input-key', 'path': '/private/path', 'files': ['user.chaos']})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['model'], 'gpt-6-astra')
        self.assertNotIn('tools', calls[0])
        self.assertLessEqual(calls[0]['max_tokens'], 512)
        payload = json.dumps(calls)
        for secret in ('private-fixture-key', 'hidden-input-key', '/private/path', 'user.chaos'):
            self.assertNotIn(secret, payload)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result['preferences'], {'aiAdvice': True})
        self.assertEqual(result['advice']['status'], 'completed')
        self.assertNotIn('private-fixture-key', result['advice']['text'])
        self.assertNotIn('\x1b', result['advice']['text'])
        self.assertNotIn('\u202e', result['advice']['text'])
        self.assertEqual(closed, [True])

    def test_ai_missing_credentials_and_provider_failure_are_nonfatal_and_private(self):
        with patch.object(Settings, 'client', side_effect=AssertionError('Must not call without key')):
            result = configure(self.root, ai=True)
        self.assertEqual(result['advice']['status'], 'skipped')
        self.existing()
        with patch.object(Settings, 'client', side_effect=RuntimeError('private-fixture-key')):
            result = configure(self.root, ai=True)
        self.assertEqual(result['advice']['status'], 'failed')
        self.assertNotIn('private-fixture-key', json.dumps(result))

    def test_legacy_transient_environment_is_rejected_before_persistence(self):
        with patch.dict(os.environ, {'CHAOS_MODEL_KEY': 'private-fixture-key'}):
            with self.assertRaises(ConfigurationError) as error:
                configure(self.root)
        self.assertNotIn('private-fixture-key', str(error.exception))
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
