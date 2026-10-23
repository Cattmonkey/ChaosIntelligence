"""Learning sources keep provider:model; replace only external model responses."""
from pathlib import Path
import copy
import unittest
from unittest.mock import patch

from chaos import Framework, Settings
from chaos.framework import RunFailed
from chaos.evaluation import evaluate
from chaos.harness import NativeHarness
from chaos.language import LanguageError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples' / 'learning-models'
BUDGET_INPUT = {'items': [{'unitCents': 2500, 'quantity': 2}, {'unitCents': 1200, 'quantity': 3}], 'limitCents': 10000}
BUDGET_RESULT = {'totalCents': 8600, 'limitCents': 10000, 'remainingCents': 1400, 'withinBudget': True}
ACCOUNTANT = {'kind': 'agent', 'name': 'accountant', 'config': {
    'provider': 'model', 'role': 'executor', 'route': 'learning-accountant',
    'prompt': '调用 calculate_budget 工具，参数 input 为 null。根据工具返回的整数分金额解释是否超出预算。只引用工具结果，不自行编造金额。',
    'tools': [], 'maxSteps': 4}}


class ModelFunctionToolLearningTests(unittest.TestCase):
    def setUp(self):
        self.source = (EXAMPLES / 'function-tools.chaos').read_text()
        settings_patch = patch.object(Settings, 'resolve', side_effect=AssertionError('fixture used real settings'))
        client_patch = patch.object(NativeHarness, '_client', side_effect=AssertionError('fixture used network'))
        self.no_settings = settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.no_client = client_patch.start()
        self.addCleanup(client_patch.stop)

    def run_case(self, value, actions):
        return evaluate(self.source, value, fixtures={'agents': [
            {'definition': copy.deepcopy(ACCOUNTANT), 'input': value, 'actions': actions}]})

    def test_model_calls_actual_language_tool_and_keeps_audit_result(self):
        result = self.run_case(BUDGET_INPUT, [
            {'tool': 'calculate_budget', 'input': None, 'expect': BUDGET_RESULT},
            {'final': '预算内，剩余 14 元。'}])
        self.assertEqual(result['value'], {'budget': BUDGET_RESULT, 'explanation': '预算内，剩余 14 元。', 'toolCalls': 1})
        self.assertTrue(result['fixtures']['passed'])
        self.assertEqual(result['fixtures']['agents'][0]['completed'], 1)

    def test_over_budget_remains_explicit_even_with_unreliable_explanation(self):
        value = {**BUDGET_INPUT, 'limitCents': 8000}
        expected = {**BUDGET_RESULT, 'limitCents': 8000, 'remainingCents': -600, 'withinBudget': False}
        result = self.run_case(value, [
            {'tool': 'calculate_budget', 'input': None, 'expect': expected},
            {'final': 'Untrusted model prose'}])
        self.assertEqual(result['value']['budget'], expected)

    def test_final_without_real_tool_call_is_rejected(self):
        with self.assertRaises(LanguageError) as caught:
            self.run_case(BUDGET_INPUT, [{'final': 'I calculated it'}])
        self.assertEqual(caught.exception.code, 'user_throw')
        self.assertEqual(caught.exception.value, '智能体没有调用预算工具，未通过程序验收')

    def test_model_cannot_replace_the_order_via_tool_input(self):
        with self.assertRaises(LanguageError):
            self.run_case(BUDGET_INPUT, [
                {'tool': 'calculate_budget', 'input': {'limitCents': 99999}, 'expect': BUDGET_RESULT},
                {'final': 'changed'}])

    def test_invalid_input_is_rejected_before_any_model_request(self):
        for value in (
            {**BUDGET_INPUT, 'limitCents': -1},
            {**BUDGET_INPUT, 'items': [{'unitCents': 100, 'quantity': -1}]},
            {**BUDGET_INPUT, 'items': [{'unitCents': 2.5, 'quantity': 1}]},
        ):
            with self.subTest(value=value), self.assertRaises(LanguageError):
                evaluate(self.source, value, fixtures={})
        self.no_settings.assert_not_called()
        self.no_client.assert_not_called()


class ModelBotLearningTests(unittest.TestCase):
    def setUp(self):
        self.source = (EXAMPLES / 'goal-bot.chaos').read_text()
        self.calls = []
        self.routes = []

    def run_case(self, answers, value=None):
        answers = iter(answers)
        calls, routes = self.calls, self.routes
        resolve = Settings.resolve
        def resolved(settings, agent_id='', role='executor'):
            routes.append((agent_id, role))
            return resolve(settings, agent_id, role)
        def respond(harness, prompt, supplied, **kwargs):
            kwargs['checkpoint']()
            self.assertEqual(kwargs['tools'], [])
            self.assertEqual(harness.config['maxSteps'], 2)
            self.assertIn('previous', prompt)
            calls.append(copy.deepcopy(supplied))
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        value = {'topic': '解释本语言', 'minChars': 10, 'maxChars': 30} if value is None else value
        with patch.dict('os.environ', {'CHAOS_MODEL_URL': ''}), \
             patch.object(Settings, 'resolve', resolved), \
             patch.object(NativeHarness, 'run', respond), \
             patch.object(NativeHarness, '_client', side_effect=AssertionError('unexpected model network')):
            with Framework(settings=Settings(':memory:')) as app:
                project = app.create_project('learning-bot', source=self.source, capabilities=[])
                result = app.start(project['id'], project['version'], value, mode='source').result(10)
                events = app.events(project['id'])['items']
        return result, events

    def test_actual_bot_iterates_model_drafts_and_validates_language_contract(self):
        result, events = self.run_case(['短稿', '这是一段满足长度范围的中文说明正文。'])
        self.assertEqual(result['status'], 'achieved')
        self.assertTrue(result['done'])
        self.assertEqual(result['iterations'], 2)
        self.assertEqual(result['output'], {'text': '这是一段满足长度范围的中文说明正文。', 'characters': 18, 'lengthAccepted': True})
        self.assertEqual(self.calls[0]['previous'], '')
        self.assertEqual(self.calls[1]['previous'], '短稿')
        self.assertEqual(self.calls[1]['feedback'], '本次为 2 个字符，请调整到要求的区间')
        self.assertEqual(self.calls[1]['goal'], '产出满足明确长度合同的中文说明')
        self.assertEqual(self.calls[1]['guidance'], [])
        self.assertEqual(self.routes, [('learning-bot-writer', 'executor')] * 2)
        self.assertTrue(any(event['type'] == 'bot.action.completed' for event in events))
        self.assertEqual(result['context']['draft'], result['output']['text'])

    def test_failed_length_contract_exhausts_after_three_calls(self):
        result, unused = self.run_case(['短稿'] * 3)
        self.assertEqual(result['status'], 'exhausted')
        self.assertFalse(result['done'])
        self.assertEqual(result['iterations'], 3)
        self.assertFalse(result['output']['lengthAccepted'])
        self.assertEqual(len(self.calls), 3)

    def test_model_failure_requires_reconciliation_instead_of_success(self):
        result, unused = self.run_case([RuntimeError('fixture model failure')])
        self.assertEqual(result['status'], 'needs_reconciliation')
        self.assertFalse(result['done'])
        self.assertIsNone(result['output'])
        self.assertEqual(len(self.calls), 1)

    def test_invalid_length_or_topic_stops_before_model(self):
        for value in (
            {'topic': '目标', 'minChars': 0, 'maxChars': 30},
            {'topic': '目标', 'minChars': 10, 'maxChars': 9},
            {'topic': '目标', 'minChars': 10, 'maxChars': 1001},
            {'topic': 42, 'minChars': 10, 'maxChars': 30},
        ):
            with self.subTest(value=value), self.assertRaises(RunFailed):
                self.run_case([], value)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.routes, [])


if __name__ == '__main__':
    unittest.main()
