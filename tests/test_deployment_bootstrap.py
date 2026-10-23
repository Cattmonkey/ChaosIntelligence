"""Dependency transaction and old-bootstrap compatibility regression."""
import contextlib, io, json
from pathlib import Path
import subprocess, tempfile, unittest
from unittest.mock import patch
from chaos.project import init_project, main, Project
from chaos.project_launcher import setup
from chaos.project_environment import install, replace, rollback
from test_project_environment import make_package

class DeploymentBootstrapTests(unittest.TestCase):
    def test_new_bootstrap_keeps_setup_for_old_runtime(self):
        import sys
        from chaos import project_launcher as launcher
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.chaos/runtime').mkdir(parents=True)
            with patch.object(sys,'argv',['chaos.py','setup']), patch.object(launcher,'setup',return_value=0) as run, patch.object(sys,'path',list(sys.path)):
                self.assertEqual(launcher._launch(root),0)
                run.assert_called_once_with(root)

    def test_settings_write_without_fchmod(self):
        import os
        from chaos.settings import Settings
        with tempfile.TemporaryDirectory() as folder:
            original=getattr(os,'fchmod',None)
            if original is not None:del os.fchmod
            try:
                path=Path(folder)/'settings.json'
                settings=Settings(path)
                settings.update({'runtime':{'workspace':'.chaos/tasks'}}, version=settings.public()['version'])
                self.assertEqual(json.loads(path.read_text())['runtime']['workspace'],'.chaos/tasks')
            finally:
                if original is not None:os.fchmod=original

    def test_failed_smoke_preserves_old_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'.chaos';runtime=state/'runtime';runtime.mkdir(parents=True)
            (runtime/'requirements.txt').write_text('packaging>=23\n')
            deps=state/'deps';deps.mkdir();(deps/'keep').write_text('before')
            def pip(argv,**kwargs):
                Path(argv[argv.index('--target')+1]).mkdir()
                return subprocess.CompletedProcess(argv,0)
            def reject(payload):
                self.assertTrue(payload.is_dir());raise ValueError('actual smoke failed')
            with patch('chaos.project_launcher.subprocess.run',side_effect=pip):
                with self.assertRaisesRegex(ValueError,'actual smoke failed'):setup(root,validator=reject)
            self.assertEqual((deps/'keep').read_text(),'before')
            self.assertFalse((deps/'.chaos-dependencies.json').exists())
            self.assertFalse((state/'deps.previous').exists())
            self.assertFalse(list(state.glob('deps-stage-*')))

    def test_old_configure_refuses_before_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);init_project(root)
            before={p.name:p.read_bytes() for p in root.iterdir()}
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main(['configure','--yes'],root=root,environment_locked=True),1)
            self.assertIn('setup --yes',errors.getvalue())
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})

    def test_replace_and_rollback_preserve_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=make_package(root/'old');new=make_package(root/'new','0.80.0')
            from chaos.portable import extract_portable
            runtime=root/'runtime';extract_portable(old,runtime)
            host=root/'host';host.mkdir();install(runtime,host)
            saved=host/'.chaos/deployment.json'
            saved.write_text(json.dumps({'schema':'chaos.deployment/1','preferences':{'profile':'core'}}))
            before=saved.read_bytes()
            replace(Project(host),new);self.assertEqual(saved.read_bytes(),before)
            rollback(Project(host));self.assertEqual(saved.read_bytes(),before)
