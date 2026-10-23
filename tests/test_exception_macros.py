"""Native exception templates retain hygiene and dynamic error contexts."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


class ExceptionMacros(unittest.TestCase):
    def variants(self, source, expected, **options):
        for text in (source, Framework.format_source(source, line_width=40),
                     format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text, **options), expected)

    def test_patterns_guards_and_sibling_scopes_are_hygienic(self):
        source='''macro recover(action,fallback){quote{
try{unquote(action)()}catch {value:value,.._} if value>40 {value}
catch error{unquote(fallback)}}};let value=42;let error=7;
[recover!(fn(){throw 41},error),recover!(fn(){throw 2},value),value,error]'''
        self.variants(source,[41,42,42,7])

    def test_constructor_lookups_use_outer_template_scope(self):
        source='''data Result{Fail(value)};
macro recover(tag,action){quote{let C=unquote(tag);
try{unquote(action)()}catch {value:C(C),.._}{C+1}catch other{throw}}};
recover!(Fail,fn(){throw Fail(41)})'''
        self.variants(source,42)

    def test_quoted_helper_rethrows_dynamic_caller_error(self):
        source='''macro relay(){quote{fn(){throw}}};let again=relay!();
try{try{throw 42}catch error{again()}}catch outer{[outer.code,outer.value]}'''
        self.variants(source,['user_throw',42])

    def test_guard_rethrow_bypasses_siblings_and_preserves_finally(self):
        source='''macro choose(action){quote{try{unquote(action)()}
catch error if do{throw}{0}catch fallback{99}}};var cleaned=0;
let value=try{choose!(fn(){throw 42})}catch outer{outer.value}
finally{cleaned=cleaned+1};[value,cleaned]'''
        self.variants(source,[42,1])

    def test_imported_async_template_keeps_guard_and_body_context(self):
        files={'forms.chaos':'''macro recover(action,ready,pause){quote{async fn(){
try{await unquote(action)()}catch {value:value,.._} if await unquote(ready)(value)
{await unquote(pause)(0);throw}catch other{0}}}};export{recover}'''}
        source='''import "forms.chaos" as F;async fn fail(){throw 42};
async fn ready(value){await sleep(0);value==42};let run=F.recover!(fail,ready,sleep);
try{await run()}catch outer{outer.value}'''
        self.variants(source,42,files=files)

    def test_generator_template_keeps_its_error_across_yield(self):
        source='''macro stream(){quote{fn*(){try{throw 42}
catch {value:value,.._}{yield value;throw}catch other{yield 0}}}};
let create=stream!();let source=create();let first=next(source).value;
[first,try{next(source)}catch outer{outer.value}]'''
        self.variants(source,[42,42])

    def test_expanded_control_errors_are_rejected_before_effects(self):
        for source in (
            'macro bad(){quote{try{0}catch e if do{break}{1}catch other{2}}};bad!()',
            'macro bad(){quote{try{0}catch e if await sleep(0){1}catch other{2}}};fn regular(){bad!()};regular()',
            'fn run(){[try{0}catch e if do{return true}{1}catch other{2} for x in [1]]};run()',
        ):
            with self.subTest(source=source):
                seen=[]
                with self.assertRaises(LanguageError):
                    Evaluator(tool_handler=lambda *args:seen.append(args)).run('tool("before",null);'+source)
                self.assertEqual(seen,[])

    def test_analysis_remains_inert_and_pure_compilation_does_not_rethrow(self):
        source='macro handle(){quote{try{throw 42}catch {value:value,.._}{value}}};handle!()'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            self.assertNotIn('runtime_error',[d.get('code') for d in Framework.analyze(source)['diagnostics']])
        with self.assertRaises(LanguageError):
            Framework.evaluate('macro unsupported(){throw};unsupported!()')


if __name__=='__main__':unittest.main()
