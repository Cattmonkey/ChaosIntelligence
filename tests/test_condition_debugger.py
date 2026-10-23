"""Interactive recovery on the original logical interpreter extent."""
import json
import threading
import time
import unittest
from chaos import Framework, Settings
from chaos.debugger import options, restart_arguments
from chaos.language import Evaluator, LanguageError, ResourceLimit, Native
from chaos.store import Conflict


class ConditionDebuggerLanguage(unittest.TestCase):
    def evaluator(self, selector=0, **options):
        snapshots=[]
        def hook(metadata,node,env,frames):
            snapshots.append(metadata)
            item=metadata['restarts'][selector] if metadata['restarts'] else None
            return 0, None if item is None else {'restartId':item['id'],'args':[42],'kwargs':{}}
        evaluator=Evaluator(condition_debug_hook=hook,step_limit=200000,**options)
        return evaluator,snapshots

    def test_explicit_without_session_is_original_condition_error(self):
        self.assertEqual(Evaluator().run('try{invoke_debugger(condition("missing","need",{n:42}))}catch e{[e.code,e.value.kind,e.value.["data"].n]}'),['condition_error','missing',42])

    def test_inline_null_is_recovery_and_execution_continues_once(self):
        evaluator,snapshots=self.evaluator()
        source='var calls=0;let result=with restart_bind([{name:"use",function:fn(x){calls=calls+1;null}}]) as r {invoke_debugger(condition("x"))};[result,calls]'
        self.assertEqual(evaluator.run(source),[None,1]);self.assertEqual(len(snapshots),1)

    def test_case_duplicate_anonymous_identity_unwinds_original_cleanup(self):
        evaluator,snapshots=self.evaluator(2)
        source='var seen=[];let result=restart_case(fn(){try{invoke_debugger(condition("x"));seen=append(seen,"after")}finally{seen=append(seen,"cleanup")}},[{name:"same",function:fn(x){0}},{name:"same",function:fn(x){1}},{name:null,function:fn(x){seen=append(seen,"restart");x}}]);[result,seen]'
        self.assertEqual(evaluator.run(source),[42,['cleanup','restart']])
        self.assertEqual([x['name'] for x in snapshots[0]['restarts']],['same','same',None])
        self.assertEqual(len({x['id'] for x in snapshots[0]['restarts']}),3)

    def test_case_null_is_recovery(self):
        evaluator,_=self.evaluator()
        self.assertIsNone(evaluator.run('restart_case(fn(){invoke_debugger(condition("x"))},[{name:"use",function:fn(x){null}}])'))

    def test_condition_test_filters_once_without_named_recomputation(self):
        evaluator,snapshots=self.evaluator()
        source='var tests=0;let result=with restart_bind([{name:"use",test:fn(c){tests=tests+1;c.kind=="x"},function:fn(x){x}},{name:"hidden",test:fn(c){tests=tests+1;false},function:fn(x){0}}]) as r {invoke_debugger(condition("x"))};[result,tests]'
        self.assertEqual(evaluator.run(source),[42,2]);self.assertEqual([x['name'] for x in snapshots[0]['restarts']],['use'])

    def test_default_error_does_not_run_tests_or_hook(self):
        evaluator,snapshots=self.evaluator()
        result=evaluator.run('var tests=0;with restart_bind([{name:"use",test:fn(c){tests=tests+1;true},function:fn(x){x}}]) as r {try{error_condition(condition("x"))}catch e{null}};tests')
        self.assertEqual(result,0);self.assertEqual(snapshots,[])

    def test_opt_in_error_uses_handlers_then_debugger(self):
        evaluator,snapshots=self.evaluator(condition_pause=True)
        source='var seen=[];let value=with restart_bind([{name:"use",function:fn(x){x}}]) as r {with handler_bind([{predicate:fn(c){true},handler:fn(c){seen=append(seen,"declined")}}]) as h {error_condition(condition("x"))}};[value,seen]'
        self.assertEqual(evaluator.run(source),[42,['declined']]);self.assertEqual(len(snapshots),1)

    def test_existing_handler_transfer_bypasses_automatic_debugger(self):
        evaluator,snapshots=self.evaluator(condition_pause=True)
        source='restart_case(fn(){with handler_bind([{predicate:fn(c){true},handler:fn(c){invoke_restart("use",[42])}}]) as h {error_condition(condition("x"))}},[{name:"use",function:fn(x){x}}])'
        self.assertEqual(evaluator.run(source),42);self.assertEqual(snapshots,[])

    def test_child_task_does_not_inherit_parent_restart(self):
        evaluator,snapshots=self.evaluator(condition_pause=True)
        source='async fn child(){try{error_condition(condition("child"))}catch e{e.code}};with restart_bind([{name:"parent",function:fn(x){x}}]) as r {await child()}'
        self.assertEqual(evaluator.run(source),'condition_error');self.assertEqual(snapshots[0]['restarts'],[])

    def test_generator_own_holder_and_captured_environment(self):
        evaluator,snapshots=self.evaluator()
        source='fn* values(){with restart_bind([{name:"own",function:fn(x){x}}]) as r {yield invoke_debugger(condition("x"))}};let stream=values();let result=with restart_bind([{name:"parent",function:fn(x){0}}]) as r {next(stream)};close(stream);result.value'
        self.assertEqual(evaluator.run(source),42);self.assertEqual([x['name'] for x in snapshots[0]['restarts']],['own'])

    def test_async_restart_case_transparent_cleanup(self):
        evaluator,_=self.evaluator()
        source='var seen=[];let result=restart_case(async fn(){await sleep(0);try{invoke_debugger(condition("x"))}finally{seen=append(seen,"cleanup")}},[{name:"use",function:async fn(x){await sleep(0);seen=append(seen,"restart");x}}]);[result,seen]'
        self.assertEqual(evaluator.run(source),[42,['cleanup','restart']])

    def test_debug_discovery_nested_reentry_rejected_and_depth_restored(self):
        evaluator,_=self.evaluator()
        source='with restart_bind([{name:"use",test:fn(c){invoke_debugger(c);true},function:fn(x){x}}]) as r {invoke_debugger(condition("x"))}'
        with self.assertRaises(LanguageError) as caught:evaluator.run(source)
        self.assertEqual(caught.exception.code,'condition_protocol_error');self.assertEqual(evaluator.condition_debug_depth,0)
        self.assertTrue(all(scope.state=='closed' for scope in evaluator.suspension.condition_scopes))

    def test_restart_callback_can_open_new_independent_pause(self):
        evaluator,snapshots=self.evaluator()
        source='restart_case(fn(){invoke_debugger(condition("first"))},[{name:"outer",function:fn(x){with restart_bind([{name:"inner",function:fn(y){y}}]) as r {invoke_debugger(condition("second"))}}}])'
        self.assertEqual(evaluator.run(source),42);self.assertEqual([x['condition']['kind'] for x in snapshots],['first','second'])

    def test_limited_caller_cannot_gain_outer_restart_capability(self):
        evaluator,_=self.evaluator(tool_handler=lambda name,value:42)
        limited=Native('limited',lambda fn:evaluator.invoke(fn,[]),evaluator,capabilities=frozenset())
        source='try{restart_case(fn(){limited(fn(){invoke_debugger(condition("x"))})},[{name:"use",function:fn(x){tool("secret",null)}}])}catch e{e.code}'
        self.assertEqual(evaluator.run(source,{'limited':limited}),'capability_error')

    def test_unoffered_restart_id_is_rejected(self):
        evaluator=Evaluator(condition_debug_hook=lambda *unused:(0,{'restartId':'restarts-999/0','args':[],'kwargs':{}}))
        with self.assertRaises(LanguageError) as caught:evaluator.run('with restart_bind([{name:"use",function:fn(){42}}]) as r {invoke_debugger(condition("x"))}')
        self.assertEqual(caught.exception.code,'restart_error')

    def test_closed_restart_after_hook_is_not_revived(self):
        evaluator=None
        def hook(metadata,*unused):
            from chaos.conditions import release
            for scope in evaluator.suspension.condition_scopes:release(scope)
            return 0,{'restartId':metadata['restarts'][0]['id'],'args':[],'kwargs':{}}
        evaluator=Evaluator(condition_debug_hook=hook)
        with self.assertRaises(LanguageError) as caught:evaluator.run('with restart_bind([{name:"use",function:fn(){42}}]) as r {invoke_debugger(condition("x"))}')
        self.assertEqual(caught.exception.code,'restart_error')


class ConditionDebuggerSession(unittest.TestCase):
    def setUp(self):
        self.app=Framework(':memory:',settings=Settings(':memory:'));self.addCleanup(self.app.close)
    def start(self,source,**opts):
        p=self.app.create_project('recover-'+str(time.time_ns()),source=source)
        return self.app.start_debug(p['id'],p['version'],pause_on_entry=False,pause_on_condition=True,**opts)
    def paused(self,run,after=0):
        end=time.monotonic()+3
        while time.monotonic()<end:
            state=run.debugger()
            if state['status']=='paused' and state['pauseId']>after:return state
            if state['status'] not in ('running','paused'):self.fail(str(run.get()))
            time.sleep(.005)
        self.fail('Condition did not pause')
    def restore(self,run,state,args=None,kwargs=None,index=0):
        return run.debug_command('restart',pause_id=state['pauseId'],restart_id=state['conditionRecovery']['restarts'][index]['id'],args=[] if args is None else args,kwargs={} if kwargs is None else kwargs)

    def test_fixed_saved_run_exact_id_keyword_binder_and_inert_snapshot(self):
        source='var tests=0;let value=restart_case(fn(){error_condition(condition("x","need",{n:42}))},[{name:"same",function:fn(x){0}},{name:"same",test:fn(c){tests=tests+1;true},function:fn(x:Int,/,*,bonus:Int=2){x+bonus}}]);[value,tests]'
        run=self.start(source);state=self.paused(run)
        self.assertEqual(state['reason'],'condition');self.assertIn('restart',state['commands'])
        for _ in range(3):self.assertEqual(run.debugger()['conditionRecovery'],state['conditionRecovery'])
        copied=run.debugger();copied['conditionRecovery']['restarts'].clear()
        self.assertEqual(len(run.debugger()['conditionRecovery']['restarts']),2)
        self.restore(run,state,[40],{'bonus':2},1)
        self.assertEqual(run.result(3),[42,1]);self.assertIsNone(run.debugger()['conditionRecovery'])

    def test_invalid_json_and_stale_cas_preserve_pause(self):
        run=self.start('with restart_bind([{name:"use",function:fn(x){x}}]) as r {error_condition(condition("x"))}');state=self.paused(run)
        for args,kwargs in ((None,{}),([] ,[]),([float('nan')],{}),([object()],{}),([],{1:0}),(['x'*100001],{})):
            with self.subTest(args_type=type(args).__name__),self.assertRaises(ValueError):
                run.debug_command('restart',pause_id=state['pauseId'],restart_id=state['conditionRecovery']['restarts'][0]['id'],args=args,kwargs=kwargs)
            self.assertEqual(run.debugger()['status'],'paused');self.assertEqual(run.debugger()['pauseId'],state['pauseId'])
        for pause_id,restart_id in ((True,state['conditionRecovery']['restarts'][0]['id']),(state['pauseId']-1,state['conditionRecovery']['restarts'][0]['id']),(state['pauseId'],'use')):
            with self.assertRaises(Conflict):run.debug_command('restart',pause_id=pause_id,restart_id=restart_id,args=[42],kwargs={})
        self.restore(run,state,[42]);self.assertEqual(run.result(3),42)

    def test_continue_declines_and_keeps_original_condition_error(self):
        run=self.start('try{error_condition(condition("x","need"))}catch e{[e.code,e.value.kind]}');state=self.paused(run)
        self.assertEqual(state['conditionRecovery']['restarts'],[]);self.assertNotIn('restart',state['commands'])
        run.debug_command('continue',pause_id=state['pauseId']);self.assertEqual(run.result(3),['condition_error','x'])

    def test_cancel_and_timeout_never_invoke_clause(self):
        source='restart_case(fn(){error_condition(condition("x"))},[{name:"use",function:fn(){42}}])'
        run=self.start(source);state=self.paused(run);run.cancel()
        with self.assertRaises(Conflict):self.restore(run,state)
        self.assertEqual(run.wait(3)['status'],'cancelled')
        run=self.start(source,pause_timeout=.1);self.paused(run);self.assertEqual(run.wait(3)['status'],'failed');self.assertIn('暂停超时',run.get()['error'])

    def test_recovery_args_are_copied_before_waking(self):
        run=self.start('with restart_bind([{name:"use",function:fn(values){values[0]}}]) as r {error_condition(condition("x"))}');state=self.paused(run)
        values=[[42]];self.restore(run,state,values);values[0][0]=0
        self.assertEqual(run.result(3),42)

    def test_metadata_redacted_bounded_and_no_live_values(self):
        run=self.start('let password="secret-value";with restart_bind([{name:null,report:"use value",function:fn(){42}}]) as r {error_condition(condition("x",data={password:password}))}')
        state=self.paused(run);text=json.dumps(state)
        self.assertNotIn('secret-value',text);self.assertIsNone(state['conditionRecovery']['restarts'][0]['name'])
        def inspect(value):
            self.assertIn(type(value),(type(None),bool,int,float,str,list,dict))
            if type(value) is dict:
                for item in value.values():inspect(item)
            elif type(value) is list:
                for item in value:inspect(item)
        inspect(state);self.restore(run,state);self.assertEqual(run.result(3),42)

    def test_explicit_pause_with_automatic_condition_disabled(self):
        p=self.app.create_project('explicit',source='with restart_bind([{name:"use",function:fn(x){x}}]) as r {invoke_debugger(condition("x"))}')
        run=self.app.start_debug(p['id'],1,pause_on_entry=False);state=self.paused(run);self.restore(run,state,[42]);self.assertEqual(run.result(3),42)

    def test_bounded_recovery_listing_does_not_offer_hidden_ids(self):
        source='let bindings=map(fn(i){ {name:"same",function:fn(){i}} },range(128));with restart_bind([{name:"outer",function:fn(){999}}]) as outer {with restart_bind(bindings) as inner {error_condition(condition("x"))}}'
        run=self.start(source);state=self.paused(run);metadata=state['conditionRecovery']
        self.assertEqual(len(metadata['restarts']),128);self.assertEqual(metadata['total'],129);self.assertTrue(metadata['truncated'])
        self.assertNotIn('outer',[x['name'] for x in metadata['restarts']]);self.restore(run,state,index=127);self.assertEqual(run.result(3),127)

    def test_pause_options_strict_default_and_json_limits(self):
        self.assertFalse(options(True,{'main.chaos':'1'})['pauseOnCondition'])
        for value in (1,'yes',None):
            with self.assertRaises(ValueError):options({'pauseOnCondition':value},{'main.chaos':'1'})
        cyclic=[];cyclic.append(cyclic)
        with self.assertRaises(ValueError):restart_arguments(cyclic,{})

    def test_known_long_credential_is_redacted_before_preview_truncation(self):
        fake_key='test-redaction-boundary-'+('q'*700)
        self.app._engine.settings._data['providers']['redaction-test']={'apiKey':fake_key}
        raw=('a'*480)+fake_key
        key=('k'*150)+fake_key
        payload=json.dumps({'caption':raw,'nested':[raw],key:raw})
        source='with restart_bind([{name:"use",report:'+json.dumps(raw)+',function:fn(){42}}]) as r {error_condition(condition("x",'+json.dumps(fake_key)+','+payload+'))}'
        run=self.start(source);state=self.paused(run);text=json.dumps(state)
        self.assertNotIn('test-redaction-boundary-',text)
        self.assertIn('[redacted]',text)
        self.restore(run,state);self.assertEqual(run.result(3),42)

    def test_imported_condition_has_original_module_frame_and_lexical_locals(self):
        files={'main.chaos':'import "worker.chaos" as W;let mainValue=40;W.work(mainValue)',
               'worker.chaos':'fn work(value){with restart_bind([{name:"use",function:fn(){value+2}}]) as r {invoke_debugger(condition("x"))}};export{work}'}
        p=self.app.create_project('imported-recovery',source=files['main.chaos'],files=files)
        run=self.app.start_debug(p['id'],1,pause_on_entry=False);state=self.paused(run)
        self.assertEqual(state['location']['file'],'worker.chaos')
        self.assertEqual(state['frames'][-1]['id'],'module:main.chaos')
        locals_={item['name']:item['value'] for item in state['frames'][0]['locals']}
        self.assertEqual(locals_['value'],40)
        self.restore(run,state);self.assertEqual(run.result(3),42)
