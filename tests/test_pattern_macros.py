"""Pattern bindings preserve compiler purity and lexical alpha-renaming."""
import unittest
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


class PatternMacros(unittest.TestCase):
    def both(self, source, expected, **options):
        self.assertEqual(Evaluator().run(source, **options), expected)
        self.assertEqual(Evaluator().run(format_program(parse_program(source)), **options), expected)

    def test_pure_bindings_and_comprehensions(self):
        self.both('''macro m(){let [a,{x:b,..rest}]:List=[20,{x:21,z:9}];
            let rows=[a+b for [a,b] in [[1,2],[3,4]]];
            syntax_literal([a,b,rest,rows])};m!()''',[20,21,{'z':9},[3,7]])

    def test_pure_shape_errors_and_runtime_effects_stay_before_execution(self):
        for body in ('let [a,b]=[1];quote{42}',
                     'let [a,a]=[1,2];quote{42}',
                     'let [a,b]=[1,2];let [c,b]=[3,4];quote{42}',
                     'let rows=[a for [a] in [[1,2]]];quote{42}',
                     'var [a,b]=[1,2];quote{42}',
                     'let C(a)=[1];quote{42}'):
            calls=[]
            with self.subTest(body=body), self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run(
                    'tool("before",null);macro m(){'+body+'};m!()')
            self.assertEqual(calls,[])

    def test_quote_decl_rhs_scope_and_unquote_identity(self):
        self.both('''macro m(value,body){quote{do{let [x,y]=unquote(value);
            [x,y,unquote(body)]}}};let x=90;[m!([20,21],x),x]''',[[20,21,90],90])
        self.both('''macro m(){quote{do{let x=20;do{let [x,y]=[x,21];x+y}}}};m!()''',41)

    def test_quoted_mutable_leaves_and_closures(self):
        self.both('''macro m(){quote{do{var [x,y]:List=[20,21];
            fn increment(){x=x+1};increment();[x,y]}}};let x=90;[m!(),x]''',[[21,21],90])

    def test_quoted_for_and_comprehension_closures(self):
        self.both('''macro m(rows){quote{do{var out=[];for [x,y] in unquote(rows){
            out=out+[fn(){x+y}]};[out[0](),out[1]()]}}};m!([[1,2],[3,4]])''',[3,7])
        self.both('''macro m(rows){quote{[x+y+z for [x,y] in unquote(rows)
            for [x,z] in [[x+10,y]] if x>10]}};let x=90;[m!([[1,2],[3,4]]),x]''',[[15,21],90])

    def test_quoted_generators_and_async_loop(self):
        self.both('''macro m(rows){quote{(x+y for [x,y] in unquote(rows))}};
            let g=m!([[20,21]]);[next(g).value,next(g).done]''',[41,True])
        self.both('''macro m(rows){quote{async (x+y async for [x,y] in unquote(rows))}};
            async fn* rows(){yield ([20,21])};let g=m!(rows());
            [(await anext(g)).value,(await anext(g)).done]''',[41,True])
        self.both('''macro m(rows){quote{do{var result=0;async for [x,y] in unquote(rows){
            result=result+x+y};result}}};async fn* rows(){yield ([20,21])};m!(rows())''',41)

    def test_constructor_alias_and_outer_lookup_before_pattern_binders(self):
        self.both('''data Box{Boxed(value)};macro m(ctor,value){quote{do{
            let C=unquote(ctor);let C(x)=unquote(value);x}}};m!(Boxed,Boxed(42))''',42)
        self.both('''data Box{Boxed(value)};macro m(ctor){quote{do{
            let C=unquote(ctor);[x+C for [C,C(x)] in [[1,C(41)]]]}}};m!(Boxed)''',[42])
        self.both('''data Box{Boxed(value)};macro m(ctor,value){quote{do{
            let C=unquote(ctor);match unquote(value){C(C)=>C}}}};m!(Boxed,Boxed(42))''',42)

    def test_qualified_constructor_alias(self):
        files={'box.chaos':'data Box{Boxed(value)};export{Boxed}'}
        self.both('''import "box.chaos" as B;macro m(module,value){quote{do{
            let M=unquote(module);let M.Boxed(x)=unquote(value);x}}};m!(B,B.Boxed(42))''',42,files=files)

    def test_closed_constructor_and_duplicate_template_errors(self):
        for source in ('''data Box{Boxed(value)};macro m(value){quote{do{
                let Boxed(x)=unquote(value);x}}};m!(Boxed(42))''',
                '''macro m(){quote{do{let x=1;let [x,y]=[2,3];y}}};m!()'''):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)

    def test_async_macro_pattern_cannot_cross_sync_function_boundary(self):
        calls=[]
        with self.assertRaises(LanguageError):
            Evaluator(tool_handler=lambda *args:calls.append(args)).run('''
                tool("before",null);macro m(xs){quote{async for [x] in unquote(xs){x}}};
                fn f(){m!([[1]])};f()''')
        self.assertEqual(calls,[])

if __name__=='__main__':unittest.main()
