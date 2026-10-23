"""Learning examples use real Settings persistence without contacting a provider."""
import importlib.util
import importlib
import contextlib
import io
import sys
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chaos.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure_models_example', ROOT / 'examples/configure_models.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class ModelConfigurationExample(unittest.TestCase):
    def setUp(self):
        self.clean = patch.dict(os.environ, {'CHAOS_MODEL_URL': '', 'CHAOS_MODEL_KEY': '', 'CHAOS_MODEL_NAME': ''})
        self.clean.start()
        self.addCleanup(self.clean.stop)

    def test_private_persistence_and_safe_output_use_actual_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            value = example.configure(Settings(path), {
                'CHAOS_BASE_URL': 'https://api.example.com/v1',
                'CHAOS_TEXT_KEY': 'fixture-secret-not-a-real-key',
                'CHAOS_PLANNER_MODEL': 'gpt-6-astra',
            }, fresh=True)
            reloaded = Settings(path)
            resolved = reloaded.resolve(role='planner')
            self.assertEqual(resolved['apiKey'], 'fixture-secret-not-a-real-key')
            self.assertEqual(resolved['model'], 'gpt-6-astra')
            self.assertEqual(resolved['baseURL'], 'https://api.example.com/v1')
            self.assertFalse(reloaded.public()['roles']['planner']['autoUpgrade'])
            self.assertEqual(reloaded.runtime()['workspace'], '.chaos/tasks')
            self.assertNotIn('fixture-secret', json.dumps(value))
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_partial_setup_preserves_existing_routes_and_uses_per_provider_url(self):
        settings = Settings(':memory:')
        settings.update({'providers': {'text': {'apiKey': 'preserved-key'}},
                         'agents': {'kept': {'provider': 'grok', 'model': 'grok-custom'}}}, 1)
        example.configure(settings, {'CHAOS_BASE_URL': 'https://api.example.com/v1',
                                     'CHAOS_GROK_BASE_URL': 'https://example.invalid/v1',
                                     'CHAOS_GROK_KEY': 'grok-fixture-key'})
        self.assertEqual(settings.resolve()['apiKey'], 'preserved-key')
        self.assertEqual(settings.resolve('kept')['baseURL'], 'https://example.invalid/v1')
        self.assertEqual(settings.resolve('kept')['model'], 'grok-custom')
        self.assertTrue(settings.public()['roles']['planner']['autoUpgrade'])

    def test_invalid_configuration_leaves_existing_file_byte_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            settings = Settings(path)
            example.configure(settings, {'CHAOS_TEXT_KEY': 'kept-fixture-key'})
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                example.configure(settings, {'CHAOS_BASE_URL': 'http://external.invalid/v1'})
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, 'Unset'):
                example.configure(settings, {'CHAOS_MODEL_URL': 'https://example.invalid/v1'})
            self.assertEqual(path.read_bytes(), before)

    def test_cli_rejects_private_settings_symlink_without_touching_target(self):
        from chaos.project import init_project
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            init_project(root)
            external = Path(tmp) / 'outside.json'
            Settings(external).update({'providers': {'text': {'apiKey': 'original-fixture-key'}}}, 1)
            before = external.read_bytes()
            (root / '.chaos').mkdir()
            (root / '.chaos/settings.json').symlink_to(external)
            previous_path = sys.path[:]
            try:
                with patch.dict(os.environ, {'CHAOS_TEXT_KEY': 'replacement-fixture-key'}), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(example.main(['--project', str(root)]), 1)
            finally:
                sys.path[:] = previous_path
            self.assertEqual(external.read_bytes(), before)

    def selected_project(self, root):
        from chaos.project import init_project
        from chaos.project_launcher import ENV_SCHEMA
        init_project(root)
        state = root / '.chaos'
        selected = state / 'environments' / ('env-' + 'a' * 32)
        for env in (state, selected):
            package = env / 'runtime/chaos'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('', encoding='utf-8')
            (env / 'runtime/requirements.txt').write_text('', encoding='utf-8')
        (state / 'environment.json').write_text(json.dumps({
            'schema': ENV_SCHEMA, 'current': selected.name, 'previous': 'base',
        }), encoding='utf-8')
        return state, selected

    def test_helper_imports_selected_runtime_and_matching_dependencies(self):
        from chaos.project_launcher import dependency_id
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            base, selected = self.selected_project(root)
            (base / 'runtime/config_runtime_probe.py').write_text(
                "raise AssertionError('the obsolete base runtime was loaded')", encoding='utf-8')
            (selected / 'runtime/config_runtime_probe.py').write_text(
                "origin = 'selected-runtime'", encoding='utf-8')
            deps = selected / 'deps'
            deps.mkdir()
            (deps / 'config_deps_probe.py').write_text("origin = 'selected-deps'", encoding='utf-8')
            (deps / '.chaos-dependencies.json').write_text(json.dumps({
                'identity': dependency_id(selected / 'runtime'),
            }), encoding='utf-8')
            previous_path = sys.path[:]
            try:
                with example.project_imports(root) as runtime:
                    self.assertEqual(runtime, selected / 'runtime')
                    self.assertEqual(importlib.import_module('config_runtime_probe').origin, 'selected-runtime')
                    self.assertEqual(importlib.import_module('config_deps_probe').origin, 'selected-deps')
                    self.assertNotIn(str(base / 'runtime'), sys.path)
            finally:
                sys.modules.pop('config_runtime_probe', None)
                sys.modules.pop('config_deps_probe', None)
            self.assertEqual(sys.path, previous_path)

    def test_helper_does_not_load_incompatible_selected_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            _, selected = self.selected_project(root)
            deps = selected / 'deps'
            deps.mkdir()
            (deps / '.chaos-dependencies.json').write_text(
                '{"identity": "another-interpreter"}', encoding='utf-8')
            with example.project_imports(root):
                self.assertNotIn(str(deps), sys.path)

    def test_helper_holds_environment_lock_for_entire_import_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            self.selected_project(root)
            probe = """import runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
bootstrap = runpy.run_path(str(root / 'chaos.py'))
try:
    with bootstrap['environment_lock'](root, exclusive=True):
        print('available')
except ValueError:
    print('busy')
"""
            command = [sys.executable, '-I', '-S', '-c', probe, str(root)]
            with example.project_imports(root):
                locked = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
                self.assertEqual(locked.stdout.strip(), 'busy')
            released = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(released.stdout.strip(), 'available')

    def test_helper_rejects_invalid_pointer_without_base_fallback_and_restores_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            base, _ = self.selected_project(root)
            (base / 'environment.json').write_text(
                '{"schema":"chaos.environment/1","current":"../base","previous":null}', encoding='utf-8')
            previous_path = sys.path[:]
            with self.assertRaisesRegex(ValueError, 'Invalid environment identifier'):
                with example.project_imports(root):
                    self.fail('Invalid selection must not fall back to the valid base runtime')
            self.assertEqual(sys.path, previous_path)

    def test_helper_missing_selected_runtime_never_uses_existing_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            _, selected = self.selected_project(root)
            (selected / 'runtime/chaos/__init__.py').unlink()
            previous_path = sys.path[:]
            with self.assertRaisesRegex(ValueError, 'Selected runtime is missing'):
                with example.project_imports(root):
                    self.fail('Missing selected runtime must not fall back')
            self.assertEqual(sys.path, previous_path)

    def test_model_program_is_valid_and_calls_the_named_language_agent(self):
        from chaos import Framework, Registry, Final
        from chaos.modules import ProgramBundle
        source = (ROOT / 'examples/learning-models/main.chaos').read_text()
        ProgramBundle(source)
        # A registered deterministic provider exercises the language call without a network.
        registry = Registry()
        seen = []
        @registry.provider('learning.fixture')
        def responder(request, context):
            context.checkpoint()
            seen.append(request.input)
            return Final('fixture explanation')
        fixture_source = source.replace('provider: "model"', 'provider: "learning.fixture"')
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('learning', source=fixture_source, capabilities=[])
            result = app.start(project['id'], project['version'], '闭包', mode='source').result(5)
            self.assertEqual(result, 'fixture explanation')
            self.assertEqual(seen, ['闭包'])


if __name__ == '__main__':
    unittest.main()
