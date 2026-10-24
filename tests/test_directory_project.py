"""Local projects use the real Framework and original CAS Workspace protocol."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos.project import Project,ProjectError,DirectoryIDE,LocalProjectFacade,discover,init_project,main,project_files,working_directory
from chaos.workspace import WorkspaceConflict,META
from chaos.language import LanguageError


class DirectoryProjectTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)/'project'
        # Tests have no model credentials or inherited provider routing.
        self.environment=patch.dict(os.environ,{name:''for name in('CHAOS_MODEL_URL','CHAOS_MODEL_KEY','CHAOS_MODEL_NAME')});self.environment.start();self.addCleanup(self.environment.stop)
    def create(self):init_project(self.root);return self.root
    def source(self,text):self.root.joinpath('main.chaos').write_text(text)
    def save(self,ide,text):
        project=ide.app.get_project('workspace');project.update(source=text,files={'main.chaos':text},entry='main.chaos')
        return ide.app.save_project(project,from_view='source')
    def wait_for(self,predicate,timeout=4):
        deadline=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>=deadline:self.fail('project synchronization did not reach the expected state')
            time.sleep(.02)

    def test_init_preserves_nonempty_host_and_uses_exact_packaged_launcher(self):
        self.root.mkdir(parents=True);self.root.joinpath('host.py').write_text('untouched')
        templates=project_files();result=init_project(self.root)
        self.assertEqual(result['entry'],'main.chaos');self.assertEqual(self.root.joinpath('host.py').read_text(),'untouched')
        for name,data in templates.items():self.assertEqual(self.root.joinpath(name).read_bytes(),data)
        self.assertEqual(self.root.joinpath('main.chaos').read_text(),'42\n')
        before={p.name:p.read_bytes()for p in self.root.iterdir()}
        with self.assertRaises(ProjectError):init_project(self.root)
        self.assertEqual({p.name:p.read_bytes()for p in self.root.iterdir()},before)

    def test_init_preflights_all_collisions_and_never_overwrites_symlinks(self):
        for collision in project_files():
            target=self.root/collision;self.root.mkdir(parents=True,exist_ok=True);target.write_text('original')
            with self.assertRaises(ProjectError):init_project(self.root)
            self.assertEqual(list(self.root.iterdir()),[target]);self.assertEqual(target.read_text(),'original');target.unlink()
        target=self.root/'main.chaos';outside=self.root.parent/'outside';outside.write_text('private');target.symlink_to(outside)
        with self.assertRaises(ProjectError):init_project(self.root)
        self.assertTrue(target.is_symlink());self.assertEqual(outside.read_text(),'private')

    def test_marker_entry_paths_and_nearest_discovery_are_confined(self):
        self.create();nested=self.root/'src';nested.mkdir();nested.joinpath('entry.chaos').write_text('42')
        self.root.joinpath('chaos.project.json').write_text(json.dumps({'schema':'chaos.project/1','entry':'src/entry.chaos'}))
        with working_directory(nested):self.assertEqual(discover().root,self.root.resolve())
        self.assertEqual(Project(self.root).entry,'src/entry.chaos')
        for entry in('../outside.chaos','/outside.chaos','std/math.chaos','.chaos/runtime/main.chaos','src/../entry.chaos'):
            self.root.joinpath('chaos.project.json').write_text(json.dumps({'schema':'chaos.project/1','entry':entry}))
            with self.assertRaises((ProjectError,LanguageError)):Project(self.root)
        self.root.joinpath('chaos.project.json').write_text('{"schema":"chaos.project/1","entry":"main.chaos","entry":"other.chaos"}')
        with self.assertRaises(ProjectError):Project(self.root)

    def test_real_Framework_push_and_IDE_pull_share_original_workspace_versions(self):
        self.create();self.source('40+2')
        with DirectoryIDE(self.root)as ide:
            self.assertEqual(ide.app.get_project('workspace')['source'],'40+2')
            self.assertEqual(ide.workspace.metadata['server'],'local')
            self.assertEqual(ide.app.start('workspace',ide.workspace.metadata['version'],None,mode='source').result(5),42)
            self.source('20+23');pushed=ide.sync();self.assertEqual(pushed['status'],'pushed')
            self.assertEqual(ide.app.get_project('workspace')['source'],'20+23')
            saved=self.save(ide,'21+23');pulled=ide.sync();self.assertEqual(pulled,{'status':'pulled','version':saved['version']})
            self.assertEqual(self.root.joinpath('main.chaos').read_text(),'21+23')
            self.assertEqual(ide.workspace.metadata['files'],{'main.chaos':'21+23'})
            self.assertIsInstance(ide.workspace.client,LocalProjectFacade)

    def test_real_conflict_retains_both_sources_and_original_recovery_evidence(self):
        self.create()
        with DirectoryIDE(self.root)as ide:
            self.source('41');self.save(ide,'43')
            with self.assertRaises(WorkspaceConflict):ide.sync()
            self.assertEqual(self.root.joinpath('main.chaos').read_text(),'41')
            self.assertEqual(ide.app.get_project('workspace')['source'],'43')
            backup=json.loads(self.root.joinpath('.chaos-conflict.json').read_text())
            self.assertEqual(backup['remote']['source'],'43');self.assertEqual(backup['base']['server'],'local')
            ide.start_sync(.1);self.wait_for(lambda:ide.sync_error is not None)
            self.assertTrue(ide.stop.is_set());ide.watcher.join(2);self.assertFalse(ide.watcher.is_alive())

    def test_transient_syntax_waits_then_recovers_without_publishing_bad_source(self):
        self.create();output=io.StringIO();errors=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors),DirectoryIDE(self.root)as ide:
            self.source('let broken=');ide.start_sync(.1)
            self.wait_for(lambda:'project.sync_waiting'in errors.getvalue())
            before=ide.app.get_project('workspace');self.assertEqual(before['source'],'42\n')
            time.sleep(.25);self.assertEqual(errors.getvalue().count('project.sync_waiting'),1)
            self.source('40+2');self.wait_for(lambda:ide.app.get_project('workspace')['source']=='40+2'and'project.synced'in output.getvalue())
            self.assertIsNone(ide.sync_error);self.assertFalse(ide.stop.is_set())
            self.assertIn('project.synced',output.getvalue())
        self.assertFalse(ide.watcher.is_alive());self.assertTrue(ide.app is None)

    def test_first_invalid_source_creates_no_database_and_edit_can_retry(self):
        self.create();self.source('let unfinished=')
        with self.assertRaises(LanguageError):DirectoryIDE(self.root).open()
        self.assertFalse(self.root.joinpath('.chaos/project.sqlite3').exists())
        self.assertFalse(self.root.joinpath(META).exists())
        self.source('40+2')
        with DirectoryIDE(self.root)as ide:
            self.assertEqual(ide.app.get_project('workspace')['source'],'40+2')
            self.assertEqual(ide.app.get_project('workspace')['name'],self.root.name)

    def test_existing_database_without_metadata_is_rejected_without_overwrite_and_lock_releases(self):
        self.create()
        with DirectoryIDE(self.root):pass
        metadata=self.root/META;original=metadata.read_bytes();metadata.unlink();database=self.root/'.chaos/project.sqlite3';before=database.read_bytes()
        with self.assertRaises(ProjectError):DirectoryIDE(self.root).open()
        self.assertEqual(database.read_bytes(),before);metadata.write_bytes(original)
        with DirectoryIDE(self.root)as ide:self.assertEqual(ide.workspace.status()['server'],'local')

    def test_IDE_exclusive_lock_failure_cleanup_and_relocation_keep_local_identity(self):
        self.create()
        with DirectoryIDE(self.root)as ide:
            self.assertEqual(ide.app._engine.settings.runtime()['workspace'],'.chaos/tasks')
            settings=ide.app._engine.settings
            settings.update({'runtime':{'maxSteps':13}},settings.public()['version'])
            self.assertEqual(json.loads(self.root.joinpath('.chaos/settings.json').read_text())['runtime']['workspace'],'.chaos/tasks')
            with self.assertRaises(ProjectError):DirectoryIDE(self.root).open()
        moved=self.root.parent/'moved';shutil.move(str(self.root),moved)
        with DirectoryIDE(moved)as ide:
            self.assertEqual(ide.workspace.metadata['server'],'local')
            self.assertEqual(ide.workspace.directory,moved.resolve());self.assertEqual(ide.sync()['status'],'unchanged')
            with working_directory(moved):self.assertEqual(Path(ide.app._engine.settings.resolve()['workspace']).resolve(),moved.resolve()/'.chaos/tasks')
            self.assertFalse(self.root.exists())
        with DirectoryIDE(moved):pass

    def test_project_cli_default_entry_imports_config_isolation_and_cwd_restore(self):
        self.create();self.source('import "lib.chaos" as L;L.answer()');self.root.joinpath('lib.chaos').write_text('fn answer(){42};export{answer}')
        foreign=self.root.parent/'foreign';foreign.mkdir();original=Path.cwd();previous=os.environ.get('CHAOS_CONFIG');output=io.StringIO()
        with working_directory(foreign),contextlib.redirect_stdout(output):
            self.assertEqual(main(['check'],root=self.root),0)
            self.assertEqual(main(['run'],root=self.root),0)
            self.assertEqual(Path.cwd(),foreign.resolve())
        self.assertEqual(Path.cwd(),original);self.assertEqual(os.environ.get('CHAOS_CONFIG'),previous)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]),42)
        self.assertFalse(self.root.joinpath('.chaos/project.sqlite3').exists())
        self.assertFalse(self.root.joinpath('.chaos/cli.sqlite3').exists())
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['run','--persistent'],root=self.root),0)
        self.assertTrue(self.root.joinpath('.chaos/cli.sqlite3').is_file());self.assertFalse(self.root.joinpath('.chaos/project.sqlite3').exists())

    def test_entry_marker_change_stops_before_overwriting_disk_or_remote(self):
        self.create()
        with DirectoryIDE(self.root)as ide:
            self.root.joinpath('other.chaos').write_text('43')
            self.root.joinpath('chaos.project.json').write_text(json.dumps({'schema':'chaos.project/1','entry':'other.chaos'}))
            with self.assertRaises(ProjectError):ide.sync()
            self.assertEqual(ide.app.get_project('workspace')['source'],'42\n')
            self.assertEqual(self.root.joinpath('main.chaos').read_text(),'42\n')
