"""Real modes share one language/scheduler/permission path, including recording."""
import json
import threading
import unittest
from dataclasses import FrozenInstanceError

from chaos import Framework, Settings
from chaos.extensions import Registry, Final, ToolCall
from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable
from chaos.modes import Plan, Goal, Skill, make, validate_definition, skill_source_definition


class AgentModeTests(unittest.TestCase):
    def evaluate(self,source,**kwargs):return Evaluator(**kwargs).run(source)
    def start(self,app,project,value=None):return app.start(project['id'],project['version'],value,mode='source',auto_optimize=False)

    def test_plan_and_goal_are_cold_and_first_class(self):
        source='var calls=0;let p:Plan=plan([{id:"x",call:"work"}]);let bindings={work:fn(ctx){calls=calls+1;ctx.input+1}};let cold=p(41,bindings);let before=calls;let result=await cold;[before,result,calls,type_of(p)]'
        self.assertEqual(self.evaluate(source),[0,{'x':42},1,'Plan'])
        source='let p=plan([{id:"x",call:"f"}]);let runner=partial(run_plan,p,{f:fn(ctx){ctx.input}});let wrapped=compose(fn(t){t},runner);(await apply(wrapped,[42])).x'
        self.assertEqual(self.evaluate(source),42)
        self.assertEqual(self.evaluate('let g=goal("done","p","v");let task=g(null,{});[type_of(g),type_of(task)]'),['Goal','Task'])

    def test_definition_is_copied_and_immutable(self):
        evaluator=Evaluator();source=[{'id':'x','call':'f'}]
        value=make({'kind':'plan','name':'copy','steps':source},evaluator);source[0]['call']='other'
        self.assertEqual(value.definition['steps'][0]['call'],'f')
        changed=value.definition;changed['name']='changed';self.assertEqual(value.definition['name'],'copy')
        with self.assertRaises(FrozenInstanceError):value.definition_json='{}'
        self.assertIn('$mode',serializable(value));self.assertNotIn('owner',serializable(value))
        self.assertEqual(self.evaluate('let p=plan([{id:"x",call:"f"}]);type_of(parse_json(json_stringify(p)))'),'Record')

    def test_validation_happens_before_any_action(self):
        cases=['plan([])','plan([{id:"x",call:"f"},{id:"x",call:"g"}])','plan([{id:"x",call:"f",needs:["missing"]}])','plan([{id:"x",call:"f",needs:["x"]}])','plan([{id:"x",call:"f",extra:0}])','goal("x","p","v",true)','goal("x","p","v",101)']
        for source in cases:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.evaluate(source)
        source='var calls=0;let p=plan([{id:"a",call:"ok"},{id:"b",call:"missing"}]);try{await run_plan(p,{ok:fn(ctx){calls=calls+1}})}catch e{null};calls'
        self.assertEqual(self.evaluate(source),0)
        with self.assertRaises(ResourceLimit):Evaluator().run('plan([{id:"x",call:"f"}],input)',{'input':'x'*250001})

    def test_dependencies_are_topological_sequential_and_filtered(self):
        source='var seen=[];let p=plan([{id:"end",call:"end",needs:["a"]},{id:"b",call:"b"},{id:"a",call:"a"}]);let out=await run_plan(p,{a:fn(ctx){seen=append(seen,"a");40},b:fn(ctx){seen=append(seen,"b");0},end:fn(ctx){seen=append(seen,"end");[keys(ctx.results),ctx.results.a+2]}},null);[out,seen]'
        self.assertEqual(self.evaluate(source),[{'b':0,'a':40,'end':[['a'],42]},['b','a','end']])

    def test_async_callbacks_are_awaited_once_streams_rejected(self):
        self.assertEqual(self.evaluate('let p=plan([{id:"x",call:"f"}]);await run_plan(p,{f:async fn(ctx){42}})'),{'x':42})
        for body in ['fn*(ctx){yield 42}','async fn*(ctx){yield 42}','async fn(ctx){async fn f(){42};f()}']:
            with self.subTest(body=body),self.assertRaises(LanguageError):self.evaluate('await run_plan(plan([{id:"x",call:"f"}]),{f:'+body+'})')

    def test_plan_failure_does_not_execute_later_steps(self):
        self.assertEqual(self.evaluate('var seen=[];let p=plan([{id:"a",call:"bad"},{id:"b",call:"good"}]);try{await run_plan(p,{bad:fn(ctx){throw "no"},good:fn(ctx){seen=append(seen,1)}})}catch e{null};seen'),[])

    def test_modes_and_callbacks_cannot_cross_owner(self):
        other=Evaluator();foreign=other.run('plan([{id:"x",call:"f"}])')
        with self.assertRaises(LanguageError):Evaluator().run('await run_plan(input,{f:fn(ctx){42}})',{'input':foreign})
        fn=Evaluator().run('fn(ctx){42}')
        with self.assertRaises(LanguageError):Evaluator().run('await run_plan(plan([{id:"x",call:"f"}]),{f:input})',{'input':fn})

    def test_goal_requires_verification_and_keeps_state(self):
        source='let p=plan([{id:"x",call:"step"}]);let g=goal("reach3","planner","verify",3);await pursue(g,{planner:fn(ctx){p},step:fn(ctx){ctx.input+1},verify:fn(ctx){{done:ctx.results.x==3,state:ctx.results.x,evidence:[ctx.iteration,ctx.results.x]}}},0)'
        result=self.evaluate(source);self.assertEqual(result,{'done':True,'state':3,'evidence':[3,3],'iterations':3,'status':'achieved'})
        result=self.evaluate(source.replace('ctx.results.x==3','false'))
        self.assertFalse(result['done']);self.assertEqual(result['status'],'exhausted');self.assertEqual(result['iterations'],3)

    def test_goal_rejects_done_text_invalid_bool_and_plain_plan_record(self):
        base='let p=plan([{id:"x",call:"step"}]);await pursue(goal("x","planner","verify"),{planner:fn(ctx){p},step:fn(ctx){"DONE"},verify:fn(ctx){BODY}})'
        for body in ['"DONE"','{done:1,state:null,evidence:null}','{done:true,state:fn(x){x},evidence:null}','{done:true,state:null}']:
            with self.subTest(body=body),self.assertRaises(LanguageError):self.evaluate(base.replace('BODY',body))
        with self.assertRaises(LanguageError):self.evaluate(base.replace('fn(ctx){p}','fn(ctx){mode_info(p)}').replace('BODY','{done:true,state:null,evidence:null}'))

    def test_mode_host_operations_are_cold_and_pure_context_refuses(self):
        seen=[]
        evaluator=Evaluator(mode_handler=lambda op,payload:seen.append(op))
        evaluator.run('let p=plan([{id:"x",call:"f"}]);save_mode(p,"saved");42');self.assertEqual(seen,[])
        with self.assertRaises(LanguageError):self.evaluate('await save_mode(plan([{id:"x",call:"f"}]),"saved")')

    def test_trace_records_mode_progress_without_value_dumps(self):
        events=[];self.evaluate('await run_plan(plan([{id:"x",call:"f"}]),{f:fn(ctx){42}})',trace_handler=events.append)
        names=[e['event'] for e in events];self.assertIn('mode.plan.started',names);self.assertIn('mode.plan.step.started',names);self.assertIn('mode.plan.step.completed',names);self.assertIn('mode.plan.completed',names)
        self.assertFalse(any('output' in event for event in events if event['event'].startswith('mode.')))

    def test_artifact_save_load_and_terminal_goal_do_not_repeat(self):
        source='let p=plan([{id:"x",call:"step"}]);try{await save_mode(p,"saved")}catch e{null};let loaded=await load_mode("saved");let g=goal("reach42","planner","verify",3);let result=await pursue(g,{planner:fn(ctx){loaded},step:fn(ctx){ctx.input+1},verify:fn(ctx){{done:ctx.results.x==42,state:ctx.results.x,evidence:ctx.results}}},40,key="target");[result,mode_info(loaded)]'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source=source);first=self.start(app,project).result(5)
            second=self.start(app,project).result(5);self.assertEqual(first,second);self.assertEqual(first[0]['iterations'],2)
            self.assertEqual(app.mode(project['id'],'target')['checkpoint']['phase'],'done')

    def test_pending_failure_requires_reconciliation_and_counter_survives(self):
        source='let p=plan([{id:"x",call:"step"}]);await pursue(goal("x","planner","verify",3),{planner:fn(ctx){p},step:fn(ctx){if ctx.input==0{throw "interrupted"}else{42}},verify:fn(ctx){{done:true,state:42,evidence:ctx.results}}},0,key="target")'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source=source)
            self.assertEqual(self.start(app,project).wait(5)['status'],'failed')
            saved=app.mode(project['id'],'target');self.assertEqual(saved['status'],'needs_reconciliation');self.assertEqual(saved['checkpoint']['iteration'],1)
            self.assertEqual(self.start(app,project).wait(5)['status'],'failed')
            app.reconcile_goal(project['id'],'target',saved['revision'],1,evidence={'reviewed':True})
            result=self.start(app,project).result(5);self.assertTrue(result['done']);self.assertEqual(result['iterations'],2)

    def test_record_and_parameterized_replay_are_real_tools(self):
        source='let r=await record_skill(fn(x){tool("text.normalize",x)}," first ",[{name:"text",step:0,path:[]}]);let out=await r.skill({text:" second "});[r.output,out,mode_info(r.skill),skill_source(r.skill)]'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source=source);run=self.start(app,project);result=run.result(5)
            self.assertEqual(result[:2],['first',['second']]);self.assertEqual(result[2]['actions'][0]['input'],' first ')
            calls=[e for e in app.events(project['id'],run_id=run.id)['items'] if e['type']=='tool.completed'];self.assertEqual(len(calls),2)
            exported=result[3];ev=Evaluator(tool_handler=lambda n,v:v.strip());self.assertEqual(ev.run('import "skill.chaos" as S;S.recorded({text:" third "})',files={'skill.chaos':exported}),['third'])
            with self.assertRaises(LanguageError):Evaluator(tool_handler=lambda n,v:v).run('import "skill.chaos" as S;S.recorded({})',files={'skill.chaos':exported})

    def test_skill_paths_are_explicit_and_source_cannot_inject(self):
        definition={'kind':'skill','actions':[{'tool':'fixture.echo','input':{'one':'same','two':'same','items':[0]}}],'parameters':[{'name':'value','step':0,'path':['one']}]}
        source=skill_source_definition(definition,'safe')
        result=Evaluator(tool_handler=lambda n,v:v).run('import "s.chaos" as S;S.safe({value:"changed"})',files={'s.chaos':source})
        self.assertEqual(result,[{'one':'changed','two':'same','items':[0]}])
        for name in ['x);tool("bad",0)','tool','get','fn','x.y']:
            with self.subTest(name=name),self.assertRaises(LanguageError):skill_source_definition(definition,name)
        for path in [['missing'],['items',True],['items',-1]]:
            bad={**definition,'parameters':[{'name':'v','step':0,'path':path}]}
            with self.assertRaises(LanguageError):validate_definition(bad)
        with self.assertRaises(LanguageError):validate_definition({**definition,'parameters':[{'name':'a','step':0,'path':[]},{'name':'b','step':0,'path':['one']}]})

    def test_agent_internal_tool_is_recorded_once(self):
        registry=Registry()
        registry.provider('fixture')(lambda request,ctx:ToolCall('text.normalize',' recorded ') if not request.history else Final('complete'))
        source='agent worker{provider:"fixture",tools:["text.normalize"]};let r=await record_skill(worker,null);[r.output,mode_info(r.skill),await replay_skill(r.skill)]'
        with Framework(':memory:',settings=Settings(':memory:'),registry=registry) as app:
            project=app.create_project('mode',source=source);result=self.start(app,project).result(5)
            self.assertEqual(result[0],'complete');self.assertEqual(len(result[1]['actions']),1);self.assertEqual(result[2],['recorded'])

    def test_recording_inherits_spawned_tasks_and_seals_after_join(self):
        source='async fn child(){tool("text.normalize"," child ")}let r=await record_skill(fn(x){spawn(child());42});[r.output,mode_info(r.skill)]'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source=source);result=self.start(app,project).result(5)
            self.assertEqual(result[0],42);self.assertEqual(result[1]['actions'],[{'tool':'text.normalize','input':' child '}])

    def test_unrelated_task_never_enters_recording(self):
        registry=Registry();barrier=threading.Barrier(2)
        registry.tool('fixture.echo')(lambda value,ctx:(barrier.wait(timeout=3),value)[1])
        source='async fn outside(){tool("fixture.echo","outside")}spawn(outside());let r=await record_skill(fn(x){tool("fixture.echo","inside")});mode_info(r.skill)'
        with Framework(':memory:',settings=Settings(':memory:'),registry=registry) as app:
            project=app.create_project('mode',source=source,capabilities=['fixture.echo']);result=self.start(app,project).result(5)
            self.assertEqual(result['actions'],[{'tool':'fixture.echo','input':'inside'}])

    def test_failed_and_caught_failed_recordings_cannot_seal(self):
        for action in ['fn(x){tool("text.normalize"," first ");throw "fail"}','fn(x){try{tool("data.select",{record:{},key:"missing"})}catch e{null};tool("text.normalize","after")}']:
            with Framework(':memory:',settings=Settings(':memory:')) as app:
                project=app.create_project('mode',source='await record_skill('+action+')');run=self.start(app,project);self.assertEqual(run.wait(5)['status'],'failed')
                artifacts=app.modes(project['id']);rows=artifacts['items'] if isinstance(artifacts,dict) else artifacts
                self.assertTrue(rows);self.assertTrue(all(row['status']!='ready' for row in rows))

    def test_replay_rechecks_permissions(self):
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source='let s=await load_mode("skill");await replay_skill(s)',capabilities=[])
            app.save_mode(project['id'],'skill',{'kind':'skill','actions':[{'tool':'text.normalize','input':' x '}],'parameters':[]})
            self.assertEqual(self.start(app,project).wait(5)['status'],'failed')

    def test_recording_cannot_adopt_foreign_scope_task_or_nested_scope(self):
        for source in ['async fn child(){tool("text.normalize","x")}let old=child();await record_skill(fn(x){old})','await record_skill(fn(x){record_skill(fn(y){tool("text.normalize","x")})})']:
            with Framework(':memory:',settings=Settings(':memory:')) as app:
                project=app.create_project('mode',source=source);self.assertEqual(self.start(app,project).wait(5)['status'],'failed')

    def test_cancelled_recording_drains_worker_and_never_publishes_skill(self):
        registry=Registry();started=threading.Event();finished=threading.Event()
        def wait(value,ctx):
            started.set()
            try:
                while True:ctx.checkpoint();finished.wait(.005)
            finally:finished.set()
        registry.tool('fixture.wait')(wait)
        with Framework(':memory:',settings=Settings(':memory:'),registry=registry) as app:
            project=app.create_project('mode',source='await record_skill(fn(x){tool("fixture.wait",null)})',capabilities=['fixture.wait']);run=self.start(app,project)
            self.assertTrue(started.wait(2));run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled');self.assertTrue(finished.is_set())
            rows=app.modes(project['id']);rows=rows['items'] if isinstance(rows,dict) else rows
            self.assertTrue(all(row['status']!='ready' for row in rows))
    def test_recording_discards_cold_children_without_running_them(self):
        source='var called=0;async fn unused(){called=called+1;tool("text.normalize","unused")}let r=await record_skill(fn(x){unused();tool("text.normalize","real")});[called,mode_info(r.skill)]'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source=source);result=self.start(app,project).result(5)
            self.assertEqual(result[0],0);self.assertEqual(result[1]['actions'],[{'tool':'text.normalize','input':'real'}])

    def test_started_failed_child_prevents_recording_seal(self):
        source='async fn child(){throw "child failed"}await record_skill(fn(x){tool("text.normalize","real");spawn(child());42})'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source=source);self.assertEqual(self.start(app,project).wait(5)['status'],'failed')
            self.assertTrue(all(row['status']!='ready' for row in app.modes(project['id'])['items']))

    def test_concurrent_recorded_tools_rejected_before_second_dispatch(self):
        registry=Registry();calls=[];finished=threading.Event()
        @registry.tool('fixture.wait')
        def wait(value,ctx):
            calls.append(value)
            try:
                finished.wait(.1);ctx.checkpoint();return value
            finally:finished.set()
        source='async fn action(x){try{await gather([async fn(){tool("fixture.wait",1)}(),async fn(){tool("fixture.wait",2)}()])}catch e{null};42}await record_skill(action)'
        with Framework(':memory:',settings=Settings(':memory:'),registry=registry) as app:
            project=app.create_project('mode',source=source,capabilities=['fixture.wait']);run=self.start(app,project)
            self.assertEqual(run.wait(5)['status'],'failed');self.assertEqual(len(calls),1);self.assertTrue(finished.is_set())
            self.assertTrue(all(row['status']!='ready' for row in app.modes(project['id'])['items']))

    def test_skill_parameter_paths_preserve_lists_and_reject_extra_values(self):
        definition={'kind':'skill','actions':[{'tool':'fixture.echo','input':{'items':[1,2],'literal':'unchanged'}}],'parameters':[{'name':'n','step':0,'path':['items',1]}]}
        from chaos.modes import substitute
        self.assertEqual(substitute(definition,{'n':42})[0]['input'],{'items':[1,42],'literal':'unchanged'})
        for values in ({},{'n':42,'extra':1}):
            with self.assertRaises(LanguageError):substitute(definition,values)

    def test_recording_internal_await_spawn_gather_and_cancel_reject_foreign_scope(self):
        for operation in ['await old','await gather([old])','spawn(old)','cancel(old)']:
            with self.subTest(operation=operation),Framework(':memory:',settings=Settings(':memory:')) as app:
                source='async fn outside(){tool("text.normalize"," outside ")}let old=outside();async fn action(x){'+operation+';tool("text.normalize"," inside ")}await record_skill(action)'
                project=app.create_project('mode',source=source);run=self.start(app,project)
                self.assertEqual(run.wait(5)['status'],'failed')
                events=app.events(project['id'],run_id=run.id)['items']
                self.assertEqual([e for e in events if e['type']=='tool.started'],[])
                self.assertTrue(all(row['status']!='ready' for row in app.modes(project['id'])['items']))

    def test_recording_internal_await_same_scope_is_recorded(self):
        source='async fn child(){tool("text.normalize"," child ")}async fn action(x){await child();tool("text.normalize"," action ")}let r=await record_skill(action);mode_info(r.skill)'
        with Framework(':memory:',settings=Settings(':memory:')) as app:
            project=app.create_project('mode',source=source);result=self.start(app,project).result(5)
            self.assertEqual(result['actions'],[{'tool':'text.normalize','input':' child '},{'tool':'text.normalize','input':' action '}])
    def test_recording_closes_all_stream_flavors_before_sealing(self):
        cases=[
            ('fn* stream(){try{yield tool("text.normalize","first")}finally{tool("text.normalize","cleanup")}}','let s=stream();next(s)'),
            ('async fn* stream(){try{yield tool("text.normalize","first")}finally{tool("text.normalize","cleanup")}}','let s=stream();await anext(s)'),
            ('class Stream{fn __iter__(self){self}fn __next__(self){tool("text.normalize","first");{done:false,value:1}}fn __close__(self){tool("text.normalize","cleanup");null}}','let s=iter(Stream());next(s)'),
            ('class Stream{fn __aiter__(self){self}async fn __anext__(self){tool("text.normalize","first");{done:false,value:1}}async fn __aclose__(self){tool("text.normalize","cleanup");null}}','let s=aiter(Stream());await anext(s)')]
        for declaration,consume in cases:
            with self.subTest(declaration=declaration),Framework(':memory:',settings=Settings(':memory:')) as app:
                source=declaration+';let r=await record_skill(async fn(x){'+consume+';42});mode_info(r.skill)'
                project=app.create_project('mode',source=source);run=self.start(app,project);result=run.result(5)
                self.assertEqual([a['input'] for a in result['actions']],['first','cleanup'])
                events=app.events(project['id'],run_id=run.id)['items'];self.assertEqual(len([e for e in events if e['type']=='tool.completed']),2)

    def test_recording_foreign_stream_and_raw_alias_refused(self):
        cases=[('fn* g(){yield tool("text.normalize","outside")}let old=g()','next(old)'),('async fn* g(){yield tool("text.normalize","outside")}let old=g()','await anext(old)'),('class C{fn __iter__(self){self}fn __next__(self){tool("text.normalize","outside");{done:true,value:null}}}let raw=C();let old=iter(raw)','next(raw)')]
        for declaration,consume in cases:
            with self.subTest(consume=consume),Framework(':memory:',settings=Settings(':memory:')) as app:
                project=app.create_project('mode',source=declaration+';await record_skill(async fn(x){'+consume+'})');run=self.start(app,project)
                self.assertEqual(run.wait(5)['status'],'failed');self.assertEqual([e for e in app.events(project['id'],run_id=run.id)['items'] if e['type']=='tool.started'],[])
                self.assertTrue(all(a['status']!='ready' for a in app.modes(project['id'])['items']))

    def test_recording_stream_cleanup_failure_never_seals(self):
        for flavor,consume in [('fn*','next(s)'),('async fn*','await anext(s)')]:
            with self.subTest(flavor=flavor),Framework(':memory:',settings=Settings(':memory:')) as app:
                source=flavor+' stream(){try{yield tool("text.normalize","first")}finally{throw "close failed"}}await record_skill(async fn(x){let s=stream();'+consume+';42})'
                project=app.create_project('mode',source=source);self.assertEqual(self.start(app,project).wait(5)['status'],'failed')
                self.assertTrue(all(a['status']!='ready' for a in app.modes(project['id'])['items']))

    def test_outside_task_cannot_consume_a_recording_stream(self):
        import time
        registry=Registry();gate=threading.Event()
        @registry.tool('fixture.gate')
        def wait(value,ctx):
            if value=='outside':
                self.assertTrue(gate.wait(2));ctx.checkpoint()
            else:gate.set();time.sleep(.03);ctx.checkpoint()
            return value
        source='var shared=null;fn* stream(){yield tool("text.normalize","escaped")}async fn outside(){tool("fixture.gate","outside");try{next(shared)}catch e{"blocked"}}let other=spawn(outside());let r=await record_skill(fn(x){shared=stream();tool("fixture.gate","inside");42});[await other,mode_info(r.skill)]'
        with Framework(':memory:',settings=Settings(':memory:'),registry=registry) as app:
            project=app.create_project('mode',source=source,capabilities=['fixture.gate','text.normalize']);result=self.start(app,project).result(5)
            self.assertEqual(result[0],'blocked');self.assertEqual(result[1]['actions'],[{'tool':'fixture.gate','input':'inside'}])
