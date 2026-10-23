import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable
from chaos.cancellation import CancelShield, create, info, owned
from chaos.suspension import TaskCancelled


class CancelShieldTests(unittest.TestCase):
    def test_native_value_lifecycle_nested_and_async_syntax(self):
        value = Evaluator().run('''
          let guard:CancelShield=cancel_shield();let before=cancel_shield_info(guard);
          async fn work(){with guard as g {
            let first=cancel_shield_info(g);
            let second=async with cancel_shield() as h {cancel_shield_info(h)};
            [first,second,type_of(g),hasattr(g,"cancel")]
          }};
          [before,await work(),cancel_shield_info(guard)]
        ''')
        before,(first,second,kind,has_member),after=value
        self.assertEqual((before['state'],before['taskId'],before['depth']),('new',None,0))
        self.assertEqual((first['depth'],second['depth'],first['taskId']==second['taskId']),(1,2,True))
        self.assertEqual((kind,has_member),('CancelShield',False))
        self.assertEqual((after['state'],after['depth'],after['cancellationDeferred']),('closed',0,False))

    def test_self_cancel_delays_until_exit_and_skips_outer_finally(self):
        calls=[]
        evaluator=Evaluator(tool_handler=lambda name,value:calls.append((name,value)))
        with self.assertRaises(TaskCancelled):
            evaluator.run('''
              var me=null;
              async fn work(){try{with cancel_shield() as guard {
                cancel(me);await sleep(0);
                tool("capture",[task_info(me),cancel_shield_info(guard)]);42
              }}finally{tool("outside",0)}};
              me=work();await me
            ''')
        self.assertEqual([item[0] for item in calls],['capture'])
        task,guard=calls[0][1]
        self.assertTrue(task['cancellationRequested']);self.assertTrue(task['cancellationDeferred'])
        self.assertEqual(task['shieldDepth'],1);self.assertTrue(guard['cancellationDeferred'])
        task=evaluator.suspension.tasks[0]
        self.assertTrue(task.cancel_event.is_set());self.assertEqual(task.cancel_sources,{'explicit'})
        self.assertEqual(task.cancel_shields,[])

    def test_nested_scope_cannot_consume_outer_request(self):
        calls=[]
        evaluator=Evaluator(tool_handler=lambda name,value:calls.append(value))
        with self.assertRaises(TaskCancelled):
            evaluator.run('''var me=null;async fn work(){with cancel_shield() as outer {
              with cancel_shield() as inner {cancel(me);tool("log",cancel_shield_info(inner).depth)};
              tool("log",[task_info(me).shieldDepth,task_info(me).cancellationDeferred]);42
            }};me=work();await me''')
        self.assertEqual(calls,[2,[1,True]])

    def test_pending_cancellation_overrides_return_and_ordinary_error(self):
        for ending in ('return 9','throw "failure"','42'):
            with self.subTest(ending=ending),self.assertRaises(TaskCancelled):
                Evaluator().run('var me=null;async fn work(){with cancel_shield() as g{cancel(me);'+ending+'}};me=work();await me')

    def test_normal_return_break_continue_and_tail_call(self):
        self.assertEqual(Evaluator().run('''
          fn plus(x){x+1}async fn work(){var count=0;
            for n in [1,2,3,4] {with cancel_shield() as g {
              if n==2{continue}else{null};if n==4{break}else{null};count=count+n
            }};with cancel_shield() as h{return plus(count)}
          };await work()
        '''),5)

    def test_group_failure_deferred_but_not_suppressed_and_cleanup_runs(self):
        self.assertEqual(Evaluator().run('''
          var events=[];async fn bad(){await sleep(0);throw "original"}
          try{async with task_group() as group{
            group.spawn(bad());try{with cancel_shield() as guard {
              await sleep(0.002);events=events+[cancel_shield_info(guard).cancellationDeferred];42
            }}finally{events=events+["finally"]}
          }}catch e{events=events+[e.value]};events
        '''),[True,'finally','original'])

    def test_group_sibling_cancellation_waits_protected_member(self):
        self.assertEqual(Evaluator().run('''
          var events=[];async fn sibling(){with cancel_shield() as g{
            await sleep(0.004);events=events+["protected"]}}
          async fn bad(){await sleep(0.001);throw "original"}
          try{async with task_group() as g {g.spawn(sibling());g.spawn(bad())}}
          catch e{events=events+[e.value]};events
        '''),['protected','original'])

    def test_await_cancelled_foreign_task_is_not_shielded(self):
        calls=[]
        evaluator=Evaluator(tool_handler=lambda n,v:calls.append(v))
        with self.assertRaises(TaskCancelled):
            evaluator.run('''async fn value(){1};let foreign=value();cancel(foreign);
              async fn work(){with cancel_shield() as g{await foreign;tool("bad",1)}};await work()''')
        self.assertEqual(calls,[])
        self.assertEqual(evaluator.suspension.tasks[1].cancel_sources,set())

    def test_child_tasks_do_not_inherit(self):
        self.assertEqual(Evaluator().run('''var me=null;async fn child(){task_info(me).shieldDepth};
          async fn work(){with cancel_shield() as g{me=child();await me}};await work()'''),0)

    def test_closed_guard_reentry_and_root_entry_rejected(self):
        self.assertEqual(Evaluator().run('''let g=cancel_shield();async fn work(){
          with g as first{1};try{with g as second{2}}catch e{e.code}
        };await work()'''),'cancel_shield_error')
        self.assertEqual(Evaluator().run('try{with cancel_shield() as g{1}}catch e{e.code}'),'cancel_shield_error')

    def test_stack_cannot_extend_lexical_scope(self):
        self.assertEqual(Evaluator().run('''async fn work(){with exit_stack() as stack {
          try{stack.enter_context(cancel_shield())}catch e{e.code}
        }};await work()'''),'cancel_shield_error')

    def test_guard_members_cannot_mutate(self):
        self.assertEqual(Evaluator().run('''let g=cancel_shield();[
          try{setattr(g,"state","active")}catch e{e.code},
          try{delattr(g,"state")}catch e{e.code},
          try{g["state"]}catch e{e.code}
        ]'''),['attribute_error','attribute_error','argument_error'])

    def test_sync_and_async_generator_cannot_yield_guard_outward(self):
        for prefix,consume in [('fn*','next(stream)'),('async fn*','await anext(stream)')]:
            with self.subTest(prefix=prefix):
                evaluator=Evaluator()
                value=evaluator.run(prefix+' values(){with cancel_shield() as g{yield 1}};'+
                    'async fn work(){let stream=values();try{'+consume+'}catch e{e.code}};await work()')
                self.assertEqual(value,'cancel_shield_error')
                self.assertTrue(all(g.state=='closed' for g in evaluator.suspension.cancel_shields))
                self.assertTrue(all(not t.cancel_shields for t in evaluator.suspension.tasks))

    def test_consuming_nested_generator_does_not_escape_shield(self):
        self.assertEqual(Evaluator().run('''fn* inner(){yield 20;yield 22};
          async fn work(){with cancel_shield() as g{let stream=inner();next(stream).value+next(stream).value}};
          await work()'''),42)

    def test_generator_context_cannot_suspend_shield(self):
        self.assertEqual(Evaluator().run('''fn* resource(){with cancel_shield() as g{yield 1}};
          async fn work(){try{with generator_context(resource()) as r{r}}catch e{e.code}};await work()'''),'cancel_shield_error')

    def test_owner_epoch_recording_capability_and_serialized_copy(self):
        evaluator=Evaluator();scheduler=evaluator.suspension;scheduler.begin()
        other=Evaluator();other.suspension.begin()
        try:
            guard=create(evaluator)
            self.assertEqual(serializable(guard)['$handle'],'CancelShield')
            with self.assertRaisesRegex(LanguageError,'解释器'):owned(guard,other)
            with self.assertRaisesRegex(LanguageError,'需要 CancelShield'):info(evaluator,serializable(guard))
            def empty():
                yield
            task=scheduler.task(empty());scheduler.current=task.runner
            task.recording_scope='recording-one'
            with self.assertRaisesRegex(LanguageError,'录制'):owned(guard,evaluator)
            task.recording_scope=None;task.runner.capabilities=frozenset()
            with self.assertRaisesRegex(LanguageError,'broader'):owned(guard,evaluator)
            scheduler.current=None;scheduler.finish();scheduler.begin()
            with self.assertRaisesRegex(LanguageError,'周期'):owned(guard,evaluator)
        finally:scheduler.current=None;scheduler.finish();other.suspension.finish()

    def test_snapshot_is_bounded_and_info_inert(self):
        evaluator=Evaluator();scheduler=evaluator.suspension;scheduler.begin()
        try:
            for _ in range(105):create(evaluator)
            snapshot=scheduler.snapshot(3)
            self.assertEqual((len(snapshot['shields']),snapshot['shieldTotal'],snapshot['shieldsTruncated']),(3,105,True))
            self.assertEqual(info(evaluator,scheduler.cancel_shields[0])['state'],'new')
            self.assertFalse(scheduler.tasks)
        finally:scheduler.finish()

    def test_channel_rejects_foreign_guard_without_getters(self):
        from chaos.channels import validate_payload
        evaluator=Evaluator();evaluator.suspension.begin()
        other=Evaluator();other.suspension.begin()
        try:
            guard=create(other)
            with self.assertRaisesRegex(LanguageError,'执行上下文'):validate_payload(evaluator,{'nested':[guard]})
        finally:evaluator.suspension.finish();other.suspension.finish()

    def test_cancellation_guard_count_limit(self):
        evaluator=Evaluator();evaluator.suspension.begin()
        try:
            evaluator.suspension.cancel_shields=[None]*10000
            with self.assertRaises(ResourceLimit):create(evaluator)
        finally:evaluator.suspension.cancel_shields=[];evaluator.suspension.finish()
