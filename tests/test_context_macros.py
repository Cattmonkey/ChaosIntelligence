"""Resource bindings participate in the same hygienic macro scopes."""
import unittest

from chaos.language import Evaluator, LanguageError
from chaos.program import format_program, parse_program


RESOURCE='''class Resource {
  var value=0;
  fn __init__(self,value){self.value=value;null}
  fn __enter__(self){self.value}
  fn __exit__(self,error){null}
  async fn __aenter__(self){self.value}
  async fn __aexit__(self,error){null}
};'''


class ContextMacros(unittest.TestCase):
    def run_both(self,source,expected,**kwargs):
        self.assertEqual(Evaluator().run(source,**kwargs),expected)
        self.assertEqual(Evaluator().run(format_program(parse_program(source)),**kwargs),expected)

    def test_introduced_binding_does_not_capture_caller_body(self):
        source=RESOURCE+'''macro scope(manager,body){quote{with unquote(manager) as value {unquote(body)}}};
          let value=42;scope!(Resource(9),value)'''
        self.run_both(source,42)

    def test_introduced_binding_references_and_multimanager_hygiene(self):
        source=RESOURCE+'''macro scope(first,second){
          quote{with unquote(first) as value, unquote(second)(value+2) as result {result}}
        };let value=100;let result=200;[scope!(Resource(40),Resource),value,result]'''
        self.run_both(source,[42,100,200])

    def test_qualified_module_macro_keeps_definition_helper(self):
        files={'scope.chaos':'''macro inner(manager){quote{with unquote(manager) as item{item+1}}};
          macro scope(manager){quote{inner!(unquote(manager))}};export{scope}'''}
        self.run_both(RESOURCE+'import "scope.chaos" as S;let item=100;S.scope!(Resource(41))',42,files=files)

    def test_async_macro_context_and_return_scope(self):
        source=RESOURCE+'''macro scope(manager){quote{async with unquote(manager) as value{value}}};
          async fn f(){scope!(Resource(42))};await f()'''
        self.run_both(source,42)
        source=RESOURCE+'''macro scope(manager){quote{with unquote(manager) as value{return value}}};
          fn f(){scope!(Resource(42));99};f()'''
        self.run_both(source,42)

    def test_macro_expansion_checks_async_defaults_classes_and_controls(self):
        macro='macro scope(){quote{async with input as value{value}}};'
        invalid=['fn f(){scope!()}',
                 'fn f(){fn nested(x=scope!()){x}}',
                 'fn f(){class C{let x=scope!()}}',
                 'macro m(){quote{with input as value{return value}}};m!()',
                 'macro m(){quote{with input as value{break}}};m!()']
        for source in invalid:
            calls=[]
            with self.subTest(source=source),self.assertRaises(LanguageError):
                Evaluator(tool_handler=lambda *args:calls.append(args)).run('tool("bad",null);'+macro+source)
            self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
