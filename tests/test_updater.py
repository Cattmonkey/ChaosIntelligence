import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from chaos.updater import (UpdateManager, UpdateError, atomic_json, canonical_manifest,
                           compare_versions, public_key_bytes, validate_url, verify_manifest)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'app'
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.installs = []
        self.wheel = self.wheel_bytes()
        self.manifest = self.signed()
        self.manager = self.make_manager()
        self.manager.configure({'feedURL': 'https://releases.example/latest.json', 'publicKey': self.public.hex(), 'autoCheck': False})

    def wheel_bytes(self, version='0.6.0', extras=None):
        output = io.BytesIO()
        files = {name: '' for name in ['chaos/__init__.py', 'chaos/version.py', 'chaos/web/index.html',
                 'chaos/web/app.js', 'chaos/web/control.html', 'chaos/vendor/deepseek/manifest.json',
                 'chaos/docs/SDK.md', 'chaos/examples/counter-machine.chaos']}
        files['chaos_intelligence-' + version + '.dist-info/METADATA'] = 'Name: chaos-intelligence\nVersion: ' + version + '\n'
        files.update(extras or {})
        with zipfile.ZipFile(output, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        return output.getvalue()

    def signed(self, **changes):
        value = {'schemaVersion': 1, 'version': '0.6.0', 'channel': 'stable', 'publishedAt': '2026-10-07T00:00:00Z',
                 'apiVersion': 'v1', 'storageVersion': 1, 'installerVersion': 1,
                 'artifacts': [{'kind': 'wheel', 'filename': 'chaos_intelligence-0.6.0-py3-none-any.whl',
                                'url': 'https://releases.example/application.whl',
                                'size': len(self.wheel), 'sha256': hashlib.sha256(self.wheel).hexdigest()}]}
        value.update(changes)
        value['signature'] = {'algorithm': 'ed25519', 'keyId': hashlib.sha256(self.public).hexdigest()[:16],
                              'value': base64.b64encode(self.key.sign(canonical_manifest(value))).decode('ascii')}
        return value

    def download(self, url, limit):
        return json.dumps(self.manifest).encode() if url.endswith('.json') else self.wheel

    def install(self, wheel, release, version):
        self.installs.append(version)
        python = release / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        python.parent.mkdir(parents=True)
        python.write_text('fixture interpreter')

    def make_manager(self, **kwargs):
        return UpdateManager(self.home, '0.5.0', installer=self.install, downloader=self.download, **kwargs)

    def old_release(self, version='0.5.0', previous=None):
        python = self.manager.get_release_python(version)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text('old release')
        atomic_json(self.home / 'current.json', {'version': version, 'previousVersion': previous, 'healthConfirmed': True})

    def assert_code(self, code, call):
        with self.assertRaises(UpdateError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)

    def test_signed_check_and_no_execution(self):
        status = self.manager.check()
        self.assertEqual(status['latestVersion'], '0.6.0')
        self.assertTrue(status['available'])
        self.assertFalse(status['busy'])
        self.assertIsNotNone(status['lastCheckedAt'])
        self.assertEqual(self.installs, [])
        self.assertNotIn('publicKey', status['config'])

    def test_signature_tampering_cannot_install(self):
        self.manifest['version'] = '0.7.0'
        self.assert_code('invalid_signature', self.manager.prepare)
        self.assertEqual(self.installs, [])
        self.assertFalse((self.home / 'releases').exists())

    def test_untrusted_key_and_missing_signature_rejected(self):
        other = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.assert_code('invalid_signature', lambda: verify_manifest(self.manifest, other))
        self.manifest.pop('signature')
        self.assert_code('invalid_signature', self.manager.check)

    def test_incompatible_and_downgrade_releases_rejected(self):
        for changes, code in [({'apiVersion': 'v2'}, 'incompatible_release'),
                              ({'storageVersion': 2}, 'incompatible_release'),
                              ({'installerVersion': 2}, 'incompatible_release'),
                              ({'schemaVersion': True}, 'incompatible_release'),
                              ({'channel': 'beta'}, 'incompatible_release'),
                              ({'version': '0.4.0'}, 'downgrade_rejected')]:
            with self.subTest(changes=changes):
                self.manifest = self.signed(**changes)
                self.assert_code(code, self.manager.check)

    def test_artifact_integrity_checked_before_installer(self):
        self.wheel = self.wheel[:-1] + b'X'
        self.assert_code('integrity_failed', self.manager.prepare)
        self.assertEqual(self.installs, [])
        self.assertEqual(list((self.home / 'staging').iterdir()), [])

    def test_short_and_oversized_downloads_fail(self):
        self.wheel = self.wheel[:-1]
        self.assert_code('integrity_failed', self.manager.prepare)
        self.wheel += b'XXX'
        self.assert_code('download_failed', self.manager.prepare)
        self.assertEqual(self.installs, [])

    def test_path_traversal_in_signed_wheel_rejected(self):
        self.wheel = self.wheel_bytes(extras={'../../outside.txt': 'bad'})
        self.manifest = self.signed()
        self.assert_code('invalid_artifact', self.manager.prepare)
        self.assertEqual(self.installs, [])
        self.assertFalse((self.home.parent / 'outside.txt').exists())

    def test_wrong_wheel_metadata_rejected(self):
        self.wheel = self.wheel_bytes('0.7.0')
        self.manifest = self.signed()
        self.assert_code('invalid_artifact', self.manager.prepare)

    def test_prepare_never_switches_active_or_touches_user_data(self):
        self.old_release()
        user = self.home / 'workspace.sqlite3'
        user.write_text('user data')
        status = self.manager.prepare()
        self.assertEqual(status['preparedVersion'], '0.6.0')
        self.assertEqual(json.loads((self.home / 'current.json').read_text())['version'], '0.5.0')
        self.assertEqual(user.read_text(), 'user data')
        self.assertEqual(self.installs, ['0.6.0'])

    def test_republished_identical_wheel_reuses_prepared_release(self):
        self.manager.prepare()
        self.manifest = self.signed(publishedAt='2026-10-08T00:00:00Z')
        self.manager.prepare()
        self.assertEqual(self.installs, ['0.6.0'])

    def test_failed_install_removes_candidate_and_preserves_pointer(self):
        self.old_release()
        original = (self.home / 'current.json').read_bytes()
        def broken(wheel, release, version):
            (release / 'partial').write_text('partial')
            raise RuntimeError('fixture failure')
        manager = self.make_manager(managed=True)
        manager._installer = broken
        self.assert_code('update_failed', manager.apply)
        self.assertEqual((self.home / 'current.json').read_bytes(), original)
        self.assertFalse((self.home / 'releases' / '0.6.0').exists())
        self.assertEqual(list((self.home / 'staging').iterdir()), [])

    def test_unmanaged_apply_does_not_download_or_replace_source(self):
        self.assert_code('unmanaged_installation', self.manager.apply)
        self.assertFalse((self.home / 'releases').exists())

    def test_activation_and_rollback_keep_exact_previous(self):
        self.old_release()
        calls = []
        manager = self.make_manager(managed=True, on_activate=calls.append)
        status = manager.apply()
        self.assertEqual(status['activeVersion'], '0.6.0')
        self.assertEqual(status['previousVersion'], '0.5.0')
        self.assertTrue(status['restartRequired'])
        self.assertEqual(calls, ['0.6.0'])
        self.assert_code('restart_pending', manager.apply)
        status = manager.rollback()
        self.assertEqual(status['activeVersion'], '0.5.0')
        self.assertEqual(status['previousVersion'], '0.6.0')

    def test_callback_failure_restores_pointer(self):
        self.old_release()
        original = (self.home / 'current.json').read_bytes()
        errors = []
        def failed(_):
            raise RuntimeError('restart failure')
        manager = self.make_manager(managed=True, on_activate=failed, on_error=errors.append)
        self.assert_code('update_failed', manager.apply)
        self.assertEqual(json.loads((self.home / 'current.json').read_bytes()), json.loads(original))
        self.assertFalse(manager.status()['restartRequired'])
        self.assertEqual(len(errors), 1)

    def test_waits_for_active_work_then_activates(self):
        self.old_release()
        checks = iter([False, True])
        manager = self.make_manager(managed=True, can_activate=lambda: next(checks), activation_timeout=2)
        status = manager.apply()
        self.assertEqual(status['activeVersion'], '0.6.0')
        self.assertIn('update.waiting_idle', [e['type'] for e in status['events']])

    def test_busy_timeout_preserves_pointer_and_clears_gate(self):
        self.old_release()
        errors = []
        manager = self.make_manager(managed=True, can_activate=lambda: False, activation_timeout=.01, on_error=errors.append)
        self.assert_code('not_idle', manager.apply)
        self.assertEqual(manager.status()['activeVersion'], '0.5.0')
        self.assertEqual(errors[0].code, 'not_idle')

    def test_bootstrap_allows_same_version_but_rejects_existing_pointer(self):
        self.wheel = self.wheel_bytes('0.5.0')
        self.manifest = self.signed(version='0.5.0')
        self.manifest['artifacts'][0]['filename'] = 'chaos_intelligence-0.5.0-py3-none-any.whl'
        self.manifest['signature']['value'] = base64.b64encode(self.key.sign(canonical_manifest(self.manifest))).decode()
        self.assertEqual(self.manager.adopt()['activeVersion'], '0.5.0')
        self.assert_code('already_managed', self.manager.adopt)

    def test_prune_only_after_health_keeps_previous_and_user_data(self):
        self.old_release()
        obsolete = self.home / 'releases' / '0.4.0'
        obsolete.mkdir()
        (obsolete / 'old').write_text('obsolete')
        arbitrary = self.home / 'releases' / 'user-files'
        arbitrary.mkdir()
        (self.home / 'user-data').write_text('preserve')
        manager = self.make_manager(managed=True)
        manager.apply()
        self.assertTrue(obsolete.exists())
        manager.confirm_health('0.6.0')
        self.assertFalse(obsolete.exists())
        self.assertTrue(manager.get_release_python('0.5.0').exists())
        self.assertTrue(arbitrary.exists())
        self.assertEqual((self.home / 'user-data').read_text(), 'preserve')

    def test_stale_health_cannot_trigger_pruning(self):
        self.old_release()
        manager = self.make_manager(managed=True)
        manager.apply()
        new = UpdateManager(self.home, '0.6.0', managed=True)
        atomic_json(self.home / 'health.json', {'ok': True, 'version': '0.6.0', 'instanceId': 'old-instance'})
        with patch.dict(os.environ, {'CHAOS_INSTANCE_ID': 'new-instance'}):
            new._confirm_supervisor_health()
        self.assertFalse(json.loads((self.home / 'current.json').read_text())['healthConfirmed'])
        atomic_json(self.home / 'health.json', {'ok': True, 'version': '0.6.0', 'instanceId': 'new-instance'})
        with patch.dict(os.environ, {'CHAOS_INSTANCE_ID': 'new-instance'}):
            new._confirm_supervisor_health()
        self.assertTrue(json.loads((self.home / 'current.json').read_text())['healthConfirmed'])

    def test_status_distinguishes_prior_health_and_retains_rollback_reason(self):
        self.old_release()
        atomic_json(self.home / 'health.json', {'ok': True, 'version': '0.5.0', 'instanceId': 'old-instance', 'rolledBackFrom': '0.6.0'})
        with patch.dict(os.environ, {'CHAOS_INSTANCE_ID': 'new-instance'}):
            status = self.manager.status()
        self.assertTrue(status['healthConfirmed'])
        self.assertFalse(status['health']['currentInstance'])
        self.assertEqual(status['state'], 'idle')
        with patch.dict(os.environ, {'CHAOS_INSTANCE_ID': 'old-instance'}):
            self.assertEqual(self.manager.status()['state'], 'rolled_back')
            self.assertEqual(self.manager.check()['state'], 'rolled_back')

    def test_cas_and_trust_pin_survive_other_manager_changes(self):
        stale = self.make_manager()
        version = stale.status()['version']
        self.manager.configure({'intervalSeconds': 120}, version)
        self.assert_code('conflict', lambda: stale.configure({'intervalSeconds': 300}, version))
        self.assertEqual(stale.status()['config']['intervalSeconds'], 120)
        other = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.assert_code('trust_locked', lambda: stale.configure({'publicKey': other.hex()}))
        self.assertEqual(stale.status()['config']['keyId'], hashlib.sha256(self.public).hexdigest()[:16])

    def test_cross_process_lock_prevents_concurrent_mutation(self):
        other = self.make_manager()
        with self.manager._file_lock():
            self.assert_code('busy', other.check)
        self.assertFalse(other.status()['busy'])

    def test_async_jobs_return_and_exclude_concurrent_requests(self):
        entered, finish = threading.Event(), threading.Event()
        def blocked(url, limit):
            entered.set()
            finish.wait(3)
            return self.download(url, limit)
        self.manager._downloader = blocked
        self.manager.start('check')
        self.assertTrue(entered.wait(2))
        self.assertTrue(self.manager.status()['busy'])
        self.assert_code('busy', self.manager.check)
        self.assert_code('busy', lambda: self.manager.start('check'))
        finish.set()
        self.manager._thread.join(3)
        self.assertFalse(self.manager.status()['busy'])
        self.assertEqual(self.manager.status()['state'], 'available')

    def test_keys_support_raw_hex_base64_and_pem_files(self):
        for value in (self.public, self.public.hex(), base64.b64encode(self.public).decode()):
            self.assertEqual(public_key_bytes(value), self.public)
        keyfile = self.home / 'publisher.pem'
        keyfile.write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.assertEqual(public_key_bytes(str(keyfile)), self.public)
        self.assertEqual(public_key_bytes(keyfile), self.public)

    def test_optional_browser_is_reinstalled_and_preserved_for_rollback(self):
        manager = self.make_manager(preserve_browser=True)
        release = self.home / 'releases' / '0.6.0'
        release.mkdir(parents=True)
        with patch('chaos.updater.venv.EnvBuilder') as builder, patch('chaos.updater.subprocess.run') as run, patch.object(manager, '_self_test') as smoke:
            manager._install(Path('/tmp/chaos_intelligence-0.6.0-py3-none-any.whl'), release, '0.6.0')
        self.assertFalse(builder.call_args.kwargs['with_pip'])
        self.assertTrue(run.call_args_list[1].args[0][-1].endswith('[browser]'))
        self.assertEqual(run.call_args_list[2].args[0][-3:], ['playwright', 'install', 'chromium'])
        self.assertEqual(run.call_args_list[2].kwargs['env']['PLAYWRIGHT_SKIP_BROWSER_GC'], '1')
        smoke.assert_called_once()

    def test_optional_browser_failure_aborts_release(self):
        import subprocess
        manager = self.make_manager(preserve_browser=True)
        with patch('chaos.updater.venv.EnvBuilder'), patch('chaos.updater.subprocess.run', side_effect=[None, None, subprocess.CalledProcessError(1, ['browser'])]):
            self.assert_code('install_failed', lambda: manager._install(Path('/tmp/candidate.whl'), self.home, '0.6.0'))

    def test_interrupted_release_and_staging_are_cleaned_before_retry(self):
        release = self.home / 'releases' / '0.6.0'
        release.mkdir(parents=True)
        (release / 'partial.py').write_text('abandoned')
        staging = self.home / 'staging' / 'release-abandoned'
        staging.mkdir(parents=True)
        (staging / 'partial.whl').write_text('incomplete download')
        self.manager.prepare()
        self.assertFalse((release / 'partial.py').exists())
        self.assertFalse(staging.exists())
        self.assertTrue((release / '.release.json').exists())

    def test_interrupted_cleanup_preserves_active_previous_and_symlink_targets(self):
        self.old_release('0.7.0', '0.6.0')
        previous = self.home / 'releases' / '0.6.0'
        previous.mkdir()
        (previous / 'keep').write_text('previous')
        self.assert_code('release_exists', self.manager.prepare)
        self.assertEqual((previous / 'keep').read_text(), 'previous')
        import shutil
        shutil.rmtree(previous)
        external = self.home.parent / 'external'
        external.mkdir()
        (external / 'keep').write_text('outside')
        previous.symlink_to(external, target_is_directory=True)
        self.assert_code('invalid_state', self.manager.prepare)
        self.assertEqual((external / 'keep').read_text(), 'outside')

    def test_installer_children_inherit_update_lock_on_posix(self):
        if os.name == 'nt':
            self.skipTest('POSIX advisory lock inheritance')
        with self.manager._file_lock(), patch('chaos.updater.subprocess.run') as run:
            self.manager._command(['fixture'])
            self.assertEqual(run.call_args.kwargs['pass_fds'], (self.manager._lock_fd,))

    def test_secure_url_policy_and_semantic_ordering(self):
        for url in ('http://releases.example/x', 'file:///tmp/x', 'https://user:secret@example.com/x', 'https://example.com/x#fragment'):
            self.assert_code('invalid_url', lambda: validate_url(url))
        self.assertEqual(validate_url('https://example.com/x?signature=abc'), 'https://example.com/x?signature=abc')
        self.assertEqual(validate_url('http://127.0.0.1:9876/x', True), 'http://127.0.0.1:9876/x')
        for older, newer in [('0.9.0', '0.10.0'), ('1.0.0-rc.2', '1.0.0-rc.10'), ('1.0.0-rc.10', '1.0.0')]:
            self.assertLess(compare_versions(older, newer), 0)
        self.assertEqual(compare_versions('1.0.0+build1', '1.0.0+build2'), 0)


if __name__ == '__main__':
    unittest.main()
