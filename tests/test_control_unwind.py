"""Control transfers take original cleanup paths and never replay effects."""
import unittest
from chaos.language import Evaluator,LanguageError,Native,ResourceLimit


class ControlUnwindTests(unittest.TestCase):
    def value(self,source,**options):return Evaluator(step_limit=300000,**options).run(source)

    def test_wind_order_all_results_and_transfer(self):
        self.assertEqual(self.value('''var seen=[];let p=prompt_tag();
          let result=multiple_value_list(fn(){call_prompt(p,fn(){dynamic_wind(
            fn(){seen=append(seen,"before")},fn(){seen=append(seen,"body");abort_prompt(p,1,2)},
            fn(){seen=append(seen,"after")})})});[result,seen]'''),[[1,2],['before','body','after']])
        self.assertEqual(self.value('''multiple_value_list(fn(){dynamic_wind(fn(){null},fn(){return_values(1,2)},fn(){return_values(9,8)})})'''),[1,2])

    def test_wind_before_failure_skips_body_after(self):
        self.assertEqual(self.value('''var seen=[];try{dynamic_wind(fn(){throw "before"},
          fn(){seen=append(seen,"body")},fn(){seen=append(seen,"after")})}catch e{e.value};seen'''),[])

    def test_wind_after_error_replaces_body_transfer(self):
        self.assertEqual(self.value('''let p=prompt_tag();try{call_prompt(p,fn(){dynamic_wind(fn(){null},
          fn(){abort_prompt(p,42)},fn(){throw "cleanup"})})}catch e{e.value}'''),'cleanup')
        self.assertEqual(self.value('''try{dynamic_wind(fn(){null},fn(){throw "body"},fn(){throw})}catch e{e.value}'''),'body')

    def test_hard_limit_skips_wind_callback(self):
        effects=[];e=Evaluator(tool_handler=lambda name,value:effects.append(name))
        def stop():raise ResourceLimit('fixture')
        e.env.bind('stop',Native('stop',stop,e))
        with self.assertRaises(ResourceLimit):e.run('dynamic_wind(fn(){null},stop,fn(){tool("after",null)})')
        self.assertEqual(effects,[])

    def test_with_and_exit_stack_unwind_once_lifo(self):
        self.assertEqual(self.value('''var seen=[];class C{var n=null;fn __init__(self,n){self.n=n;null}
          fn __enter__(self){seen=append(seen,"enter"+self.n);self}
          fn __exit__(self,e){seen=append(seen,[self.n,e]);true}}
          let p=prompt_tag();let result=call_prompt(p,fn(){with exit_stack() as s{
            s.enter_context(C("a"));s.enter_context(C("b"));abort_prompt(p,42)}});[result,seen]'''),[42,['entera','enterb',['b',None],['a',None]]])

    def test_async_exit_can_transfer_in_same_logical_scope(self):
        self.assertEqual(self.value('''var seen=[];let p=prompt_tag();class C{
          async fn __aenter__(self){await sleep(0);self}
          async fn __aexit__(self,e){await sleep(0);seen=append(seen,prompt_available(p));abort_prompt(p,42)}}
          let result=call_prompt(p,async fn(){async with C() as c{1}});[result,seen]'''),[42,[True]])

    def test_async_cleanup_finishes_before_handler(self):
        self.assertEqual(self.value('''var seen=[];let p=prompt_tag();class C{
          async fn __aenter__(self){self}
          async fn __aexit__(self,e){await sleep(0);seen=append(seen,"cleanup");null}}
          let result=call_prompt(p,async fn(){async with C() as c{abort_prompt(p,41)}},
            fn(n){seen=append(seen,"handler");n+1});[result,seen]'''),[42,['cleanup','handler']])

    def test_task_group_body_inherits_prompt_members_do_not(self):
        self.assertEqual(self.value('''var seen=[];let p=prompt_tag();async fn child(){prompt_available(p)}
          let result=call_prompt(p,async fn(){async with task_group() as g{
            seen=append(seen,await g.spawn(child()));abort_prompt(p,42)}});[result,seen]'''),[42,[False]])

    def test_condition_restart_and_prompt_cleanup_compose(self):
        self.assertEqual(self.value('''var seen=[];let p=prompt_tag();let result=call_prompt(p,fn(){
          restart_case(fn(){try{invoke_restart("go")}finally{seen=append(seen,"restart-cleanup")}},
            [{name:"go",function:fn(){abort_prompt(p,42)}}])});[result,seen]'''),[42,['restart-cleanup']])

