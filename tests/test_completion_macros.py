"""Normal completion keeps macro hygiene and existing control boundaries."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


class CompletionMacros(unittest.TestCase):
    def variants(self, source, expected, **options):
        for text in (source, Framework.format_source(source, line_width=40),
                     format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text, **options), expected)

    def test_loop_template_bindings_and_else_outer_names_are_hygienic(self):
        source='''macro search(items,otherwise){quote{for (item,) in unquote(items){
if item>0 {break}else{null}} else {unquote(otherwise)}}};
let item=42;[search!([],item),search!([(1,)],item),item]'''
        self.variants(source,[42,None,42])

    def test_try_template_catch_is_sibling_of_success_and_finally(self):
        source='''macro complete(work,failed,success,cleanup){quote{
try{unquote(work)()}catch error{unquote(failed)(error.value)}
else{unquote(success)()}finally{unquote(cleanup)()}}};
var events=[];fn mark(){events=append(events,"clean")};
let error=42;
let a=complete!(fn(){20},fn(e){e},fn(){error},mark);
let b=complete!(fn(){throw 22},fn(e){e},fn(){99},mark);[a,b,events,error]'''
        self.variants(source,[42,22,['clean','clean'],42])

    def test_success_errors_bypass_same_catch_after_template_expansion(self):
        source='''macro operation(action){quote{try{20}catch error{99}else{unquote(action)()}}};
try{operation!(fn(){throw "success failed"})}catch error{error.value}'''
        self.variants(source,'success failed')

    def test_imported_async_template_waits_in_normal_channel_completion(self):
        files={'forms.chaos':'''macro drain(source,finished){quote{async fn(){
async for (value,) in unquote(source) {value} else {await unquote(finished)()}}}};export{drain}'''}
        source='''import "forms.chaos" as F;let messages=channel(1);
await channel_send(messages,(20,));channel_close(messages);var seen=0;
async fn done(){await sleep(0);seen=seen+1;42};
let consume=F.drain!(messages,done);let task=consume();let before=seen;[before,await task,seen]'''
        self.variants(source,[0,42,1],files=files)

    def test_else_outer_loop_control_and_generator_resume(self):
        source='''macro end(){quote{for x in []{x}else{break}}};
var seen=0;while true{seen=seen+1;end!();seen=99};
macro stream(){quote{fn*(){for x in [20]{yield x}else{yield 22}}}};
let produce=stream!();[seen,collect(produce())]'''
        self.variants(source,[1,[20,22]])

    def test_expanded_invalid_control_is_rejected_before_any_effect(self):
        for source in (
            'macro bad(){quote{while false{}else{break}}};bad!()',
            'macro bad(){quote{for x in []{}else{continue}}};bad!()',
            'macro bad(){quote{fn(){while false{}else{break}}}};while true{bad!();break}',
            'macro bad(){quote{while false{}else{await sleep(0)}}};fn regular(){bad!()};regular()',
        ):
            with self.subTest(source=source):
                seen=[]
                with self.assertRaises(LanguageError):
                    Evaluator(tool_handler=lambda *args:seen.append(args)).run('tool("before",null);'+source)
                self.assertEqual(seen,[])

    def test_comprehension_boundary_visits_both_completion_branches(self):
        self.variants('[while false{}else{42} for x in [1,2]]',[42,42])
        for source in (
            'while true{[while false{}else{break} for x in [1]];break}',
            'fn run(){[try{1}catch e{0}else{return 42} for x in [1]]};run()',
            'while true{[try{1}catch e{0}else{continue} for x in [1]];break}',
        ):
            with self.subTest(source=source):
                seen=[]
                with self.assertRaises(LanguageError):
                    Evaluator(tool_handler=lambda *args:seen.append(args)).run('tool("before",null);'+source)
                self.assertEqual(seen,[])

    def test_analysis_stays_inert_and_compiler_phase_stays_explicit(self):
        source='macro work(){quote{for x in []{}else{42}}};work!()'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            self.assertNotIn('runtime_error',[d.get('code')for d in Framework.analyze(source)['diagnostics']])
        with self.assertRaises(LanguageError):
            Framework.evaluate('macro work(){while false{}else{syntax_literal(42)}};work!()')


if __name__=='__main__':unittest.main()
