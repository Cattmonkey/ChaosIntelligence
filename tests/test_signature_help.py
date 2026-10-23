"""Signature help reads callable metadata and lexical argument boundaries only."""
import unittest
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Evaluator


class SignatureHelpTests(unittest.TestCase):
    def help(self, source, **kwargs):
        value = analyze(source, **kwargs)['signatureHelp']
        self.assertIsNotNone(value, source)
        return value

    def test_positional_keyword_default_and_rest(self):
        prefix = 'fn f(a:Int,b=2,*rest,required:String,**options){a};f('
        value = self.help(prefix + '1,')
        self.assertEqual(value['activeParameter'], 1)
        self.assertEqual(value['activeArgument'], 1)
        self.assertTrue(value['parameters'][1]['optional'])
        self.assertEqual(value['parameters'][0]['type'], 'Int')
        self.assertEqual(self.help(prefix + '1,2,3,')['activeParameter'], 2)
        self.assertEqual(self.help(prefix + 'required=')['activeParameter'], 3)
        self.assertEqual(self.help(prefix + 'unknown=')['activeParameter'], 4)
        self.assertEqual(self.help(prefix + '*input')['activeParameter'], 2)
        self.assertEqual(self.help(prefix + '**input')['activeParameter'], 4)
        self.assertIsNone(self.help(prefix + '*input,')['activeParameter'])
        self.assertIsNone(self.help(prefix + 'required="yes",')['activeParameter'])

    def test_nested_literals_comments_and_calls_do_not_count_internal_commas(self):
        prefix = 'fn f(a,b,c){a};fn g(x,y){x};f('
        self.assertEqual(self.help(prefix + '[1,2], {a:1,b:2},')['activeParameter'], 2)
        self.assertEqual(self.help(prefix + '"(,) ", // , )\n')['activeParameter'], 1)
        nested = self.help(prefix + 'g(1,')
        self.assertTrue(nested['label'].startswith('g('))
        self.assertEqual(nested['activeParameter'], 1)
        self.assertEqual(self.help(prefix + 'g(1,2),')['activeParameter'], 1)

    def test_alias_shadowing_and_unfinished_nested_scope(self):
        result = self.help('fn target(a,b){a};let alias=target;alias(1,')
        self.assertEqual(result['activeParameter'], 1)
        self.assertIn('target(', result['label'])
        source = 'fn f(a,b){a};fn inner(){let f=2;f('
        self.assertIsNone(analyze(source)['signatureHelp'])
        self.assertIsNone(analyze('fn f(a){a};f(1)')['signatureHelp'])
        self.assertIsNone(analyze('fn unfinished(')['signatureHelp'])
        for declaration in ('fn map(', 'fn* map(', 'async fn* map(', 'class map(', 'agent map(', 'macro map('):
            self.assertIsNone(analyze(declaration)['signatureHelp'], declaration)

    def test_imports_bound_methods_and_constructors(self):
        files = {'math.chaos': 'fn add(left:Int,right:Int)->Int{left+right};export{add}'}
        help_ = self.help('import "math.chaos" as M;M.add(2,', files=files)
        self.assertEqual(help_['parameters'][1]['name'], 'right')
        prefix = 'class A{fn __init__(self,value:Int){null};fn add(self,x:Int,*,y=2){x+y}};'
        constructor = self.help(prefix + 'A(')
        self.assertEqual([p['name'] for p in constructor['parameters']], ['value'])
        method = self.help(prefix + 'let a=A(1);a.add(1,y=')
        self.assertEqual([p['name'] for p in method['parameters']], ['x', 'y'])
        self.assertEqual(method['activeParameter'], 1)

    def test_agent_program_session_and_macro_use_real_callable_metadata(self):
        self.assertEqual(self.help('agent Thinker {};Thinker(')['parameters'][0]['name'], 'input')
        self.assertEqual(self.help('let p=current_program();p(')['parameters'][0]['name'], 'input')
        source = 'agent Thinker {};let s=await session_open("chat",Thinker);s(null,message_id='
        self.assertEqual(self.help(source)['parameters'][1]['name'], 'message_id')
        macro = self.help('macro id(x){quote{unquote(x)}};id!(')
        self.assertEqual(macro['parameters'][0]['type'], 'Syntax')

    def test_utf16_cursor_and_no_execution(self):
        source = 'let emoji="🙂";fn f(a,b=tool("x",null)){a};f(1,2)'
        position = source.rindex(',') + 1
        utf16 = len(source[:position].encode('utf-16-le')) // 2
        with patch.object(Evaluator, 'run', side_effect=AssertionError('must not execute')), patch.object(Evaluator, 'evaluate', side_effect=AssertionError('must not execute')):
            result = self.help(source, position=utf16)
        self.assertEqual(result['to'], utf16)
        self.assertEqual(result['activeParameter'], 1)
        self.assertEqual(result['from'], len(source[:source.rindex('(')].encode('utf-16-le')) // 2)

    def test_unknown_named_argument_and_no_parameters_do_not_fabricate_target(self):
        self.assertIsNone(self.help('fn f(a){a};f(unknown=')['activeParameter'])
        result = self.help('fn f(){42};f(')
        self.assertEqual(result['parameters'], [])
        self.assertIsNone(result['activeParameter'])

    def test_await_suspension_is_not_the_callable_with_same_name(self):
        prefix = 'fn await(a,b){a};async fn task(){42};'
        self.assertIsNone(analyze(prefix + 'await (')['signatureHelp'])
        self.assertEqual(self.help(prefix + 'await(')['parameters'][0]['name'], 'a')
        self.assertEqual(self.help('let obj={await:fn(a,b){a}};obj.await (')['parameters'][0]['name'], 'a')
