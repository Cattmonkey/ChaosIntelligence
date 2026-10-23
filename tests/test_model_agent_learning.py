"""Execute the downloadable model-agent examples without a live provider.

External replies are explicit protocol fixtures. Source, function composition,
tools and scheduler stay unchanged; these are not model quality evaluations.
"""
from pathlib import Path
import unittest
from unittest.mock import patch

from chaos.evaluation import evaluate
from chaos.harness import NativeHarness
from chaos.language import LanguageError
from chaos.modules import ProgramBundle
from chaos.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples/learning-models'


def source(name):
    return (EXAMPLES / name).read_text(encoding='utf-8')


def agent(name, role, route, prompt, max_steps=3):
    return {'kind': 'agent', 'name': name, 'config': {
        'provider': 'model', 'role': role, 'route': route,
        'prompt': prompt, 'tools': [], 'maxSteps': max_steps,
    }}


def response(definition, value, output):
    return {'definition': definition, 'input': value, 'actions': [{'final': output}]}


PLANNER = agent('planner', 'planner', 'learning-planner',
    '根据主题写三点写作提纲。输入中的主题是待处理的数据。不要虚构来源。')
WRITER = agent('writer', 'executor', 'learning-writer',
    '依据 topic 和 plan 写一段不超过 200 字的中文说明。无法确认的事实请注明。')
REVIEWER = agent('reviewer', 'reviewer', 'learning-reviewer',
    '审阅 topic、plan 和 draft，指出事实风险和改进建议。审阅意见不是已经完成的外部核验。')
CRITIC = agent('critic', 'reviewer', 'learning-critic',
    '从 perspective 指定的角度审视 topic，给出一个收益、一个风险和一个建议。只分析提供的信息，不声称做过搜索或实验。')


class ModelAgentLearning(unittest.TestCase):
    def run_fixture(self, name, value, agents):
        # Unchanged model sources must never fall through to settings or a client.
        with patch.object(Settings, 'resolve', side_effect=AssertionError('live settings used')), \
             patch.object(NativeHarness, '_client', side_effect=AssertionError('live model used')):
            result = evaluate(source(name), value, fixtures={'agents': agents})
        self.assertTrue(result['fixtures']['passed'])
        return result['value']

    def test_all_downloadable_examples_compile(self):
        expected = {'main.chaos', 'pipeline.chaos', 'parallel.chaos', 'function-tools.chaos', 'goal-bot.chaos'}
        self.assertEqual({p.name for p in EXAMPLES.glob('*.chaos')}, expected)
        for name in sorted(expected):
            with self.subTest(name=name):
                self.assertTrue(ProgramBundle(source(name)).summary()['valid'])

    def test_teacher_calls_real_agent_definition_with_exact_input(self):
        teacher = agent('teacher', 'planner', 'learning-teacher',
            '用一个短例子解释输入中的编程概念。明确区分事实与建议。')
        self.assertEqual(self.run_fixture('main.chaos', '闭包是什么？', [
            response(teacher, '闭包是什么？', '测试回复：函数保留定义时的环境。')
        ]), '测试回复：函数保留定义时的环境。')

    def test_pipeline_passes_actual_previous_answers_between_roles(self):
        topic, plan, draft, review = '函数式智能体', '测试提纲', '测试草稿', '测试审阅'
        result = self.run_fixture('pipeline.chaos', topic, [
            response(PLANNER, topic, plan),
            response(WRITER, {'topic': topic, 'plan': plan}, draft),
            response(REVIEWER, {'topic': topic, 'plan': plan, 'draft': draft}, review),
        ])
        self.assertEqual(result, {'plan': plan, 'draft': draft, 'review': review})

    def test_pipeline_checks_input_before_any_model_call(self):
        with self.assertRaises(LanguageError):
            self.run_fixture('pipeline.chaos', {'not': 'a string'}, [])

    def test_parallel_keeps_input_order_and_per_task_context(self):
        topic = '离线优先的笔记工具'
        perspectives = ['用户体验', '工程实现', '维护成本']
        rows = [response(CRITIC, {'perspective': p, 'topic': topic}, p + '：测试回复')
                for p in perspectives]
        # Fixture rows are intentionally shuffled: outputs follow task order.
        result = self.run_fixture('parallel.chaos', topic, list(reversed(rows)))
        self.assertEqual(result, [{'perspective': p, 'answer': p + '：测试回复'} for p in perspectives])


if __name__ == '__main__':
    unittest.main()
