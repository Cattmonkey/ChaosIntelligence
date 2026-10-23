"""A single parser-backed, source-preserving formatter for every authoring UI."""
import unittest
from pathlib import Path
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError, Parser, ResourceLimit, tokenize
from chaos.program import plain
from chaos.source_format import format_source


class SourceFormattingTests(unittest.TestCase):
    def check(self, source, **options):
        result = format_source(source, **options)
        old = plain(Parser(source, legacy_items=options.get('language_version', 2) == 1).program())
        self.assertEqual(plain(Parser(result).program()), old)
        again = dict(options, language_version=2)
        self.assertEqual(format_source(result, **again), result)
        old_text = [(t.kind, t.value) for t in tokenize(source, trivia=True)
                    if t.kind in ('string', 'number', 'comment')]
        new_text = [(t.kind, t.value) for t in tokenize(result, trivia=True)
                    if t.kind in ('string', 'number', 'comment')]
        self.assertEqual(old_text, new_text)
        return result

    def test_nested_calls_wrap_and_remain_independently_compact(self):
        source = 'let answer=calculate(first(100,200,300),second(400,500,600),third(700,800,900));answer'
        result = self.check(source, line_width=40)
        self.assertIn('calculate(\n  first(100, 200, 300),\n', result)
        self.assertIn('\n  third(700, 800, 900)\n);', result)
        self.assertTrue(all(len(line) <= 40 for line in result.splitlines()))

    def test_nested_lists_and_records_wrap_at_each_level(self):
        source = 'let values=[{description:"alpha",values:[111,222,333,444,555,666]},{description:"beta",values:[777,888,999]}];values'
        result = self.check(source, line_width=40)
        self.assertIn('let values = [\n', result)
        self.assertIn('\n  {\n    description:', result)
        self.assertIn('values: [\n      111,\n', result)
        self.assertTrue(all(len(line) <= 40 for line in result.splitlines()))

    def test_short_collections_stay_compact(self):
        result = self.check('let r={a:1,b:[2,3]};r.a')
        self.assertIn('{a: 1, b: [2, 3]}', result)

    def test_long_function_parameters_and_spreads(self):
        source = 'fn collect(first:Int,second:Int=2,*remaining,required:Int=3,**options)->List{[first,second,remaining,required,options]};collect(*[1,2,3],required=4,**{answer:42})'
        result = self.check(source, line_width=40, indent_size=4)
        self.assertIn('fn collect(\n    first: Int,\n', result)
        self.assertIn('*remaining,', result)
        self.assertIn('**options', result)
        self.assertEqual(Evaluator().run(result), [1, 2, [3], 4, {'answer': 42}])

    def test_statement_boundaries_without_semicolons(self):
        result = self.check('let first=1 let second=2 first+second')
        self.assertEqual(result, 'let first = 1\nlet second = 2\nfirst + second\n')
        self.assertEqual(Evaluator().run(result), 3)

    def test_comments_preserve_text_and_attachment(self):
        source = '// header  \nfn f(){let x=1; // x  \n// separate  \nx+1};f() # end  '
        result = self.check(source)
        self.assertIn('let x = 1; // x  \n', result)
        self.assertIn('\n  // separate  \n', result)
        self.assertTrue(result.endswith('f() # end  \n'))

    def test_comment_forces_multiline_call_and_list(self):
        source = 'fn f(x,y){x+y};f(1, // first\n2);[1, # first\n2]'
        result = self.check(source)
        self.assertIn('f(\n  1, // first\n  2\n)', result)
        self.assertIn('[\n  1, # first\n  2\n]', result)

    def test_standalone_comment_after_expression_stays_separate(self):
        result = self.check('fn f(){42\n// explanation  \n};f()')
        self.assertIn('\n  // explanation  \n', result)
        self.assertNotIn('42 // explanation', result)

    def test_strings_unicode_numbers_and_escapes_are_verbatim(self):
        source = "let 变量='中文 🙂 \\\" // #';let x=1.20e+2;let y=90071992547409939999;[变量,x,y]"
        result = self.check(source, line_width=40)
        self.assertIn("'中文 🙂 \\\" // #'", result)
        self.assertIn('1.20e+2', result)
        self.assertIn('90071992547409939999', result)

    def test_indivisible_strings_and_comments_may_exceed_target(self):
        raw = 'x' * 100
        result = self.check('let text="' + raw + '"; // ' + raw + '\ntext', line_width=40)
        self.assertIn('"' + raw + '"', result)
        self.assertIn('// ' + raw, result)
        self.assertGreater(max(map(len, result.splitlines())), 40)

    def test_await_callable_and_suspension_whitespace(self):
        source = 'fn await(x){x};async fn f(){42};[await(1),await (f())]'
        result = self.check(source, line_width=40)
        self.assertIn('await(1)', result)
        self.assertIn('await (f())', result)
        self.assertEqual(Evaluator().run(result), [1, 42])

    def test_yield_from_and_parenthesized_soft_keywords(self):
        source = 'fn* stream(){yield (1);yield from ([2,3])};collect(stream())'
        result = self.check(source)
        self.assertIn('yield (1)', result)
        self.assertIn('yield from ([2, 3])', result)
        self.assertEqual(Evaluator().run(result), [1, 2, 3])

    def test_macros_quotes_and_qualified_macro_names(self):
        source = 'macro identity(value){quote{unquote(value)}};identity!({answer:[1,2,3,4,5,6,7,8,9,10]});M.external!(1,2,3)'
        result = self.check(source, line_width=40)
        self.assertIn('identity!(', result)
        self.assertIn('M.external!(', result)
        self.assertEqual(Evaluator().run(self.check('macro m(v){quote{unquote(v)}};m!(42)')), 42)

    def test_multiline_decorators_do_not_merge_with_declarations(self):
        source = 'fn decorate(first,second,third){fn(f){f}}\n@decorate("one long argument", "second long argument", "third long argument")\nfn f(){42}\nf()'
        result = self.check(source, line_width=40)
        self.assertIn('@decorate(\n', result)
        self.assertIn('\n)\nfn f()', result)
        self.assertEqual(Evaluator().run(result), 42)

    def test_decorator_comment_and_multiple_decorators(self):
        source = 'fn identity(f){f}\n@identity // first  \n@identity\nfn f(){42}\nf()'
        result = self.check(source)
        self.assertIn('@identity // first  \n@identity\nfn', result)
        self.assertEqual(Evaluator().run(result), 42)

    def test_control_flow_patterns_classes_and_async_generators(self):
        samples = [
            'var x=0;while x<2{try{x=x+1}finally{null}};x',
            'data O{None,Some(value)};match Some(1){Some(x)=>x,None()=>0}',
            'match [1,2,3]{[head,..tail]=>[head,tail],_=>null}',
            'class C{var x=1;fn read(self){self.x}};C().read()',
            'async fn* items(){yield 1;yield 2};await acollect(items())',
            'let values=[1,2,3,4];values[1:4:2]',
            'let x=-1;[!false,x*-2]',
        ]
        for source in samples:
            with self.subTest(source=source):
                formatted = self.check(source, line_width=40)
                self.assertEqual(Evaluator().run(formatted), Evaluator().run(source))

    def test_version_one_access_becomes_explicit_and_preserves_comments(self):
        samples = ['let x={answer:42};x["answer"]',
                   'let x={answer:42};(x)["answer"]',
                   'let x={nested:{answer:42}};x["nested"]["answer"]',
                   'let x={answer:42};x[ // key\n"answer"]',
                   'class C{var answer=1};let x=C();x["answer"]=42;x["answer"]']
        for source in samples:
            with self.subTest(source=source):
                result = self.check(source, language_version=1, line_width=40)
                self.assertIn('.[', result)
                self.assertEqual(Evaluator().run(result), 42)

    def test_invalid_drafts_never_yield_replacement(self):
        for source in ('fn f(', 'let x=', '"unterminated', '@identity fn f(){}'):
            with self.subTest(source=source), self.assertRaises(LanguageError):
                format_source(source)

    def test_options_are_strict_and_bounded(self):
        for option, values in [('line_width', [True, False, 39, 241, '88', None, 88.0]),
                               ('indent_size', [True, False, 0, 9, '2', None, 2.0]),
                               ('language_version', [True, False, 0, 3, '2', None, 2.0])]:
            for value in values:
                with self.subTest(option=option, value=value), self.assertRaises(LanguageError):
                    format_source('42', **{option: value})
        for value in (None, 42, [], {}):
            with self.subTest(source=value), self.assertRaises(LanguageError):
                format_source(value)
        with self.assertRaises(LanguageError):
            format_source('42', file=None)

    def test_option_boundaries_and_custom_indentation(self):
        for width in (40, 240):
            for indent in (1, 8):
                self.check('fn f(){let x={a:[1,2,3]};x}', line_width=width, indent_size=indent)
        self.assertIn('\n    42\n', self.check('fn f(){42}', indent_size=4))

    def test_no_code_is_evaluated(self):
        with patch.object(Evaluator, 'run', side_effect=AssertionError('executed')), \
             patch.object(Evaluator, 'evaluate', side_effect=AssertionError('executed')):
            self.check('agent a{};fn f(x=tool("side-effect",null)){a(x)};f()')

    def test_empty_source_and_comment_only(self):
        self.assertEqual(self.check('  \n'), '\n')
        self.assertEqual(self.check('// retain  \n'), '// retain  \n')

    def test_parser_resource_limits_are_retained(self):
        with self.assertRaises(ResourceLimit):
            format_source(' ' * 100001)
        with self.assertRaises(ResourceLimit):
            format_source('(' * 1000 + '42' + ')' * 1000)

    def test_every_repository_example_remains_equivalent_and_idempotent(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / 'examples').rglob('*.chaos'):
            with self.subTest(path=str(path)):
                self.check(path.read_text(), file=str(path), line_width=72)


if __name__ == '__main__':
    unittest.main()
