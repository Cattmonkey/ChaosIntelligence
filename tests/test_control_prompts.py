"""Observable identity prompts and actual dynamic-extent escapes."""
import unittest
from chaos.language import Evaluator,LanguageError


class PromptControlTests(unittest.TestCase):
    def value(self,source):return Evaluator(step_limit=300000).run(source)

    def test_identity_names_and_nearest_matching_prompt(self):
        self.assertEqual(self.value('''let a=prompt_tag("same");let b=prompt_tag("same");
          [a==a,a==b,call_prompt(a,fn(){call_prompt(b,fn(){abort_prompt(a,42)},fn(x){0})}),
           call_prompt(a,fn(){call_prompt(a,fn(){abort_prompt(a,2)},fn(x){x+3})+1})]'''),[True,False,42,6])

    def test_normal_and_abort_zero_null_many_are_distinct(self):
        self.assertEqual(self.value('''let p=prompt_tag();[
          multiple_value_list(fn(){call_prompt(p,fn(){return_values(1,2)})}),
          multiple_value_list(fn(){call_prompt(p,fn(){abort_prompt(p)})}),
          multiple_value_list(fn(){call_prompt(p,fn(){abort_prompt(p,null)})}),
          multiple_value_list(fn(){call_prompt(p,fn(){abort_prompt(p,3,4)})})]'''),[[1,2],[],[None],[3,4]])

    def test_handler_runs_once_outside_removed_target(self):
        self.assertEqual(self.value('''var seen=[];let p=prompt_tag();
          let result=multiple_value_list(fn(){call_prompt(p,fn(){seen=append(seen,"body");abort_prompt(p,1,2)},
            fn(a,b){seen=append(seen,prompt_available(p));return_values(a+b,b)})});[result,seen]'''),[[3,2],['body',False]])

    def test_handler_can_target_outer_same_tag(self):
        self.assertEqual(self.value('''let p=prompt_tag();call_prompt(p,fn(){
          call_prompt(p,fn(){abort_prompt(p,2)},fn(x){abort_prompt(p,x+40)})})'''),42)

    def test_escape_skips_pending_expression_and_catch(self):
        self.assertEqual(self.value('''var seen=[];let result=call_escape(fn(exit){
          try{10+exit(42);seen=append(seen,"after")}catch e{seen=append(seen,"catch")}
          finally{seen=append(seen,"finally")}});[result,seen]'''),[42,['finally']])

    def test_escape_payload_and_normal_callback_contract(self):
        self.assertEqual(self.value('''[multiple_value_list(fn(){call_escape(fn(exit)->String{exit(1,2)})}),
          multiple_value_list(fn(){call_escape(fn(exit){exit()})}),
          multiple_value_list(fn(){call_escape(fn(exit){return_values(null,3)})})]'''),[[1,2],[],[None,3]])
        with self.assertRaises(LanguageError):self.value('call_escape(fn(exit)->String{1})')

    def test_escape_metadata_inside_after_and_barrier(self):
        self.assertEqual(self.value('''var captured=null;let inside=call_escape(fn(exit){captured=exit;
          [escape_info(exit).active,exit.available,control_barrier(fn(){exit.available})]});
          [inside,escape_info(captured).state,captured.active,captured.available,
           try{captured(1)}catch e{e.code}]'''),[[True,True,False],'closed',False,False,'escape_error'])

    def test_barrier_blocks_outward_but_allows_internal_prompt(self):
        self.assertEqual(self.value('''let p=prompt_tag();call_prompt(p,fn(){control_barrier(fn(){
          [prompt_available(p),try{abort_prompt(p,1)}catch e{e.code},
           call_prompt(p,fn(){abort_prompt(p,42)})]})})'''),[False,'control_barrier_error',42])

    def test_missing_target_and_callable_arguments_are_eager(self):
        self.assertEqual(self.value('let p=prompt_tag();[prompt_available(p),try{abort_prompt(p)}catch e{e.code}]'),[False,'prompt_error'])
        self.assertEqual(self.value('''var seen=[];try{dynamic_wind(fn(){seen=append(seen,1)},42,fn(){null})}catch e{null};seen'''),[])
        for source in ('call_prompt({},fn(){1})','call_escape(42)','control_barrier(42)','prompt_tag(1)'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.value(source)

    def test_first_class_escape_apply_partial_and_tail_calls(self):
        self.assertEqual(self.value('''fn deep(n,exit){if n>0{deep(n-1,exit)}else{apply(exit,[42,7])}};
          multiple_value_list(fn(){call_escape(fn(exit){let go=partial(deep,1000,exit);go()})})'''),[42,7])

    def test_with_scope_binding_exact_identity_and_one_shot(self):
        self.assertEqual(self.value('''let p=prompt_tag();let s=prompt_scope(p);var same=false;
          let result=with s as entered{same=entered==s;abort_prompt(p,42)};
          [result,same,s.state,s.active,try{with s as again{1}}catch e{e.code}]'''),[42,True,'closed',False,'context_error'])

