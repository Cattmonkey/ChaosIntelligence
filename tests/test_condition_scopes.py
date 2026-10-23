import unittest
from chaos.language import Evaluator,LanguageError,ResourceLimit,Native
from chaos.conditions import RestartTransfer
from chaos.suspension import hard_error


class ConditionLogicalScopes(unittest.TestCase):
    def value(self,source):return Evaluator(step_limit=250000,timeout=3).run(source)
    def test_actual_child_starts_empty(self):
        self.assertEqual(self.value('async fn child(){[find_restart("x"),compute_restarts()]};with restart_bind([{name:"x",function:fn(){42}}]) as r {await child()}'),[None,[]])
    def test_child_cannot_jump_parent_handle(self):
        self.assertEqual(self.value('async fn child(handle){try{invoke_restart(handle)}catch e{e.code}};restart_case(async fn(){await child(find_restart("x"))},[{name:"x",function:fn(){42}}])'),'restart_error')
    def test_transparent_async_body_handler_clause(self):
        source='var seen=[];let answer=restart_case(async fn(){seen=append(seen,"body");with handler_bind([{predicate:async fn(c){await sleep(0);true},handler:async fn(c){seen=append(seen,"handler");await sleep(0);invoke_restart("x",[42])}}]) as h {try{error_condition(condition("x"))}finally{seen=append(seen,"cleanup")}}},[{name:"x",function:async fn(x){await sleep(0);seen=append(seen,"restart");x}}]);[answer,seen]'
        self.assertEqual(self.value(source),[42,['body','handler','cleanup','restart']])
    def test_callback_normal_async_call_stays_cold_empty(self):
        self.assertEqual(self.value('async fn child(){find_restart("x")};with restart_bind([{name:"x",function:fn(){42}}]) as r {with handler_bind([{predicate:fn(c){true},handler:async fn(c){let t=child();let absent=await t;if absent==null{invoke_restart("x")}else{null}}}]) as h {signal_condition(condition("x"))}}'),None)
    def test_returned_handler_cold_handle_rejected(self):
        for function in ('async fn child(){42}','fn* child(){yield 42}'):
            with self.subTest(function=function),self.assertRaises(LanguageError):self.value(function+';with handler_bind([{predicate:fn(c){true},handler:fn(c){child()}}]) as h {signal_condition(condition("x"))}')
    def test_generator_callback_rejected_before_body(self):
        for source in ('restart_case(fn*(){throw "ran"},[])','restart_case(partial(fn*(){throw "ran"}),[])'):
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:self.value(source)
            self.assertEqual(caught.exception.code,'condition_protocol_error')
    def test_generator_scope_own_and_retained(self):
        source='fn* gen(){with restart_bind([{name:"own",function:fn(x){x}}]) as r {yield find_restart("parent");yield invoke_restart("own",[42])}};let stream=gen();let first=with restart_bind([{name:"parent",function:fn(){0}}]) as r {next(stream)};let second=next(stream);close(stream);[first.value,second.value]'
        self.assertEqual(self.value(source),[None,42])
    def test_suspended_generator_handle_cannot_invoke_in_caller(self):
        source='fn* gen(){with restart_bind([{name:"x",function:fn(){42}}]) as r {yield find_restart("x")}};let stream=gen();let handle=next(stream).value;let rejected=try{invoke_restart(handle)}catch e{e.code};close(stream);rejected'
        self.assertEqual(self.value(source),'restart_error')
    def test_async_generator_scopes_close_bookkeeping(self):
        e=Evaluator(step_limit=100000)
        self.assertEqual(e.run('async fn* gen(){with restart_bind([{name:"x",function:fn(){42}}]) as r {await sleep(0);yield invoke_restart("x")}};let stream=gen();let result=await anext(stream);await aclose(stream);result.value'),42)
        self.assertTrue(all(scope.state=='closed' for scope in e.suspension.condition_scopes))
    def test_taskgroup_body_shares_members_empty(self):
        source='async fn child(){find_restart("x")};restart_case(async fn(){async with task_group() as g {let task=g.spawn(child());let absent=await task;invoke_restart("x",[absent])}},[{name:"x",function:fn(value){[value,42]}}])'
        self.assertEqual(self.value(source),[None,42])
    def test_dynamic_exit_stack_lifo_and_foreign_preflight(self):
        source='let s=exit_stack();s.enter_context(restart_bind([{name:"x",function:fn(){42}}]));async fn foreign(){try{s.close()}catch e{e.code}};let rejected=await foreign();let value=invoke_restart("x");s.close();[rejected,value,find_restart("x")]'
        self.assertEqual(self.value(source),['ownership_error',42,None])
    def test_dynamic_exit_stack_non_lifo_no_pop(self):
        source='let s=exit_stack();s.enter_context(restart_bind([{name:"x",function:fn(){42}}]));let rejected=with handler_bind([]) as h {try{s.close()}catch e{e.code}};let value=invoke_restart("x");s.close();[rejected,value]'
        self.assertEqual(self.value(source),['context_error',42])
    def test_async_stack_helper_shares_holder(self):
        self.assertEqual(self.value('let s=exit_stack(true);s.enter_context(restart_bind([{name:"x",function:fn(){42}}]));await s.aclose();find_restart("x")'),None)
    def test_hard_budget_cannot_be_recovered(self):
        e=Evaluator(step_limit=100)
        with self.assertRaises(ResourceLimit):e.run('restart_case(fn(){with handler_bind([{predicate:fn(c){true},handler:fn(c){invoke_restart("x")}}]) as h {while true{null}}},[{name:"x",function:fn(){42}}])')
        self.assertTrue(all(scope.state=='closed' for scope in e.suspension.condition_scopes))
    def test_transfer_class_is_not_hard_error(self):
        self.assertFalse(hard_error(RestartTransfer(None,[],{},None,{})))
    def test_foreign_epoch_descriptor_cannot_restore(self):
        handle=Evaluator().run('with restart_bind([{name:"x",function:fn(){42}}]) as r {find_restart("x")}')
        with self.assertRaises(LanguageError):Evaluator().run('invoke_restart(input)',{'input':handle})
    def test_reentrant_driver_transparent(self):
        e=Evaluator()
        def bridge():return e.invoke(e.env.get('invoke_restart'),['x',[42]])
        self.assertEqual(e.run('restart_case(fn(){bridge()},[{name:"x",function:fn(x){x}}])',{'bridge':Native('bridge',bridge,e)}),42)
    def test_privileged_outer_restart_cannot_escalate(self):
        e=Evaluator(tool_handler=lambda name,value:42)
        limited=Native('limited',lambda fn:e.invoke(fn,[]),e,capabilities=frozenset())
        source='try{restart_case(fn(){limited(fn(){invoke_restart("x")})},[{name:"x",function:fn(){tool("secret",null)}}])}catch e{e.code}'
        self.assertEqual(e.run(source,{'limited':limited}),'capability_error')
