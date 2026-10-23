"""Resource scopes share one source/IR representation on every frontend."""
import copy
import json
import unittest

from chaos.language import Evaluator, LanguageError, Parser, tokenize
from chaos.program import decode_program, format_program, parse_program, patch_program, plain, project_program, templates
from chaos.source_format import format_source


SOURCE='''class R {
  let value=40;
  fn __enter__(self){self.value}
  fn __exit__(self,error){null}
}; with R() as x, R() as y {x+y-38}'''


class ContextIR(unittest.TestCase):
    def test_canonical_roundtrip_and_structural_edit(self):
        ir=parse_program(SOURCE)
        original=json.loads(json.dumps(ir))
        canonical=format_program(original)
        self.assertEqual(plain(decode_program(ir)),plain(Parser(canonical).program()))
        self.assertEqual(Evaluator().run(canonical),42)
        context=ir['root']['statements'][1]
        self.assertEqual(context['kind'],'with')
        self.assertEqual(set(context)-{'id','span'},{'kind','name','manager','body'})
        target=context['body']['statements'][0]['body']['statements'][0]['right']['id']
        edited=patch_program(ir,[{'op':'set','id':target,'field':'value','value':37}])
        self.assertEqual(Evaluator().run(format_program(edited)),43)
        graph=project_program(edited)
        self.assertTrue(any(edge['slot']=='manager' for edge in graph['edges']))

    def test_async_roundtrip_and_templates(self):
        source='async with input as a, other(a) as b {b}'
        ir=parse_program(source)
        self.assertEqual(ir['root']['statements'][0]['kind'],'async_with')
        self.assertEqual(plain(decode_program(ir)),plain(Parser(format_program(ir)).program()))
        for kind in ('with','async_with'):
            self.assertEqual(templates()[kind]['kind'],kind)

    def test_malformed_ir_and_illegal_async_context_rejected(self):
        ir=parse_program('fn f(){with input as x{x}}')
        scope=ir['root']['statements'][0]['function']['body']['statements'][0]
        for change in ('async','body','name'):
            bad=copy.deepcopy(ir);node=bad['root']['statements'][0]['function']['body']['statements'][0]
            if change=='async':node['kind']='async_with'
            elif change=='body':node['body']=copy.deepcopy(templates()['literal'])
            else:node['name']='x;tool("bad",null)'
            with self.subTest(change=change),self.assertRaises(LanguageError):format_program(bad)
        self.assertEqual(scope['name'],'x')

    def test_source_formatter_preserves_comments_tokens_and_ast(self):
        sources=[SOURCE,
            '// leading\nwith factory("as") as x, // acquired first\n second(x) as y { // scope\n y // result\n} // trailing\n',
            'async with (await computer_open("https://example.test")) as browser {browser}',
            'let with=1;with import "std/math" as M;M.pi',
        ]
        for source in sources:
            with self.subTest(source=source):
                formatted=format_source(source,line_width=40)
                tokens=lambda text:[(token.kind,token.value)for token in tokenize(text,trivia=True)if token.kind!='space']
                self.assertEqual(tokens(source),tokens(formatted))
                self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
                self.assertEqual(format_source(formatted,line_width=40),formatted)


if __name__=='__main__':unittest.main()
