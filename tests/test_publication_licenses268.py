"""Release terms survive all supported packaging forms without licensing user code."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from chaos.portable import (PackageError, build_portable, build_project,
                            extract_portable, verify_portable)
from chaos.project_environment import _read_runtime
from chaos.release import copy_source, validate_wheel, ReleaseError
from test_portable_package import wheel
from test_release import fixture


PREFIX = 'chaos_intelligence-0.79.0.dist-info/licenses/'
TERMS = {
    'LICENSE': b'Original project license fixture: no AI training.\n',
    'THIRD_PARTY_NOTICES.md': b'Third parties retain their respective terms.\n',
    'vendor/deepseek/LICENSE': b'MIT fixture A\n',
    'vendor/deepseek/upstream/LICENSE': b'MIT fixture B\n',
    'vendor/deepseek/upstream/vendor/cordis/LICENSE': b'MIT fixture C\n',
    'vendor/openui/LICENSE': b'Apache fixture\n',
    'vendor/openui/THIRD_PARTY_LICENSES.txt': b'Bundled component terms\n',
    'web/vendor/LICENSES.txt': b'Frontend component terms\n',
}


def licensed_wheel(folder, mutate=None):
    def extend(files):
        import chaos
        root = Path(chaos.__file__).parent
        for name in ('project.py', 'project_launcher.py', 'project_environment.py'):
            files['chaos/' + name] = (root / name).read_bytes()
        files['chaos/docs/PROJECT_README.md'] = b'Project README fixture\n'
        files.update({PREFIX + name: content for name, content in TERMS.items()})
        metadata = next(name for name in files if name.endswith('/METADATA'))
        files[metadata] += (b'License-Expression: LicenseRef-Chaos-No-AI-Training-1.0\n'
                            + ''.join('License-File: ' + name + '\n' for name in TERMS).encode())
        if mutate:
            mutate(files)
        record = next(name for name in files if name.endswith('/RECORD'))
        rows = [[name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(), str(len(data))]
                for name, data in files.items() if name != record]
        rows.append([record, '', ''])
        output = io.StringIO()
        csv.writer(output).writerows(rows)
        files[record] = output.getvalue().encode()
    return wheel(folder, extend)


class PublicationLicenseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def source(self):
        source = fixture(self.root / 'source')
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            (source / name).write_bytes(TERMS[name])
        return source

    def release_wheel(self, source, *, missing=None, changed=None, flattened=False):
        files = {}
        for directory in ('chaos', 'web', 'vendor', 'docs', 'examples'):
            for path in (source / directory).rglob('*'):
                if path.is_file():
                    name = path.relative_to(source).as_posix()
                    files[name if directory == 'chaos' else 'chaos/' + name] = path.read_bytes()
        prefix = 'chaos_intelligence-0.6.0.dist-info/'
        files[prefix + 'METADATA'] = b'Metadata-Version: 2.4\nName: chaos-intelligence\nVersion: 0.6.0\n'
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            if name != missing and (source / name).exists():
                files[prefix + ('' if flattened else 'licenses/') + name] = (
                    b'tampered terms' if name == changed else (source / name).read_bytes())
        output = self.root / 'release.whl'
        with zipfile.ZipFile(output, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return output

    def test_source_snapshot_carries_terms_without_private_files(self):
        source = self.source()
        (source / '.env').write_text('fixture private credential')
        (source / 'settings.json').write_text('fixture local configuration')
        destination = self.root / 'snapshot'
        copy_source(source, destination)
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            self.assertEqual((destination / name).read_bytes(), TERMS[name])
        self.assertFalse((destination / '.env').exists())
        self.assertFalse((destination / 'settings.json').exists())

    def test_release_rejects_missing_or_changed_terms(self):
        source = self.source()
        validate_wheel(self.release_wheel(source), source, '0.6.0')
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            with self.subTest(name=name, action='missing'):
                with self.assertRaisesRegex(ReleaseError, 'missing license document'):
                    validate_wheel(self.release_wheel(source, missing=name), source, '0.6.0')
            with self.subTest(name=name, action='changed'):
                with self.assertRaisesRegex(ReleaseError, 'license differs'):
                    validate_wheel(self.release_wheel(source, changed=name), source, '0.6.0')

    def test_legacy_release_is_accepted_but_flattened_new_terms_are_rejected(self):
        source = fixture(self.root / 'source')
        validate_wheel(self.release_wheel(source), source, '0.6.0')
        (source / 'LICENSE').write_bytes(TERMS['LICENSE'])
        with self.assertRaisesRegex(ReleaseError, 'setuptools>=77'):
            validate_wheel(self.release_wheel(source, flattened=True), source, '0.6.0')

    def test_portable_retains_original_and_upstream_terms_and_detects_changes(self):
        archive = self.root / 'portable.zip'
        build_portable(licensed_wheel(self.root), archive)
        destination = self.root / 'runtime'
        extract_portable(archive, destination)
        for name, data in TERMS.items():
            self.assertEqual((destination / (PREFIX + name)).read_bytes(), data)
        self.assertIn('dist-info/licenses/LICENSE', (destination / 'README.md').read_text())
        (destination / (PREFIX + 'LICENSE')).write_bytes(b'changed')
        with self.assertRaises(PackageError):
            verify_portable(destination)

    def test_project_terms_follow_versioned_runtime_not_user_programs(self):
        archive = self.root / 'project.zip'
        build_project(licensed_wheel(self.root), archive)
        # This is the same path used by the existing replacement protocol.
        runtime = _read_runtime(archive)
        for name, data in TERMS.items():
            self.assertEqual(runtime[PREFIX + name], data)
        with zipfile.ZipFile(archive) as source:
            self.assertNotIn('LICENSE', source.namelist())
            self.assertNotIn('THIRD_PARTY_NOTICES.md', source.namelist())
            self.assertIn('main.chaos', source.namelist())

    def test_wheel_only_packaging_rejects_missing_declared_license_even_with_valid_record(self):
        for name in TERMS:
            with self.subTest(name=name):
                source = licensed_wheel(self.root, lambda files: files.pop(PREFIX + name))
                with self.assertRaisesRegex(PackageError, 'missing declared license file'):
                    build_portable(source, self.root / 'never.zip')
                self.assertFalse((self.root / 'never.zip').exists())

    def test_custom_license_requires_terms_even_if_metadata_declaration_removed(self):
        def mutate(files):
            metadata = next(name for name in files if name.endswith('/METADATA'))
            files[metadata] = b'\n'.join(line for line in files[metadata].split(b'\n')
                                        if not line.startswith(b'License-File:'))
            files.pop(PREFIX + 'LICENSE')
        with self.assertRaisesRegex(PackageError, 'missing Chaos license document'):
            build_portable(licensed_wheel(self.root, mutate), self.root / 'never.zip')

    def test_legacy_license_file_metadata_remains_readable(self):
        def mutate(files):
            metadata = next(name for name in files if name.endswith('/METADATA'))
            files[metadata] = b'\n'.join(line for line in files[metadata].split(b'\n')
                                        if not line.startswith((b'License-File:', b'License-Expression:')))
            files[metadata] += b'License-File: LICENSE\n'
            files[PREFIX.replace('/licenses/', '/') + 'LICENSE'] = files.pop(PREFIX + 'LICENSE')
        archive = self.root / 'legacy.zip'
        build_portable(licensed_wheel(self.root, mutate), archive)
        self.assertTrue(archive.exists())


if __name__ == '__main__':
    unittest.main()
