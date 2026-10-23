"""Task inspection uses real builtin metadata without executing a scheduler."""
import unittest
from unittest.mock import patch
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator
from chaos.suspension import Scheduler


class TaskInfoIDE(unittest.TestCase):
    def symbols(self, source):
        return {item['name']: item['type'] for item in analyze(source)['symbols']}

    def test_task_info_signature_and_record_contract(self):
        info = builtin_symbols()['task_info']
        self.assertEqual(info.parameters, [('task', 'Task', 'positional', False)])
        expected = {'id':'String','state':'String','phase':'String','kind':'String',
                    'parentTaskId':'Any','groupId':'Any','channelId':'Any','memberTaskIds':'List','memberCount':'Int',
                    'membersTruncated':'Bool','cancellationRequested':'Bool','cancellationDeferred':'Bool','shieldDepth':'Int','observed':'Bool','location':'Record','timing':'Any','selection':'Any'}
        self.assertEqual({key:value.type for key,value in info.result_info.members.items()}, expected)
        source = 'async fn work()->Int{42};let state=task_info(work());'
        result = analyze(source + 'state.')
        self.assertEqual({item['label'] for item in result['completions']}, set(expected))
        values = self.symbols(source + 'let phase=state.phase;let n=state.memberCount;let limited=state.membersTruncated')
        self.assertEqual([values[name] for name in ('phase','n','limited')], ['String','Int','Bool'])

    def test_known_wrong_task_argument_is_diagnosed(self):
        self.assertTrue(any(item['code']=='type_mismatch' for item in analyze('task_info(42)')['diagnostics']))
        self.assertFalse(analyze('fn inspect(value){task_info(value)}')['diagnostics'])

    def test_spawn_preserves_positional_keyword_and_alias_completion(self):
        source = 'async fn work()->Int{42};let start=spawn;'
        for expression in ('spawn(work())','spawn(task=work())','start(work())'):
            with self.subTest(expression=expression):
                values = self.symbols(source + 'let task=' + expression + ';let answer=await task;answer')
                self.assertEqual(values['task'], 'Task')
                self.assertEqual(values['answer'], 'Int')

    def test_spawn_retains_structured_awaited_metadata(self):
        source = 'async fn* stream(){yield 42};let task=spawn(anext(stream()));let result=await task;result.'
        self.assertEqual({item['label'] for item in analyze(source)['completions']}, {'done','value'})
        values = self.symbols(source[:-len('result.')] + 'let done=result.done;done')
        self.assertEqual(values['done'], 'Bool')

    def test_dynamic_spread_mutable_binding_and_shadowed_spawn_stay_conservative(self):
        for source in ('let task=spawn(*input);let answer=await task;',
                       'async fn f()->Int{42};var task=f();let answer=await spawn(task);',
                       'async fn f()->Int{42};fn spawn(task)->String{"local"};let answer=await spawn(f());'):
            with self.subTest(source=source):
                self.assertEqual(self.symbols(source)['answer'], 'Any')

    def test_gather_completion_remains_list(self):
        values = self.symbols('async fn f()->Int{42};let joined=gather([f(),f()]);let answers=await spawn(joined)')
        self.assertEqual(values['answers'], 'List')

    def test_editor_analysis_never_observes_starts_or_executes_tasks(self):
        with patch.object(Scheduler, 'info', autospec=True, side_effect=AssertionError('must not inspect task')), \
             patch.object(Scheduler, 'spawn', autospec=True, side_effect=AssertionError('must not start task')), \
             patch.object(Evaluator, 'run', side_effect=AssertionError('must not evaluate')):
            result = analyze('async fn work()->Int{42};let task=spawn(work());task_info(task)')
        self.assertTrue(result['complete'])
