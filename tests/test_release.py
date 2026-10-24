import base64
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from chaos.release import (ReleaseError, build_release, canonical_manifest, copy_source,
                           generate_key, source_version, validate_wheel)


def fixture(root):
    root.mkdir()
    for directory in ('chaos', 'web', 'vendor/deepseek', 'docs', 'examples', 'tests', 'scripts'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / 'chaos/__init__.py').write_text('from .version import __version__\n')
    (root / 'chaos/version.py').write_text('__version__ = "0.6.0"\n')
    (root / 'chaos/old.py').write_text('VALUE = 42\n')
    (root / 'web/index.html').write_text('<h1>Release fixture</h1>\n')
    (root / 'vendor/deepseek/LICENSE').write_text('Fixture license\n')
    (root / 'docs/SDK.md').write_text('Fixture stable interface\n')
    (root / 'examples/demo.chaos').write_text('1 + 2\n')
    (root / 'scripts/release.py').write_text('# fixture release entry point\n')
    (root / 'tests/test_contract.py').write_text('import unittest\nclass Contract(unittest.TestCase):\n def test_compat(self): self.assertEqual(1 + 2, 3)\n')
    (root / 'README.md').write_text('Release fixture\n')
    (root / 'pyproject.toml').write_text('[build-system]\nrequires=["setuptools>=58", "wheel"]\nbuild-backend="setuptools.build_meta"\n')
    (root / 'setup.cfg').write_text('[metadata]\nname = chaos-intelligence\nversion = attr: chaos.version.__version__\n[options]\npackages = find:\n[options.packages.find]\ninclude = chaos\n')
    (root / 'setup.py').write_text('from pathlib import Path\nfrom setuptools import setup\nfrom setuptools.command.build_py import build_py\nclass Build(build_py):\n def run(self):\n  super().run()\n  for d in ("web", "vendor/deepseek", "docs", "examples"):\n   self.copy_tree(d, str(Path(self.build_lib) / "chaos" / d))\nsetup(cmdclass={"build_py": Build})\n')
    return root


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = fixture(self.root / 'source')
        self.private = self.root / 'keys/maintainer.pem'
        self.public = self.root / 'keys/maintainer.pub'
        generate_key(self.private, self.public, self.source)
        self.output = self.root / 'published'

    def build(self, **kwargs):
        return build_release(self.source, self.output, 'https://updates.example.test/chaos',
                             self.private, **kwargs)

    def test_key_permissions_outside_checkout_and_no_overwrite(self):
        if os.name == 'posix':
            self.assertEqual(self.private.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ReleaseError, 'existing'):
            generate_key(self.private, self.public, self.source)
        with self.assertRaisesRegex(ReleaseError, 'outside'):
            generate_key(self.source / 'key.pem', self.root / 'new.pub', self.source)
        if os.name == 'posix':
            self.private.chmod(0o644)
            with self.assertRaisesRegex(ReleaseError, '0600'):
                self.build(skip_tests=True)

    def test_snapshot_excludes_state_secrets_stale_builds_and_unlisted_files(self):
        for name in ('.git/config', '.chaos/settings.json', '.development/receipt.json',
                     'chaos/__pycache__/old.pyc', 'chaos/build/stale.py', 'chaos/.env',
                     'chaos/signing.pem', 'vendor/deepseek/keys/private.key',
                     'dist/stale.whl', 'unexpected.txt', 'chaos/old.egg-info/PKG-INFO',
                     'docs/session.sqlite', 'docs/session.sqlite-wal', 'docs/session.sqlite-shm',
                     'examples/runtime.sqlite3', 'examples/runtime.sqlite3-journal',
                     'web/state.db', 'web/state.db-shm', 'vendor/deepseek/debug.log'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fictional excluded fixture')
        snapshot = self.root / 'snapshot'
        copy_source(self.source, snapshot)
        self.assertEqual((snapshot / 'web/index.html').read_bytes(), (self.source / 'web/index.html').read_bytes())
        self.assertTrue((snapshot / 'scripts/release.py').exists())
        paths = [str(path.relative_to(snapshot)) for path in snapshot.rglob('*')]
        self.assertFalse(any('fictional excluded fixture' in path.read_text()
                             for path in snapshot.rglob('*') if path.is_file()))
        self.assertNotIn('chaos/old.egg-info', paths)

    def test_snapshot_blocks_credential_literals_without_echoing_value(self):
        secret = 'sk-' + 'A1b2' * 12
        (self.source / 'README.md').write_text('Introduction\n' + secret + '\n')
        with self.assertRaises(ReleaseError) as caught:
            copy_source(self.source, self.root / 'snapshot')
        self.assertIn('README.md:2', str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))
        self.assertFalse((self.root / 'snapshot/README.md').exists())

    def test_snapshot_blocks_external_private_values_without_packaging_them(self):
        endpoint = 'https://publisher-' + 'only.invalid/v1'
        private_file = self.root / 'publisher-values.json'
        private_file.write_text(json.dumps([endpoint]))
        (self.source / 'docs/SDK.md').write_text('Example\n' + endpoint)
        with patch.dict(os.environ, {'CHAOS_RELEASE_PRIVATE_VALUES_FILE': str(private_file)}):
            with self.assertRaises(ReleaseError) as caught:
                copy_source(self.source, self.root / 'blocked')
            self.assertIn('docs/SDK.md:2', str(caught.exception))
            self.assertNotIn(endpoint, str(caught.exception))
            (self.source / 'docs/SDK.md').write_text('Safe documented configuration')
            copy_source(self.source, self.root / 'safe')
        self.assertFalse((self.root / 'safe/publisher-values.json').exists())

    def test_snapshot_rejects_invalid_or_in_checkout_private_value_lists(self):
        private_file = self.root / 'publisher-values.json'
        for contents in ('invalid-json', '{}', '[null]', '[""]'):
            private_file.write_text(contents)
            with patch.dict(os.environ, {'CHAOS_RELEASE_PRIVATE_VALUES_FILE': str(private_file)}):
                with self.assertRaisesRegex(ReleaseError, 'external JSON array'):
                    copy_source(self.source, self.root / 'snapshot')
            self.assertFalse((self.root / 'snapshot').exists())
        private_file = self.source / 'publisher-values.json'
        private_file.write_text('[]')
        with patch.dict(os.environ, {'CHAOS_RELEASE_PRIVATE_VALUES_FILE': str(private_file)}):
            with self.assertRaisesRegex(ReleaseError, 'external JSON array'):
                copy_source(self.source, self.root / 'snapshot')

    def test_symlinks_cannot_smuggle_external_files_into_release(self):
        (self.source / 'docs/external').symlink_to(self.private)
        with self.assertRaisesRegex(ReleaseError, 'symlink'):
            copy_source(self.source, self.root / 'snapshot')

    def test_stable_version_required_and_source_not_rewritten(self):
        path = self.source / 'chaos/version.py'
        for version in ('0.5.0', '0.6.0rc1', '1.0', '01.2.3'):
            contents = '__version__ = ' + repr(version) + '\n'
            path.write_text(contents)
            with self.subTest(version=version), self.assertRaises(ReleaseError):
                source_version(self.source)
            self.assertEqual(path.read_text(), contents)

    def test_real_build_runs_tests_signs_full_package_and_publishes_feed_last(self):
        from chaos import release
        calls = []
        original = release._atomic_write
        def write(path, data):
            calls.append(path.name)
            original(path, data)
        with patch('chaos.release._atomic_write', side_effect=write):
            result = self.build(include_source=True)
        self.assertTrue(result['testsRun'])
        self.assertFalse(result['published'])
        self.assertEqual(calls[-1], 'feed.json')
        self.assertFalse((self.output / '.release.lock').exists())
        manifest = json.loads(Path(result['manifest']).read_text())
        public = serialization.load_pem_public_key(self.public.read_bytes())
        signature = base64.b64decode(manifest['signature']['value'], validate=True)
        public.verify(signature, canonical_manifest(manifest))
        altered = dict(manifest, version='9.9.9')
        with self.assertRaises(InvalidSignature):
            public.verify(signature, canonical_manifest(altered))
        artifact = manifest['artifacts'][0]
        self.assertEqual(artifact['sha256'], hashlib.sha256(Path(result['wheel']).read_bytes()).hexdigest())
        self.assertEqual(artifact['size'], Path(result['wheel']).stat().st_size)
        self.assertEqual(artifact['url'], 'https://updates.example.test/chaos/' + Path(result['wheel']).name)
        with zipfile.ZipFile(result['wheel']) as archive:
            self.assertTrue({'chaos/version.py', 'chaos/web/index.html', 'chaos/vendor/deepseek/LICENSE',
                             'chaos/docs/SDK.md', 'chaos/examples/demo.chaos'} <= set(archive.namelist()))
        source_archive = next(self.output.glob('*-source.tar.gz'))
        with tarfile.open(source_archive) as archive:
            self.assertTrue(any(name.endswith('/tests/test_contract.py') for name in archive.getnames()))
            self.assertTrue(any(name.endswith('/scripts/release.py') for name in archive.getnames()))
            self.assertFalse(any('.egg-info' in name or '/build/' in name for name in archive.getnames()))
        self.assertFalse((self.source / 'build').exists())
        self.assertFalse(list(self.source.glob('*.egg-info')))
        self.assertEqual((self.source / 'chaos/version.py').read_text(), '__version__ = "0.6.0"\n')

    def test_failed_compatibility_test_does_not_publish(self):
        (self.source / 'tests/test_contract.py').write_text('import unittest\nclass Contract(unittest.TestCase):\n def test_failure(self): self.fail("fixture regression")\n')
        with self.assertRaisesRegex(ReleaseError, 'fixture regression'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_new_version_contains_no_deleted_file_and_does_not_mutate_old_wheel(self):
        first = self.build(skip_tests=True)
        old_wheel = Path(first['wheel']).read_bytes()
        (self.source / 'chaos/old.py').unlink()
        (self.source / 'chaos/version.py').write_text('__version__ = "0.6.1"\n')
        second = self.build(skip_tests=True)
        with zipfile.ZipFile(second['wheel']) as archive:
            self.assertNotIn('chaos/old.py', archive.namelist())
        self.assertEqual(Path(first['wheel']).read_bytes(), old_wheel)
        self.assertEqual(json.loads((self.output / 'feed.json').read_text())['version'], '0.6.1')

    def test_reusing_version_with_changed_content_preserves_feed(self):
        self.build(skip_tests=True)
        original_feed = (self.output / 'feed.json').read_bytes()
        (self.source / 'chaos/old.py').write_text('VALUE = 43\n')
        with self.assertRaisesRegex(ReleaseError, 'different metadata|immutable'):
            self.build(skip_tests=True)
        self.assertEqual((self.output / 'feed.json').read_bytes(), original_feed)
        self.assertFalse((self.output / '.release.lock').exists())

    def test_identical_build_is_reproducible_including_source_archive(self):
        first = self.build(skip_tests=True, include_source=True)
        original_wheel = Path(first['wheel']).read_bytes()
        original_source = next(self.output.glob('*-source.tar.gz')).read_bytes()
        self.build(skip_tests=True, include_source=True)
        self.assertEqual(Path(first['wheel']).read_bytes(), original_wheel)
        self.assertEqual(next(self.output.glob('*-source.tar.gz')).read_bytes(), original_source)

    def test_incomplete_package_is_rejected(self):
        wheel = self.root / 'broken.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('chaos/version.py', '__version__ = "0.6.0"\n')
        with self.assertRaisesRegex(ReleaseError, 'content mismatch'):
            validate_wheel(wheel, self.source, '0.6.0')

    def test_build_environment_preserves_only_interpreter_user_site_and_no_model_keys(self):
        from chaos.release import _build_environment
        site_directory = self.root / 'user-site'
        site_directory.mkdir()
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture-secret', 'CHAOS_PROVIDER_KEY': 'fixture-secret',
                                     'PYTHONPATH': '/fixture/untrusted-checkout',
                                     'CHAOS_RELEASE_PRIVATE_VALUES_FILE': '/fixture/publisher-values.json'}), \
                patch('chaos.release.site.ENABLE_USER_SITE', True), \
                patch('chaos.release.site.getusersitepackages', return_value=str(site_directory)):
            environment = _build_environment(self.root / 'isolated-home')
        self.assertNotIn('OPENAI_API_KEY', environment)
        self.assertNotIn('CHAOS_PROVIDER_KEY', environment)
        self.assertNotIn('CHAOS_RELEASE_PRIVATE_VALUES_FILE', environment)
        self.assertEqual(environment['PYTHONPATH'], str(site_directory.resolve()))
        self.assertEqual(environment['HOME'], str(self.root / 'isolated-home'))

    def test_private_key_cannot_be_inside_upload_directory(self):
        with self.assertRaisesRegex(ReleaseError, 'outside the release output'):
            build_release(self.source, self.private.parent, 'https://updates.example.test',
                          self.private, skip_tests=True)

    def test_plain_http_and_credential_urls_are_rejected(self):
        for url in ('http://updates.example.test', 'https://user:secret@updates.example.test',
                    'https://updates.example.test/?token=fictional', 'https://updates.example.test:bad',
                    'https://bad host', 'https://@updates.example.test'):
            with self.subTest(url=url), self.assertRaisesRegex(ReleaseError, 'HTTPS'):
                build_release(self.source, self.output, url, self.private, skip_tests=True)


if __name__ == '__main__':
    unittest.main()
