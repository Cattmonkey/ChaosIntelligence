import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.task_groups import create, info, GroupRun, ScopeOutcome
from chaos.suspension import Await, Effect


class TaskGroupTests(unittest.TestCase):
    def test_top_level_native_scope_and_cold_info(self):
        value = Evaluator().run('''
          let g=task_group();let before=task_group_info(g);
          async fn child(x){x+1}
          let result=async with g as group {
            let a=group.spawn(child(2)); let b=group.spawn(child(4));
            [await a,await b,task_group_info(group)]
          };
          [type_of(g),before,result,task_group_info(g)]
        ''')
        kind,before,result,after=value
        self.assertEqual(kind,'TaskGroup');self.assertEqual(before['state'],'new')
        self.assertIsNone(before['bodyTaskId']);self.assertEqual(before['memberCount'],0)
        self.assertEqual(result[:2],[3,5]);self.assertEqual(after['state'],'closed')
        self.assertEqual(after['memberCount'],2);self.assertNotIn(after['bodyTaskId'],after['memberTaskIds'])

    def test_spawn_same_group_is_idempotent_and_result_order_is_await_order(self):
        self.assertEqual(Evaluator().run('''
          var calls=0;async fn f(){calls=calls+1;42}
          async with task_group() as g {
            let t=f();g.spawn(t);g.spawn(t);
            [await t,task_group_info(g).memberCount,calls]
          }
        '''),[42,1,1])

    def test_spawn_getattr_and_readonly_handle(self):
        self.assertEqual(Evaluator().run('''
          async fn f(){9}async with task_group() as g {
            [hasattr(g,"spawn"),hasattr(g,"missing"),await getattr(g,"spawn")(f())]
          }
        '''),[True,False,9])

    def test_return_break_continue_are_scoped_outcomes(self):
        self.assertEqual(Evaluator().run('''
          async fn f(){async with task_group() as g{return 8}}
          var count=0;
          for n in [1,2,3,4]{async with task_group() as g {if n==2{continue}else{null};if n==4{break}else{null};count=count+n}};
          [await f(),count]
        '''),[8,4])

    def test_child_failure_cannot_be_caught_inside_body_but_runs_finally(self):
        self.assertEqual(Evaluator(step_limit=3000).run('''
          var events=[];async fn bad(){throw "original"}
          try{async with task_group() as g {
            g.spawn(bad());try {while true {1}} catch e {events=events+["caught-inside"]}
            finally {events=events+["finally"]}
          }}catch e {events=events+[e.value]};events
        '''),['finally','original'])

    def test_exit_protocol_runs_but_cannot_suppress_group_failure(self):
        self.assertEqual(Evaluator(step_limit=3000).run('''
          var events=[];
          class C {fn __enter__(self){self};fn __exit__(self,e){events=events+[e.value];true}}
          async fn bad(){throw "original"}
          try{async with task_group() as g {with C() as c {g.spawn(bad());while true {1}}}}
          catch e{events=events+["outside"]};events
        '''),['original','outside'])

    def test_scope_failure_overrides_pending_return(self):
        self.assertEqual(Evaluator().run('''
          async fn bad(){throw "original"}
          async fn f(){async with task_group() as g{g.spawn(bad());return 7}}
          try{await f()}catch e{e.value}
        '''),'original')

    def test_gather_closure_is_owned_and_duplicates_preserved(self):
        value=Evaluator().run('''
          async fn f(n){n};let g=task_group();
          let result=async with g as group {
            let a=f(1);let b=f(2);
            await group.spawn(gather([gather([a,b]),a]))
          };[result,task_group_info(g).memberCount]
        ''')
        self.assertEqual(value,[[[1,2],1],4])

    def test_gather_admission_failure_is_atomic(self):
        value=Evaluator().run('''
          async fn f(n){n};let external=spawn(f(1));
          async with task_group() as g {
            let cold=f(2);let batch=gather([cold,external]);
            let error=try{g.spawn(batch);null}catch e{e.code};
            [error,task_info(cold),task_info(batch),task_group_info(g)]
          }
        ''')
        self.assertEqual(value[0],'task_group_error')
        for task in value[1:3]:
            self.assertEqual(task['state'],'created');self.assertFalse(task['observed']);self.assertIsNone(task['groupId'])
        self.assertEqual(value[3]['memberCount'],0)

    def test_group_is_one_shot_and_spawn_requires_member(self):
        self.assertEqual(Evaluator().run('''
          let g=task_group();async fn f(){1};
          let before=try{g.spawn(f());null}catch e{e.code};
          async with g as g {1};
          let twice=try{async with g as g {2};null}catch e{e.code};
          let after=try{g.spawn(f());null}catch e{e.code};[before,twice,after]
        '''),['task_group_error']*3)

    def test_members_can_spawn_during_normal_join(self):
        self.assertEqual(Evaluator().run('''
          var count=0;async fn leaf(){count=count+1}
          async fn branch(g){g.spawn(leaf());null}
          let g=task_group();async with g as g {g.spawn(branch(g));null};
          [count,task_group_info(g).memberCount]
        '''),[1,2])

    def test_nested_scopes_have_real_topology(self):
        a,b=Evaluator().run('''
          async with task_group() as outer {
            async with task_group() as inner {[task_group_info(outer),task_group_info(inner)]}
          }
        ''')
        self.assertEqual(b['parentGroupId'],a['id']);self.assertEqual(b['ownerTaskId'],a['bodyTaskId'])

    def test_external_running_task_await_is_not_owned(self):
        done=threading.Event();cancelled=[]
        def handler(kind,payload,cancel):
            if payload['name']=='other':
                done.wait(2);cancelled.append(cancel.is_set());return 7
            done.set();raise LanguageError('original')
        value=Evaluator(tool_handler=lambda *a:None,effect_handler=handler).run('''
          async fn other(){tool("other",0)};async fn bad(){tool("fail",0)};
          let external=spawn(other());
          try{async with task_group() as g {g.spawn(bad());await external}}catch e{null};
          await external
        ''')
        self.assertEqual(value,7);self.assertEqual(cancelled,[False])

    def test_body_effect_cancel_is_local_and_finally_can_use_effects(self):
        started=threading.Event();finished=threading.Event();calls=[]
        def handler(kind,payload,cancel):
            name=payload['name'];calls.append(name)
            if name=='body':
                started.set();self.assertTrue(cancel.wait(2));finished.set();return 0
            if name=='bad':
                self.assertTrue(started.wait(2));raise LanguageError('original')
            self.assertTrue(finished.is_set());self.assertFalse(cancel.is_set());return 0
        result=Evaluator(tool_handler=lambda *a:None,effect_handler=handler).run('''
          async fn bad(){tool("bad",0)}
          try{async with task_group() as g {g.spawn(bad());try{tool("body",0)}finally{tool("cleanup",0)}}}
          catch e {e.message}
        ''')
        self.assertEqual(result,'original');self.assertEqual(calls[-1],'cleanup')

    def test_reentrant_idle_interrupt_waits_for_actual_effect_completion(self):
        entered=threading.Event();cancelled=threading.Event();release=threading.Event();ended=threading.Event()
        idle=[]
        class ProbeEvaluator(Evaluator):
            def tick(self):
                if cancelled.is_set() and not release.is_set():
                    idle.append(self.suspension.current)
                    release.set()
                return super().tick()
        def handler(kind,payload,cancel):
            if payload['name']=='body':
                entered.set();self.assertTrue(cancel.wait(2));cancelled.set()
                self.assertTrue(release.wait(2));ended.set();return 0
            self.assertTrue(entered.wait(2));raise LanguageError('original')
        events=[]
        def trace(event):
            if event['event']=='task.cancelled' and event.get('kind')=='scope':
                events.append(ended.is_set())
        value=ProbeEvaluator(tool_handler=lambda *a:None,effect_handler=handler,trace_handler=trace).run('''
          async fn bad(){tool("bad",0)}
          try{async with task_group() as g {g.spawn(bad());map(fn(n){tool("body",n)},[1])}}
          catch e{e.message}
        ''')
        self.assertEqual(value,'original');self.assertEqual(idle,[None]);self.assertEqual(events,[True])

    def test_late_hard_from_body_effect_overrides_ordinary_and_skips_finally(self):
        started=threading.Event();hard=ResourceLimit('late hard');cleanup=[]
        def handler(kind,payload,cancel):
            if payload['name']=='body':
                started.set();self.assertTrue(cancel.wait(2));raise hard
            if payload['name']=='bad':
                self.assertTrue(started.wait(2));raise LanguageError('original')
            cleanup.append(payload['name'])
        evaluator=Evaluator(tool_handler=lambda *a:None,effect_handler=handler)
        with self.assertRaises(ResourceLimit) as caught:
            evaluator.run('''async fn bad(){tool("bad",0)}
              try{async with task_group() as g {g.spawn(bad());try{tool("body",0)}finally{tool("cleanup",0)}}}
              catch e {0}''')
        self.assertIs(caught.exception,hard);self.assertEqual(cleanup,[])

    def test_generator_scope_yield_is_rejected_and_drained(self):
        self.assertEqual(Evaluator().run('''
          async fn* f(){async with task_group() as g {yield 1}}
          try{await anext(f())}catch e{e.code}
        '''),'task_group_error')

    def test_consumed_generators_inside_scope_are_supported(self):
        self.assertEqual(Evaluator().run("fn* values(){yield 1;yield 2};async with task_group() as g {collect(values())}"),[1,2])
        self.assertEqual(Evaluator().run("async fn* values(){yield 1;yield 2};async with task_group() as g {await acollect(values())}"),[1,2])

    def test_body_lifecycle_reports_failures_and_scope_membership(self):
        events=[]
        value=Evaluator(trace_handler=events.append).run('try{async with task_group() as g {throw "body"}}catch e{e.value}')
        self.assertEqual(value,'body')
        created=[event for event in events if event['event']=='task.created' and event.get('kind')=='scope']
        failed=[event for event in events if event['event']=='task.failed' and event.get('kind')=='scope']
        self.assertEqual(len(created),1);self.assertEqual(len(failed),1)
        self.assertIsNotNone(created[0]['groupId']);self.assertEqual(created[0]['groupId'],failed[0]['groupId'])
        self.assertEqual([event['event'] for event in events if event['event'].startswith('group.')],['group.created','group.entered','group.closed'])

    def test_context_borrowed_cold_task_is_detached_without_cancellation(self):
        self.assertEqual(Evaluator(step_limit=3000).run('''
          var released=false;async fn outside(){while !released{1};41}
          let external=outside();
          class Gate{fn __aenter__(self){external};async fn __aexit__(self,e){null}}
          async fn bad(){throw "first"}
          let code=try{async with task_group() as g {g.spawn(bad());async with Gate() as x {0}}}
            catch e{e.value};
          let requested=task_info(external).cancellationRequested;released=true;
          [code,requested,await external]
        '''),['first',False,41])

    def test_new_context_gather_rejects_borrowed_closure_before_start(self):
        result=Evaluator().run('''
          async fn child(){42};let borrowed=child();var local=null;var batch=null;
          class Gate{fn __aenter__(self){local=child();batch=gather([local,borrowed]);batch};async fn __aexit__(self,e){null}}
          async with task_group() as g {
            let code=try{async with Gate() as x{0}}catch e{e.code};
            [code,task_info(local).state,task_info(local).observed,task_info(batch).state,task_info(borrowed).observed]
          }
        ''')
        self.assertEqual(result,['task_group_error','created',False,'created',False])

    def test_cleanup_awaiting_internally_cancelled_peer_retains_original(self):
        self.assertEqual(Evaluator().run('''
          async fn peer(){while true{1}};async fn bad(){throw "first"}
          try{async with task_group() as g {
            let p=g.spawn(peer());g.spawn(bad());try{while true{1}}finally{await p}
          }}catch e{e.value}
        '''),'first')

    def test_scope_uses_original_budget(self):
        with self.assertRaises(ResourceLimit):
            Evaluator(step_limit=80).run('async with task_group() as g {while true {1}}')

    def test_handle_epoch_and_foreign_owner(self):
        evaluator=Evaluator();handle=evaluator.run('task_group()')
        for other in [evaluator,Evaluator()]:
            other.suspension.begin()
            try:
                with self.assertRaises(LanguageError): info(other,handle)
            finally: other.suspension.finish()

    def test_snapshot_is_bounded_and_detached(self):
        evaluator=Evaluator();scheduler=evaluator.suspension;scheduler.begin()
        try:
            groups=[create(evaluator) for _ in range(105)]
            snap=scheduler.snapshot();self.assertEqual(snap['groupTotal'],105)
            self.assertEqual(len(snap['groups']),100);self.assertTrue(snap['groupsTruncated'])
            snap['groups'][0]['location']['line']=999
            self.assertNotIn('line',groups[0].location)
        finally:scheduler.finish()


if __name__=='__main__':unittest.main()
