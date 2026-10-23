"""Cancellation metadata is shared and inert, including aliases and context bindings."""
import inspect
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Native
from chaos.native_specs import SHAPES, spec_for


class CancelShieldIDE(unittest.TestCase):
    def symbols(self, source, **kwargs):
        return {item['name']: item for item in analyze(source, **kwargs)['symbols']}

    def test_factory_signature_annotations_and_runtime_shape(self):
        env = Evaluator().env
        for name in ('cancel_shield', 'cancel_shield_info'):
            native = env.get(name)
            self.assertIs(type(native), Native)
            self.assertEqual(native.spec_id, name)
            self.assertEqual(list(inspect.signature(native.call).parameters),
                             [p.name for p in spec_for(name).parameters])
        table = builtin_symbols()
        self.assertEqual(table['cancel_shield'].parameters, [])
        self.assertEqual(table['cancel_shield'].returns, 'CancelShield')
        self.assertEqual(table['cancel_shield_info'].parameters,
                         [('shield', 'CancelShield', 'positional', False)])
        expected = {'id': 'String', 'state': 'String', 'taskId': 'Any', 'location': 'Record',
                    'cancellationRequested': 'Bool', 'cancellationDeferred': 'Bool', 'depth': 'Int'}
        self.assertEqual({k: v.type for k, v in table['cancel_shield_info'].result_info.members.items()}, expected)
        info = Framework.evaluate('cancel_shield_info(cancel_shield())')
        self.assertEqual(set(info), {field.name for field in SHAPES['CancelShieldInfo']})
        self.assertEqual(info['state'], 'new')
        self.assertIsNone(info['taskId'])
        self.assertEqual(info['depth'], 0)
        source = 'fn inspect(value:CancelShield)->CancelShield{value};let shield=inspect(cancel_shield());shield'
        result = analyze(source)
        self.assertFalse(result['diagnostics'])
        self.assertEqual(self.symbols(source)['shield']['type'], 'CancelShield')
        self.assertTrue(any(row['type'] == 'type' for row in result['tokens']))

    def test_both_lexical_contexts_aliases_and_scope(self):
        for syntax in ('with', 'async with'):
            source = 'let protect=cancel_shield;async fn run(){' + syntax + ' protect() as shield {' \
                     'let info=cancel_shield_info(shield);let delayed=info.cancellationDeferred;delayed}}'
            result = analyze(source)
            self.assertTrue(result['complete'])
            self.assertFalse(result['diagnostics'])
            values = self.symbols(source)
            self.assertEqual(values['shield']['type'], 'CancelShield')
            self.assertEqual(values['delayed']['type'], 'Bool')
        self.assertTrue(any(row['code'] == 'undefined_name' for row in analyze(
            'async fn run(){with cancel_shield() as shield{null};shield}')['diagnostics']))

    def test_module_factory_alias_and_record_fields(self):
        files = {'protection.chaos': 'let protect=cancel_shield;let info=cancel_shield_info;export{protect,info}'}
        source = 'import "protection.chaos" as P;async fn run(){with P.protect() as shield{let state=P.info(shield);state.depth}}'
        self.assertFalse(analyze(source, files=files)['diagnostics'])
        self.assertEqual(self.symbols(source, files=files)['shield']['type'], 'CancelShield')
        self.assertEqual({row['label'] for row in analyze('cancel_shield_info(cancel_shield()).')['completions']},
                         {field.name for field in SHAPES['CancelShieldInfo']})
        help = analyze('cancel_shield_info(shield=')['signatureHelp']
        self.assertEqual(help['activeParameter'], 0)
        self.assertEqual(help['parameters'][0]['type'], 'CancelShield')
        info = builtin_symbols()['task_info'].result_info
        self.assertEqual(info.members['cancellationDeferred'].type, 'Bool')
        self.assertEqual(info.members['shieldDepth'].type, 'Int')

    def test_shadowed_mutable_dynamic_values_and_stack_entry_are_conservative(self):
        samples = [
            'fn cancel_shield()->Record{{}};with cancel_shield() as shield{shield}',
            'var factory=cancel_shield;with factory() as shield{shield}',
            'with input() as shield{shield}',
            'let shield=exit_stack().enter_context(cancel_shield());shield',
            'let shield=await exit_stack(true).enter_async_context(cancel_shield());shield',
        ]
        for source in samples:
            with self.subTest(source=source):
                self.assertEqual(self.symbols(source)['shield']['type'], 'Any')
        result = analyze('cancel_shield_info(42)')
        self.assertTrue(any(row['code'] == 'type_mismatch' for row in result['diagnostics']))

    def test_analysis_and_rendering_do_not_create_or_query_runtime_shields(self):
        from chaos import cancellation
        from chaos.code_presentation import render_code
        source = 'async fn run(){with cancel_shield() as shield{cancel_shield_info(shield).cancellationDeferred}}'
        with patch.object(Evaluator, 'run', side_effect=AssertionError('no execution')), \
             patch.object(cancellation, 'create', side_effect=AssertionError('no creation')), \
             patch.object(cancellation, 'info', side_effect=AssertionError('no runtime query')):
            result = analyze(source)
            html = render_code(source, 'chaos')
        self.assertFalse(result['diagnostics'])
        self.assertIn('cancel_shield() -&gt; CancelShield', html)
        self.assertIn('cancellationDeferred: Bool', html)


if __name__ == '__main__': unittest.main()
