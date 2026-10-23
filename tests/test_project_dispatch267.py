"""Project selection, offline discovery and bound-runtime dispatch regressions."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from chaos import command_intelligence, project_deployment
from chaos.project import init_project, main, working_directory
from chaos.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


class ProjectDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.outside = self.folder / 'outside'
        self.outside.mkdir()
        self.a = self.folder / 'first'
        self.b = self.folder / 'second project'
        init_project(self.a)
        init_project(self.b)

    def subprocess(self, args, *, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CHAOS_', 'OPENAI_'))}
        env['PYTHONPATH'] = str(ROOT)
        return subprocess.run([sys.executable, '-m', 'chaos', 'project', *args],
                              cwd=cwd or self.outside, env=env, text=True,
                              capture_output=True, timeout=25)

    def test_help_and_parse_errors_are_offline_outside_project(self):
        for command in ([], ['ask'], ['assist'], ['assist', 'ask'], ['setup'], ['configure'], ['doctor']):
            with self.subTest(command=command):
                result = self.subprocess(command + ['--help'])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
                self.assertNotIn('Traceback', result.stderr)
        for command in (['ask'], ['assist', 'apply'], ['doctor', '--models'], ['setup', '--unknown']):
            result = self.subprocess(command)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn('No chaos.project.json', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_missing_projects_report_structured_errors_without_state(self):
        for command in (['ask', 'help'], ['assist', 'show', 'job', '--project', 'workspace'],
                        ['setup', '--yes'], ['configure', '--yes'], ['doctor']):
            result = self.subprocess(command)
            self.assertEqual(result.returncode, 1, (command, result.stdout, result.stderr))
            self.assertIn('No chaos.project.json', json.loads(result.stderr.splitlines()[-1])['error'])
            self.assertNotIn('Traceback', result.stderr)
        result = self.subprocess(['doctor', '--json'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'failed')
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_directory_selection_matches_regular_run_and_doctor_preserves_files(self):
        (self.b / 'main.chaos').write_text('40 + 2')
        settings = self.b / '.chaos/settings.json'
        Settings(settings).update({'runtime': {'workspace': '.chaos/custom-workspace'}}, 1)
        before = settings.read_bytes()
        result = self.subprocess(['run', '--directory', str(self.b)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), 42)
        result = self.subprocess(['doctor', '--directory', str(self.b), '--profile', 'core', '--json'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['command'], 'doctor')
        self.assertEqual(settings.read_bytes(), before)
        self.assertEqual((self.b / 'main.chaos').read_text(), '40 + 2')
        self.assertFalse((self.outside / '.chaos').exists())
        self.assertFalse((self.a / '.chaos').exists())

    def test_ask_selected_settings_and_explicit_relative_config_are_used_and_restored(self):
        settings_path = self.b / '.chaos/settings.json'
        explicit_path = self.b / 'custom.json'
        for path, model in ((settings_path, 'project-model'), (explicit_path, 'explicit-model')):
            Settings(path).update({'providers': {'text': {'apiKey': 'fixture-only'}},
                                  'roles': {'executor': {'model': model, 'autoUpgrade': False}}}, 1)
        before = {path: path.read_bytes() for path in (settings_path, explicit_path)}
        models = []
        class Client:
            def __init__(self, config): self.chat = NS(completions=self)
            def create(self, **kwargs):
                models.append(kwargs['model'])
                return NS(choices=[NS(message=NS(content='fixture answer', tool_calls=[]))])
            def close(self): pass
        def settings(path=None): return Settings(path, client_factory=Client)
        with working_directory(self.outside), patch.dict(os.environ, {'CHAOS_CONFIG': 'inherited.json'}), \
                patch('chaos.settings.Settings', side_effect=settings), contextlib.redirect_stdout(io.StringIO()) as out:
            for extra in ([], ['--config', 'custom.json']):
                self.assertEqual(main(['ask', 'help', '--directory', str(self.b), *extra]), 0)
                self.assertEqual(Path.cwd(), self.outside)
                self.assertEqual(os.environ['CHAOS_CONFIG'], 'inherited.json')
        self.assertEqual(models, ['project-model', 'explicit-model'])
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertNotIn('fixture-only', out.getvalue())

    def test_ask_failure_restores_callers_directory_and_configuration(self):
        def fail(args):
            self.assertEqual(Path.cwd(), self.b)
            self.assertEqual(os.environ['CHAOS_CONFIG'], str(self.b / '.chaos/settings.json'))
            raise RuntimeError('fixture runtime failure')
        with working_directory(self.outside), patch.dict(os.environ, {'CHAOS_CONFIG': 'original.json'}), \
                patch.object(command_intelligence, 'ask', side_effect=fail), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(main(['ask', 'help', '--directory', str(self.b)]), 1)
            self.assertEqual(Path.cwd(), self.outside)
            self.assertEqual(os.environ['CHAOS_CONFIG'], 'original.json')
        self.assertEqual(json.loads(output.getvalue()), {'error': 'fixture runtime failure'})
        self.assertFalse((self.b / '.chaos').exists())

    def test_literal_prompt_after_separator_is_not_reinterpreted_as_option(self):
        with working_directory(self.outside), patch.object(command_intelligence, 'ask', return_value=0) as ask:
            self.assertEqual(main(['ask', '--directory', str(self.b), '--', '--directory']), 0)
        self.assertEqual(ask.call_args.args[0].question, '--directory')
        self.assertEqual(ask.call_args.args[0].directory, str(self.b))

    def test_assistant_parses_directory_at_either_subcommand_level_without_http(self):
        from chaos import assistant_cli
        seen = []
        def run(args):
            seen.append((args.prompt, args.directory, Path.cwd(), args.project))
            return 0
        with working_directory(self.outside), patch.object(assistant_cli, 'run', side_effect=run):
            for argv in (['assist', '--directory', str(self.b), 'ask', '--project', 'workspace', '--', '--directory'],
                         ['assist', 'ask', '--directory', str(self.b), '--project', 'workspace', '--', '--directory']):
                self.assertEqual(main(argv), 0)
                self.assertEqual(Path.cwd(), self.outside)
        self.assertEqual(seen, [('--directory', str(self.b), self.b, 'workspace')] * 2)

    def test_bound_launcher_rejects_other_roots_before_state_or_handlers(self):
        commands = (['ask', 'help'], ['assist', 'show', 'job', '--project', 'workspace'],
                    ['setup', '--yes'], ['configure', '--yes'], ['doctor'],
                    ['run', '-c', '42'], ['status'])
        with patch.object(command_intelligence, 'ask') as ask, \
                patch.object(project_deployment, '_run') as deploy, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            for command in commands:
                self.assertEqual(main([*command, '--directory', str(self.b)], root=self.a,
                                      environment_locked='exclusive'), 1, command)
        ask.assert_not_called()
        deploy.assert_not_called()
        self.assertIn('bound to its own project', errors.getvalue())
        self.assertFalse((self.a / '.chaos').exists())
        self.assertFalse((self.b / '.chaos').exists())

    def test_deployment_help_precedes_discovery_and_old_lock_refusal(self):
        with working_directory(self.outside), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                project_deployment.main(argv=['setup', '--help'])
            self.assertEqual(raised.exception.code, 0)
            with self.assertRaises(SystemExit) as raised:
                main(['configure', '--help'], root=self.a, environment_locked=True)
            self.assertEqual(raised.exception.code, 0)
        self.assertIn('--directory', output.getvalue())
        self.assertFalse((self.a / '.chaos').exists())
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_bound_target_reuses_only_its_lock_and_unbound_target_takes_lock(self):
        locks = []
        @contextlib.contextmanager
        def lock(root, *, exclusive):
            locks.append((root, exclusive))
            yield
        with patch.object(project_deployment.launcher, 'environment_lock', side_effect=lock), \
                patch.object(project_deployment, '_run', return_value={'status': 'ready'}) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['setup', '--directory', str(self.a), '--yes', '--json'],
                                  root=self.a, environment_locked='exclusive'), 0)
            self.assertEqual(run.call_args.args[0], self.a)
            self.assertEqual(locks, [])
            with working_directory(self.outside):
                self.assertEqual(main(['doctor', '--directory', str(self.b), '--json']), 0)
            self.assertEqual(locks, [(self.b, False)])
            self.assertEqual(run.call_args.args[0], self.b)

    def test_shared_lock_cannot_configure_and_unbound_lock_claim_is_ignored(self):
        with patch.object(project_deployment, '_run', return_value={'status': 'ready'}) as run, \
                patch.object(project_deployment.launcher, 'environment_lock', return_value=contextlib.nullcontext()) as lock, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for command in ('setup', 'configure'):
                self.assertEqual(main([command, '--yes', '--json'], root=self.a, environment_locked='shared'), 1)
            run.assert_not_called()
            lock.assert_not_called()
            self.assertFalse((self.a / '.chaos').exists())
            self.assertEqual(project_deployment.main(None, ['setup', '--directory', str(self.b), '--yes', '--json'],
                                                    environment_locked='exclusive'), 0)
            lock.assert_called_once_with(self.b, exclusive=True)
            self.assertEqual(project_deployment.main(self.a, ['setup', '--yes', '--json'],
                                                    environment_locked=True), 0)
            self.assertEqual(lock.call_count, 1)

    def test_docs_dispatch_uses_installed_runtime_without_project(self):
        with working_directory(self.outside), patch('chaos.documentation.main', return_value=0) as docs:
            self.assertEqual(main(['docs', 'show', 'getting-started']), 0)
        docs.assert_called_once_with(['show', 'getting-started'])
        self.assertEqual(list(self.outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
