import unittest
from chaos import Framework, Registry, ProviderSpec, Final
from chaos.language import Evaluator


class ConditionAgentIntegration(unittest.TestCase):
    def test_real_registry_agent_selects_structured_restart(self):
        registry=Registry();registry.register_provider(ProviderSpec('recover',lambda request,context:Final('use')))
        with Framework(':memory:',registry=registry) as app:
            source='agent planner {provider:"recover",model:"local",tools:[]};restart_case(fn(){with handler_bind([{predicate:fn(c){c.kind=="missing"},handler:fn(c){let choice=planner({condition:condition_info(c),restarts:map(restart_info,compute_restarts(c))});invoke_restart(choice,[c.["data"].fallback])}}]) as policy {error_condition(condition("missing",data={fallback:42}))}},[{name:"use",function:fn(value){value}}])'
            project=app.create_project('condition-agent',source=source)
            self.assertEqual(app.start(project['id'],1,None,mode='source').result(5),42)
    def test_async_resource_exit_can_transfer_to_outer_case(self):
        source='class Scope {async fn __aenter__(self){null};async fn __aexit__(self,error){await sleep(0);invoke_restart("x",[42])}};restart_case(async fn(){async with Scope() as item {0}},[{name:"x",function:fn(x){x}}])'
        self.assertEqual(Evaluator(step_limit=100000).run(source),42)
    def test_exit_stack_cleanup_order(self):
        source='var seen=[];let value=restart_case(fn(){with exit_stack() as stack {stack.callback(fn(){seen=append(seen,"first")});stack.callback(fn(){seen=append(seen,"second")});invoke_restart("x",[42])}},[{name:"x",function:fn(x){seen=append(seen,"restart");x}}]);[value,seen]'
        self.assertEqual(Evaluator().run(source),[42,['second','first','restart']])
    def test_inline_async_case_result_cold_value_is_not_awaited(self):
        source='async fn child(){42};let task=restart_case(fn(){invoke_restart("x")},[{name:"x",function:fn(){child()}}]);[task_info(task).state,await task]'
        self.assertEqual(Evaluator().run(source),['created',42])

    def test_fresh_async_exit_stack_callback_can_choose_parent_restart(self):
        source='var seen=[];let result=restart_case(async fn(){async with exit_stack(true) as stack {stack.push_async_callback(async fn(){await sleep(0);seen=append(seen,"cleanup");invoke_restart("x",[42])});0}},[{name:"x",function:fn(x){seen=append(seen,"restart");x}}]);[result,seen]'
        self.assertEqual(Evaluator().run(source),[42,['cleanup','restart']])
    def test_group_failure_wins_cleanup_restart(self):
        source='var recovered=false;async fn bad(){throw "original"};let result=try{restart_case(async fn(){async with task_group() as group {async with exit_stack(true) as stack {stack.push_async_callback(async fn(){await sleep(0);invoke_restart("x",[42])});group.spawn(bad());while true {1}}}},[{name:"x",function:fn(x){recovered=true;x}}])}catch e{e.value};[result,recovered]'
        self.assertEqual(Evaluator(step_limit=100000).run(source),['original',False])
