"""The swarm is ordinary Chaos source, with real task/capability boundaries."""
import json
import threading
import time
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, Settings
from chaos.language import Evaluator, LanguageError, Native
from chaos.suspension import TaskCancelled, TERMINAL
from tests.test_team_functions import definition

IMPORT='import "std/swarm" as S;'


class SwarmTests(unittest.TestCase):
    def evaluate(self, source, bindings=None, **options):
        return Evaluator(step_limit=150000, **options).run(IMPORT+source, bindings)

    def test_signed_cyclic_barrier_uses_old_states_and_retains_order(self):
        before,after=self.evaluate('''let old=S.create({b:2,a:1},[
          {source:"a",target:"b",weight:-1},{source:"b",target:"a",weight:0.5}]);
          fn update(c){c.state+c.incoming[0].edge.weight*c.incoming[0].state}
          [old,await S.step(old,{a:update,b:update})]''')
        self.assertEqual(list(after['states']),['b','a'])
        self.assertEqual(before['states'],{'b':2,'a':1})
        self.assertEqual(after['states'],{'b':1,'a':2})
        self.assertEqual(after['tick'],1);self.assertEqual(before['tick'],0)
        self.assertEqual(before['edges'],after['edges'])

    def test_exact_schema_and_preflight_before_any_effect(self):
        cases=['{states:{a:1},edges:[],tick:0,extra:0}',
               '{states:{},edges:[],tick:0}', '{states:{a:1},edges:[],tick:true}',
               '{states:{a:1},edges:[],tick:-1}',
               '{states:{a:1},edges:[{source:"a",target:"missing",weight:1}],tick:0}',
               '{states:{a:1},edges:[{source:"a",target:"a",weight:true}],tick:0}',
               '{states:{a:1},edges:[{source:"a",target:"a",weight:1,extra:0}],tick:0}',
               '{states:{a:1},edges:[{source:"a",target:"a",weight:1},{source:"a",target:"a",weight:0}],tick:0}']
        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                self.assertEqual(self.evaluate('var calls=0;fn f(c){calls=calls+1;0};try{await S.step('+snapshot+',{a:f},prepare=f)}catch e{null};calls'),0)

    def test_schema_limits_and_zero_self_edge(self):
        states={str(i):i for i in range(65)}
        with self.assertRaises(LanguageError):self.evaluate('S.create(input)',{'input':states})
        self.assertEqual(len(self.evaluate('S.create(input)',{'input':dict(list(states.items())[:64])})['states']),64)
        with self.assertRaises(LanguageError):self.evaluate('S.create({a:1},input)',{'input':[{'source':'a','target':'a','weight':0}]*4097})
        self.assertEqual(self.evaluate('let s=S.create({a:1},[{source:"a",target:"a",weight:0}]);await S.step(s,{a:fn(c){c.state}})')['states'],{'a':1})
        for identifier in ('','x'*129):
            with self.subTest(identifier=identifier),self.assertRaises(LanguageError):self.evaluate('S.create(input)',{'input':{identifier:1}})

    def test_data_checker_rejects_live_nested_values_before_serialization(self):
        expressions=['fn(x){x}','tuple([1])','set([1])','Type','current_program()',
                     '(async fn(){1})()','(fn*(){yield 1})()','ui_from_openui("root=Text(\"x\")")']
        for expression in expressions:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):
                self.evaluate('S.create({a:{nested:['+expression+']}})')
        self.assertEqual(self.evaluate('S.create({a:{"$handle":"Task","kind":"Function"}})')['states']['a'],{'$handle':'Task','kind':'Function'})

    def test_nonfinite_data_and_weights_are_rejected(self):
        for value in (float('nan'),float('inf'),float('-inf')):
            with self.subTest(value=value),self.assertRaises(LanguageError):self.evaluate('S.create({a:input})',{'input':value})

    def test_worker_map_and_hooks_are_all_validated_before_preparation(self):
        for workers in ('{a:fn(c){1}}','{a:fn(c){1},b:42}','{a:fn(c){1},other:fn(c){1}}'):
            with self.subTest(workers=workers):
                self.assertEqual(self.evaluate('var calls=0;fn p(c){calls=calls+1;c};try{await S.step(S.create({a:1,b:2}),'+workers+',prepare=p)}catch e{null};calls'),0)
        self.assertEqual(self.evaluate('var calls=0;fn f(c){calls=calls+1;c.state};try{await S.step(S.create({a:1}),{a:f},rewire=42)}catch e{null};calls'),0)

    def test_all_prepare_callbacks_finish_before_any_worker(self):
        self.assertEqual(self.evaluate('''var events=[];
          fn p(c){events=append(events,"prepare:"+c.id);c}
          fn w(c){events=append(events,"worker:"+c.id);c.state}
          await S.step(S.create({a:1,b:2}),{a:w,b:w},prepare=p);events'''),['prepare:a','prepare:b','worker:a','worker:b'])
        self.assertEqual(self.evaluate('''var calls=0;fn w(c){calls=calls+1;0}
          fn p(c){if c.id=="b"{throw "invalid"}else{c}}
          try{await S.step(S.create({a:1,b:2}),{a:w,b:w},prepare=p)}catch e{null};calls'''),0)

    def test_prepare_is_a_real_higher_order_function_without_special_names(self):
        self.assertEqual(self.evaluate('''fn adapt(c){[c.state,c.tick,c.id]}
          await S.step(S.create({anything:40}),{anything:fn(x){x[0]+2}},prepare=adapt)''')['states'],{'anything':42})

    def test_async_workers_are_cold_and_awaited_exactly_once(self):
        self.assertEqual(self.evaluate('''var calls=0;async fn w(c){calls=calls+1;c.state+1}
          let pending=S.step(S.create({a:1}),{a:w});[calls,type_of(pending),(await pending).states,calls]'''),[0,'Task',{'a':2},1])
        self.assertEqual(self.evaluate('''var innerCalls=0;async fn inner(){innerCalls=innerCalls+1;1}
          async fn outer(c){inner()};let error=try{await S.step(S.create({a:0}),{a:outer})}catch e{e.code};
          [error,innerCalls]'''),['user_throw',0])
        with self.assertRaises(LanguageError):self.evaluate('fn* w(c){yield 1};await S.step(S.create({a:0}),{a:w})')

    def test_same_group_shared_task_executes_once(self):
        self.assertEqual(self.evaluate('''var calls=0;async fn shared(){calls=calls+1;42};let cold=shared();
          let result=await S.step(S.create({a:1,b:2}),{a:fn(c){cold},b:fn(c){cold}});
          [result.states,calls,task_info(cold).state]'''),[{'a':42,'b':42},1,'completed'])

    def test_running_borrowed_task_is_rejected_and_not_cancelled(self):
        value=self.evaluate('''async fn outside(){await sleep(0.02);42};let external=spawn(outside());
          let error=try{await S.step(S.create({a:0}),{a:fn(c){external}})}catch e{e.code};
          [error,task_info(external).cancellationRequested,await external]''')
        self.assertEqual(value,['task_group_error',False,42])

    def test_failure_drains_adopted_workers_and_runs_finally(self):
        ev=Evaluator(step_limit=150000)
        value=ev.run(IMPORT+'''var events=[];async fn slow(c){await sleep(0.1);99}
          async fn bad(c){try{await sleep(0.01);throw "first"}finally{events=append(events,"clean")}};let old=S.create({slow:1,bad:2});
          let error=try{await S.step(old,{slow:slow,bad:bad})}catch e{e.value};[error,events,old]''')
        self.assertEqual(value[:2],['first',['clean']]);self.assertEqual(value[2]['tick'],0)
        self.assertTrue(all(g.state=='closed' for g in ev.suspension.groups))

    def test_explicit_cancel_drains_worker_cleanup(self):
        entered=threading.Event();drained=threading.Event();cancelled=[]
        def effect(kind,payload,cancel):
            entered.set();cancelled.append(cancel.wait(2));time.sleep(.02);drained.set();return 1
        ev=Evaluator(step_limit=150000,tool_handler=lambda *args:None,effect_handler=effect)
        with self.assertRaises(TaskCancelled):
            ev.run(IMPORT+'''async fn worker(c){tool("block",0)}
              let pending=spawn(S.step(S.create({a:0}),{a:worker}));while !started(){await sleep(0.001)};cancel(pending);await pending''',
                   {'started':Native('started',entered.is_set,ev)})
        self.assertTrue(entered.is_set());self.assertTrue(drained.is_set());self.assertEqual(cancelled,[True])
        self.assertTrue(all(g.state=='closed' for g in ev.suspension.groups))
        self.assertTrue(all(t.state in TERMINAL for t in ev.suspension.tasks))

    def test_cancelled_operations_drain_before_the_caller_continues(self):
        # Observing before Evaluator.finish detects an otherwise hidden leak:
        # end-of-run global cancellation must not substitute for local ownership.
        for operation in ('S.step(s,{a:worker})','S.run(s,{a:worker},1)',
                          'anext(S.frames(s,{a:worker},1))'):
            with self.subTest(operation=operation):
                started=threading.Event();ended=threading.Event();cancelled=[]
                def effect(kind,payload,cancel):
                    started.set();cancelled.append(cancel.wait(2));time.sleep(.02);ended.set();return 1
                ev=Evaluator(step_limit=150000,tool_handler=lambda *args:None,effect_handler=effect)
                value=ev.run(IMPORT+'''async fn worker(c){tool("block",0)}let s=S.create({a:0});
                  let operation=spawn('''+operation+''');
                  while !started(){await sleep(0.001)};
                  cancel(operation);await sleep(0.08);observed()''',
                  {'started':Native('started',started.is_set,ev),'observed':Native('observed',ended.is_set,ev)})
                self.assertTrue(value);self.assertEqual(cancelled,[True])
                self.assertTrue(all(g.state=='closed' for g in ev.suspension.groups))

    def test_frames_commit_measure_stop_then_yield_and_close(self):
        value=self.evaluate('''var events=[];fn w(c){events=append(events,"worker");c.state+1}
          fn m(s){events=append(events,"measure");s.states.a}
          fn stop(f){events=append(events,"stop");f.snapshot.tick==2}
          let stream=S.frames(S.create({a:0}),{a:w},10,measure=m,stop=stop);
          let collected=await acollect(stream);[collected,events]''')
        self.assertEqual([f['metrics'] for f in value[0]],[1,2])
        self.assertEqual(value[1],['worker','measure','stop']*2)
        self.assertEqual(self.evaluate('''var calls=0;fn w(c){calls=calls+1;c.state+1};
          let stream=S.frames(S.create({a:0}),{a:w},10);await anext(stream);await aclose(stream);calls'''),1)

    def test_zero_rounds_validates_without_invoking_hooks(self):
        value=self.evaluate('''var calls=0;fn f(x){calls=calls+1;throw "called"};
          [await S.run(S.create({a:1}),{a:f},0,prepare=f,rewire=f,measure=f,stop=f),calls]''')
        self.assertEqual(value,[{'snapshot':{'states':{'a':1},'edges':[],'tick':0},'metrics':None},0])
        for args in ('{a:42},0','{a:fn(c){1}},-1','{a:fn(c){1}},10001','{a:fn(c){1}},true'):
            with self.subTest(args=args),self.assertRaises(LanguageError):self.evaluate('await S.run(S.create({a:1}),'+args+')')

    def test_stop_is_strict_bool_and_hooks_do_not_implicitly_await(self):
        with self.assertRaises(LanguageError):self.evaluate('await S.run(S.create({a:0}),{a:fn(c){1}},1,stop=fn(f){1})')
        for option,callback in [('prepare','async fn(c){c}'),('rewire','async fn(a,b){a.edges}'),('measure','async fn(s){1}'),('stop','async fn(f){true}')]:
            with self.subTest(option=option),self.assertRaises(LanguageError):
                self.evaluate('await S.run(S.create({a:0}),{a:fn(c){1}},1,'+option+'='+callback+')')

    def test_rewire_occurs_after_barrier_and_changes_next_round_only(self):
        value=self.evaluate('''fn worker(c){c.state+sum(map(fn(i){i.edge.weight*i.state},c.incoming))}
          fn rewire(old,next){[{source:"a",target:"b",weight:-1}]}
          await S.run(S.create({a:1,b:2}),{a:worker,b:worker},2,rewire=rewire)''')
        self.assertEqual(value['snapshot']['states'],{'a':1,'b':1});self.assertEqual(value['snapshot']['tick'],2)
        self.assertEqual(self.evaluate('''var effects=0;fn w(c){effects=effects+1;c.state+1};let old=S.create({a:0});
          try{await S.step(old,{a:w},rewire=fn(o,n){[{source:"new",target:"a",weight:1}]})}catch e{null};[old.tick,old.states.a,effects]'''),[0,0,1])

    def test_native_mutation_cannot_modify_inputs_or_future_frames(self):
        ev=Evaluator(step_limit=150000)
        def prepare(ctx):
            ctx['state']['n']+=1
            if ctx['incoming']:ctx['incoming'][0]['state']['n']=999
            return ctx
        def mutate(frame):frame['snapshot']['states']['a']['n']=999;return None
        value=ev.run(IMPORT+'''let initial=S.create({a:{n:1},b:{n:2}},[{source:"a",target:"b",weight:1}]);
          let stream=S.frames(initial,{a:fn(c){c.state},b:fn(c){c.state}},2,prepare=prepare);
          let first=await anext(stream);mutate(first.value);let second=await anext(stream);await aclose(stream);
          [initial,second.value]''',{'prepare':Native('prepare',prepare,ev),'mutate':Native('mutate',mutate,ev)})
        self.assertEqual(value[0]['states'],{'a':{'n':1},'b':{'n':2}})
        self.assertEqual(value[1]['snapshot']['states'],{'a':{'n':3},'b':{'n':4}})

    def test_team_role_permissions_and_wide_task_guard_are_preserved(self):
        effects=[]
        source='''let t=team_from(policy);async fn wide(){tool("secret",42)};let borrowed=wide();
          let crew=team_bind(t,{worker:fn(c){borrowed}});let role=team_role(crew,"worker");
          let error=try{await S.step(S.create({a:0}),{a:role})}catch e{e.code};
          [error,task_info(borrowed).state,task_info(borrowed).cancellationRequested,await borrowed]'''
        value=self.evaluate(source,{'policy':definition()},tool_handler=lambda name,value:effects.append(name) or value)
        self.assertEqual(value,['capability_error','created',False,42]);self.assertEqual(effects,['secret'])

    def test_real_framework_agent_worker_and_team_role(self):
        registry=Registry();seen=[]
        registry.register_provider(ProviderSpec('local.worker',lambda req,ctx:seen.append(req.input) or Final(req.input['state']+1)))
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        source=IMPORT+'agent a{provider:"local.worker",tools:[]};let p=team_from('+json.dumps(definition())+');let crew=team_bind(p,{worker:a});await S.step(S.create({robot:41}),{robot:team_role(crew,"worker")})'
        app.create_project('swarm',source=source,capabilities=[])
        self.assertEqual(app.start('swarm',1,None,mode='source').result(10)['states'],{'robot':42})
        self.assertEqual(seen[0],{'id':'robot','state':41,'incoming':[],'tick':0})

    def test_logistic_map_matches_independent_numerical_calculation(self):
        value=self.evaluate('''fn logistic(c){3.9*c.state*(1-c.state)}
          await S.run(S.create({base:0.2,perturbed:0.200001}),{base:logistic,perturbed:logistic},6,
            measure=fn(s){abs(s.states.base-s.states.perturbed)})''')
        expected=[0.2,0.200001]
        for _ in range(6):expected=[3.9*x*(1-x) for x in expected]
        for key,x in zip(('base','perturbed'),expected):self.assertAlmostEqual(value['snapshot']['states'][key],x,places=14)
        self.assertAlmostEqual(value['metrics'],abs(expected[0]-expected[1]),places=14)


if __name__=='__main__':unittest.main()
