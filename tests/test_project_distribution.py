import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from chaos.portable import build_project, PackageError, _manifest
from chaos.project_launcher import setup
from test_portable_package import wheel


class ProjectDistributionTests(unittest.TestCase):
    def test_project_wraps_verified_runtime_without_mixing_user_files(self):
        import base64
        import csv
        import hashlib
        import io
        import chaos

        def extend(files):
            for name in ('project.py', 'project_launcher.py', 'project_environment.py'):
                files['chaos/' + name] = Path(chaos.__file__).with_name(name).read_bytes()
            files['chaos/docs/PROJECT_README.md'] = (Path(chaos.__file__).parent.parent / 'docs/PROJECT_README.md').read_bytes()
            record = next(n for n in files if n.endswith('/RECORD'))
            rows = [[n, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(), str(len(data))]
                    for n, data in files.items() if n != record]
            rows.append([record, '', ''])
            out = io.StringIO(); csv.writer(out).writerows(rows)
            files[record] = out.getvalue().encode()

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'project.zip'
            build_project(wheel(root, extend), archive)
            with zipfile.ZipFile(archive) as z:
                runtime = {n[len('.chaos/runtime/'):]: z.read(n) for n in z.namelist() if n.startswith('.chaos/runtime/')}
                self.assertEqual(_manifest(runtime)['version'], '0.79.0')
                self.assertEqual(json.loads(z.read('chaos.project.json'))['entry'], 'main.chaos')
                self.assertEqual(z.read('chaos.py'), runtime['chaos/project_launcher.py'])
                self.assertEqual(z.read('README.md'), runtime['chaos/docs/PROJECT_README.md'])
                self.assertNotIn('main.chaos', runtime)
                self.assertNotIn('.chaos/settings.json', z.namelist())
            previous = archive.read_bytes()
            with self.assertRaises(PackageError):
                build_project(root / 'input.whl', archive)
            self.assertEqual(archive.read_bytes(), previous)

    def test_failed_setup_preserves_existing_dependencies_and_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); runtime = root / '.chaos/runtime'; runtime.mkdir(parents=True)
            (runtime / 'requirements.txt').write_text('example>=1\n')
            deps = root / '.chaos/deps'; deps.mkdir(); (deps / 'previous').write_text('keep')
            with patch('chaos.project_launcher.subprocess.run', return_value=subprocess.CompletedProcess([], 7)) as run:
                self.assertEqual(setup(root), 7)
            self.assertIn('--target', run.call_args.args[0])
            self.assertEqual((deps / 'previous').read_text(), 'keep')
            self.assertEqual((runtime / 'requirements.txt').read_text(), 'example>=1\n')
            self.assertFalse((root / '.chaos/deps.previous').exists())

    def test_successful_setup_replaces_managed_dependencies_as_one_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); runtime = root / '.chaos/runtime'; runtime.mkdir(parents=True)
            (runtime / 'requirements.txt').write_text('example>=1\n')
            deps = root / '.chaos/deps'; deps.mkdir(); (deps / 'obsolete').write_text('old')
            def install(argv):
                target = Path(argv[argv.index('--target') + 1]); target.mkdir()
                (target / 'current').write_text('new')
                return subprocess.CompletedProcess(argv, 0)
            with patch('chaos.project_launcher.subprocess.run', side_effect=install):
                self.assertEqual(setup(root), 0)
            self.assertEqual((deps / 'current').read_text(), 'new')
            self.assertFalse((deps / 'obsolete').exists())
            self.assertFalse((root / '.chaos/deps.previous').exists())


if __name__ == '__main__':
    unittest.main()
