"""Native group analysis preserves runtime identities without executing code."""
import unittest
from unittest.mock import patch
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator


class TaskGroupIDE(unittest.TestCase):
    def symbols(self, source):
        return {item['name']: item for item in analyze(source)['symbols']}

    def test_factory_group_metadata_and_type_annotation_contract(self):
        table=builtin_symbols()
        self.assertEqual(table['task_group'].parameters, [('errors', 'String', 'keyword_only', True)])
        self.assertEqual(table['task_group'].returns, 'TaskGroup')
        self.assertEqual(table['task_group_info'].parameters, [('group','TaskGroup','positional',False)])
        expected={'id':'String','state':'String','ownerTaskId':'Any','bodyTaskId':'Any','parentGroupId':'Any',
                  'memberTaskIds':'List','memberCount':'Int','membersTruncated':'Bool','location':'Record'}
        self.assertEqual({key:info.type for key,info in table['task_group_info'].result_info.members.items()},expected)
        self.assertEqual({item['label'] for item in analyze('let info=task_group_info(task_group());info.')['completions']},set(expected))
        annotated=analyze('fn register(group:TaskGroup,task:Task){group.spawn(task)}')
        self.assertFalse(annotated['diagnostics'])
        self.assertTrue(any(row['type']=='type' for row in annotated['tokens']))
        self.assertEqual(self.symbols('fn build()->TaskGroup{task_group()};let group=build();')['group']['type'],'TaskGroup')

    def test_async_context_alias_binding_and_scope(self):
        source='let factory=task_group;async with factory() as group {let info=task_group_info(group);group.spawn(input)}'
        result=analyze(source)
        self.assertTrue(result['complete']);self.assertFalse(result['diagnostics'])
        self.assertEqual(self.symbols(source)['group']['type'],'TaskGroup')
        self.assertTrue(any(row['code']=='undefined_name' for row in analyze(source+';group')['diagnostics']))
        self.assertEqual(self.symbols('with task_group() as group {group}')['group']['type'],'Any')

    def test_spawn_retains_awaited_type_positional_keyword_and_method_alias(self):
        prefix='async fn work()->Int{42};async with task_group() as group {'
        for body in ('let task=group.spawn(work());let answer=await task;',
                     'let task=group.spawn(task=work());let answer=await task;',
                     'let start=group.spawn;let task=start(work());let answer=await task;'):
            with self.subTest(body=body):
                values=self.symbols(prefix+body+'answer}')
                self.assertEqual(values['task']['type'],'Task');self.assertEqual(values['answer']['type'],'Int')
        source='async fn* stream(){yield 42};async with task_group() as group {let step=await group.spawn(anext(stream()));let done=step.done;done}'
        self.assertEqual(self.symbols(source)['done']['type'],'Bool')

    def test_member_completion_and_known_invalid_argument(self):
        source='async with task_group() as group {group.'
        self.assertEqual({row['label'] for row in analyze(source)['completions']},{'spawn'})
        for source in ('task_group_info(42)','async with task_group() as group {group.spawn(42)}'):
            self.assertTrue(any(row['code']=='type_mismatch' for row in analyze(source)['diagnostics']))
        result=analyze('async with task_group() as group {group.spawn(input)}')
        self.assertFalse(result['diagnostics'])

    def test_shadowed_factory_dynamic_arguments_and_mutable_tasks_are_conservative(self):
        samples=['fn task_group()->Record{{spawn:fn(task)->String{"local"}}};async with task_group() as group {let answer=await group.spawn(input);answer}',
                 'async with task_group() as group {let answer=await group.spawn(*input);answer}',
                 'async fn f()->Int{42};var task=f();async with task_group() as group {let answer=await group.spawn(task);answer}']
        for source in samples:
            with self.subTest(source=source):self.assertEqual(self.symbols(source)['answer']['type'],'Any')

    def test_analysis_never_runs_group_factory_members_or_info(self):
        from chaos import task_groups
        with patch.object(task_groups,'info',side_effect=AssertionError('must not inspect runtime group')), \
             patch.object(Evaluator,'run',side_effect=AssertionError('must not run source')):
            result=analyze('async fn f()->Int{42};async with task_group() as group {let t=group.spawn(f());[await t,task_group_info(group)]}')
        self.assertTrue(result['complete']);self.assertFalse(result['diagnostics'])
