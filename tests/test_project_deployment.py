"""Real isolated runtime checks and deployment state/CLI failure boundaries."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from chaos import project_deployment as deployment


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'main.chaos').write_text('tool("must.never.run",null)')
        (self.root / 'chaos.project.json').write_text('{"schema":"chaos.project/1","entry":"main.chaos"}')

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = deployment.main(self.root, list(args) + ['--json'])
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_core_smoke_executes_selected_language_without_user_program(self):
        info = deployment.environment_report(self.root)
        result = deployment.self_test(info, profile='core')
        self.assertTrue(result['ok'], result)
        self.assertEqual([check['value'] for check in result['checks']], [720, 42, [40, 42]])
        self.assertFalse((self.root / '.chaos/settings.json').exists())

    def test_bundled_standard_cannot_use_unrelated_global_dependencies(self):
        info = deployment.environment_report(self.root)
        info['bundled'] = True
        result = deployment.self_test(info, profile='standard', deps=self.root/'empty-staged-deps')
        self.assertFalse(result['ok'])
        dependency_checks = [item for item in result['checks'] if item['name'].startswith('dependency.')]
        self.assertEqual(len(dependency_checks), 4)
        self.assertTrue(all(item['status'] == 'failed' for item in dependency_checks), dependency_checks)
        self.assertTrue(all(item['status'] == 'passed' for item in result['checks'][4:]))

    def test_unverified_bundled_dependencies_fail_before_subprocess(self):
        info = deployment.environment_report(self.root)
        info['bundled'] = True
        with patch.object(deployment.subprocess, 'run', side_effect=AssertionError('must check identity first')):
            result = deployment.self_test(info, profile='standard')
        self.assertFalse(result['ok'])
        self.assertEqual(result['checks'][0]['name'], 'dependencies.identity')

    def test_json_configure_is_one_document_preferences_reused_and_no_ai_can_disable(self):
        calls = []
        def configure(root, **kwargs):
            calls.append(kwargs)
            return {'status': 'ready', 'preferences': {'aiAdvice': kwargs['ai']}}
        with patch('chaos.deployment_settings.configure', side_effect=configure):
            code, report, progress = self.invoke('configure', '--profile', 'core', '--ai')
            self.assertEqual(code, 0, report)
            self.assertEqual(report['preferences'], {'profile': 'core', 'aiAdvice': True})
            self.assertIn('[5/5]', progress)
            self.assertNotIn('\x1b', progress)
            saved = deployment.read_state(self.root)
            self.assertEqual(saved['status'], 'ready')
            self.assertEqual(self.invoke('configure', '--yes')[0], 0)
            self.assertEqual(self.invoke('configure', '--no-ai')[0], 0)
        self.assertEqual([call['ai'] for call in calls], [True, True, False])
        self.assertTrue(all(not call['interactive'] for call in calls))
        self.assertFalse(deployment.read_state(self.root)['preferences']['aiAdvice'])
        self.assertEqual((self.root/'main.chaos').read_text(), 'tool("must.never.run",null)')

    def test_doctor_is_read_only_of_settings_and_deployment_state(self):
        state = self.root / '.chaos'; state.mkdir()
        settings = state / 'settings.json'; settings.write_text('{"private":"sentinel"}')
        before = settings.read_bytes()
        with patch('chaos.deployment_settings.configure', side_effect=AssertionError('doctor must not configure')):
            code, report, progress = self.invoke('doctor', '--profile', 'core')
        self.assertEqual(code, 0, report)
        self.assertIn(report['gpu']['status'], ('available', 'cpu-only', 'not-installed', 'unavailable'))
        self.assertEqual(settings.read_bytes(), before)
        self.assertFalse((state / 'deployment.json').exists())

    def test_failed_self_test_does_not_write_success_or_invoke_model_settings(self):
        bad = {'ok': False, 'checks': [{'name': 'runtime.async', 'status': 'failed', 'error': 'RuntimeError'}]}
        with patch.object(deployment, 'self_test', return_value=bad), patch('chaos.deployment_settings.configure') as configure:
            code, report, progress = self.invoke('configure', '--profile', 'core')
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('runtime.async', report['error'])
        self.assertIn('失败', progress)
        configure.assert_not_called()
        self.assertFalse((self.root / '.chaos/deployment.json').exists())

    def test_staged_self_test_failure_prevents_installer_adoption(self):
        adopted = []
        info = deployment.environment_report(self.root); info['bundled'] = True
        def install(root, *, reporter, validator):
            payload = self.root / 'staged-deps'; payload.mkdir()
            validator(payload)
            adopted.append(True)
            return 0
        with patch.object(deployment, 'environment_report', return_value=info), \
             patch.object(deployment.launcher, 'setup', side_effect=install), patch('chaos.deployment_settings.configure') as configure:
            code, report, _ = self.invoke('configure')
        self.assertEqual(code, 1, report)
        self.assertEqual(adopted, [])
        configure.assert_not_called()
        self.assertFalse((self.root / '.chaos/deployment.json').exists())

    def test_symlink_deployment_record_rejected_without_following(self):
        outside = self.root / 'outside.json'; outside.write_text('sentinel')
        state = self.root / '.chaos'; state.mkdir()
        (state / 'deployment.json').symlink_to(outside)
        code, report, _ = self.invoke('configure', '--profile', 'core')
        self.assertEqual(code, 1)
        self.assertIn('nonsymlink', report['error'])
        self.assertEqual(outside.read_text(), 'sentinel')

    def test_reporter_respects_terminal_and_no_color_and_strips_controls(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        colored, plain = Terminal(), Terminal()
        with patch.dict(os.environ, {}, clear=True):
            deployment.Reporter(stream=colored)('passed', 'safe\x1b[31m\nline')
            deployment.Reporter(stream=plain, no_color=True)('passed', 'safe\x1b[31m\nline')
        self.assertIn('\x1b[32m', colored.getvalue())
        self.assertNotIn('\x1b', plain.getvalue())
        self.assertEqual(len(plain.getvalue().splitlines()), 1)

    def test_changed_environment_is_reported_without_writing_on_doctor(self):
        receipt = {'schema': deployment.SCHEMA, 'environment': {'fingerprint': 'old'},
                   'preferences': {'profile': 'core', 'aiAdvice': False}, 'status': 'ready'}
        deployment.write_state(self.root, receipt)
        before = (self.root / '.chaos/deployment.json').read_bytes()
        code, report, progress = self.invoke('doctor')
        self.assertEqual(code, 0, report)
        self.assertTrue(report['environmentChanged'])
        self.assertIn('环境已变化', progress)
        self.assertEqual((self.root / '.chaos/deployment.json').read_bytes(), before)

    def test_gpu_uses_only_dependencies_the_launcher_would_activate(self):
        info = deployment.environment_report(self.root)
        info['bundled'] = True
        deps = Path(info['dependencies']); deps.mkdir(parents=True)
        marker = deps / '.chaos-dependencies.json'
        marker.write_text('{"identity":"wrong-ABI"}')
        commands = []
        def run(command, **kwargs):
            commands.append(json.loads(command[-1]))
            return subprocess.CompletedProcess(command, 0, '{"status":"not-installed","cuda":false,"mps":false}')
        with patch.object(deployment.subprocess, 'run', side_effect=run):
            deployment.gpu_report(info)
            marker.unlink()
            deployment.gpu_report(info)
        self.assertNotIn(str(deps), commands[0])
        self.assertIn(str(deps), commands[1])

    def test_advice_is_visible_in_human_progress_and_json_stdout_stays_parseable(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        advice = {'status': 'completed', 'text': '可先运行核心语言示例。'}
        with patch('chaos.deployment_settings._advice', return_value=advice) as request:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = deployment.main(self.root, ['configure', '--yes', '--profile', 'core', '--ai'])
            self.assertEqual(code, 0, stdout.getvalue())
            self.assertIn(advice['text'], stdout.getvalue())
            code, report, progress = self.invoke('configure', '--yes')
            self.assertEqual(code, 0, report)
            self.assertEqual(report['settings']['advice'], advice)
            self.assertIn(advice['text'], progress)
            self.assertEqual(request.call_count, 2)

    def test_matching_dependencies_reinstall_only_when_explicit_and_still_validates(self):
        smoke = {'ok': True, 'checks': [{'name': 'runtime.async', 'status': 'passed', 'value': [40, 42]}]}
        info = deployment.environment_report(self.root); info['bundled'] = True
        staged = self.root / 'staged'; staged.mkdir()
        def install(root, *, reporter, validator):
            validator(staged)
            return 0
        with patch.object(deployment, 'environment_report', return_value=info), \
             patch.object(deployment.launcher, 'dependencies_ready', return_value=True), \
             patch.object(deployment.launcher, 'setup', side_effect=install) as setup, \
             patch.object(deployment, 'self_test', return_value=smoke) as test, \
             patch('chaos.deployment_settings.configure', return_value={'status': 'ready'}):
            self.assertEqual(self.invoke('setup', '--yes')[0], 0)
            setup.assert_not_called()
            code, report, progress = self.invoke('setup', '--yes', '--reinstall')
            self.assertEqual(code, 0, report)
            self.assertEqual(setup.call_count, 1)
            self.assertEqual(report['stagedSelfTest'], smoke)
            self.assertEqual(test.call_args_list[-2].kwargs['deps'], staged)
            self.assertIn('重新暂存安装', progress)

    def test_reinstall_rejects_doctor_core_and_skip_install_without_installer(self):
        with patch.object(deployment.launcher, 'setup') as setup:
            for arguments in [('doctor',), ('configure', '--profile', 'core'), ('setup', '--skip-install')]:
                with self.subTest(arguments=arguments):
                    code, report, _ = self.invoke(*arguments, '--reinstall')
                    self.assertEqual(code, 1)
                    self.assertIn('--profile standard', report['error'])
            setup.assert_not_called()
        self.assertFalse((self.root / '.chaos/deployment.json').exists())

    def test_installed_sdk_only_checks_host_dependencies_and_refuses_reinstall(self):
        smoke = {'ok': True, 'checks': [{'name': 'runtime.async', 'status': 'passed', 'value': [40, 42]}]}
        self.assertFalse(deployment.environment_report(self.root)['bundled'])
        with patch.object(deployment.launcher, 'setup') as setup, \
             patch.object(deployment, 'self_test', return_value=smoke), \
             patch('chaos.deployment_settings.configure', return_value={'status': 'ready'}):
            code, report, progress = self.invoke('configure', '--yes', '--profile', 'standard')
            self.assertEqual(code, 0, report)
            self.assertIn('仅检查当前依赖', progress)
            before = (self.root / '.chaos/deployment.json').read_bytes()
            code, report, _ = self.invoke('setup', '--yes', '--reinstall')
            self.assertEqual(code, 1)
            self.assertIn('bundled project', report['error'])
            self.assertEqual((self.root / '.chaos/deployment.json').read_bytes(), before)
            setup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
