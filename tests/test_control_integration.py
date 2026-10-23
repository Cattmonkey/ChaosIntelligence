"""Single evaluator integration across streams, tasks, Code and callable types."""
import unittest
from chaos.language import Evaluator,LanguageError


class ControlIntegrationTests(unittest.TestCase):
    def value(self,source):return Evaluator(step_limit=300000).run(source)

    def test_generator_scope_persists_yield_and_terminal_values(self):
        self.assertEqual(self.value('''let p=prompt_tag();var scope=null;fn* stream(){
          let result=with prompt_scope(p) as s{scope=s;yield prompt_available(p);abort_prompt(p,1,2)};
          return_values(result,3)}let g=stream();let first=next(g);
          let outside=[prompt_available(p),scope.active];let last=next(g);
          [first.value,outside,last.done,multiple_value_list(g),scope.state]'''),[True,[False,True],True,[1,3],'closed'])

    def test_generator_escape_unavailable_to_consumer_and_close_releases(self):
        self.assertEqual(self.value('''var saved=null;fn* stream(){with prompt_scope(prompt_tag()) as s{
          saved=s;yield s.active}}let g=stream();next(g);let active=saved.active;close(g);[active,saved.active,saved.state]'''),[True,False,'closed'])

    def test_async_generator_terminal_prompt_values(self):
        self.assertEqual(self.value('''let p=prompt_tag();async fn* stream(){with prompt_scope(p) as s{
          await sleep(0);yield 1;abort_prompt(p,2,3)}}let g=stream();let first=await anext(g);
          let last=await anext(g);[first.value,last.done,multiple_value_list(g)]'''),[1,True,[2,3]])

    def test_async_callback_await_keeps_active_prompt(self):
        self.assertEqual(self.value('''let p=prompt_tag();multiple_value_list(fn(){call_prompt(p,async fn(){
          await sleep(0);if prompt_available(p){abort_prompt(p,1,2)}else{0}})})'''),[1,2])

    def test_cold_task_and_generator_callback_results_remain_one_value(self):
        self.assertEqual(self.value('''var effects=0;async fn task(){effects=effects+1;42}
          fn* stream(){effects=effects+1;yield 1}
          let t=call_escape(fn(exit){task()});let g=call_prompt(prompt_tag(),stream);
          [type_of(t),type_of(g),effects,await t,next(g).value,effects]'''),['Task','Generator',0,42,1,2])

    def test_completed_task_preserves_control_terminal_values(self):
        self.assertEqual(self.value('''async fn task(){let p=prompt_tag();with prompt_scope(p) as s{abort_prompt(p,1,2)}}
          let t=task();await t;[multiple_value_list(t),multiple_value_list(t)]'''),[[1,2],[1,2]])

    def test_typed_control_values_and_callable_aliases(self):
        self.assertEqual(self.value('''let p:PromptTag=prompt_tag();let s:PromptScope=prompt_scope(p);
          call_escape(fn(exit:EscapeContinuation){let f:Function=exit;apply(f,[42])})'''),42)

    def test_code_function_uses_same_control_runtime(self):
        self.assertEqual(self.value('''let body=function_from(code_from("fn(exit){exit(42)}"));call_escape(body)'''),42)

    def test_std_control_aliases_share_exact_native_identities(self):
        self.assertEqual(self.value('''import "std/control" as C;
          [C.call_escape==call_escape,C.prompt_tag==prompt_tag,C.call_escape(fn(exit){exit(42)})]'''),[True,True,42])


    def test_control_handles_and_descriptors_share_inert_host_boundary(self):
        effects=[];e=Evaluator(tool_handler=lambda name,value:effects.append((name,value)))
        result=e.run('''var entered=0;class C{let value=property(fn(self){entered=entered+1;42})
          @classmethod
          fn method(cls){42}}
          let p=prompt_tag("transport");let s=prompt_scope(p);
          call_escape(fn(exit){tool("transport",[p,s,exit,C.value,C(),C.method]);null});entered''')
        self.assertEqual(result,0);self.assertEqual(len(effects),1)
        name,items=effects[0];self.assertEqual(name,'transport')
        self.assertEqual([item['$handle'] for item in items[:3]],['PromptTag','PromptScope','EscapeContinuation'])
        self.assertEqual(items[0]['name'],'transport');self.assertEqual(items[1]['state'],'created')
        self.assertTrue(items[2]['active']);self.assertEqual(items[3],{'$descriptor':'Property'})
        self.assertEqual(items[4],{'$instance':'C'});self.assertEqual(items[5],{'$boundMethod':'method','$class':'C'})
