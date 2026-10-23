"""Deployment advice uses the shared, bounded native agent without capabilities."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chaos.control import plugin_host
from chaos.deployment_settings import _advice
from chaos.harness import NativeHarness
from chaos.settings import Settings


def answer(text=None, calls=None):
    return {'choices': [{'message': {'content': text, 'tool_calls': calls}}]}


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed += 1


class DeploymentIntelligenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.path = Path(temporary.name, 'settings.json')
        self.settings = Settings(self.path)
        self.settings.update({'providers': {'text': {'apiKey': 'private-primary-fixture'}},
            'runtime': {'maxSteps': 8, 'timeout': 90}}, self.settings.public()['version'])
        self.hosts = []
        self.events = []

    def update(self, changes):
        self.settings.update(changes, self.settings.public()['version'])

    def advice(self, responses, environment=None):
        before = self.path.read_bytes()
        client = Client(responses)
        def make_host(settings):
            host = plugin_host(settings)
            self.hosts.append(host)
            return host
        with patch('chaos.deployment_settings.plugin_host', side_effect=make_host), \
                patch.object(self.settings, 'client', return_value=client) as client_factory:
            receipt = _advice(self.settings, environment or {}, lambda *e: self.events.append(e))
        self.assertEqual(before, self.path.read_bytes())
        for host in self.hosts:
            self.assertTrue(all(row['state'] == 'disposed' for row in host.inventory()))
        return receipt, client, client_factory

    def test_shared_fallback_health_and_budgets_survive_retry(self):
        self.update({'providers': {'text': {'models': [{'id': 'gpt-6-astra'}, {'id': 'gpt-6-sol'}]}}})
        route = self.settings.resolve('intelligence-deployment', 'planner')
        self.assertGreaterEqual(len(route['candidates']), 2)
        failed = RuntimeError('private-primary-fixture transport details')
        failed.status_code = 503
        with patch.object(self.settings, 'report_failure', wraps=self.settings.report_failure) as failure, \
                patch.object(self.settings, 'report_success', wraps=self.settings.report_success) as success:
            receipt, client, factory = self.advice([failed, answer('部署建议')])
        self.assertEqual(receipt, {'status': 'completed', 'text': '部署建议'})
        self.assertEqual([request['model'] for request in client.requests], route['candidates'][:2])
        failure.assert_called_once_with('text', route['candidates'][0])
        success.assert_called_once_with('text', route['candidates'][1])
        self.assertEqual(self.settings.resolve('intelligence-deployment', 'planner')['model'], route['candidates'][1])
        config = factory.call_args.kwargs['resolved']
        self.assertEqual((config['maxSteps'], config['maxOutputTokens'], config['timeout']), (1, 384, 20))
        for request in client.requests:
            self.assertEqual(request['max_tokens'], 384)
            self.assertGreater(request['timeout'], 0)
            self.assertLessEqual(request['timeout'], 20)
            self.assertNotIn('tools', request)
            self.assertNotIn('tool_choice', request)
        self.assertEqual(client.closed, 1)

    def test_agent_override_endpoint_key_model_backend_and_shorter_timeout_are_used(self):
        self.update({'runtime': {'backend': 'codex', 'timeout': 5},
            'agents': {'intelligence-deployment': {'provider': 'grok', 'role': 'reviewer',
                'baseURL': 'https://deployment.example/v1', 'apiKey': 'private-agent-fixture',
                'model': 'deployment-reviewer', 'backend': 'native', 'autoUpgrade': False}}})
        receipt, client, factory = self.advice([answer('ok')])
        self.assertEqual(receipt['status'], 'completed')
        config = factory.call_args.kwargs['resolved']
        self.assertEqual(config['role'], 'reviewer')
        self.assertEqual(config['provider'], 'grok')
        self.assertEqual(config['baseURL'], 'https://deployment.example/v1')
        self.assertEqual(config['apiKey'], 'private-agent-fixture')
        self.assertEqual(config['model'], 'deployment-reviewer')
        self.assertEqual(config['backend'], 'native')
        self.assertEqual(config['timeout'], 5)
        self.assertLessEqual(client.requests[0]['timeout'], 5)

    def test_disabled_plugin_skips_before_client_creation_and_is_closed(self):
        self.update({'plugins': [{'id': 'native', 'enabled': False}]})
        receipt, client, factory = self.advice([])
        self.assertEqual(receipt['status'], 'skipped')
        factory.assert_not_called()
        self.assertEqual(client.requests, [])
        self.assertEqual(len(self.hosts), 1)
        self.assertEqual(self.events, [])

    def test_non_native_backend_never_launches_or_silently_switches(self):
        self.update({'agents': {'intelligence-deployment': {'backend': 'codex'}}})
        with patch('chaos.harness.CodexHarness.run', side_effect=AssertionError('Must not launch')) as codex:
            receipt, client, factory = self.advice([])
        self.assertEqual(receipt['status'], 'skipped')
        self.assertIn('native', receipt['reason'])
        factory.assert_not_called()
        codex.assert_not_called()
        self.assertEqual(client.requests, [])
        self.assertEqual(self.hosts, [])
        self.assertEqual(self.events, [])

    def test_missing_key_skips_before_plugins_and_client(self):
        self.update({'providers': {'text': {'clearKey': True}}})
        receipt, client, factory = self.advice([])
        self.assertEqual(receipt['status'], 'skipped')
        factory.assert_not_called()
        self.assertEqual(client.requests, [])
        self.assertEqual(self.hosts, [])
        self.assertEqual(self.events, [])

    def test_undeclared_tool_response_fails_without_executing_any_tools(self):
        response = answer(calls=[{'id': 'request', 'function': {'name': 'code.execute',
            'arguments': '{"code":"raise AssertionError()"}'}}])
        with patch('chaos.execution.CodeExecutor.run', side_effect=AssertionError('No code capability')) as execute:
            receipt, client, _ = self.advice([response])
        self.assertEqual(receipt['status'], 'failed')
        execute.assert_not_called()
        self.assertEqual(len(client.requests), 1)
        self.assertNotIn('tools', client.requests[0])
        self.assertEqual(client.closed, 1)

    def test_untrusted_text_redacts_all_configured_keys_and_terminal_controls(self):
        self.update({'providers': {'grok': {'apiKey': 'private-secondary-fixture'}}})
        text = '\x1b[31m建议\x1b[0m private-primary-fixture private-secondary-fixture\u202e\x1b]0;danger\x07'
        receipt, client, _ = self.advice([answer(text)], environment={
            'system': 'Darwin\x1b[31m', 'architecture': 'arm64', 'pythonVersion': '3.13',
            'apiKey': 'private-inventory-fixture', 'path': '/private/source', 'files': ['user.chaos'],
            'gpu': {'path': '/private/gpu'}})
        self.assertEqual(receipt['status'], 'completed')
        public = json.dumps([receipt, self.events, client.requests])
        for forbidden in ('private-primary-fixture', 'private-secondary-fixture', 'private-inventory-fixture',
                          '/private/source', 'user.chaos', '/private/gpu', '\\u001b', '\\u202e', 'danger'):
            self.assertNotIn(forbidden, public)
        inventory = json.loads(client.requests[0]['messages'][1]['content'])['environment']
        self.assertEqual(inventory, {'system': 'Darwin', 'architecture': 'arm64', 'pythonVersion': '3.13'})

    def test_empty_answer_and_transport_errors_stay_nonfatal_and_private(self):
        for response in (answer('  '), answer('\x1b[31m\x1b[0m\u202e'), RuntimeError('private-primary-fixture failure')):
            with self.subTest(response=type(response).__name__):
                receipt, client, _ = self.advice([response])
                self.assertEqual(receipt['status'], 'failed')
                self.assertNotIn('private-primary-fixture', json.dumps(receipt))
                self.assertEqual(client.closed, 1)


class NativeOutputBudgetTests(unittest.TestCase):
    config = {'provider': 'text', 'baseURL': 'https://example.invalid/v1', 'apiKey': 'fixture',
              'model': 'primary', 'timeout': 5, 'maxSteps': 1}

    def test_invalid_output_budget_rejected_before_client_creation(self):
        for budget in (None, 0, -1, 65537, True, False, 1.5, '384', [], {}):
            with self.subTest(budget=budget):
                factory = Mock(side_effect=AssertionError('Must validate before creating client'))
                harness = NativeHarness({**self.config, 'maxOutputTokens': budget}, client_factory=factory)
                with self.assertRaisesRegex(ValueError, 'maxOutputTokens'):
                    harness.run('task', 'input')
                factory.assert_not_called()

    def test_boundary_output_budgets_and_unspecified_backwards_compatibility(self):
        for options in ({}, {'maxOutputTokens': 1}, {'maxOutputTokens': 65536}):
            with self.subTest(options=options):
                client = Client([answer('done')])
                harness = NativeHarness({**self.config, **options}, client_factory=lambda config: client)
                self.assertEqual(harness.run('task', 'input'), 'done')
                if options:
                    self.assertEqual(client.requests[0]['max_tokens'], options['maxOutputTokens'])
                else:
                    self.assertNotIn('max_tokens', client.requests[0])
                self.assertEqual(client.closed, 1)


if __name__ == '__main__':
    unittest.main()
