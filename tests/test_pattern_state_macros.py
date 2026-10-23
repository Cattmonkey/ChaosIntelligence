"""Structural writes and resource binding retain real hygienic identities."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Evaluator, LanguageError
from chaos.program import parse_program, format_program


RESOURCE='''class Resource {
 var value=null;fn __init__(self,value){self.value=value;null}
 fn __enter__(self){self.value}fn __exit__(self,error){null}
 async fn __aenter__(self){self.value}async fn __aexit__(self,error){null}
};'''


class PatternStateMacros(unittest.TestCase):
    def both(self,source,expect,**kwargs):
        for text in (source,Framework.format_source(source,line_width=40),format_program(parse_program(source))):
            self.assertEqual(Framework.evaluate(text,**kwargs),expect)

    def test_local_assignment_reuses_fresh_declaration_identity(self):
        source='''macro exchange(){quote{var [a,b]=[20,22];[a,b]=[b,a];[a,b]}}
let a=100;let b=200;[exchange!(),exchange!(),a,b]'''
        self.both(source,[[22,20],[22,20],100,200])

    def test_free_writer_targets_rejected_before_any_effect(self):
        for target in ('[value]','{item:value}','[value,..rest]'):
            calls=[]
            source='tool("before",null);macro mutate(){quote{'+target+'=[42]}};var value=1;var rest=[];mutate!()'
            with self.subTest(target=target),self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run(source)
            self.assertEqual(calls,[])

    def test_explicit_whole_syntax_keeps_caller_write_targets(self):
        self.both('macro run(step){quote{unquote(step)}};var [a,b]=[20,22];run!(do{[a,b]=[b,a]});[a,b]',[22,20])

    def test_constructor_dependency_and_existing_target_are_distinct(self):
        source='''data Result{Ok(value)}
macro extract(ctor,value){quote{let C=unquote(ctor);var result=0;C(result)=unquote(value);result}}
let result=100;[extract!(Ok,Ok(42)),result]'''
        self.both(source,[42,100])

    def test_resource_pattern_leaves_are_fresh_and_caller_body_stays_caller(self):
        source=RESOURCE+'''macro scoped(manager,body){quote{with unquote(manager) as [left,right]{[left+right,unquote(body)]}}}
let left=100;let right=200;[scoped!(Resource([20,22]),left),left,right]'''
        self.both(source,[[42,100],100,200])

    def test_nested_mixed_resources_and_async_imported_macro(self):
        files={'forms.chaos':'''macro scoped(factory){quote{
 async with unquote(factory)([20,22]) as [left,right], unquote(factory)({sum:left+right}) as {sum:answer}{answer}
}};export{scoped}'''}
        self.both(RESOURCE+'import "forms.chaos" as F;let answer=100;[F.scoped!(Resource),answer]',[42,100],files=files)

    def test_illegal_expanded_async_scope_fails_before_runtime(self):
        macro='macro m(manager){quote{async with unquote(manager) as [value]{value}}};'
        for body in ('fn f(){m!(input)}','fn f(){fn g(x=m!(input)){x}}','fn f(){class C{let x=m!(input)}}'):
            calls=[]
            with self.subTest(body=body),self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run('tool("before",0);'+macro+body)
            self.assertEqual(calls,[])

    def test_pure_macro_phase_does_not_gain_state_or_resource_effects(self):
        for body in ('let x=0;[x]=[1];quote{1}', 'with input as [x]{quote{1}}'):
            with self.subTest(body=body),self.assertRaises(LanguageError):Framework.evaluate('macro m(){'+body+'};m!()')

    def test_analysis_of_new_macro_forms_never_executes(self):
        source=RESOURCE+'macro m(r){quote{var [a,b]=[0,0];[a,b]=[20,22];with unquote(r) as [x,y]{a+b+x+y}}};m!(Resource([0,0]))'
        with patch.object(Evaluator,'run',side_effect=AssertionError('inert analysis executed')):
            Framework.analyze(source)


if __name__=='__main__':unittest.main()
