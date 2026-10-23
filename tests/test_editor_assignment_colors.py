"""Assignment target roles follow the actual binding without executing edited code."""
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from chaos.ide_language import analyze
from chaos.language import Evaluator


def token_text(source, token):
    raw = source.encode('utf-16-le')
    return raw[token['from'] * 2:token['to'] * 2].decode('utf-16-le')


class EditorAssignmentColors(unittest.TestCase):
    def analysis(self, source, codes=(), **kwargs):
        with ExitStack() as stack:
            for name in ('run', 'evaluate', 'invoke', '_s_invoke', '_s_evaluate'):
                stack.enter_context(patch.object(Evaluator, name,
                    side_effect=AssertionError('Color analysis executed edited code')))
            result = analyze(source, **kwargs)
        self.assertTrue(result['complete'], result['diagnostics'])
        self.assertEqual([item['code'] for item in result['diagnostics']], list(codes))
        previous = 0
        for token in result['tokens']:
            self.assertGreaterEqual(token['from'], previous)
            self.assertGreater(token['to'], token['from'])
            self.assertTrue(token_text(source, token))
            previous = token['to']
        return result

    def roles(self, source, result, name):
        return [token['type'] for token in result['tokens']
                if token_text(source, token) == name]

    def test_scalar_callable_assignment_matches_its_read_reference(self):
        source = ('// 🙂 shifts the public UTF-16 offsets\n'
                  'var transform:Function=fn(value){value};'
                  'transform=fn(value){value+1};transform(41)')
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, 'transform'),
                         ['variable', 'function', 'function'])
        self.assertEqual(self.roles(source, result, 'value'), ['parameter'] * 4)
        self.assertEqual(self.roles(source, result, 'Function'), ['type'])
        self.assertEqual([item['name'] for item in result['symbols']].count('transform'), 1)

    def test_scalar_class_assignment_preserves_type_identity(self):
        source = 'class First{};class Second{};var Factory:Class=First;Factory=Second;Factory'
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, 'Factory'), ['type'] * 3)
        self.assertEqual(self.roles(source, result, 'First'), ['type'] * 2)
        self.assertEqual(self.roles(source, result, 'Second'), ['type'] * 2)
        self.assertEqual(self.roles(source, result, 'Class'), ['type'])

    def test_structural_list_and_record_assignment_use_existing_binding_roles(self):
        source = ('class FactoryBase{};var Constructor:Class=FactoryBase;'
                  'var transform:Function=fn(value){value};'
                  '[transform,Constructor]=[fn(value){value+1},FactoryBase];'
                  '{handler:transform}={handler:fn(value){value+2}};'
                  '[transform(40),Constructor]')
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, 'transform'),
                         ['variable', 'function', 'function', 'function'])
        self.assertEqual(self.roles(source, result, 'Constructor'), ['type'] * 3)
        self.assertEqual(self.roles(source, result, 'handler'), ['property'] * 2)

    def test_invalid_parameter_writes_keep_parameter_color_and_diagnostics(self):
        source = ('fn apply(callback:Function){callback=fn(value){value};'
                  '[callback]=[fn(value){value+1}];callback(41)}')
        result = self.analysis(source, ['immutable_binding', 'immutable_binding'])
        self.assertEqual(self.roles(source, result, 'callback'), ['parameter'] * 4)
        self.assertEqual([item['name'] for item in result['symbols']].count('callback'), 1)
        self.assertEqual(self.roles(source, result, 'apply'), ['function'])

    def test_lexically_shadowed_callable_and_native_names_use_the_local_binding(self):
        source = ('fn transform(value){value};fn local(){var transform:Int=0;'
                  'transform=1;[transform]=[2];transform};transform(41);'
                  'fn another(){var length:Int=0;length=1;[length]=[2];length};length([1])')
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, 'transform'),
                         ['function', 'variable', 'variable', 'variable', 'variable', 'function'])
        self.assertEqual(self.roles(source, result, 'length'),
                         ['variable', 'variable', 'variable', 'variable', 'function'])

    def test_unknown_targets_stay_variables_without_inventing_bindings(self):
        source = 'missing=41;[other]=[42]'
        result = self.analysis(source, ['undefined_name', 'undefined_name'])
        self.assertEqual(self.roles(source, result, 'missing'), ['variable'])
        self.assertEqual(self.roles(source, result, 'other'), ['variable'])
        self.assertEqual(result['symbols'], [])
        self.assertIsNone(result['definition'])

    def test_ordinary_variables_labels_comments_and_strings_keep_their_roles(self):
        source = ('var value:Int=0;value=1;[value]=[2];'
                  'let record={value:"value"};record.value; // value is a comment\nvalue')
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, 'value'),
                         ['variable', 'variable', 'variable', 'property', 'property', 'variable'])
        self.assertEqual(self.roles(source, result, '"value"'), ['string'])
        self.assertEqual(sum(token['type'] == 'comment' for token in result['tokens']), 1)

    def test_imported_callable_values_preserve_member_hints_and_target_definition(self):
        library = 'fn increment(value:Int){tool("must.not.run",value);value+1};export{increment}'
        source = ('import "functions.chaos" as F;var transform:Function=F.increment;'
                  'transform=F.increment;[transform(41),F.increment]')
        position = len(source[:source.index('transform=F.increment')].encode('utf-16-le')) // 2 + 2
        result = self.analysis(source, files={'main.chaos': source, 'functions.chaos': library},
                               position=position)
        self.assertEqual(self.roles(source, result, 'transform'),
                         ['variable', 'function', 'function'])
        self.assertEqual(self.roles(source, result, 'increment'), ['function'] * 3)
        self.assertEqual(self.roles(source, result, 'F'), ['variable'] * 4)
        declaration = source.index('transform:Function')
        self.assertEqual(result['definition'], {'from': declaration, 'to': declaration + len('transform'),
                                               'file': 'main.chaos'})
        self.assertEqual([item['name'] for item in result['symbols']].count('transform'), 1)


    def test_tuple_assignment_and_augmented_variables_keep_their_existing_roles(self):
        source = ('var transform:Function=fn(value){value};var total:Int=0;'
                  '(transform,total)=(fn(value){value+1},40);'
                  'total+=2;transform(total)')
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, 'transform'),
                         ['variable', 'function', 'function'])
        self.assertEqual(self.roles(source, result, 'total'), ['variable'] * 4)
        operators = [token['type'] for token in result['tokens']
                     if token_text(source, token) in ('=', '+=')]
        self.assertTrue(operators)
        self.assertEqual(set(operators), {'operator'})

    def test_unicode_assignment_target_definition_uses_public_utf16_identity(self):
        source = ('// 🙂 keeps UTF-16 offsets distinct from Python offsets\n'
                  'fn first(value:Int){value};var 处理:Function=first;'
                  '处理=first;处理(41)')
        assignment = source.index('处理=first')
        position = len(source[:assignment].encode('utf-16-le')) // 2 + 1
        result = self.analysis(source, position=position)
        self.assertEqual(self.roles(source, result, '处理'),
                         ['variable', 'function', 'function'])
        declaration = source.index('处理:Function')
        start = len(source[:declaration].encode('utf-16-le')) // 2
        self.assertEqual(result['definition'],
                         {'from': start, 'to': start + 2, 'file': 'main.chaos'})
        self.assertEqual([item['name'] for item in result['symbols']].count('处理'), 1)

if __name__ == '__main__':
    unittest.main()
