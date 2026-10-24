"""Native commands preserve the existing bootstrap's execution contract."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import chaos
from chaos.launchers import native_launchers
from chaos.project import init_project
from test_project_environment import make_package


@unittest.skipIf(os.name == 'nt', 'POSIX execution; Windows command needs a Windows host')
class NativeCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {**os.environ, 'CHAOS_PYTHON': sys.executable,
                    'PYTHONPATH': str(Path(chaos.__file__).resolve().parent.parent)}
        for key in ('CHAOS_MODEL_URL', 'CHAOS_MODEL_KEY', 'CHAOS_MODEL_NAME'):
            self.env[key] = ''

    def test_initialized_native_project_runs_expression_after_relocation(self):
        initial = self.root / 'initial'; init_project(initial)
        moved = self.root / '移动项目 with spaces'; shutil.move(str(initial), moved)
        self.assertTrue((moved / 'chaos').stat().st_mode & stat.S_IXUSR)
        result = subprocess.run([str(moved / 'chaos'), '-c', '40 + 2'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '42')

    def test_wrapper_preserves_arguments_stdin_cwd_and_failure_status(self):
        folder = self.root / 'command path'; folder.mkdir()
        (folder / 'chaos.py').write_text('import json, os, sys\n'
            'print(json.dumps([sys.argv[1:], os.getcwd(), sys.stdin.read()]))\n'
            'raise SystemExit(23)\n')
        (folder / 'chaos').write_bytes(native_launchers()['chaos']); (folder / 'chaos').chmod(0o755)
        # Explicit interpreter paths can themselves contain spaces.
        interpreter = self.root / 'python runtime'; interpreter.symlink_to(sys.executable)
        arguments = ['a b', '"quotes"', '$HOME; never execute', '', '中文']
        result = subprocess.run([str(folder / 'chaos'), *arguments], cwd=self.root,
            env={**self.env, 'CHAOS_PYTHON': str(interpreter)}, input='original stdin\n',
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout), [arguments, str(self.root.resolve()), 'original stdin\n'])

    def test_discovery_rejects_old_interpreter_then_uses_python(self):
        folder = self.root / 'commands'; folder.mkdir()
        (folder / 'python3').write_text('#!/bin/sh\nexit 1\n'); (folder / 'python3').chmod(0o755)
        (folder / 'python').symlink_to(sys.executable)
        for utility in ('dirname',): (folder / utility).symlink_to(shutil.which(utility))
        project = self.root / 'project'; init_project(project)
        result = subprocess.run([str(project / 'chaos'), '-c', '21 * 2'], cwd=self.root,
            env={**self.env, 'PATH': str(folder), 'CHAOS_PYTHON': ''}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual(result.stdout.strip(), '42')
        (folder / 'python').unlink()
        missing = subprocess.run([str(project / 'chaos'), '-c', '42'], cwd=self.root,
            env={**self.env, 'PATH': str(folder), 'CHAOS_PYTHON': ''}, capture_output=True, text=True)
        self.assertEqual(missing.returncode, 127); self.assertIn('Python 3.9+', missing.stderr)

    def test_invalid_explicit_interpreter_fails_without_fallback(self):
        project = self.root / 'project'; init_project(project)
        result = subprocess.run([str(project / 'chaos'), '-c', '42'],
            env={**self.env, 'CHAOS_PYTHON': str(self.root / 'missing python')},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 127); self.assertIn('CHAOS_PYTHON', result.stderr)

    def test_permissions_failure_rolls_back_every_generated_file(self):
        project = self.root / 'project'; project.mkdir(); (project / 'host.txt').write_text('keep')
        with patch('chaos.project.os.fchmod', side_effect=OSError('permission denied')):
            with self.assertRaises(OSError): init_project(project)
        self.assertEqual([p.name for p in project.iterdir()], ['host.txt'])


class NativeCommandPackageTests(unittest.TestCase):
    def test_archive_marks_only_posix_command_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            package = make_package(Path(folder), project=True)
            with zipfile.ZipFile(package) as archive:
                self.assertEqual(archive.read('chaos'), native_launchers()['chaos'])
                self.assertEqual(archive.read('chaos.cmd'), native_launchers()['chaos.cmd'])
                self.assertEqual(archive.getinfo('chaos').create_system, 3)
                self.assertEqual(stat.S_IMODE(archive.getinfo('chaos').external_attr >> 16), 0o755)
                self.assertEqual(stat.S_IMODE(archive.getinfo('chaos.cmd').external_attr >> 16), 0o644)
                self.assertNotIn('.chaos/settings.json', archive.namelist())


if __name__ == '__main__':
    unittest.main()
