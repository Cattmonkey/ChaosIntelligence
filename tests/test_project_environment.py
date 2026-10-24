import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import chaos
from chaos.portable import build_portable, build_project, extract_portable, PackageError, _manifest
from chaos.project import Project, project_lock
from chaos.project_environment import install, replace, rollback, status, _read_runtime
from chaos.project_launcher import environment, selection, environment_lock, dependency_id, dependencies_ready, setup
from test_portable_package import wheel


def make_package(root, version='0.79.0', *, requirement='>=3.9', broken=False, project=False, bootstrap=None):
    root.mkdir(parents=True, exist_ok=True)
    def extend(files):
        for name in ('project.py', 'project_launcher.py', 'project_environment.py', 'launchers.py', 'portable.py', 'updater.py'):
            files['chaos/' + name] = Path(chaos.__file__).with_name(name).read_bytes()
        files['chaos/docs/PROJECT_README.md'] = (Path(chaos.__file__).parent.parent / 'docs/PROJECT_README.md').read_bytes()
        if bootstrap is not None:files['chaos/project_launcher.py'] = bootstrap
        files['chaos/__init__.py'] = (b'raise RuntimeError("broken fixture")\n' if broken else
            b'class Framework:\n    @staticmethod\n    def evaluate(source): return 42\n')
        files['chaos/version.py'] = ('__version__ = ' + repr(version) + '\n').encode()
        old = 'chaos_intelligence-0.79.0.dist-info';new = 'chaos_intelligence-' + version + '.dist-info'
        for key in list(files):
            if key.startswith(old):files[new + key[len(old):]] = files.pop(key)
        meta = new + '/METADATA'
        files[meta] = files[meta].replace(b'Version: 0.79.0', ('Version: ' + version).encode()).replace(b'>=3.9', requirement.encode())
        record = new + '/RECORD'
        rows = [[n, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(), str(len(data))]
                for n, data in files.items() if n != record]
        rows.append([record, '', '']);out = io.StringIO();csv.writer(out).writerows(rows)
        files[record] = out.getvalue().encode()
    target = root / 'package.zip'
    (build_project if project else build_portable)(wheel(root, extend), target)
    return target


class ProjectEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.host = self.root / '中文 host'
        self.old = make_package(self.root / 'old');self.new = make_package(self.root / 'new', '0.80.0')
        self.runtime = self.root / 'portable';extract_portable(self.old, self.runtime)
        self.host.mkdir();(self.host / 'host.txt').write_text('host application')
        install(self.runtime, self.host);self.project = Project(self.host)
        for name, data in {'main.chaos':'41+1', '.chaos/settings.json':'{"private":"local-only"}',
                           '.chaos/project.sqlite3':'database sentinel', '.chaos/tasks/keep':'task data'}.items():
            p = self.host / name;p.parent.mkdir(exist_ok=True, parents=True);p.write_text(data)
        self.saved = {n:(self.host/n).read_bytes() for n in ('main.chaos','chaos.project.json','chaos.py','chaos','chaos.cmd','host.txt',
                            '.chaos/settings.json','.chaos/project.sqlite3','.chaos/tasks/keep')}

    def assert_user_data(self):
        self.assertEqual({n:(self.host/n).read_bytes() for n in self.saved}, self.saved)

    def ready_deps(self):
        env = environment(self.host);deps = env/'deps';deps.mkdir()
        (deps/'package.py').write_text('dependency sentinel')
        (deps/'.chaos-dependencies.json').write_text(json.dumps({'identity':dependency_id(env/'runtime')}))
        return deps

    def test_install_and_two_replacements_preserve_host_and_remove_obsolete_code(self):
        (environment(self.host)/'runtime/chaos/obsolete.py').write_text('old only')
        self.ready_deps()
        first = replace(self.project, self.new);first_env = environment(self.host)
        self.assertEqual(first['version'],'0.80.0');self.assertTrue(first['dependenciesReady'])
        self.assertFalse((first_env/'runtime/chaos/obsolete.py').exists())
        self.assertEqual((first_env/'deps/package.py').read_text(),'dependency sentinel')
        third = make_package(self.root/'third','0.81.0');replace(self.project,third)
        fourth = make_package(self.root/'fourth','0.82.0');replace(self.project,fourth)
        self.assertFalse(first_env.exists())
        self.assertEqual(len(list((self.host/'.chaos/environments').iterdir())),2)
        self.assertTrue((self.host/'.chaos/runtime').exists())
        self.assert_user_data()

    def test_rollback_recovers_matching_dependencies(self):
        self.ready_deps();replace(self.project,self.new)
        (environment(self.host)/'deps/package.py').write_text('new environment only')
        result=rollback(self.project)
        self.assertEqual(result['version'],'0.79.0')
        self.assertEqual((environment(self.host)/'deps/package.py').read_text(),'dependency sentinel')
        self.assertTrue(result['dependenciesReady']);self.assert_user_data()

    def test_project_zip_and_relocation(self):
        package=make_package(self.root/'project-new','0.80.0',project=True)
        replace(self.project,package)
        moved=self.root/'moved 项目';shutil.move(str(self.host),moved)
        self.assertEqual(status(moved)['version'],'0.80.0')
        result=subprocess.run([sys.executable,'-I','-S',str(moved/'chaos.py'),'--version'],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'0.80.0')
        if os.name != 'nt':
            native=subprocess.run([str(moved/'chaos'),'--version'],cwd=self.root,env={**os.environ,'CHAOS_PYTHON':sys.executable},capture_output=True,text=True)
            self.assertEqual(native.returncode,0,native.stderr);self.assertEqual(native.stdout.strip(),'0.80.0')

    def test_damaged_payload_preserves_pointer_runtime_and_user_data(self):
        data=_read_runtime(self.new);data['chaos/version.py']+=b'# damaged'
        damaged=self.root/'bad.zip'
        with zipfile.ZipFile(damaged,'w')as archive:
            for n,v in data.items():archive.writestr(n,v)
        with self.assertRaises(PackageError):replace(self.project,damaged)
        self.assertEqual(selection(self.host)['current'],'base');self.assert_user_data()

    def test_failed_candidate_and_incompatible_python_preserve_active_environment(self):
        for name, kwargs in [('broken',{'broken':True}),('python',{'requirement':'>=100'})]:
            package=make_package(self.root/name,'0.80.0',**kwargs)
            with self.assertRaises(PackageError):replace(self.project,package)
            self.assertEqual(selection(self.host)['current'],'base')
            self.assertEqual(list((self.host/'.chaos/environments').iterdir()),[])
            self.assert_user_data()

    def test_atomic_pointer_failure_retains_active_environment(self):
        with patch('chaos.project_environment._select',side_effect=OSError('injected commit failure')):
            with self.assertRaises(OSError):replace(self.project,self.new)
        self.assertEqual(selection(self.host)['current'],'base')
        self.assertEqual(list((self.host/'.chaos/environments').iterdir()),[]);self.assert_user_data()

    def test_live_process_or_legacy_ide_blocks_replacement(self):
        with environment_lock(self.host):
            with self.assertRaisesRegex(ValueError,'in use'):replace(self.project,self.new)
        with project_lock(self.project):
            with self.assertRaisesRegex(ValueError,'IDE'):replace(self.project,self.new)
        self.assertEqual(selection(self.host)['current'],'base');self.assert_user_data()

    def test_failed_setup_keeps_selected_and_previous_dependencies(self):
        self.ready_deps();replace(self.project,self.new)
        with patch('chaos.project_launcher.subprocess.run',return_value=subprocess.CompletedProcess([],7)):
            self.assertEqual(setup(self.host),7)
        self.assertEqual((environment(self.host)/'deps/package.py').read_text(),'dependency sentinel')
        rollback(self.project);self.assertTrue(dependencies_ready(environment(self.host)))
        self.assert_user_data()

    def test_unknown_or_platform_mismatched_dependencies_are_not_inherited(self):
        deps=self.ready_deps();(deps/'.chaos-dependencies.json').write_text('{"identity":"different platform"}')
        result=replace(self.project,self.new)
        self.assertFalse(result['dependenciesReady']);self.assertFalse((environment(self.host)/'deps').exists())
        self.assertEqual(result['next'],'python chaos.py setup');self.assert_user_data()

    def test_dependency_symlinks_fail_without_following_them(self):
        deps=self.ready_deps();(deps/'outside').symlink_to(self.root)
        with self.assertRaises(PackageError):replace(self.project,self.new)
        self.assertEqual(selection(self.host)['current'],'base');self.assert_user_data()

    def test_install_conflicts_preserve_existing_files(self):
        for name in ('main.chaos','chaos.py','chaos','chaos.cmd','.chaos'):
            target=self.root/('conflict-'+name.replace('.','_'));target.mkdir()
            (target/name).write_text('owned')
            with self.assertRaises((PackageError,ValueError)):install(self.runtime,target)
            self.assertEqual((target/name).read_text(),'owned')
            self.assertEqual(len(list(target.iterdir())),1)

    def test_install_migrates_original_bootstrap_but_preserves_customized_entry(self):
        new_runtime=self.root/'new-runtime';extract_portable(self.new,new_runtime)
        install(new_runtime,self.host);self.assertEqual(status(self.host)['version'],'0.80.0');self.assert_user_data()
        (self.host/'chaos.py').write_text('# customized')
        with self.assertRaisesRegex(PackageError,'customized'):install(new_runtime,self.host)
        self.assertEqual((self.host/'chaos.py').read_text(),'# customized')

    def test_future_bootstrap_protocol_is_rejected_before_switch(self):
        bootstrap=(self.host/'chaos.py').read_bytes().replace(b"chaos.environment/1",b"chaos.environment/2")
        package=make_package(self.root/'future-protocol','0.80.0',bootstrap=bootstrap)
        with self.assertRaises(PackageError):replace(self.project,package)
        self.assertEqual(selection(self.host)['current'],'base');self.assert_user_data()

    def test_stable_bootstrap_survives_changed_runtime_launchers_and_pruning(self):
        original=(self.host/'chaos.py').read_bytes()
        customized=original+b'# supported project customization\n'
        (self.host/'chaos.py').write_bytes(customized)
        for version in ('0.80.0','0.81.0','0.82.0'):
            package=make_package(self.root/('future-'+version),version,bootstrap=original+('# version '+version).encode())
            replace(self.project,package)
        self.assertEqual((self.host/'chaos.py').read_bytes(),customized)
        source=self.root/'latest';extract_portable(package,source)
        install(source,self.host)
        self.assertEqual((self.host/'chaos.py').read_bytes(),customized)
        self.assertEqual(status(self.host)['version'],'0.82.0')

    def test_legacy_migration_rollback_and_reinstall_do_not_overwrite_user_data(self):
        legacy_launcher=b'# legacy official launcher\n'
        legacy=make_package(self.root/'legacy','0.78.0',bootstrap=legacy_launcher)
        target=self.root/'legacy-host';(target/'.chaos').mkdir(parents=True)
        extract_portable(legacy,target/'.chaos/runtime')
        (target/'chaos.py').write_bytes(legacy_launcher)
        (target/'chaos.project.json').write_text('{"schema":"chaos.project/1","entry":"main.chaos"}')
        (target/'main.chaos').write_text('40+2')
        install(self.runtime,target)
        self.assertEqual(status(target)['version'],'0.79.0')
        rollback(Project(target));self.assertEqual(status(target)['version'],'0.78.0')
        install(self.runtime,target)
        self.assertEqual(status(target)['version'],'0.79.0')
        self.assertEqual((target/'main.chaos').read_text(),'40+2')
        self.assertFalse((target/'chaos').exists());self.assertFalse((target/'chaos.cmd').exists())

    def test_future_readme_and_cli_launcher_are_integrity_checked_not_template_locked(self):
        files=_read_runtime(self.new);files['README.md']=b'Future documentation'
        files['run_chaos.py']+=b'# future CLI bootstrap\n'
        manifest=json.loads(files['manifest.json'])
        for name in ('README.md','run_chaos.py'):
            manifest['files'][name]={'sha256':hashlib.sha256(files[name]).hexdigest(),'size':len(files[name])}
        files['manifest.json']=json.dumps(manifest).encode()
        self.assertEqual(_manifest(files)['version'],'0.80.0')
        files['run_chaos.py']+=b'broken checksum'
        with self.assertRaises(PackageError):_manifest(files)

    def test_invalid_pointer_and_symlink_state_fail_closed(self):
        pointer=self.host/'.chaos/environment.json'
        for value in ({'schema':'chaos.environment/1','current':'../outside','previous':None},
                      {'schema':'wrong','current':'base','previous':None}):
            pointer.write_text(json.dumps(value))
            with self.assertRaises(ValueError):environment(self.host)
        pointer.unlink();pointer.symlink_to(self.host/'host.txt')
        with self.assertRaises(ValueError):environment(self.host)
        pointer.unlink()
        target=self.root/'symlink-host';target.mkdir();(target/'.chaos').symlink_to(self.host/'.chaos')
        with self.assertRaises(ValueError):environment(target)

    def test_downgrade_is_rejected_and_rollback_requires_previous(self):
        with self.assertRaises(PackageError):rollback(self.project)
        replace(self.project,self.new)
        with self.assertRaisesRegex(PackageError,'older'):replace(self.project,self.old)
        self.assertEqual(status(self.host)['version'],'0.80.0');self.assert_user_data()


if __name__=='__main__':unittest.main()
