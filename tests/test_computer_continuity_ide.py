"""Computer tooling reads the shared native registry without running effects."""
import inspect
import unittest
from unittest.mock import patch

from chaos.ide_language import analyze,format_source
from chaos.language import Evaluator
from chaos.native_specs import SHAPES,spec_for
from chaos.code_presentation import render_code


class ComputerContinuityIDETests(unittest.TestCase):
    def test_registry_matches_real_named_parameters_and_optional_defaults(self):
        env=Evaluator().env.values
        names=('computer_attach','computer_open','computer_connect','computer_info','computer_observe','observation_info',
               'computer_action','action_info','computer_act','computer_close','computer_release','computer_reconcile','observation_input')
        for name in names:
            with self.subTest(name=name):
                spec=spec_for(name);self.assertEqual(env[name].spec_id,name)
                params=list(inspect.signature(env[name].call).parameters.values())
                self.assertEqual([(p.name,p.default is not p.empty) for p in params],[(p.name,p.optional) for p in spec.parameters])
        fields={item.name:item for item in SHAPES['ComputerInfo']}
        self.assertEqual({key for key,value in fields.items() if value.optional},{'sessionKey','stableSessionId','generation','requiresReconcile','continued','binding','profile','actions'})
        self.assertFalse(fields['requiresReconcile'].nullable)

    def test_cold_task_alias_await_and_persistent_field_completion(self):
        source='let open=computer_connect;let task=open("desk");let c=await task;let info=computer_info(c);info.'
        result=analyze(source,position=len(source));self.assertFalse(result['complete'])
        members={item['label']:item for item in result['completions']}
        self.assertTrue({'sessionKey','stableSessionId','generation','requiresReconcile','continued'}<=set(members))
        self.assertIn('可选',members['generation']['detail'])
        symbols={item['name']:item['type'] for item in result['symbols']}
        self.assertEqual((symbols['task'],symbols['c'],symbols['info']),('Task','Computer','Record'))
        source='let c=await computer_connect("desk");let result=await computer_release(c);result.'
        self.assertIn('requiresReconcile',{item['label'] for item in analyze(source,position=len(source))['completions']})

    def test_reconcile_signature_and_real_builtin_highlighting(self):
        source='let c=await computer_connect("desk");let o=await computer_observe(c,false);await computer_reconcile(c,o,"checked")'
        position=source.index('c,o,"')+len('c,o,')
        result=analyze(source,position=position);self.assertFalse(result['diagnostics'])
        self.assertEqual(result['signatureHelp']['activeParameter'],2);self.assertIn('evidence: String',result['signatureHelp']['label'])
        for token in result['tokens']:
            if source[token['from']:token['to']].startswith('computer_'):self.assertEqual(token['type'],'function')
        shadowed='let computer_connect=42;computer_connect'
        result=analyze(shadowed);self.assertTrue(all(t['type']=='variable' for t in result['tokens'] if shadowed[t['from']:t['to']]=='computer_connect'))

    def test_format_and_analysis_are_inert_and_idempotent(self):
        source='async with await computer_connect("desk") as c{let o=await computer_observe(c,false);await computer_reconcile(c,o,"checked");computer_info(c).generation}'
        with patch.object(Evaluator,'run',side_effect=AssertionError('execution forbidden')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('execution forbidden')):
            result=analyze(source);self.assertFalse(result['diagnostics'])
            formatted=format_source(source);self.assertEqual(format_source(formatted),formatted)
            html=render_code(formatted,'chaos');self.assertIn('chaos-token-function',html)
            self.assertIn('computer_reconcile',html)


if __name__=='__main__':unittest.main()
