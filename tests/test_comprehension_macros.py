"""Comprehensions preserve phase separation and lexical binding identity."""
import unittest
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


class ComprehensionMacros(unittest.TestCase):
    def both(self, source, expected, **kwargs):
        self.assertEqual(Evaluator().run(source, **kwargs), expected)
        self.assertEqual(Evaluator().run(format_program(parse_program(source)), **kwargs), expected)

    def test_introduced_binders_shadow_in_execution_order(self):
        self.both('''macro m(source){quote{[x*10+y for x in unquote(source) for y in [x+1] if y>2]}};
            let x=90; let y=80; [m!([1,2,3]),x,y]''', [[23,34],90,80])
        self.both('''macro m(source){quote{[x for x in unquote(source) for x in [x+1]]}};
            let x=90;[m!([1,2]),x]''', [[2,3],90])

    def test_unquoted_head_retains_callers_name(self):
        self.both('''macro m(body){quote{[unquote(body)+x for x in [1,2]]}};
            let x=40;m!(x)''', [41,42])

    def test_per_item_closures_and_dynamic_record_keys(self):
        self.both('''macro m(){quote{[fn(){x} for x in [1,2]]}};
            let f=m!();[f[0](),f[1]()]''', [1,2])
        self.both('''macro m(){quote{{x: x+x for x in ["a","b","a"]}}};m!()''', {'a':'aa','b':'bb'})

    def test_macro_calls_are_expanded_inside_every_component(self):
        self.both('''macro id(value){quote{unquote(value)}};
            [id!(x)+id!(y) for x in id!([1,2]) for y in id!([10]) if id!(x>1)]''', [12])
        self.both('''macro id(value){quote{unquote(value)}};
            {id!(x):id!(x+x) for x in id!(["a"])}''', {'a':'aa'})

    def test_generator_templates_sync_async_and_callers_input(self):
        self.both('''macro m(xs){quote{(x+1 for x in unquote(xs))}};
            let g=m!([40,41]);[next(g).value,next(g).value,next(g).done]''', [41,42,True])
        self.both('''macro m(xs){quote{async (x+1 for x in unquote(xs))}};
            let g=m!([40,41]);[(await anext(g)).value,(await anext(g)).value,(await anext(g)).done]''', [41,42,True])

    def test_pure_comprehensions_create_literals_with_strict_guards(self):
        self.both('''macro m(){let xs=[x*2 for x in [1,2,3] if x>1];
            let record={x:x+x for x in "ab"};syntax_literal([xs,record])};m!()''', [[4,6],{'a':'aa','b':'bb'}])
        for source in ('macro m(){syntax_literal([x for x in [1] if 1])};m!()',
                       'macro m(){syntax_literal({1:x for x in [1]})};m!()',
                       'macro m(){let g=(x for x in [1]);quote{42}};m!()'):
            with self.subTest(source=source),self.assertRaises(LanguageError): Evaluator().run(source)

    def test_macro_generated_control_cannot_escape_comprehension(self):
        invalid = [
            'macro bad(){quote{return 9}};fn f(){[bad!() for x in [1]]};f()',
            'macro bad(){quote{break}};for outer in [1]{[bad!() for x in [1]]}',
            'macro bad(){quote{continue}};for outer in [1]{[x for x in [1] if bad!()]}',
            'macro bad(){quote{yield 9}};fn* f(){[bad!() for x in [1]]};next(f())',
            'macro bad(){quote{await 9}};fn f(){(bad!() for x in [1])};f()',
        ]
        for source in invalid:
            calls=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run('tool("before",null);'+source)
            self.assertEqual(calls,[])

    def test_inner_explicit_functions_keep_return_scope(self):
        self.both('''macro m(){quote{[do{fn f(){return x};f()} for x in [40,42]]}};m!()''',[40,42])

    def test_qualified_macro_keeps_definition_helpers(self):
        files={'m.chaos':'''macro inner(x){quote{unquote(x)+1}};
            macro list(xs){quote{[inner!(x) for x in unquote(xs)]}};export{list}'''}
        self.both('import "m.chaos" as M;let x=99;M.list!([40,41])',[41,42],files=files)

if __name__=='__main__':unittest.main()
