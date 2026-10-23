import unittest
from chaos.language import Evaluator


class MultipleValueRuntimeTests(unittest.TestCase):
    def code(self,source):return Evaluator(step_limit=300000).run(source)

    def test_try_finally_return_and_scope_cleanup(self):
        self.assertEqual(self.code('''var seen=[];class Scope{fn __enter__(self){null};fn __exit__(self,e){seen=append(seen,1);return_values(false,99)}};
          fn f(){with Scope() as item{try{return return_values(1,2)}finally{return_values(9,8)}}};
          [multiple_value_list(f),seen,multiple_value_list(fn(){try{throw 1}catch e{return_values(e.value,2)}})]'''),
          [[1,2],[1],[1,2]])

    def test_async_completion_interleaves_without_result_register(self):
        self.assertEqual(self.code('''async fn f(x){await sleep(0);return_values(x,x+1)};
          let a=f(1);let b=f(3);
          [multiple_value_list(async fn(){await a}),multiple_value_list(async fn(){await b}),await gather([a,b])]'''),
          [[1,2],[3,4],[1,3]])

    def test_explicit_async_receiver_callbacks_and_cold_handles(self):
        self.assertEqual(self.code('''async fn f(){return_values(1,2)};
          [multiple_value_bind(2,async fn(){await f()},async fn(a,b){await sleep(0);[a,b]}),
           type_of(multiple_value_list(fn(){f()})[0]),
           type_of(multiple_value_call(async fn(*items){items},fn(){return_values(1,2)}))]'''),
          [[1,2],'Task','Task'])

    def test_generator_terminal_primary_records_and_all_delegation(self):
        self.assertEqual(self.code('''fn* inner(){yield return_values(1,9);return_values(2,3)};
          fn* outer(){return yield from inner()};let g=outer();
          [next(g),next(g),next(g)]'''),
          [{'done':False,'value':1},{'done':True,'value':2},{'done':True,'value':2}])

    def test_context_run_and_inline_and_case_restarts(self):
        self.assertEqual(self.code('''let c=copy_context();
          [multiple_value_list(fn(){context_run(c,fn(){return_values(1,2)})}),
           multiple_value_list(fn(){restart_case(fn(){invoke_restart("use")},[{name:"use",function:fn(){return_values(3,4)}}])}),
           multiple_value_list(fn(){with restart_bind([{name:"use",function:fn(){return_values(5,6)}}]) as r{invoke_restart("use")}})]'''),
          [[1,2],[3,4],[5,6]])

    def test_generic_primary_around_and_after_preserve_results(self):
        self.assertEqual(self.code('''var seen=[];var f=generic_function("f");
          f=generic_method(f,["Int"],fn(x){return_values(x,x+1)});
          f=generic_method(f,["Int"],fn(x){seen=append(seen,"after");return_values(null,99)},"after");
          [multiple_value_list(fn(){f(1)}),seen]'''),[[1,2],['after']])

    def test_boolean_tail_projection_inside_compose(self):
        self.assertEqual(self.code('''fn b(n)->Bool{if n==0{return_values(true,9)}else{true&&b(n-1)}};
          [multiple_value_list(fn(){b(2000)}),
           multiple_value_list(fn(){true&&compose(fn(x){return_values(x,99)},fn(){return_values(true,2)})()})]'''),
          [[True],[True]])

    def test_multiple_value_call_target_stays_ordinary_through_alias_callback(self):
        self.assertEqual(self.code('''let target=async fn(*items){items};
          type_of(multiple_value_list(partial(multiple_value_call,target,fn(){return_values(1,2)}))[0])'''),'Task')

    def test_compiled_resumable_flow_preserves_full_function_completion(self):
        self.assertEqual(self.code('''let f=compile_function(fn(n:Int)->Int{
          var x=0;while x<n{x=x+1};if x==2{return return_values(x,9)}else{return_values(x,8)}
          },"auto-flow");[multiple_value_list(fn(){f(2)}),multiple_value_list(fn(){f(3)})]'''),
          [[2,9],[3,8]])

    def test_generator_handle_retains_all_delegated_terminal_values(self):
        from chaos.multiple_values import components
        e=Evaluator();handle=e.run('''fn* inner(){yield 1;return_values(2,3)};
          fn* outer(){return yield from inner()};let g=outer();next(g);next(g);g''')
        self.assertEqual(components(handle.result,e),(2,3))

    def test_completed_task_receivers_read_without_replay(self):
        self.assertEqual(self.code('''var seen=0;async fn f(){seen=seen+1;return_values(1,2)};
          let t=f();await t;
          [multiple_value_list(t),multiple_value_list(t),nth_value(1,t),
           multiple_value_call(fn(*xs){xs},t),multiple_value_bind(3,t,fn(a,b,c){[a,b,c]}),
           multiple_value_list(fn(){multiple_value_prog1(t,fn(){return_values(9)})}),seen]'''),
          [[1,2],[1,2],2,[1,2],[1,2,None],[1,2],1])

    def test_completed_sync_and_async_generator_zero_null_many_results(self):
        self.assertEqual(self.code('''fn* empty(){return_values()};fn* one(){return_values(null)};
          fn* many(){yield 1;return_values(2,3)};async fn* amany(){await sleep(0);return_values(4,5)};
          let a=empty();let b=one();let c=many();let d=amany();
          next(a);next(b);next(c);next(c);await anext(d);
          [multiple_value_list(a),multiple_value_list(b),multiple_value_list(c),
           multiple_value_list(d),multiple_value_list(d),nth_value(1,c)]'''),
          [[],[None],[2,3],[4,5],[4,5],3])

    def test_incomplete_and_closed_generators_are_never_advanced(self):
        self.assertEqual(self.code('''fn* f(){yield 1;return_values(2,3)};let g=f();
          let cold=try{multiple_value_list(g)}catch e{e.code};let first=next(g);
          let suspended=try{multiple_value_list(g)}catch e{e.code};let last=next(g);
          let closed=f();close(closed);
          [cold,first,suspended,last,try{multiple_value_list(closed)}catch e{e.code}]'''),
          ['multiple_value_state_error',{'done':False,'value':1},'multiple_value_state_error',
           {'done':True,'value':2},'multiple_value_state_error'])

    def test_failed_cancelled_and_cold_tasks_reject_without_effects(self):
        self.assertEqual(self.code('''var seen=0;async fn f(){seen=seen+1;return_values(1,2)};
          async fn bad(){throw 1};let cold=f();let cancelled=f();cancel(cancelled);
          let failed=bad();try{await failed}catch e{null};
          [try{multiple_value_list(cold)}catch e{e.code},
           try{multiple_value_list(cancelled)}catch e{e.code},
           try{multiple_value_list(failed)}catch e{e.code},seen]'''),
          ['multiple_value_state_error']*3+[0])


    def test_team_explicit_task_component_is_preserved_until_caller_awaits(self):
        self.assertEqual(self.code('''var seen=0;async fn child(x){seen=seen+1;return_values(x,x+1)};
          let team=team_from({kind:"team",name:"T",entry:"actor",roles:[{id:"actor",parent:null,
            capabilities:[],agent:{kind:"agent",name:"A",config:{provider:"fixture",tools:[]}}}]});
          let bound=team_bind(team,{actor:fn(x){return_values(child(x),"metadata")}});
          let captured=multiple_value_list(async fn(){await bound(1)});
          let before=[type_of(captured[0]),captured[1],task_info(captured[0]).state,seen];
          let completion=multiple_value_list(async fn(){await captured[0]});[before,completion,seen]'''),
          [["Task","metadata","created",0],[1,2],1])
