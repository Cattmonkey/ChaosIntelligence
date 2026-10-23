"""Portable lifecycle contracts; injected OS observers never prove native service use.

Actual launchd persistence is a separate, bounded external acceptance probe.
No test here invokes launchctl/systemctl or installs a user OS task.
"""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError, replace
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chaos import ServiceError, ServiceManager, ServiceSpec
from chaos import managed, service


class SimulatedOS:
    """Command/observation adapter only; explicitly not a native OS verdict."""
    def __init__(self):
        self.calls = []; self.loaded = False; self.pid = 700; self.now = 0
        self.plan = None; self.foreign_job = False; self.health_override = None
        self.no_health = False; self.orphan = False; self.fail_command = None

    def runner(self, argv):
        self.calls.append(list(argv)); action = argv[1]
        if self.fail_command == action:
            return subprocess.CompletedProcess(argv, 4, '', 'controlled failure')
        if action == 'print':
            if not self.loaded:
                return subprocess.CompletedProcess(argv, 113, '', 'not loaded')
            path = self.plan['jobPath'] + ('.foreign' if self.foreign_job else '')
            arguments = '\n'.join('    %d = %s' % item for item in enumerate(self.plan['command']))
            text = 'path = ' + path + '\narguments = {\n' + arguments + '\n}\npid = ' + str(self.pid) + '\n'
            return subprocess.CompletedProcess(argv, 0, text, '')
        if action == 'bootstrap':
            self.loaded = True; self.pid += 1
        elif action == 'bootout':
            self.loaded = False
        else:
            raise AssertionError('Unexpected OS command: ' + repr(argv))
        return subprocess.CompletedProcess(argv, 0, '', '')

    def health(self, url):
        if self.health_override is not None:
            return dict(self.health_override)
        if not self.loaded or self.no_health:
            return None
        record = json.loads(Path(self.plan['configPath']).read_text())
        return {'status': 'ok', 'serviceId': record['nonce'], 'pid': self.pid + 1000, 'version': '0.122.0'}

    def process(self, pid):
        if not self.loaded:
            return None
        if pid == self.pid:
            return {'pid': pid, 'ppid': 1, 'birth': 'parent-' + str(pid)}
        if pid == self.pid + 1000:
            return {'pid': pid, 'ppid': 1 if self.orphan else self.pid, 'birth': 'server-' + str(pid)}
        return None

    def sleep(self, seconds):
        self.now += seconds


class ServiceLifecycle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chaos-service-contract-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.workspace = self.base / 'source workspace'
        (self.workspace / 'chaos').mkdir(parents=True)
        (self.workspace / 'chaos/__main__.py').write_text('# source identity fixture\n')
        (self.workspace / 'chaos/version.py').write_text('__version__ = \"0.122.0\"\n')
        self.data = self.base / 'user data'; self.data.mkdir()
        (self.data / 'settings.json').write_text('{"private":"keep"}\n')
        self.spec = ServiceSpec('workspace', 'source', str(Path(sys.executable).resolve()),
                                str(self.workspace), str(self.data / 'workspace.sqlite3'),
                                str(self.data / 'control'), str(self.data / 'settings.json'), port=18781)
        self.os = SimulatedOS()
        self.manager = self.make_manager()
        self.os.plan = self.manager.plan(self.spec)
        self.addCleanup(patch.stopall)
        patch('chaos.service.socket.create_connection', side_effect=ConnectionRefusedError).start()

    def make_manager(self, **options):
        arguments = dict(root=self.base / 'registry', job_dir=self.base / 'jobs', platform='darwin',
                         runner=self.os.runner, health=self.os.health, process=self.os.process,
                         clock=lambda: self.os.now, sleep=self.os.sleep)
        arguments.update(options)
        return ServiceManager(**arguments)

    def install(self):
        return self.manager.install(self.spec)

    def files(self):
        return {str(p.relative_to(self.base)): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}

    def test_spec_frozen_strict_json_roundtrip(self):
        self.assertEqual(ServiceSpec.from_dict(json.loads(json.dumps(self.spec.to_dict()))), self.spec)
        with self.assertRaises(FrozenInstanceError): self.spec.port = 1
        for extra in ({'shell': 'arbitrary'}, {'environment': {'MODEL_KEY': 'secret'}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError): ServiceSpec.from_dict(dict(self.spec.to_dict(), **extra))

    def test_spec_names_profiles_and_missing_fields_are_rejected(self):
        for name in ('', '../escape', 'job/name', 'UPPER', 'job;command', 'x' * 49):
            with self.subTest(name=name), self.assertRaises(ValueError): replace(self.spec, name=name)
        with self.assertRaises(ValueError): replace(self.spec, profile='shell')
        value = self.spec.to_dict(); del value['config']
        with self.assertRaises(ValueError): ServiceSpec.from_dict(value)

    def test_paths_reject_relative_parent_and_control_characters(self):
        for field in ('python', 'cwd', 'db', 'state_dir', 'config'):
            for value in ('relative', str(self.base / '..' / 'escape'), str(self.base) + '\ncommand'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError): replace(self.spec, **{field: value})

    def test_host_port_and_distinct_data_paths_are_strict(self):
        for value in (True, False, 0, 65536, 3.5, '8765'):
            with self.subTest(port=value), self.assertRaises(ValueError): replace(self.spec, port=value)
        for host in ('localhost', 'https://host', '127.0.0.1;command', True, False, 2130706433):
            with self.subTest(host=host), self.assertRaises(ValueError): replace(self.spec, host=host)
        with self.assertRaises(ValueError): replace(self.spec, config=self.spec.db)
        self.assertEqual(replace(self.spec, host='::1').host, '::1')

    def test_plan_is_pure_and_preserves_canonical_source_arguments(self):
        before = self.files(); self.os.calls.clear()
        plan = self.manager.plan(self.spec)
        self.assertEqual(plan['spec'], self.spec.to_dict()); self.assertFalse(plan['actualVerified'])
        self.assertEqual(plan['command'][0], self.spec.python); self.assertEqual(plan['command'][1], '-I')
        self.assertEqual(plan['command'][-2:], ['--service-spec', plan['configPath']])
        self.assertEqual(before, self.files()); self.assertEqual(self.os.calls, [])

    def test_launchd_generation_uses_argv_without_shell_and_private_logs(self):
        plan = self.manager.plan(self.spec); job = plistlib.loads(plan['content'].encode())
        self.assertEqual(job['Label'], plan['label']); self.assertEqual(job['ProgramArguments'], plan['command'])
        self.assertEqual(job['WorkingDirectory'], self.spec.cwd)
        self.assertEqual(job['KeepAlive'], {'SuccessfulExit': False})
        self.assertTrue(job['RunAtLoad']); self.assertEqual(job['EnvironmentVariables'], {'CHAOS_SERVICE_ID': '0' * 32})
        for key in ('StandardOutPath', 'StandardErrorPath'):
            self.assertIn(self.manager.root, Path(job[key]).parents)

    def test_systemd_generation_escapes_spaces_quotes_percent_without_native_claim(self):
        manager = self.make_manager(platform='linux')
        spec = replace(self.spec, cwd=str(self.base / 'space "quote" %value'))
        plan = manager.plan(spec)
        self.assertIn('WorkingDirectory="', plan['content']); self.assertIn('%%value', plan['content'])
        self.assertIn('\\"quote\\"', plan['content']); self.assertIn('KillMode=control-group', plan['content'])
        self.assertEqual(sum(line.startswith('ExecStart=') for line in plan['content'].splitlines()), 1)
        self.assertFalse(plan['actualVerified']); self.assertEqual(self.os.calls, [])
        calls = []; enabled = [False]
        def systemd(argv):
            calls.append(argv)
            if 'show' in argv:
                if not enabled[0]: return subprocess.CompletedProcess(argv, 4, 'LoadState=not-found\n', '')
                text = 'LoadState=loaded\nActiveState=inactive\nMainPID=0\nFragmentPath=' + manager.plan(self.spec)['jobPath'] + '\nDropInPaths=\n'
                return subprocess.CompletedProcess(argv, 0, text, '')
            if Path(argv[0]).name == 'busctl':
                if 'GetUnit' in argv:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({'type': 'o', 'data': ['/org/freedesktop/systemd1/unit/fixture']}), '')
                command = manager.plan(self.spec)['command']
                value = {'type': 'a(sasbttttuii)', 'data': [[command[0], command, False, 0, 0, 0, 0, 0, 0, 0]]}
                return subprocess.CompletedProcess(argv, 0, json.dumps(value), '')
            if 'disable' in argv: enabled[0] = False
            return subprocess.CompletedProcess(argv, 0, '', '')
        manager = self.make_manager(platform='linux', runner=systemd)
        manager.install(self.spec); enabled[0] = True
        self.assertTrue(manager.status(self.spec.name)['loaded'])
        self.assertFalse(manager.status(self.spec.name)['active'])
        manager.uninstall(self.spec.name)
        self.assertTrue(any('disable' in argv and '--now' in argv for argv in calls))
        self.assertFalse(enabled[0])

    def test_managed_profile_reuses_enrolled_stable_launcher(self):
        home = self.base / 'managed'; home.mkdir(); (home / 'launcher.py').write_bytes(Path(managed.__file__).read_bytes())
        (home / 'current.json').write_text(json.dumps({'version': '0.121.0'}))
        python = managed.release_python(home, '0.121.0'); python.parent.mkdir(parents=True); python.touch()
        spec = replace(self.spec, profile='managed', managed_home=str(home))
        original = {p.name: p.read_bytes() for p in (home / 'launcher.py', home / 'current.json')}
        result = self.manager.install(spec)
        self.assertTrue(result['installed']); self.assertFalse(result['started'])
        self.assertEqual(original, {p.name: p.read_bytes() for p in (home / 'launcher.py', home / 'current.json')})
        self.assertEqual(result['spec']['managed_home'], str(home))

    def test_unsupported_platform_and_mixed_profile_are_explicit(self):
        with self.assertRaises(ServiceError): self.make_manager(platform='win32')
        with self.assertRaises(ValueError): replace(self.spec, managed_home=str(self.base))
        with self.assertRaises(ValueError): replace(self.spec, profile='managed')

    def test_install_is_owned_not_started_and_second_install_preserves_bytes(self):
        result = self.install(); before = self.files()
        self.assertTrue(result['installed']); self.assertFalse(result['started']); self.assertFalse(self.os.loaded)
        self.assertFalse(any(call[1] == 'bootstrap' for call in self.os.calls))
        with self.assertRaises(ServiceError): self.install()
        self.assertEqual(before, self.files())

    def test_install_refuses_foreign_definition_and_loaded_label(self):
        job = Path(self.os.plan['jobPath']); job.parent.mkdir(); job.write_text('foreign unit\n')
        with self.assertRaises(ServiceError): self.install()
        self.assertEqual(job.read_text(), 'foreign unit\n'); job.unlink()
        self.os.loaded = True; self.os.foreign_job = True
        with self.assertRaises(ServiceError): self.install()
        self.assertFalse(Path(self.os.plan['configPath']).exists())
        self.os.loaded = False; self.os.foreign_job = False
        original = self.manager._write
        def racing(path, data):
            if Path(path) == job: job.write_text('raced foreign definition\n')
            return original(path, data)
        with patch.object(self.manager, '_write', racing), self.assertRaises(FileExistsError): self.install()
        self.assertEqual(job.read_text(), 'raced foreign definition\n')
        self.assertFalse(Path(self.os.plan['configPath']).exists())

    def test_symlink_input_and_job_parent_never_write_external_targets(self):
        external = self.base / 'external'; external.mkdir(); (external / 'sentinel').write_text('keep')
        linked = self.base / 'linked'; linked.symlink_to(external, target_is_directory=True)
        manager = self.make_manager(job_dir=linked)
        with self.assertRaises(ServiceError): manager.install(self.spec)
        with self.assertRaises(ServiceError): self.manager.install(replace(self.spec, config=str(linked / 'settings.json')))
        self.assertEqual((external / 'sentinel').read_text(), 'keep'); self.assertEqual(len(list(external.iterdir())), 1)
        logs = self.manager.root / 'logs'; logs.mkdir(parents=True, exist_ok=True)
        stdout = logs / (self.spec.name + '.stdout.log'); stdout.symlink_to(external / 'sentinel')
        with self.assertRaises(ServiceError): self.install()
        self.assertEqual((external / 'sentinel').read_text(), 'keep'); stdout.unlink()
        self.install(); stdout.unlink(); stdout.symlink_to(external / 'sentinel')
        with self.assertRaises(ServiceError): self.manager.start(self.spec.name)
        self.assertEqual((external / 'sentinel').read_text(), 'keep')
        self.assertFalse(any(call[1] == 'bootstrap' for call in self.os.calls))

    def test_partial_install_cleans_only_owned_bytes_and_preserves_concurrent_edit(self):
        original = self.manager._write; job = Path(self.os.plan['jobPath'])
        def failing(path, data, **options):
            if Path(path) == job:
                launcher = Path(self.os.plan['configPath']).parent / 'launcher.py'
                launcher.write_text('concurrent user edit\n')
                raise OSError('controlled disk failure')
            return original(path, data, **options)
        with patch.object(self.manager, '_write', failing), self.assertRaises(OSError): self.install()
        launcher = Path(self.os.plan['configPath']).parent / 'launcher.py'
        self.assertEqual(launcher.read_text(), 'concurrent user edit\n')
        self.assertFalse(job.exists()); self.assertFalse(Path(self.os.plan['configPath']).exists())

    def test_concurrent_lifecycle_lock_rejects_second_operation(self):
        with self.manager._lock():
            with self.assertRaises(ServiceError): self.make_manager().install(self.spec)
        self.assertFalse(Path(self.os.plan['configPath']).exists())
        self.assertTrue(self.install()['installed'])

    def test_tampered_owned_file_is_never_started_stopped_or_uninstalled(self):
        self.install(); job = Path(self.os.plan['jobPath']); original_bytes = job.read_bytes(); job.write_text('user changed unit\n')
        for action in ('status', 'start', 'stop', 'uninstall'):
            with self.subTest(action=action), self.assertRaises(ServiceError): getattr(self.manager, action)(self.spec.name)
        self.assertEqual(job.read_text(), 'user changed unit\n')
        self.assertFalse(any(call[1] in ('bootstrap', 'bootout') for call in self.os.calls))
        job.write_bytes(original_bytes); load = self.manager._load
        def edit_after_ownership_read(name):
            result = load(name); job.write_text('concurrent edit after ownership read\n'); return result
        with patch.object(self.manager, '_stop', return_value={}), patch.object(self.manager, '_load', edit_after_ownership_read), self.assertRaises(ServiceError):
            self.manager.uninstall(self.spec.name)
        self.assertEqual(job.read_text(), 'concurrent edit after ownership read\n')

    def test_readiness_requires_owned_nonce_and_process_ancestry(self):
        self.install(); self.os.loaded = True
        self.os.health_override = {'status': 'ok', 'serviceId': 'f' * 32, 'pid': self.os.pid + 1000, 'version': '0.122.0'}
        self.assertFalse(self.manager.status(self.spec.name)['ready'])
        nonce = json.loads(Path(self.os.plan['configPath']).read_text())['nonce']
        self.os.health_override = {'status': 'ok', 'serviceId': nonce, 'pid': self.os.pid + 1000, 'version': 'other'}
        self.assertFalse(self.manager.status(self.spec.name)['ready'])
        self.os.health_override = None; self.os.orphan = True
        self.assertFalse(self.manager.status(self.spec.name)['ready'])
        self.os.orphan = False; observe = self.os.process; parent_observations = []
        def reused_pid(pid):
            value = observe(pid)
            if value and pid == self.os.pid:
                parent_observations.append(pid); value['birth'] = 'first' if len(parent_observations) == 1 else 'reused'
            return value
        with patch.object(self.manager, '_process', reused_pid):
            self.assertFalse(self.manager.status(self.spec.name)['ready'])
        self.assertEqual(len(parent_observations), 2)
        status = self.manager.status(self.spec.name)
        self.assertTrue(status['ready']); self.assertFalse(status['actualVerified'])

    def test_start_stop_start_changes_owned_pid_without_force_kickstart(self):
        self.install(); first = self.manager.start(self.spec.name); stopped = self.manager.stop(self.spec.name)
        self.assertTrue(first['ready']); self.assertFalse(first['actualVerified']); self.assertFalse(stopped['loaded'])
        second = self.manager.start(self.spec.name)
        self.assertTrue(second['ready']); self.assertNotEqual(first['pid'], second['pid'])
        self.assertFalse(any('kickstart' in call or '-k' in call for call in self.os.calls))

    def test_start_foreign_occupied_port_does_not_bootstrap(self):
        self.install(); connection = unittest.mock.Mock()
        with patch('chaos.service.socket.create_connection', return_value=connection):
            with self.assertRaises(ServiceError): self.manager.start(self.spec.name)
        connection.close.assert_called_once(); self.assertFalse(self.os.loaded)
        self.assertFalse(any(call[1] == 'bootstrap' for call in self.os.calls))

    def test_start_timeout_retains_inspectable_profile_and_never_boots_out(self):
        self.install(); self.os.no_health = True
        with self.assertRaises(ServiceError): self.manager.start(self.spec.name, timeout=.2)
        self.assertTrue(Path(self.os.plan['configPath']).exists()); self.assertTrue(self.os.loaded)
        self.assertFalse(any(call[1] == 'bootout' for call in self.os.calls))

    def test_foreign_loaded_job_and_orphan_health_cannot_be_stopped(self):
        self.install(); self.os.loaded = True; self.os.foreign_job = True
        with self.assertRaises(ServiceError): self.manager.stop(self.spec.name)
        self.os.loaded = False; self.os.foreign_job = False
        nonce = json.loads(Path(self.os.plan['configPath']).read_text())['nonce']
        self.os.health_override = {'status': 'ok', 'serviceId': nonce, 'pid': 99999}
        with self.assertRaises(ServiceError): self.manager.stop(self.spec.name)
        self.assertFalse(any(call[1] == 'bootout' for call in self.os.calls))

    def test_uninstall_preserves_database_settings_state_and_logs(self):
        self.install(); self.manager.start(self.spec.name)
        database = Path(self.spec.db); database.write_bytes(b'user database bytes')
        state = Path(self.spec.state_dir); state.mkdir(); (state / 'record').write_text('durable state')
        log = Path(plistlib.loads(self.os.plan['content'].encode())['StandardOutPath']); log.write_text('diagnostic logs')
        result = self.manager.uninstall(self.spec.name)
        self.assertTrue(result['dataPreserved']); self.assertTrue(result['logsPreserved'])
        self.assertEqual(database.read_bytes(), b'user database bytes'); self.assertEqual(log.read_text(), 'diagnostic logs')
        self.assertEqual((state / 'record').read_text(), 'durable state'); self.assertIn('private', Path(self.spec.config).read_text())
        self.assertFalse(Path(self.os.plan['jobPath']).exists()); self.assertFalse(Path(self.os.plan['configPath']).exists())

    def test_native_command_failure_never_reflects_stderr_credentials(self):
        self.install(); self.os.fail_command = 'bootstrap'
        with self.assertRaises(ServiceError) as caught: self.manager.start(self.spec.name)
        self.assertIn('exit 4', str(caught.exception)); self.assertNotIn('controlled failure', str(caught.exception))
        self.assertTrue(Path(self.os.plan['configPath']).exists())

    def test_timeout_validation_and_uninstalled_status_do_not_launch(self):
        self.assertFalse(self.manager.status('absent')['installed'])
        for action in ('start', 'stop'):
            for timeout in (True, 0, -1, 301):
                with self.subTest(action=action, timeout=timeout), self.assertRaises(ValueError): getattr(self.manager, action)('absent', timeout=timeout)
        self.assertEqual(self.os.calls, [])

    def test_sdk_exports_are_the_canonical_classes(self):
        self.assertIs(ServiceSpec, service.ServiceSpec); self.assertIs(ServiceManager, service.ServiceManager)
        self.assertIs(ServiceError, service.ServiceError)

    def cli_arguments(self):
        return ['--root', str(self.manager.root), '--job-dir', str(self.manager.job_dir), 'plan', '--name', self.spec.name,
                '--profile', 'source', '--python', self.spec.python, '--cwd', self.spec.cwd, '--db', self.spec.db,
                '--state-dir', self.spec.state_dir, '--config', self.spec.config, '--host', self.spec.host, '--port', str(self.spec.port)]

    def test_cli_plan_matches_sdk_without_os_calls(self):
        output = io.StringIO()
        with patch.object(service, 'ServiceManager', return_value=self.manager), redirect_stdout(output):
            self.assertEqual(service.main(self.cli_arguments()), 0)
        self.assertEqual(json.loads(output.getvalue()), self.manager.plan(self.spec)); self.assertEqual(self.os.calls, [])

    def test_cli_invalid_spec_reports_error_and_preserves_files(self):
        args = self.cli_arguments(); args[args.index('--name') + 1] = '../foreign'
        error = io.StringIO(); before = self.files()
        with patch.object(service, 'ServiceManager', return_value=self.manager), redirect_stderr(error):
            self.assertEqual(service.main(args), 1)
        self.assertIn('error', json.loads(error.getvalue())); self.assertEqual(before, self.files())

    def test_source_launcher_binds_identity_cwd_and_exact_server_args(self):
        self.install(); config = Path(self.os.plan['configPath']); record = json.loads(config.read_text())
        before_cwd = Path.cwd(); before_argv = list(sys.argv); before_path = list(sys.path)
        try:
            with patch.dict(os.environ, {'CHAOS_SERVICE_ID': record['nonce'], 'PYTHONPATH': 'untrusted',
                                        'CHAOS_MANAGED_HOME': str(self.base / 'foreign updater'),
                                        'CHAOS_SUPERVISED': '1'}), patch('runpy.run_module') as run:
                self.assertEqual(managed.service_main(str(config)), 0)
                self.assertEqual(Path.cwd(), self.workspace); self.assertNotIn('PYTHONPATH', os.environ)
                self.assertNotIn('CHAOS_SUPERVISED', os.environ)
                self.assertEqual(Path(os.environ['CHAOS_MANAGED_HOME']), self.manager.root / 'updaters' / self.spec.name)
                self.assertEqual(sys.argv[1:], ['serve', '--host', self.spec.host, '--port', str(self.spec.port), '--db', self.spec.db, '--config', self.spec.config, '--state-dir', self.spec.state_dir])
                run.assert_called_once_with('chaos', run_name='__main__', alter_sys=True)
            with patch.dict(os.environ, {'CHAOS_SERVICE_ID': 'f' * 32}), self.assertRaises(RuntimeError): managed.service_main(str(config))
        finally:
            os.chdir(before_cwd); sys.argv[:] = before_argv; sys.path[:] = before_path

    def linux_manager(self):
        calls = []
        state = {'loaded': False, 'dropins': '', 'exec': None, 'unit': None, 'busExit': 0}
        manager = None
        def runner(argv):
            calls.append(list(argv))
            if 'show' in argv:
                if not state['loaded']:
                    return subprocess.CompletedProcess(argv, 4, 'LoadState=not-found\n', '')
                plan = manager.plan(self.spec)
                text = ('LoadState=loaded\nActiveState=inactive\nMainPID=0\nFragmentPath=' + plan['jobPath'] +
                        '\nDropInPaths=' + state['dropins'] + '\n')
                return subprocess.CompletedProcess(argv, 0, text, '')
            if Path(argv[0]).name == 'busctl':
                if 'GetUnit' in argv:
                    value = state['unit'] if state['unit'] is not None else {'type': 'o', 'data': ['/org/freedesktop/systemd1/unit/fixture']}
                    return subprocess.CompletedProcess(argv, state['busExit'], json.dumps(value), '')
                command = manager.plan(self.spec)['command']
                value = state['exec'] if state['exec'] is not None else {
                    'type': 'a(sasbttttuii)', 'data': [[command[0], command, False, 0, 0, 0, 0, 0, 0, 0]]}
                return subprocess.CompletedProcess(argv, state['busExit'], json.dumps(value), '')
            if 'disable' in argv:
                state['loaded'] = False
            return subprocess.CompletedProcess(argv, 0, '', '')
        manager = self.make_manager(platform='linux', runner=runner)
        return manager, state, calls

    def test_systemd_loaded_arguments_are_checked_before_every_lifecycle_action(self):
        manager, state, calls = self.linux_manager()
        manager.install(self.spec)
        state['loaded'] = True
        status = manager.status(self.spec.name)
        self.assertTrue(status['loaded']); self.assertFalse(status['actualVerified'])
        command = manager.plan(self.spec)['command']
        state['exec'] = {'type': 'a(sasbttttuii)', 'data': [[command[0], command + ['--foreign'], False, 0, 0, 0, 0, 0, 0, 0]]}
        before = self.files()
        for action in ('status', 'start', 'stop', 'uninstall'):
            with self.subTest(action=action), self.assertRaises(ServiceError):
                getattr(manager, action)(self.spec.name)
        self.assertEqual(self.files(), before)
        self.assertFalse(any('start' in call or 'disable' in call for call in calls))
        bus = next(call for call in calls if Path(call[0]).name == 'busctl' and call[-1] == 'ExecStart')
        self.assertIn('--json=short', bus)
        self.assertEqual(bus[-2:], ['org.freedesktop.systemd1.Service', 'ExecStart'])

    def test_systemd_dropins_refuse_even_when_owned_file_and_argv_match(self):
        manager, state, calls = self.linux_manager()
        manager.install(self.spec)
        state.update(loaded=True, dropins='/outside/override.conf')
        before = self.files()
        for action in ('status', 'start', 'stop', 'uninstall'):
            with self.subTest(action=action), self.assertRaises(ServiceError):
                getattr(manager, action)(self.spec.name)
        self.assertEqual(self.files(), before)
        self.assertFalse(any('start' in call or 'disable' in call for call in calls))

    def test_systemd_unavailable_or_malformed_typed_exec_identity_fails_closed(self):
        manager, state, calls = self.linux_manager()
        manager.install(self.spec); state['loaded'] = True
        command = manager.plan(self.spec)['command']
        valid_row = [command[0], command, False, 0, 0, 0, 0, 0, 0, 0]
        invalid = [None, {}, {'type': 's', 'data': 'foreign'},
                   {'type': 'a(sasbttttuii)', 'data': []},
                   {'type': 'a(sasbttttuii)', 'data': [valid_row, valid_row]}]
        for index, replacement in ((0, '/foreign/python'), (1, ['different']), (2, True),
                                   (3, False), (4, '0'), (9, 0.0)):
            row = list(valid_row); row[index] = replacement
            invalid.append({'type': 'a(sasbttttuii)', 'data': [row]})
        for value in invalid:
            with self.subTest(value=value):
                state['exec'] = value if value is not None else {'type': 'unavailable', 'data': None}
                with self.assertRaises(ServiceError): manager.status(self.spec.name)
        state['exec'] = None
        for value in ({'type': 's', 'data': ['/org/freedesktop/systemd1/unit/fixture']},
                      {'type': 'o', 'data': ['/org/foreign/unit']},
                      {'type': 'o', 'data': ['/org/freedesktop/systemd1/unit/one', '/org/freedesktop/systemd1/unit/two']},
                      {'type': 'o', 'data': []}):
            with self.subTest(unit=value):
                state['unit'] = value
                with self.assertRaises(ServiceError): manager.status(self.spec.name)
        state.update(exec=None, unit=None, busExit=1)
        with self.assertRaises(ServiceError): manager.status(self.spec.name)
        self.assertFalse(any('start' in call or 'disable' in call for call in calls))

    def test_source_updater_home_rejects_symlinks_before_install_and_os_restart(self):
        external = self.base / 'outside-updater'; external.mkdir()
        sentinel = external / 'updater.json'; sentinel.write_text('{"doNotReadOrChange":true}\n')
        updaters = self.manager.root / 'updaters'; updaters.mkdir(mode=0o700, parents=True)
        home = updaters / self.spec.name; home.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ServiceError): self.install()
        self.assertEqual(sentinel.read_text(), '{"doNotReadOrChange":true}\n')
        home.unlink(); self.install()
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)
        home.rmdir(); home.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ServiceError): self.manager.start(self.spec.name)
        config = Path(self.os.plan['configPath']); record = json.loads(config.read_text())
        before_cwd = Path.cwd(); before_argv = list(sys.argv); before_path = list(sys.path)
        try:
            with patch.dict(os.environ, {'CHAOS_SERVICE_ID': record['nonce']}), patch('runpy.run_module') as run:
                with self.assertRaises(RuntimeError): managed.service_main(str(config))
                run.assert_not_called()
        finally:
            os.chdir(before_cwd); sys.argv[:] = before_argv; sys.path[:] = before_path
        self.assertEqual(sentinel.read_text(), '{"doNotReadOrChange":true}\n')
        self.assertFalse(any(call[1] == 'bootstrap' for call in self.os.calls))

    def test_existing_managed_supervisor_keeps_exit75_and_startup_rollback(self):
        root = self.base / 'enrolled'; root.mkdir()
        for version in ('0.121.0', '0.122.0'):
            python = managed.release_python(root, version); python.parent.mkdir(parents=True); python.touch()
        managed.atomic_json(root / 'current.json', {'version': '0.122.0', 'previousVersion': '0.121.0'})
        made = []
        class Process:
            def __init__(self, argv, **options): self.argv = argv; self.code = None; self.terminated = False; made.append(self)
            def poll(self): return self.code
            def wait(self, timeout=None):
                if self.code is None: self.code = 75 if len(made) == 1 else 0
                return self.code
            def terminate(self): self.terminated = True; self.code = -15
            def kill(self): self.code = -9
        with patch.object(managed.subprocess, 'Popen', Process), patch.object(managed, '_healthy', return_value=True):
            self.assertEqual(managed.supervise(root, ['serve']), 0)
        self.assertEqual(len(made), 2); made.clear()
        with patch.object(managed.subprocess, 'Popen', Process), patch.object(managed, '_healthy', side_effect=[False, True]):
            self.assertEqual(managed.supervise(root, ['serve']), 0)
        self.assertEqual(managed.current(root)['version'], '0.121.0'); self.assertTrue(made[0].terminated)

    def test_sigterm_closes_same_server_and_preserves_update_exit75(self):
        from chaos import server
        import signal
        handlers = {}; marker = object(); closed = []
        class Server:
            server_port = 18781; restart_requested = True
            def serve_forever(self): handlers[signal.SIGTERM](signal.SIGTERM, None)
            def server_close(self): closed.append(self)
        instance = Server()
        def register(sig, handler): handlers[sig] = handler; return marker
        with patch.object(server, 'make_server', return_value=instance), patch('chaos.settings.Settings'), patch.object(signal, 'signal', register), patch.object(sys, 'argv', ['chaos', '--port', '18781']), redirect_stdout(io.StringIO()):
            self.assertEqual(server.main(), managed.RESTART_EXIT)
        self.assertEqual(closed, [instance]); self.assertIs(handlers[signal.SIGTERM], marker)


if __name__ == '__main__':
    unittest.main()
