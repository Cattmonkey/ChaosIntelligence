import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chaos import __version__
from chaos.execution import CodeExecutor
from chaos.resources import PACKAGE_ROOT


class PortableExecutionTests(unittest.TestCase):
    def test_temporary_python_script_uses_original_runtime_and_parent_environment_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);decoy = root / 'decoy/chaos';decoy.mkdir(parents=True);(decoy / '__init__.py').write_text('raise RuntimeError("foreign runtime")')
            with patch.dict(os.environ, {'PYTHONPATH':str(decoy.parent)}):
                before = dict(os.environ)
                result = CodeExecutor(root / 'tasks').run({'language':'python','code':'import chaos,json;from pathlib import Path;print(json.dumps([chaos.__version__,str(Path(chaos.__file__).resolve().parent),chaos.Framework.evaluate("40+2")]))'})
                self.assertEqual(result['exitCode'], 0, result);self.assertEqual(json.loads(result['stdout']), [__version__, str(PACKAGE_ROOT), 42])
                self.assertEqual(dict(os.environ), before)

    def test_shell_execution_keeps_original_environment_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'CHAOS_PORTABLE_FIXTURE':'original'}):
                result = CodeExecutor(folder).run({'language':'shell','code':'printf "$CHAOS_PORTABLE_FIXTURE"'})
                self.assertEqual(result['exitCode'], 0);self.assertEqual(result['stdout'], 'original')
