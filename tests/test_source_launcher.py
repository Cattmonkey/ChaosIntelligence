"""The source checkout entry stays usable without installation or project state."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from chaos.release import copy_source

ROOT = Path(__file__).resolve().parents[1]


class SourceLauncherTests(unittest.TestCase):
    def invoke(self, launcher, cwd, *args, input=None):
        return subprocess.run([sys.executable, "-I", "-S", str(launcher), *args],
                              cwd=cwd, input=input, capture_output=True, text=True, timeout=20)

    def test_source_snapshot_retains_working_entry_and_caller_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            checkout = base / "source checkout"
            copy_source(ROOT, checkout)
            working = base / "user project"
            working.mkdir()
            (working / "program.chaos").write_text("40 + 2", encoding="utf-8")
            launcher = checkout / "chaos.py"
            for args, stdin in ((("-c", "40 + 2"), None),
                                (("run", "program.chaos"), None),
                                (("run", "-"), "40 + 2")):
                with self.subTest(args=args):
                    result = self.invoke(launcher, working, *args, input=stdin)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), 42)
            failed = self.invoke(launcher, working, "-c", "let =")
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("error", failed.stderr.lower())
            self.assertEqual(sorted(p.name for p in working.iterdir()), ["program.chaos"])

    def test_native_source_entry_uses_shared_launcher_and_preserves_arguments(self):
        import os
        from chaos.launchers import native_launchers
        for name, contents in native_launchers("../chaos.py").items():
            self.assertEqual((ROOT / "bin" / name).read_bytes(), contents)
        if os.name == "nt":
            self.skipTest("POSIX wrapper execution requires POSIX; cmd is checked separately")
        with tempfile.TemporaryDirectory(prefix="chaos native ") as directory:
            env = dict(os.environ, CHAOS_PYTHON=sys.executable)
            command = [str(ROOT / "bin/chaos"), "-c", '\"hello world\"']
            result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                                    text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), "hello world")
            self.assertEqual(list(Path(directory).iterdir()), [])
