"""Real isolated browser checks for capability drafts and original versioned save."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import DeviceSpec, Framework, Registry, Settings, Workflow
from chaos.server import make_server, static_path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node unavailable')
class CapabilityAuthoringTests(unittest.TestCase):
    def browser_check(self, scenario):
        with tempfile.TemporaryDirectory(prefix='chaos-capability-ui-') as directory:
            folder = Path(directory)
            env = {key: value for key, value in os.environ.items() if not key.startswith('CHAOS_MODEL_')}
            env['CHAOS_CONFIG'] = str(folder / 'settings.json')
            # Inherited model settings never enter this local fixture.
            with patch.dict(os.environ, env, clear=True):
                registry = Registry()
                opened = []
                def factory(context):
                    opened.append(True)
                    raise AssertionError('Device inventory must never call a factory')
                registry.register_device(DeviceSpec('audit_desk', factory, description='A static test device'))
                with Framework(settings=Settings(':memory:'), registry=registry) as app:
                    for key in ('drafts', 'ir', 'conflict'):
                        app.create_project(key, source='40 + 2', capabilities=[])
                    flow = (Workflow().input().tool('tool', 'text.normalize')
                            .function('function', 'input', tools=[])
                            .agent('agent', 'Local fixture', tools=[]).output()
                            .connect('input', 'tool').connect('tool', 'function')
                            .connect('function', 'agent').connect('agent', 'output'))
                    app.create_project('filters', graph=flow, capabilities=['text.normalize'])
                    app.create_project('legacy', source='agent robot {provider:"local",tools:["device.control"]}; null', capabilities=[])
                    # Source-overlay fallback supports isolated owner-draft review;
                    # installed/full-source runs resolve every asset normally.
                    def asset(filename):
                        proposed = ROOT / 'web' / filename
                        return proposed if proposed.is_file() else static_path(filename)
                    with patch('chaos.server.static_path', asset):
                        server = make_server('127.0.0.1', 0, framework=app,
                                             state_dir=folder / 'control', update_home=folder / 'updates')
                        thread = threading.Thread(target=server.serve_forever, daemon=True)
                        thread.start()
                        try:
                            result = subprocess.run([shutil.which('node'), str(ROOT / 'tests/capability_authoring_controls.cjs'),
                                                     'http://127.0.0.1:' + str(server.server_port), scenario],
                                                    cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=75)
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            self.assertTrue(json.loads(result.stdout)['passed'])
                            self.assertEqual(opened, [])
                        finally:
                            server.shutdown()
                            thread.join(3)
                            server.server_close()
                            self.assertFalse(thread.is_alive())

    def test_source_and_ir_permissions_share_original_save(self):
        self.browser_check('drafts')

    def test_callable_choices_and_snapshot_readonly(self):
        self.browser_check('filters')

    def test_remote_revision_and_stale_dialog_preserve_drafts(self):
        self.browser_check('conflict')


if __name__ == '__main__':
    unittest.main()
