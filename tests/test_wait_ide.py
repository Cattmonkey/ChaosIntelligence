"""Shared concurrency metadata is bound to real Native values and schemas."""
import dataclasses
import inspect
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Native
from chaos.native_specs import SPECS, SHAPES, spec_for
from chaos.objects import ChaosClass
from chaos.suspension import Scheduler


class WaitIDE(unittest.TestCase):
    def test_shared_spec_names_modes_defaults_match_registered_callables(self):
        evaluator=Evaluator();env=evaluator.env.values
        for name,spec in SPECS.items():
            with self.subTest(name=name):
                if name=='Type':
                    self.assertIs(type(env[name]),ChaosClass)
                    self.assertIs(env[name],evaluator.native_type)
                    self.assertIs(env[name].metaclass,env[name])
                    native=env[name].members['__call__'].value
                    self.assertIs(type(native),Native);self.assertEqual(native.spec_id,'Type.__call__')
                    actual=list(inspect.signature(native.call).parameters.values())
                    self.assertEqual(actual[0].name,'cls')
                    actual=actual[1:]
                elif '.' in name:
                    owner,member=name.split('.')
                    if owner=='Type':native=evaluator.native_type.members[member].value
                    else:
                        factory={'TaskGroup':'task_group()','ExitStack':'exit_stack()','AsyncExitStack':'exit_stack(true)'}[owner]
                        native=evaluator.run(factory+'.'+member)
                    self.assertIs(type(native),Native);self.assertEqual(native.spec_id,name)
                    actual=list(inspect.signature(native.call).parameters.values())
                else:
                    native=env[name]
                    self.assertIs(type(native),Native);self.assertEqual(native.spec_id,name)
                    actual=list(inspect.signature(native.call).parameters.values())
                self.assertEqual([p.name for p in actual],[p.name for p in spec.parameters])
                self.assertEqual([p.default is not p.empty for p in actual],[p.optional for p in spec.parameters])
                modes={inspect.Parameter.POSITIONAL_ONLY:'positional_only',inspect.Parameter.POSITIONAL_OR_KEYWORD:'positional',inspect.Parameter.KEYWORD_ONLY:'keyword_only',inspect.Parameter.VAR_POSITIONAL:'varargs',inspect.Parameter.VAR_KEYWORD:'varkw'}
                self.assertEqual([modes[p.kind] for p in actual],[p.mode for p in spec.parameters])
        with self.assertRaises(TypeError):SPECS['fake']=None
        with self.assertRaises(dataclasses.FrozenInstanceError):spec_for('sleep').result_type='String'
        self.assertIsNone(spec_for('unregistered'))

    def test_sleep_wait_signatures_and_nullable_contract(self):
        sleep=analyze('sleep(')['signatureHelp'];self.assertEqual(sleep['parameters'][0]['type'],'Number')
        wait=analyze('task_wait([], timeout=')['signatureHelp']
        self.assertEqual(wait['activeParameter'],2);self.assertEqual(wait['parameters'][2]['type'],'Number | Null')
        self.assertIn('"first"',wait['label']);self.assertIn('不取消成员',wait['label'])
        for source in ['sleep("1")','task_wait([],timeout="bad")']:
            self.assertTrue(any(d['code']=='type_mismatch'for d in analyze(source)['diagnostics']))
        for source in ['sleep(0)','task_wait([],timeout=null)','task_wait([],timeout=1)']:
            self.assertFalse(analyze(source)['diagnostics'])

    def test_awaited_results_alias_and_spawn_preserve_real_shapes(self):
        for expression in ['task_wait([])','spawn(task_wait([]))','task_group().spawn(task_wait([]))']:
            result=analyze('(await '+expression+').')
            self.assertEqual({x['label']for x in result['completions']},{'done','pending','timedOut'})
        values={x['name']:x['type']for x in analyze('let wait=task_wait;let result=await wait([]);let expired=result.timedOut;let n=await sleep(0)')['symbols']}
        self.assertEqual(values['expired'],'Bool');self.assertEqual(values['n'],'Null')

    def test_task_shape_matches_runtime_and_timing_is_nullable(self):
        snapshot=Framework.evaluate('task_info(sleep(1))')
        self.assertEqual(set(snapshot),{f.name for f in SHAPES['TaskInfo']})
        self.assertEqual(set(snapshot['timing']),{f.name for f in SHAPES['TaskTiming']})
        result=analyze('task_info(sleep(1)).timing.')
        self.assertEqual({i['label']for i in result['completions']},{'seconds','until','timeoutSeconds','remainingSeconds'})
        self.assertTrue(all('| Null' in i['detail']for i in result['completions']))
        symbols=builtin_symbols();self.assertEqual(symbols['task_info'].result_info.members['timing'].type,'Any')

    def test_shared_metadata_reaches_document_code_without_evaluation(self):
        from chaos.code_presentation import render_code
        with patch.object(Scheduler,'spawn',side_effect=AssertionError('no task start')),patch.object(Scheduler,'info',side_effect=AssertionError('no task query')),patch.object(Evaluator,'run',side_effect=AssertionError('no evaluation')):
            html=render_code('let wait=task_wait;let result=await wait([]);result.timedOut;await sleep(0)','chaos')
        self.assertIn('task_wait(tasks: List',html);self.assertIn('sleep(seconds: Number)',html)
        self.assertIn('timedOut: Bool',html)


if __name__=='__main__':unittest.main()
