"""The terminal assistant uses existing server jobs, proposals and CAS writes."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chaos import Client, Framework, Settings
from chaos.assistant_cli import main
from chaos.client import ConflictError, TransportError
from chaos.server import make_server


def job(status='running', **fields):
    return dict({'id': 'job-1', 'projectId': 'project', 'sessionId': 'conversation',
                 'baseVersion': 7, 'status': status}, **fields)


def event(number, kind='task.iteration', task_id='job-1'):
    return {'id': number, 'type': kind, 'taskId': task_id,
            'payload': {'summary': 'observable progress'}, 'createdAt': 1}


class AssistantCLI(unittest.TestCase):
    def setUp(self):
        self.client = Mock(timeout=30)
        self.client.get_project.return_value = {'version': 7}
        self.client.start_assistant.return_value = job()
        self.client.assistant.return_value = job('completed')
        self.client.cancel_assistant.return_value = job('running')
        self.client.assistant_events.return_value = {'items': [event(1, 'task.completed')]}

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('chaos.assistant_cli.Client', return_value=self.client), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv + ['--project', 'project'])
        return code, [json.loads(row) for row in stdout.getvalue().splitlines()], stderr.getvalue()

    def test_ask_resolves_version_once_and_never_applies_or_polls_by_default(self):
        code, output, errors = self.run_cli(['ask', '解释 fn answer(){42};answer()'])
        self.assertEqual(code, 0)
        self.assertEqual(errors, '')
        self.assertEqual(output, [job()])
        self.client.get_project.assert_called_once_with('project')
        self.client.start_assistant.assert_called_once_with('project', 7, '解释 fn answer(){42};answer()', session_id=None)
        self.client.apply_assistant.assert_not_called()
        self.client.assistant.assert_not_called()

    def test_explicit_version_and_session_keep_same_conversation(self):
        self.run_cli(['ask', '继续', '--version', '4', '--session', 'existing'])
        self.client.get_project.assert_not_called()
        self.client.start_assistant.assert_called_once_with('project', 4, '继续', session_id='existing')

    def test_stream_drains_full_pages_and_waits_for_terminal_event_commit(self):
        self.client.assistant_events.side_effect = [
            {'items': [event(i) for i in range(1, 201)]},
            {'items': []},
            {'items': [event(201, 'task.completed')]},
        ]
        with patch('chaos.assistant_cli.time.sleep'):
            code, output, _ = self.run_cli(['ask', '检查', '--stream'])
        self.assertEqual(code, 0)
        self.assertEqual(output[0]['type'], 'assistant.accepted')
        self.assertEqual(output[-1]['type'], 'assistant.result')
        self.assertEqual(output[-1]['job']['status'], 'completed')
        self.assertEqual(output[-1]['after'], 201)
        self.assertEqual([x['id'] for x in output[1:-1]], list(range(1, 202)))
        self.assertEqual([call.kwargs['after'] for call in self.client.assistant_events.call_args_list], [0, 200, 200])
        self.client.cancel_assistant.assert_not_called()

    def test_failed_and_cancelled_streams_have_nonzero_exit_and_same_job_result(self):
        for status, expected in [('failed', 1), ('cancelled', 130), ('interrupted', 130)]:
            with self.subTest(status=status):
                self.client.assistant.return_value = job(status)
                self.client.assistant_events.return_value = {'items': [event(1, 'task.' + status)]}
                code, output, _ = self.run_cli(['ask', '检查', '--stream'])
                self.assertEqual(code, expected)
                self.assertEqual(output[-1]['jobId'], 'job-1')
                self.assertEqual(output[-1]['job']['status'], status)

    def test_interrupt_cancels_only_created_job_with_request_receipt(self):
        self.client.assistant_events.side_effect = KeyboardInterrupt()
        code, _, errors = self.run_cli(['ask', '检查', '--stream'])
        self.assertEqual(code, 130)
        self.client.cancel_assistant.assert_called_once_with('project', 'job-1')
        receipt = json.loads(errors)
        self.assertEqual(receipt['cancellation'], {'requested': True, 'status': 'running'})
        self.assertEqual(receipt['jobId'], 'job-1')
        self.assertEqual(self.client.timeout, 30)

    def test_interrupt_cancellation_transport_failure_is_not_claimed_stopped(self):
        self.client.assistant_events.side_effect = KeyboardInterrupt()
        self.client.cancel_assistant.side_effect = TransportError('connection failed')
        code, _, errors = self.run_cli(['ask', '检查', '--stream'])
        self.assertEqual(code, 130)
        self.assertFalse(json.loads(errors)['cancellation']['confirmed'])

    def test_interrupt_observing_other_job_never_cancels(self):
        self.client.assistant.side_effect = KeyboardInterrupt()
        code, _, _ = self.run_cli(['show', 'existing-job'])
        self.assertEqual(code, 130)
        self.client.cancel_assistant.assert_not_called()

    def test_interrupt_during_submission_reports_unknown_outcome_without_retry(self):
        self.client.start_assistant.side_effect = KeyboardInterrupt()
        code, _, errors = self.run_cli(['ask', '检查'])
        self.assertEqual(code, 130)
        self.assertTrue(json.loads(errors)['submissionUnknown'])
        self.client.start_assistant.assert_called_once()
        self.client.cancel_assistant.assert_not_called()

    def test_timeout_ends_observation_without_cancellation_and_restores_http_timeout(self):
        self.client.assistant_events.side_effect = TimeoutError('Observation deadline reached')
        code, _, errors = self.run_cli(['ask', '检查', '--stream'])
        self.assertEqual(code, 124)
        self.assertEqual(json.loads(errors)['jobId'], 'job-1')
        self.assertIn('no cancellation', json.loads(errors)['message'])
        self.client.cancel_assistant.assert_not_called()
        self.assertEqual(self.client.timeout, 30)

    def test_real_observation_budget_caps_requests_and_exits_without_stopping_job(self):
        observed_timeouts = []
        def read_events(*args, **kwargs):
            observed_timeouts.append(self.client.timeout)
            return {'items': []}
        self.client.assistant_events.side_effect = read_events
        self.client.assistant.return_value = job()
        with patch('chaos.assistant_cli.time.monotonic', side_effect=[0, 0, 0, 0, 0, .2]):
            code, _, errors = self.run_cli(['ask', '检查', '--stream', '--timeout', '.1'])
        self.assertEqual(code, 124)
        self.assertEqual(observed_timeouts, [.1])
        self.assertEqual(json.loads(errors)['jobId'], 'job-1')
        self.client.cancel_assistant.assert_not_called()
        self.assertEqual(self.client.timeout, 30)

    def test_start_conflict_is_not_retried_or_reported_as_unknown_submission(self):
        self.client.start_assistant.side_effect = ConflictError(409, 'conflict', 'Project version changed')
        code, _, errors = self.run_cli(['ask', '检查'])
        self.assertEqual(code, 1)
        self.assertNotIn('submissionUnknown', json.loads(errors))
        self.client.get_project.assert_called_once()
        self.client.start_assistant.assert_called_once()
        self.client.cancel_assistant.assert_not_called()

    def test_events_history_cancel_delegate_without_starting_or_applying(self):
        self.client.assistant_events.return_value = {'items': []}
        self.client.assistant_history.return_value = []
        self.assertEqual(self.run_cli(['events', 'existing', '--after', '42', '--limit', '50'])[0], 0)
        self.client.assistant_events.assert_called_once_with('project', 'existing', after=42, limit=50)
        self.assertEqual(self.run_cli(['history', '--session', 'existing'])[0], 0)
        self.client.assistant_history.assert_called_once_with('project', session_id='existing')
        self.assertEqual(self.run_cli(['cancel', 'existing'])[0], 0)
        self.client.cancel_assistant.assert_called_once_with('project', 'existing')
        self.client.start_assistant.assert_not_called()
        self.client.apply_assistant.assert_not_called()

    def test_apply_requires_explicit_version_and_does_not_refetch_after_conflict(self):
        with redirect_stderr(io.StringIO()), patch('chaos.assistant_cli.Client') as factory:
            with self.assertRaises(SystemExit) as caught:
                main(['apply', 'existing', '--project', 'project'])
            self.assertEqual(caught.exception.code, 2)
            factory.assert_not_called()
        self.client.apply_assistant.side_effect = ConflictError(409, 'conflict', 'Project version changed')
        code, _, errors = self.run_cli(['apply', 'existing', '--version', '7'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(errors)['error']['status'], 409)
        self.client.apply_assistant.assert_called_once_with('project', 'existing', 7)
        self.client.get_project.assert_not_called()

    def test_malformed_stream_identity_is_rejected_without_cancelling_remote_job(self):
        self.client.assistant_events.return_value = {'items': [event(1, task_id='other-job')]}
        code, output, errors = self.run_cli(['ask', '检查', '--stream'])
        self.assertEqual(code, 1)
        self.assertEqual(len(output), 1)
        self.assertIn('invalid event identity', json.loads(errors)['error'])
        self.client.cancel_assistant.assert_not_called()

    def test_invalid_limits_and_nonfinite_deadlines_rejected_before_network(self):
        cases = [['ask', 'hi', '--timeout', 'nan'], ['ask', 'hi', '--timeout', '0'],
                 ['events', 'j', '--limit', '1001'], ['events', 'j', '--after', '-1'],
                 ['ask', 'hi', '--version', '0'], ['ask', '', '--request-timeout', 'inf']]
        for argv in cases:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), patch('chaos.assistant_cli.Client') as factory:
                with self.assertRaises(SystemExit) as caught:
                    main(argv + ['--project', 'project'])
                self.assertEqual(caught.exception.code, 2)
                factory.assert_not_called()


class AssistantHTTP(unittest.TestCase):
    def test_real_server_harness_draft_review_stream_session_and_explicit_apply(self):
        requests, clients = [], []
        class Model:
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)
                self.closed = False
            def create(self, **kwargs):
                requests.append(kwargs)
                aliases = {tool['function']['description'].split(':', 1)[0]: tool['function']['name']
                           for tool in kwargs['tools']}
                count = sum(message['role'] == 'tool' for message in kwargs['messages'])
                if count == 0:
                    actions = [('language.reference', {'topic': 'getting-started'}),
                               ('draft.write', {'path': 'main.chaos', 'source': 'fn answer(){42};answer()'}),
                               ('draft.validate', {}), ('draft.evaluate', {})]
                    calls = [SimpleNamespace(id='c' + str(index), function=SimpleNamespace(
                        name=aliases[name], arguments=json.dumps(value))) for index, (name, value) in enumerate(actions)]
                    message = SimpleNamespace(content=None, tool_calls=calls)
                else:
                    message = SimpleNamespace(content='已验证返回 42；等待应用。', tool_calls=[])
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])
            def close(self):
                self.closed = True
        def factory(config):
            self.assertEqual(config['model'], 'fixture-coder')
            value = Model()
            clients.append(value)
            return value
        settings = Settings(':memory:', client_factory=factory)
        settings.update({'providers': {'text': {'baseURL': 'https://fixture.invalid/v1', 'apiKey': 'local-test-secret'}},
                         'roles': {'executor': {'model': 'fixture-coder', 'autoUpgrade': False}},
                         'runtime': {'timeout': 10}}, 1)
        with tempfile.TemporaryDirectory() as tmp, Framework(settings=settings) as app:
            server = make_server(port=0, framework=app, state_dir=Path(tmp) / 'state', update_home=Path(tmp) / 'updates')
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                origin = 'http://127.0.0.1:' + str(server.server_port)
                client = Client(origin)
                initial = client.create_project('project', source='input')
                def invoke(argv):
                    output, errors = io.StringIO(), io.StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        code = main(argv + ['--server', origin, '--project', 'project'])
                    return code, [json.loads(row) for row in output.getvalue().splitlines()], errors.getvalue()
                code, output, errors = invoke(['ask', '请根据语言文档改成返回 42', '--stream', '--poll-interval', '.05', '--timeout', '10'])
                self.assertEqual(code, 0, errors)
                result = output[-1]['job']
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['checks'][0]['output'], 42)
                self.assertTrue(result['validation']['ok'])
                self.assertEqual(client.get_project('project')['source'], 'input')
                self.assertTrue(any(row.get('type') == 'task.completed' for row in output))
                self.assertNotIn('local-test-secret', json.dumps(output))
                self.assertEqual(invoke(['history', '--session', result['sessionId']])[1][0][0]['id'], result['id'])
                code, changed, errors = invoke(['apply', result['id'], '--version', str(initial['version'])])
                self.assertEqual(code, 0, errors)
                self.assertEqual(client.start('project', changed[0]['version'], None, mode='source').result(), 42)
                self.assertTrue(clients and all(model.closed for model in clients))
                self.assertEqual(len(requests), 2)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(2)


if __name__ == '__main__':
    unittest.main()
