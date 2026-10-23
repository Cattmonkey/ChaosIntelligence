"""Bounded dependency admission uses the existing cooperative Task lifecycle."""
import hashlib
import json
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.modes import (canonical_definition, definition_hash, validate_definition,
                         mode_source_definition, skill_source_definition)
from chaos.suspension import TERMINAL


class ParallelPlanTests(unittest.TestCase):
    def evaluator(self, handler=None, **kwargs):
        return Evaluator(tool_handler=lambda *args:None, effect_handler=handler, **kwargs)

    def test_default_canonical_hash_and_serial_scan_order_are_unchanged(self):
        definition={'kind':'plan','name':'old','steps':[{'id':'x','call':'f','needs':[]}]}
        legacy=json.dumps(definition,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.assertEqual(canonical_definition(definition),legacy)
        self.assertEqual(canonical_definition({**definition,'max_concurrency':1}),legacy)
        self.assertEqual(definition_hash(definition),hashlib.sha256(legacy.encode()).hexdigest())
        self.assertNotEqual(definition_hash(definition),definition_hash({**definition,'max_concurrency':2}))
        source='''var seen=[];
          let p=plan([{id:"early",call:"early",needs:["root"]},{id:"root",call:"root"},{id:"late",call:"late"}],max_concurrency=1);
          await run_plan(p,{early:fn(c){seen=seen+["early"]},root:fn(c){seen=seen+["root"]},late:fn(c){seen=seen+["late"]}});
          [seen,has(mode_info(p),"max_concurrency")]
        '''
        self.assertEqual(Evaluator().run(source),[['root','late','early'],False])

    def test_export_is_readable_and_roundtrips_canonical_definition_hash(self):
        definition={'kind':'plan','name':'parallel','max_concurrency':2,'steps':[
            {'id':'one','call':'first'},{'id':'two','call':'second','needs':['one']}]}
        source=mode_source_definition(definition)
        self.assertEqual(source,'''let saved_mode = mode_from(
  {
    "kind": "plan",
    "max_concurrency": 2,
    "name": "parallel",
    "steps": [
      {"call": "first", "id": "one", "needs": []},
      {"call": "second", "id": "two", "needs": ["one"]}
    ]
  }
);
export {
  saved_mode
};
''')
        definitions=[definition,
            {'kind':'goal','objective':'Reach a verified result','planner':'prepare','verify':'check','max_iterations':8},
            {'kind':'skill','actions':[{'tool':'text.normalize','input':' saved input '}],'parameters':[]}]
        for item in definitions:
            with self.subTest(kind=item['kind']):
                exported=mode_source_definition(item)
                result=Evaluator().run('import "saved.chaos" as M;mode_metadata(M.saved_mode).definitionHash',files={'saved.chaos':exported})
                self.assertEqual(result,definition_hash(item))
                self.assertTrue(exported.endswith('\n'));self.assertGreater(exported.count('\n'),2)
        executable=skill_source_definition(definitions[-1])
        from chaos.source_format import format_source
        self.assertEqual(executable,format_source(executable))
        self.assertGreater(executable.count('\n'),2)
        output=Evaluator(tool_handler=lambda name,value:value.strip()).run(
            'import "skill.chaos" as S;S.recorded({})',files={'skill.chaos':executable})
        self.assertEqual(output,['saved input'])

    def test_concurrency_validation_and_all_bindings_are_preflighted(self):
        base={'kind':'plan','name':'p','steps':[{'id':'x','call':'f'}]}
        for value in (True,False,0,-1,129,1.0,'2',None):
            with self.subTest(value=value),self.assertRaises(LanguageError):
                validate_definition({**base,'max_concurrency':value})
        self.assertEqual(validate_definition({**base,'max_concurrency':128})['max_concurrency'],128)
        events=[]
        value=Evaluator(trace_handler=events.append).run('''var calls=0;
          let p=plan([{id:"a",call:"ok"},{id:"b",call:"missing"}],max_concurrency=2);
          let code=try{await run_plan(p,{ok:fn(c){calls=calls+1}})}catch e{e.code};[calls,code]
        ''')
        self.assertEqual(value,[0,'mode_error'])
        self.assertFalse(any(e['event'].startswith('mode.plan.') for e in events))

    def test_independent_effects_overlap_with_bounded_deterministic_admission(self):
        barrier=threading.Barrier(3);lock=threading.Lock();active=0;maximum=0
        events=[]
        def handler(kind,payload,cancel):
            nonlocal active,maximum
            with lock:
                active+=1;maximum=max(maximum,active)
            try:barrier.wait(3);return payload['value']
            finally:
                with lock:active-=1
        steps=[{'id':'s'+str(i),'call':'f'} for i in range(6)]
        source='let p=plan('+json.dumps(steps)+',max_concurrency=3);await run_plan(p,{f:fn(c){tool("echo",c.input)}},42)'
        result=self.evaluator(handler,trace_handler=events.append).run(source)
        self.assertEqual(result,{step['id']:42 for step in steps});self.assertEqual(maximum,3)
        starts=[e['stepId'] for e in events if e['event']=='mode.plan.step.started']
        self.assertEqual(starts,[step['id'] for step in steps])

    def test_successor_starts_after_own_dependency_without_waiting_for_wave(self):
        released=threading.Event();slow_started=threading.Event();events=[]
        def handler(kind,payload,cancel):
            if payload['name']=='slow':
                slow_started.set();self.assertTrue(released.wait(3));return 'slow'
            if payload['name']=='fast':
                self.assertTrue(slow_started.wait(3));return 'fast'
            self.assertEqual(payload['value'],{'fast':'fast'})
            released.set();return 'successor'
        result=self.evaluator(handler,trace_handler=events.append).run('''
          await run_plan(plan([{id:"next",call:"next",needs:["fast"]},{id:"slow",call:"slow"},{id:"fast",call:"fast"}],max_concurrency=2),{
            slow:fn(c){tool("slow",null)},fast:fn(c){tool("fast",null)},next:fn(c){tool("next",c.results)}
          })
        ''')
        self.assertEqual(list(result),['next','slow','fast'])
        self.assertEqual(result,{'next':'successor','slow':'slow','fast':'fast'})
        self.assertEqual([e['stepId'] for e in events if e['event']=='mode.plan.step.started'],['slow','fast','next'])

    def test_async_action_failure_stops_admission_and_drains_fresh_child(self):
        started=threading.Event();drained=threading.Event();events=[]
        def handler(kind,payload,cancel):
            if payload['name']=='work':
                started.set();self.assertTrue(cancel.wait(3));drained.set();return 0
            if payload['name']=='fail':
                self.assertTrue(started.wait(3));raise LanguageError('original')
            self.assertTrue(drained.is_set());return 'caught after drain'
        evaluator=self.evaluator(handler,trace_handler=events.append)
        result=evaluator.run('''
          let p=plan([{id:"work",call:"work"},{id:"bad",call:"bad"},{id:"later",call:"later"}],max_concurrency=2);
          try{await run_plan(p,{work:async fn(c){tool("work",null)},bad:fn(c){tool("fail",null)},later:fn(c){throw "admitted after failure"}})}
          catch e{[e.message,await async fn(){tool("caught",null)}()]}
        ''')
        self.assertEqual(result,['original','caught after drain']);self.assertTrue(drained.is_set())
        self.assertEqual([e['stepId'] for e in events if e['event']=='mode.plan.step.started'],['work','bad'])
        self.assertTrue(all(t.state in TERMINAL for t in evaluator.suspension.tasks))
        self.assertTrue(all(g.state=='closed' for g in evaluator.suspension.groups))

    def test_new_gather_is_owned_and_drained(self):
        started=threading.Barrier(3);drained=[]
        def handler(kind,payload,cancel):
            started.wait(3)
            if payload['name']=='fail':raise LanguageError('original')
            self.assertTrue(cancel.wait(3));drained.append(payload['value']);return 0
        result=self.evaluator(handler).run('''
          async fn worker(n){tool("work",n)}
          try{await run_plan(plan([{id:"work",call:"work"},{id:"bad",call:"bad"}],max_concurrency=2),{
            work:fn(c){gather([worker(1),worker(2)])},bad:fn(c){tool("fail",null)}
          })}catch e{e.message}
        ''')
        self.assertEqual(result,'original');self.assertEqual(sorted(drained),[1,2])

    def test_borrowed_cold_and_running_action_tasks_survive_peer_failure(self):
        for start in ('outside()','spawn(outside())'):
            with self.subTest(start=start):
                entered=threading.Event();release=threading.Event();cancelled=[]
                def handler(kind,payload,cancel):
                    if payload['name']=='outside':
                        entered.set();self.assertTrue(release.wait(3));cancelled.append(cancel.is_set());return 42
                    self.assertTrue(entered.wait(3));release.set();raise LanguageError('original')
                result=self.evaluator(handler).run('''
                  async fn outside(){tool("outside",null)};let external='''+start+''';
                  let error=try{await run_plan(plan([{id:"borrow",call:"borrow"},{id:"bad",call:"bad"}],max_concurrency=2),{
                    borrow:fn(c){external},bad:fn(c){tool("fail",null)}
                  })}catch e{e.message};
                  [error,await external,task_info(external).cancellationRequested]
                ''')
                self.assertEqual(result,['original',42,False]);self.assertEqual(cancelled,[False])

    def test_fresh_gather_with_borrowed_peer_is_refused_without_claiming_it(self):
        result=Evaluator().run('''
          async fn f(){42};let external=f();var local=null;
          let error=try{await run_plan(plan([{id:"x",call:"x"}],max_concurrency=2),{
            x:fn(c){local=f();gather([local,external])}
          })}catch e{e.code};
          [error,task_info(local).state,task_info(local).observed,task_info(external).state,task_info(external).observed,await external]
        ''')
        self.assertEqual(result,['task_group_error','created',False,'created',False,42])

    def test_late_hard_failure_during_drain_preserves_exact_identity(self):
        started=threading.Event();hard=ResourceLimit('late hard')
        def handler(kind,payload,cancel):
            if payload['name']=='work':
                started.set();self.assertTrue(cancel.wait(3));raise hard
            self.assertTrue(started.wait(3));raise LanguageError('ordinary first')
        with self.assertRaises(ResourceLimit) as caught:
            self.evaluator(handler).run('''
              try{await run_plan(plan([{id:"work",call:"work"},{id:"bad",call:"bad"}],max_concurrency=2),{
                work:async fn(c){tool("work",null)},bad:fn(c){tool("fail",null)}
              })}catch e{0}
            ''')
        self.assertIs(caught.exception,hard)

    def test_explicit_cancellation_drains_parallel_steps(self):
        started=threading.Event();drained=threading.Event()
        def handler(kind,payload,cancel):
            if payload['name']=='work':
                started.set();self.assertTrue(cancel.wait(3));drained.set();return 0
            self.assertTrue(started.wait(3));return 0
        evaluator=self.evaluator(handler)
        result=evaluator.run('''
          let p=plan([{id:"a",call:"work"},{id:"b",call:"work"}],max_concurrency=2);
          let t=spawn(run_plan(p,{work:async fn(c){tool("work",null)}}));
          await async fn(){tool("wait-started",null)}();cancel(t);await task_wait([t],until="all");task_info(t).state
        ''')
        self.assertEqual(result,'cancelled');self.assertTrue(drained.is_set())
        self.assertTrue(all(g.state=='closed' for g in evaluator.suspension.groups))

    def test_same_plan_concurrent_executions_have_correlated_step_identity(self):
        events=[]
        value=Evaluator(trace_handler=events.append).run('''
          let p=plan([{id:"x",call:"f"}],name="shared",max_concurrency=2);
          await gather([run_plan(p,{f:async fn(c){await sleep(0);c.input}},1),run_plan(p,{f:fn(c){c.input}},2)])
        ''')
        self.assertEqual(value,[{'x':1},{'x':2}])
        starts=[e for e in events if e['event']=='mode.plan.started']
        self.assertEqual(len({e['executionId'] for e in starts}),2)
        for started in starts:
            related=[e for e in events if e.get('executionId')==started['executionId']]
            self.assertEqual([e['event'] for e in related],['mode.plan.started','mode.plan.step.started','mode.plan.step.completed','mode.plan.completed'])
            for event in related:
                for key in ('name','definitionHash','maxConcurrency'):
                    self.assertEqual(event[key],started[key])
                self.assertNotIn('input',event);self.assertNotIn('results',event)
                if event['event'].startswith('mode.plan.step.'):
                    self.assertIn('stepTaskId',event)

    def test_execution_identity_is_unique_across_evaluators(self):
        events=[]
        for _ in range(2):
            Evaluator(trace_handler=events.append).run('await run_plan(plan([{id:"x",call:"f"}]),{f:fn(c){42}})')
        started=[event for event in events if event['event']=='mode.plan.started']
        self.assertNotEqual(started[0]['executionId'],started[1]['executionId'])


if __name__=='__main__':unittest.main()
