"""Contextual resource syntax retains older identifiers and async boundaries."""
import time
import unittest

from chaos.language import Evaluator, LanguageError, Parser
from chaos.program import plain


SYNC = '''class Resource {
  var value=0;
  fn __init__(self,value){self.value=value;null}
  fn __enter__(self){self.value}
  fn __exit__(self,error){null}
};'''
ASYNC = '''class AsyncResource {
  async fn __aenter__(self){42}
  async fn __aexit__(self,error){null}
};'''


class ContextSyntax(unittest.TestCase):
    def test_contextual_identifiers_and_old_adjacent_statements(self):
        sources = [
            ('fn with(x){x};with(42)',42),
            ('fn with(x){x};with (42)',42),
            ('let with=1;with import "std/math" as M;42',42),
            ('let with=1;with {x:2}',{'x':2}),
            ('let with=1;with if true{2}else{3}',2),
            ('let async=1;let with=2;async with',2),
            ('let with=40;with+2',42),
            ('let with={value:42};with.value',42),
            ('let with=[42];with[0]',42),
        ]
        for source, expected in sources:
            with self.subTest(source=source):
                self.assertEqual(Evaluator().run(source),expected)

        from chaos.set_values import SetValue
        for source in ('let with=1;with {1}',
                       'let with=1;fn resource(x){x};with resource(1) {1}',
                       'let with=1;fn resource(x){x};(with) resource(1) {1}'):
            with self.subTest(source=source):
                value=Evaluator().run(source)
                self.assertIs(type(value),SetValue)
                self.assertEqual(value.items,(1,))
        # Without an as header, with retains its ordinary identifier grammar.
        adjacent=plain(Parser('with resource(1) {1}').program())
        self.assertEqual([node[0] for node in adjacent[1]],['name','call','set'])

    def test_nested_headers_and_body_have_real_source_ranges(self):
        source='with first() as a,\n second(a) as b, third(b) as c {\n c\n}'
        outer=Parser(source,'resource.chaos').program()[1][0]
        middle=outer[3][1][0];inner=middle[3][1][0]
        self.assertEqual([outer[0],middle[0],inner[0]],['with']*3)
        self.assertEqual([outer[1],middle[1],inner[1]],['a','b','c'])
        self.assertEqual((middle.span['line'],middle.span['column']),(2,2))
        self.assertEqual((inner.span['line'],inner.span['column']),(2,18))
        self.assertEqual(inner[3][1][0].span['line'],3)
        for node in (outer,middle,inner):
            self.assertEqual(node.span['file'],'resource.chaos')
            self.assertEqual(node.span['endLine'],4)
            self.assertEqual(node[3][0],'block')

    def test_multimanager_binding_scope_and_result(self):
        self.assertEqual(Evaluator().run(SYNC+'let a=99;let result=with Resource(40) as a, Resource(a+2) as b {b};[result,a]'),[42,99])
        self.assertEqual(Evaluator().run(SYNC+'with Resource(42) as _ {1}'),1)
        with self.assertRaises(LanguageError):
            Evaluator().run(SYNC+'with Resource(42) as hidden {hidden};hidden')

    def test_invalid_syntax_rejected_before_effects(self):
        invalid = [
            'with Resource(1) as x', 'with Resource(1) as {}',
            'with Resource(1) as let {1}', 'with Resource(1) as x, {1}',
            'with Resource(1) as x {return 1}',
            'with Resource(1) as x {break}',
            'async with AsyncResource() as x {continue}',
        ]
        for source in invalid:
            calls=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run('tool("before",null);'+SYNC+ASYNC+source)
            self.assertEqual(calls,[])

    def test_async_scope_context_and_nested_function_boundaries(self):
        self.assertEqual(Evaluator().run(ASYNC+'async with AsyncResource() as value {value}'),42)
        self.assertEqual(Evaluator().run(ASYNC+'async fn f(){async with AsyncResource() as value {value}};await f()'),42)
        self.assertEqual(Evaluator().run(ASYNC+'async fn* f(){async with AsyncResource() as value {yield value}};await acollect(f())'),[42])
        invalid = [
            'fn f(){async with AsyncResource() as x{x}}',
            'fn* f(){async with AsyncResource() as x{yield x}}',
            'async fn f(){fn g(){async with AsyncResource() as x{x}}}',
            'fn f(){class Inner{let x=async with AsyncResource() as x{x}}}',
            'fn f(){fn inner(x=async with AsyncResource() as v{v}){x}}',
            'fn f(){fn inner(x=await gather([])){x}}',
        ]
        for source in invalid:
            with self.subTest(source=source),self.assertRaises(LanguageError):
                Parser(ASYNC+source).program()

    def test_defaults_keep_enclosing_async_permission(self):
        sources = [
            'fn f(x=async with AsyncResource() as v{v}){x};f()',
            'async fn outer(){fn f(x=async with AsyncResource() as v{v}){x};f()};await outer()',
            'class C{let x=async with AsyncResource() as v{v}};C().x',
            'async fn outer(){class C{let x=async with AsyncResource() as v{v}};C().x};await outer()',
        ]
        for source in sources:
            with self.subTest(source=source):self.assertEqual(Evaluator().run(ASYNC+source),42)
        self.assertEqual(Evaluator().run('fn f(x=await gather([])){x};f()'),[])

    def test_deep_context_and_legacy_call_parsing_is_bounded(self):
        legacy='fn with(x){x};'+'with('*60+'42'+')'*60
        nested='input'
        for index in range(45):nested='with ('+nested+') as value'+str(index)+' {value'+str(index)+'}'
        adjacent=';'.join(['with']*3000)
        started=time.monotonic()
        for source in (legacy,nested,adjacent):
            result=Parser(source).program()
            self.assertEqual(result[0],'block')
        self.assertLess(time.monotonic()-started,3.0)
        self.assertEqual(plain(Parser(legacy).program())[1][-1][0],'call')


if __name__=='__main__':unittest.main()
