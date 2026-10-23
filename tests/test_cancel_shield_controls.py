import threading
import unittest

from chaos.language import Evaluator, ExecutionControl, LanguageError, Native, ResourceLimit
from chaos.suspension import TaskCancelled
from chaos import Framework, Registry, ProviderSpec, ToolSpec, ToolCall, Final, Settings


class CancelShieldControlTests(unittest.TestCase):
    def test_pending_local_cancel_cannot_extend_steps_or_deadline(self):
        for options in ({'step_limit':200},{'timeout':0.01}):
            evaluator=Evaluator(**options)
            with self.subTest(options=options),self.assertRaises(ResourceLimit):
                evaluator.run('var me=null;async fn work(){with cancel_shield() as g{cancel(me);while true{1}}};me=work();await me')
            self.assertTrue(all(not task.cancel_shields for task in evaluator.suspension.tasks))

    def test_global_cancel_interrupts_protected_wait_and_drains_worker(self):
        stopped=threading.Event();entered=threading.Event();finished=threading.Event();after=[]
        def effect(kind,payload,cancel):
            if payload['name']=='block':
                entered.set();stopped.set()
                self.assertTrue(cancel.wait(2));finished.set();raise TaskCancelled('ack')
            after.append(payload['name'])
        evaluator=Evaluator(tool_handler=lambda n,v:None,effect_handler=effect,cancelled=stopped.is_set)
        with self.assertRaises(ResourceLimit):
            evaluator.run('async fn work(){try{with cancel_shield() as g{tool("block",0);tool("after",0)}}finally{tool("cleanup",0)}};await work()')
        self.assertTrue(entered.is_set());self.assertTrue(finished.is_set());self.assertEqual(after,[])
        self.assertTrue(all(not task.cancel_shields for task in evaluator.suspension.tasks))

    def test_local_cancel_preserves_active_effect_and_stable_event(self):
        entered=threading.Event();released=threading.Event();calls=[];events=[]
        def effect(kind,payload,cancel):
            name=payload['name'];events.append((name,cancel))
            if name=='block':
                entered.set();self.assertTrue(released.wait(2));self.assertFalse(cancel.is_set());calls.append(name);return 41
            if name=='wait':self.assertTrue(entered.wait(2));return None
            if name=='release':released.set();return None
            calls.append(name);self.assertFalse(cancel.is_set());return 42
        evaluator=Evaluator(tool_handler=lambda n,v:None,effect_handler=effect)
        with self.assertRaises(TaskCancelled):
            evaluator.run('''var target=null;async fn work(){with cancel_shield() as guard{
              tool("block",0);tool("after",0)}};
              async fn stop(){tool("wait",0);cancel(target);tool("release",0)};
              target=work();let peer=spawn(stop());await target''')
        self.assertEqual(calls,['block','after'])
        worker_events=[event for name,event in events if name in ('block','after')]
        self.assertIs(worker_events[0],worker_events[1])
        self.assertTrue(worker_events[0].is_set())

    def test_epoch_shutdown_does_not_wait_for_protected_sleep(self):
        evaluator=Evaluator(timeout=1)
        # sleep(0) lets the unobserved task enter before top-level completion.
        evaluator.run('async fn work(){with cancel_shield() as g{await sleep(10000)}};spawn(work());await sleep(0);42')
        self.assertTrue(evaluator.suspension.cancel_shields)
        self.assertTrue(all(g.state=='closed' for g in evaluator.suspension.cancel_shields))
        self.assertTrue(all(t.state in ('cancelled','completed') for t in evaluator.suspension.tasks))

    def test_host_control_identity_wins_pending_local_request(self):
        for error in (ExecutionControl('host'),ResourceLimit('host budget'),KeyboardInterrupt(),SystemExit(7),TaskCancelled('unattributed')):
            def effect(kind,payload,cancel):raise error
            evaluator=Evaluator(tool_handler=lambda n,v:None,effect_handler=effect)
            with self.subTest(kind=type(error).__name__),self.assertRaises(type(error)) as caught:
                evaluator.run('var me=null;async fn work(){with cancel_shield() as g{cancel(me);tool("host",0)}};me=work();await me')
            self.assertIs(caught.exception,error)

    def test_borrowed_cancel_identity_survives_deferred_internal_group_cancel(self):
        evaluator=Evaluator()
        with self.assertRaises(TaskCancelled) as caught:
            evaluator.run('''async fn external(){1};let borrowed=external();cancel(borrowed);
              async fn member(){with cancel_shield() as g {await sleep(0.003);await borrowed}}
              async fn bad(){await sleep(0.001);throw "ordinary"}
              async with task_group() as group{group.spawn(member());group.spawn(bad())}''')
        borrowed=evaluator.suspension.tasks[0]
        self.assertIs(caught.exception._cancel_event,borrowed.cancel_event)

    def test_two_cancellation_sources_survive_deferral(self):
        evaluator=Evaluator()
        requests=[]
        def trace(event):
            if event.get('event')=='shield.entered':
                task=evaluator.suspension.current.task
                source=object();requests.append(source)
                evaluator.suspension._cancel(task,source)
                evaluator.suspension.cancel(task)
        evaluator.trace_handler=trace
        with self.assertRaises(TaskCancelled):
            evaluator.run('async fn work(){with cancel_shield() as g{await sleep(0);42}};await work()')
        self.assertEqual(evaluator.suspension.tasks[0].cancel_sources,{requests[0],'explicit'})

    def test_nested_native_driver_obeys_same_logical_scope(self):
        evaluator=Evaluator();calls=[]
        evaluator.tool_handler=lambda name,value:calls.append(name)
        # Reentrant host native shares one logical Task and original frames.
        def bridge_with_task(task):
            from chaos.language import Parser,Env
            env=Env(evaluator.env);env.bind('me',task)
            tree=Parser('with cancel_shield() as nested {cancel(me);tool("nested",0)}').program()
            return evaluator.evaluate(tree[1][0],env)
        native=Native('bridge',bridge_with_task,evaluator)
        with self.assertRaises(TaskCancelled):
            evaluator.run('var me=null;async fn work(){with cancel_shield() as outer{bridge(me);tool("after",0)}};me=work();await me',{'bridge':native})
        self.assertEqual(calls,['nested','after'])

    def test_actual_agent_handler_checkpoint_then_recording_abort(self):
        calls=[];registry=Registry()
        registry.register_tool(ToolSpec('app.checkpoint',lambda value,context:calls.append(value) or value))
        registry.register_provider(ProviderSpec('choose',lambda request,context:
            ToolCall('work',request.input) if not request.history else Final(request.history[-1]['output'])))
        source='''var job=null;
          async fn protected(x){with cancel_shield() as guard {
            cancel(job);tool("app.checkpoint",x);await sleep(0);x+1
          }}
          fn dispatch(x){job=protected(x);job}
          agent a{provider:"choose",tools:["app.checkpoint"]};
          let bound=agent_bind(a,[agent_tool("work",dispatch,capabilities=["app.checkpoint"])]);
          async fn action(x){await bound(x)};await record_skill(action,41)
        '''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('shield-agent',source=source,capabilities=['app.checkpoint'])
            run=app.start('shield-agent',1,None,mode='source')
            state=run.wait(10)
            self.assertEqual(state['status'],'failed')
            self.assertEqual(state['error'],'任务已取消')
            self.assertEqual(calls,[41])
            modes=app.modes('shield-agent')['items']
            self.assertTrue(modes)
            self.assertTrue(all(item['status']!='ready' for item in modes))
            events=app.events('shield-agent',limit=300)['items']
            self.assertTrue(any(item['type']=='program.shield.deferred' for item in events))

    def test_async_context_private_operation_drains_before_owner_hard_exit(self):
        entered=threading.Event();finished=threading.Event();owner_terminal=[]
        hard=ExecutionControl('private-interrupt')
        evaluator=Evaluator(tool_handler=lambda n,v:None)
        def effect(kind,payload,cancel):
            if payload['name']=='block':
                entered.set();self.assertTrue(cancel.wait(2));finished.set();raise TaskCancelled('ack')
            self.assertTrue(entered.wait(2));return None
        evaluator.effect_handler=effect
        def interrupt(task):evaluator.suspension.interrupt(task,hard)
        def trace(event):
            if event.get('event')=='task.cancelled' and event.get('shieldDepth')==0:
                task=next(t for t in evaluator.suspension.tasks if t.id==event['taskId'])
                if task.runner.error is hard:owner_terminal.append(finished.is_set())
        evaluator.trace_handler=trace
        with self.assertRaises(ExecutionControl) as caught:
            evaluator.run('''var owner=null;class Resource {
              async fn __aenter__(self){tool("block",0)}
              async fn __aexit__(self,error){null}
            }
            async fn work(){with cancel_shield() as g{async with Resource() as r{r}}}
            async fn stop(){tool("wait",0);interrupt_task(owner)}
            owner=work();spawn(stop());await owner''',{'interrupt_task':Native('interrupt_task',interrupt,evaluator)})
        self.assertIs(caught.exception,hard);self.assertTrue(finished.is_set());self.assertEqual(owner_terminal,[True])

    def test_async_context_borrowed_operation_keeps_cancellation_ownership(self):
        entered=threading.Event();released=threading.Event();seen=[]
        hard=ExecutionControl('borrowed-interrupt')
        evaluator=Evaluator(tool_handler=lambda n,v:None)
        def effect(kind,payload,cancel):
            if payload['name']=='block':entered.set();self.assertTrue(released.wait(2));return 7
            self.assertTrue(entered.wait(2));return None
        evaluator.effect_handler=effect
        def interrupt(task):evaluator.suspension.interrupt(task,hard)
        def trace(event):
            if event.get('event')=='task.cancelled':
                task=next(t for t in evaluator.suspension.tasks if t.id==event['taskId'])
                if task.runner.error is hard:
                    borrowed=evaluator.suspension.tasks[0]
                    seen.append(set(borrowed.cancel_sources));released.set()
        evaluator.trace_handler=trace
        with self.assertRaises(ExecutionControl) as caught:
            evaluator.run('''async fn external(){tool("block",0)};let borrowed=spawn(external());
            var owner=null;class Resource {
              fn __aenter__(self){borrowed}
              async fn __aexit__(self,error){null}
            }
            async fn work(){with cancel_shield() as g{async with Resource() as r{r}}}
            async fn stop(){tool("wait",0);interrupt_task(owner)}
            owner=work();spawn(stop());await owner''',{'interrupt_task':Native('interrupt_task',interrupt,evaluator)})
        self.assertIs(caught.exception,hard);self.assertEqual(seen,[set()])
