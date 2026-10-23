"""Actual Bot lifecycle frames and exact original native algorithm evidence."""
import copy
import threading
from contextlib import contextmanager,nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from chaos.language import Evaluator,LanguageError,ExecutionControl,ResourceLimit,Native,TailCall
from chaos.execution_frames import ExecutionCursor,Done,Push,External,InvocationFrame,frame_roots
from chaos.execution_bot import (BOT_FRAMES,BotRunFrame,BotHostFrame,BotCallbackFrame,
    BotPhaseFrame,BotPhaseBodyFrame,BotCloseOwnedFrame,BotCleanupFrame,BotDecisionFrame,
    BotDrainFrame,BotOutcomeFrame,BotCommitFrame)
from chaos.bot_runtime import Decision,BotRequest
from chaos.bot_values import Bot,State
from chaos.suspension import Runner,TaskCancelled

VERIFY='fn(s){{done:true,context:s.context,output:s.evidence[0]}}'
BASIC='let b=bot("goal",fn(s){40},fn(p,s){p+2},'+VERIFY+');let r=await b();[r.status,r.output,r.state.iteration]'


class Host:
    """Deterministic admission fixture; real scheduling still owns all effects."""
    def __init__(self):
        self.events=[];self.effects=[];self.revision=0;self.messages=[]
        self.decisions={};self.sequence=0;self.overrides={};self.close_error=None
    def queue(self,value='new',message_id='guide'):
        self.revision+=1
        self.messages.append({'runId':'fixture','messageId':message_id,'revision':self.revision,'input':value,'status':'received'})
    def control(self,op,payload):
        self.events.append((op,copy.deepcopy(payload)))
        if op in self.overrides:return self.overrides[op](payload)
        if op=='bot_open':return {'scopeId':payload['scopeId'],'revision':self.revision}
        if op=='bot_receive':
            message=self.messages[0] if self.messages else None
            return {'received':message is not None,'message':copy.deepcopy(message),'closed':False,'revision':self.revision}
        if op=='bot_adopt':
            if self.messages and self.messages[0]['messageId']==payload['messageId']:self.messages.pop(0)
            return {'applied':True,'revision':payload['revision']}
        if op=='bot_decision':
            if payload['revision']!=self.revision:return {'admitted':False,'revision':self.revision,'decisionId':None,'ticket':None}
            self.sequence+=1;identity='decision-'+str(self.sequence)
            self.decisions[identity]={'actions':[],'phase':payload['phase']}
            return {'admitted':True,'revision':self.revision,'decisionId':identity,'ticket':'ticket-'+str(self.sequence)}
        if op=='bot_outcome':return {'revision':self.revision,'actions':copy.deepcopy(self.decisions.get(payload['decisionId'],{}).get('actions',[]))}
        if op=='bot_commit':return {'committed':payload['revision']==self.revision,'revision':self.revision}
        if op=='bot_close':
            if self.close_error is not None:raise self.close_error
            return {'closed':True}
        raise AssertionError(op)
    def guidance(self,op,payload,caps=None,team=None):return self.control(op,payload)
    def effect(self,kind,payload,event):
        if kind=='guidance':return self.control(payload['operation'],payload['payload'])
        if kind=='tool':
            self.effects.append((payload['name'],copy.deepcopy(payload['value'])))
            guard=payload.get('_bot_guard')
            if guard is not None:
                for part in guard.get('ancestors',[])+[guard]:
                    item=self.decisions.get(part['decisionId'])
                    if item is not None:item['actions'].append({'id':'action-'+str(len(self.effects)),'kind':'tool:'+payload['name'],'status':'completed'})
            return payload['value']
        raise AssertionError(kind)


def snapshot(source=BASIC,legacy=False,limit=100000,cancel=None,setup=None,host=None,*,effect_policy="already_done"):
    from _bot_reference144 import reference
    from _effect_schedule158 import effect_schedule
    host=Host() if host is None else host
    trace,debug=[],[]
    e=Evaluator(step_limit=limit,timeout=30,guidance_handler=host.guidance,effect_handler=host.effect,
                tool_handler=lambda name,value:value,trace_handler=trace.append)
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    if setup is not None:setup(e,host)
    sequence=iter(range(10000))
    with effect_schedule(effect_policy) if effect_policy is not None else nullcontext(), patch('chaos.bot_runtime.uuid.uuid4',side_effect=lambda:SimpleNamespace(hex=format(next(sequence),'032x'))):
        with reference() if legacy else nullcontext():
            try:outcome=('value',e.run(source))
            except LanguageError as error:outcome=('error',error.record())
            except ExecutionControl as error:outcome=('control',type(error).__name__,str(error))
    return outcome,e.steps,host.events,host.effects,trace,debug


@contextmanager
def active(e,caps=None):
    e.suspension.begin()
    runner=Runner(iter(()),task=SimpleNamespace(recording_scope=None,id='fixture',cancel_event=threading.Event(),interrupt=None))
    runner.capabilities=caps;runner.control_state=e.suspension.control_state
    runner.context_state=e.suspension.context_state;runner.condition_state=e.suspension.condition_state
    runner.decimal_state=e.suspension.decimal_state
    e.suspension.current=runner
    try:yield runner
    finally:e.suspension.current=None;e.suspension.finish()


class BotExecutionFrames(unittest.TestCase):
    def live(self,e,kind):return next(f for f in reversed(e._execution_cursor.frames) if type(f) is kind)
    def check(self,source,expected,**options):
        modern=snapshot(source,**options);old=snapshot(source,legacy=True,**options)
        self.assertEqual(modern[0],('value',expected));self.assertEqual(modern,old)
    def bot(self,e,caps=None):return Bot(e,e.suspension.epoch,State('fixture','goal',None),None,None,None,1,caps)
    def test_complete_lifecycle_expected_and_original_steps_effects_debug_trace(self):
        self.check(BASIC,['achieved',42,1])
    def test_bot_call_is_cold_until_awaited(self):
        self.check('let b=bot("g",fn(s){tool("plan",null)},fn(p,s){p},'+VERIFY+');let t=b();[type_of(t),bot_info(b).busy]', ['Task',False])
    def test_async_callback_uses_ordinary_invocation_then_owned_await(self):
        seen=[];e=Evaluator();host=Host();e.guidance_handler=host.guidance;e.effect_handler=host.effect
        original=BotCallbackFrame.step
        def step(frame,cursor,value,error):
            action=original(frame,cursor,value,error)
            if type(action) is Push and type(action.frame) is InvocationFrame:
                seen.append((action.frame.force,action.frame.all_results))
            return action
        with patch.object(BotCallbackFrame,'step',step):
            value=e.run('let b=bot("g",async fn(s){await sleep(0);40},async fn(p,s){await sleep(0);p+2},'+VERIFY+');(await b()).output')
        self.assertEqual(value,42);self.assertEqual(seen,[(False,True)]*3)
    def test_callback_preserves_primary_of_multiple_results(self):
        self.check('let b=bot("g",fn(s){return_values(40,"ignored")},fn(p,s){return_values(p+2,"ignored")},fn(s){return_values({done:true,context:null,output:s.evidence},"ignored")});(await b()).output',[42])
    def test_callback_rejects_unconsumed_generator(self):
        result=snapshot('fn* stream(s){yield 42};let b=bot("g",stream,fn(p,s){p},'+VERIFY+');try{await b()}catch e{e.code}')
        self.assertEqual(result[0],('value','bot_error'));self.assertEqual(result,snapshot('fn* stream(s){yield 42};let b=bot("g",stream,fn(p,s){p},'+VERIFY+');try{await b()}catch e{e.code}',legacy=True))
    def test_guidance_is_adopted_once_before_callback(self):
        def setup(e,h):h.queue({'x':42})
        self.check('let b=bot("g",fn(s){s.guidance[0].input.x},fn(p,s){p},'+VERIFY+');let r=await b();[r.output,length(r.state.guidance),r.state.revision]',[42,1,1],setup=setup)
    def test_duplicate_guidance_receipt_does_not_reapply_native_state(self):
        def setup(e,h):
            h.queue(42);h.messages.append(copy.deepcopy(h.messages[0]))
        self.check('let b=bot("g",fn(s){length(s.guidance)},fn(p,s){p},'+VERIFY+');let r=await b();[r.output,length(r.state.guidance)]',[1,1],setup=setup)
    def test_planning_revision_change_replans_without_stale_executor(self):
        def setup(e,h):
            e.env.bind('queue',Native('queue',lambda:h.queue(),e))
        self.check('var once=false;fn plan(s){if !once{once=true;queue()}else{null};length(s.guidance)};let b=bot("g",plan,fn(p,s){tool("act",p)},'+VERIFY+');let r=await b();[r.output,r.state.iteration]',[1,2],setup=setup)
    def test_finite_superseded_plan_exhaustion_adopts_without_executor_replay(self):
        def setup(e,h):e.env.bind('queue',Native('queue',lambda:h.queue(),e))
        self.check('let b=bot("g",fn(s){queue();42},fn(p,s){tool("act",p)},'+VERIFY+',null,1);let r=await b();[r.status,r.state.evidence,r.state.revision]', ['exhausted',[],1],setup=setup)
    def test_planner_failure_without_external_prefix_propagates_and_clears_busy(self):
        self.check('let b=bot("g",fn(s){throw "plan"},fn(p,s){p},'+VERIFY+');let x=try{await b()}catch e{e.value};[x,bot_info(b).status,bot_info(b).busy]',['plan','failed',False])
    def test_planning_external_prefix_failure_requires_reconciliation(self):
        self.check('let b=bot("g",fn(s){tool("act",42);throw "plan"},fn(p,s){p},'+VERIFY+');let r=await b();[r.status,r.done,bot_info(b).busy]', ['needs_reconciliation',False,False])
    def test_execution_failure_records_outcome_and_blocks_reinvocation(self):
        self.check('let b=bot("g",fn(s){42},fn(p,s){tool("act",p);throw "execute"},'+VERIFY+');let r=await b();[r.status,try{await b()}catch e{e.code},length(r.state.history)]',['needs_reconciliation','bot_state_error',2])
    def test_verifier_failure_preserves_completed_execution_evidence(self):
        self.check('let b=bot("g",fn(s){40},fn(p,s){p+2},fn(s){throw "verify"});let r=await b();[r.status,r.state.evidence]', ['needs_reconciliation',[42]])
    def test_commit_revision_race_never_replays_admitted_execution(self):
        def setup(e,h):e.env.bind('queue',Native('queue',lambda:h.queue(),e))
        self.check('let b=bot("g",fn(s){42},fn(p,s){tool("act",p)},fn(s){queue();{done:true,context:null,output:42}});let r=await b();[r.status,r.done,r.state.evidence]', ['needs_reconciliation',False,[42]],setup=setup)
    def test_nonterminal_iterations_commit_context_then_exhaust(self):
        self.check('let b=bot("g",fn(s){s.context+1},fn(p,s){p},fn(s){{done:false,context:s.evidence[-1],output:s.evidence}},0,2);let r=await b();[r.status,r.state.context,r.output,r.state.iteration]', ['exhausted',2,[1,2],2])
    def test_nested_bot_keeps_parent_guard_and_once_only_action(self):
        self.check('let child=bot("child",fn(s){s.input},fn(p,s){tool("act",p+2)},'+VERIFY+');let parent=bot("parent",fn(s){40},async fn(p,s){(await child(p)).output},'+VERIFY+');(await parent()).output',42)
    def test_owned_suspended_stream_is_closed_before_verification(self):
        self.check('var closed=0;async fn* stream(){try{yield 1}finally{closed+=1}};async fn execute(p,s){let g=stream();await anext(g);42};let b=bot("g",fn(s){null},execute,fn(s){{done:true,context:null,output:closed}});(await b()).output',1)
    def test_stream_cleanup_error_obeys_original_phase_error_priority(self):
        self.check('async fn* stream(){try{yield 1}finally{throw "cleanup"}};async fn execute(p,s){let g=stream();await anext(g);throw "body"};let b=bot("g",fn(s){null},execute,'+VERIFY+');(await b()).status','needs_reconciliation')
    def test_all_host_receipts_are_validated_on_original_error_paths(self):
        cases={'bot_open':{},'bot_decision':{'admitted':True,'revision':0,'decisionId':'','ticket':''},'bot_outcome':{'revision':0,'actions':[{'id':'x','kind':'tool','status':'invalid'}]},'bot_commit':{}}
        for op,packet in cases.items():
            def setup(e,h,op=op,packet=packet):h.overrides[op]=lambda payload:copy.deepcopy(packet)
            with self.subTest(operation=op):
                a=snapshot(BASIC,setup=setup);b=snapshot(BASIC,legacy=True,setup=setup)
                self.assertEqual(a,b)
                self.assertTrue(a[0][0]=='error' or a[0][1][0]=='needs_reconciliation')
    def test_close_receipt_loss_is_retired_and_busy_always_cleared(self):
        def setup(e,h):h.overrides['bot_open']=lambda payload:{}
        a=snapshot('let b=bot("g",fn(s){1},fn(p,s){p},'+VERIFY+');let x=try{await b()}catch e{e.code};[x,bot_info(b).busy]',setup=setup)
        self.assertEqual(a[0],('value',['bot_error',False]));self.assertEqual(a,snapshot('let b=bot("g",fn(s){1},fn(p,s){p},'+VERIFY+');let x=try{await b()}catch e{e.code};[x,bot_info(b).busy]',legacy=True,setup=setup))
        self.assertEqual([op for op,p in a[2]].count('bot_close'),1)
    def test_trusted_retirement_restores_exact_saved_runner_caps_and_team(self):
        e=Evaluator();seen=[];caps=frozenset({'outer'});team={'scope':'outer'}
        with active(e,caps) as runner:
            runner.team_scope=team;b=self.bot(e,frozenset());frame=BotRunFrame(b,None);frame.owner=e
            e.effect_handler=lambda *args:None
            def close(op,payload,authority,provenance):
                self.assertIs(runner.capabilities,caps);self.assertIs(runner.team_scope,team)
                self.assertIs(authority,caps);self.assertEqual(payload['status'],'needs_reconciliation');seen.append(op);return {'closed':True}
            e.guidance_handler=close
            frame.step(SimpleNamespace(owner=e),None,None)
            self.assertEqual(runner.capabilities,frozenset())
            frame.token=Decision(e,e.suspension.epoch,frame.scope_id,0,'d','t',body_entered=True)
            frame.phase='plan'
            frame.close(SimpleNamespace(owner=e))
            self.assertEqual(seen,['bot_close']);self.assertFalse(b.state.busy)
    def test_phase_guard_restores_exact_original_runner_after_current_changes(self):
        e=Evaluator()
        with active(e) as runner:
            prior=object();runner.bot_guard=prior
            token=Decision(e,e.suspension.epoch,'scope',0,'','')
            frame=BotPhaseFrame(token,None,[]);frame.owner=e
            frame.step(SimpleNamespace(owner=e),None,None)
            self.assertIs(runner.bot_guard,token)
            other=Runner(iter(()));other.bot_guard='other';e.suspension.current=other
            frame.close(SimpleNamespace(owner=e))
            self.assertIs(runner.bot_guard,prior);self.assertEqual(other.bot_guard,'other');self.assertFalse(token.active)
    def test_paused_proposal_field_is_actual_executor_input(self):
        original=BotRunFrame.step
        def step(frame,cursor,value,error):
            if frame.phase=='planning_outcome':frame.proposal=40
            return original(frame,cursor,value,error)
        with patch.object(BotRunFrame,'step',step):self.assertEqual(snapshot(BASIC.replace('fn(s){40}','fn(s){1}'))[0],('value',['achieved',42,1]))
    def test_paused_verified_field_is_actual_commit_output(self):
        original=BotRunFrame.step
        def step(frame,cursor,value,error):
            if frame.phase=='verification_outcome':frame.verified['output']=42
            return original(frame,cursor,value,error)
        with patch.object(BotRunFrame,'step',step):self.assertEqual(snapshot(BASIC.replace('fn(p,s){p+2}','fn(p,s){p+1}'))[0],('value',['achieved',42,1]))
    def test_callback_argument_snapshot_drives_actual_call_and_is_not_live_state(self):
        original=BotCallbackFrame.step
        def step(frame,cursor,value,error):
            if frame.phase=='start' and getattr(frame.function,'name','')=='plan':frame.args[0]['context']=40
            return original(frame,cursor,value,error)
        source='fn plan(s){s.context};let b=bot("g",plan,fn(p,s){p+2},'+VERIFY+',0);let r=await b();[r.output,r.state.context]'
        with patch.object(BotCallbackFrame,'step',step):self.assertEqual(snapshot(source)[0],('value',[42,0]))
    def test_every_frame_pending_root_rejects_foreign_language_values(self):
        from chaos.capture_graph import seal
        from chaos.task_groups import ScopeOutcome
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        with active(e):
            b=self.bot(e);t=Decision(e,e.suspension.epoch,'scope',0,'d','t')
            cases=[(BotRunFrame(b,None),'proposal'),(BotHostFrame('x',None),'payload'),(BotCallbackFrame(None,[]),'value'),
                (BotPhaseFrame(t,None,[]),'outcome'),(BotPhaseBodyFrame(t,None,[]),'outcome'),(BotCloseOwnedFrame(t,None),'handles'),
                (BotCleanupFrame((),None),'handle'),(BotDecisionFrame('scope',0),'packet'),(BotDrainFrame(b,'scope'),'message'),
                (BotOutcomeFrame(b,'scope',t),'row'),(BotCommitFrame(b,'scope',True,True,None,None),'output')]
            self.assertEqual({type(f) for f,k in cases},set(BOT_FRAMES))
            for frame,field in cases:
                with self.subTest(frame=type(frame).__name__):
                    setattr(frame,field,(foreign,) if field=='handles' else ScopeOutcome(value=foreign) if field=='outcome' else foreign)
                    cursor=ExecutionCursor(e,frame)
                    with self.assertRaises(LanguageError) as caught:seal(e,(cursor,),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
            frame=BotRunFrame(b,None);frame.state=State('changed',foreign,None)
            with self.assertRaises(LanguageError) as caught:seal(e,(ExecutionCursor(e,frame),),None)
            self.assertEqual(caught.exception.code,'ownership_error')
    def test_frame_and_request_args_root_bounds_authority_and_atomicity(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        for args in ((foreign,),[foreign]):
            for target in (BotCallbackFrame(None,args),BotRequest('callback',(None,args))):
                if type(target) is BotCallbackFrame:target.owner=e
                with self.assertRaises(LanguageError):seal(e,(target,),None)
        frame=BotCallbackFrame(None,([None]*10000,[None]*10000));frame.owner=e
        with self.assertRaises(ResourceLimit):seal(e,(frame,),None)
        deep=None
        for _ in range(102):deep=[deep]
        frame.args=(deep,)
        with self.assertRaises(ResourceLimit):seal(e,(frame,),None)
        capsule=NextDescriptor(e,None);token=Native('next',lambda:None,e,generic=capsule,captures=())
        frame.args=(foreign,token)
        with self.assertRaises(LanguageError):seal(e,(frame,),frozenset())
        self.assertIsNone(capsule.capabilities);self.assertIsNone(token.capabilities)
        frame.args=(token,);seal(e,(frame,),frozenset())
        self.assertEqual(capsule.capabilities,frozenset())
        frame.owner=Evaluator()
        with self.assertRaises(LanguageError) as caught:seal(e,(frame,),None)
        self.assertEqual(caught.exception.code,'ownership_error')
        with active(e,frozenset({'outside'})):
            active_token=NextDescriptor(e,None);saved_token=NextDescriptor(e,None)
            inside=Native('inside',lambda:None,e,generic=active_token,captures=())
            outside=Native('outside',lambda:None,e,generic=saved_token,captures=())
            run=BotRunFrame(self.bot(e,frozenset()),None)
            run.proposal=inside;run.previous_authority=frozenset({'outside'});run.previous_team=outside
            cursor=ExecutionCursor(e,run)
            seal(e,(cursor,),frozenset({'outside'}))
            self.assertEqual(active_token.capabilities,frozenset())
            self.assertEqual(saved_token.capabilities,frozenset({'outside'}))
            seal(e,(cursor,),frozenset())
            self.assertEqual(saved_token.capabilities,frozenset())
        class Trap:
            def __iter__(self):raise AssertionError('host args iteration')
            def __getattr__(self,name):raise AssertionError('host args introspection')
        frame.owner=e;frame.args=Trap()
        seal(e,(frame,BotRequest('callback',(None,Trap()))),None)
    def test_hard_and_nonlocal_controls_preserve_cleanup_and_exact_error_identity(self):
        from chaos.control_prompts import NonlocalTransfer
        for failure in (ResourceLimit('hard'),ExecutionControl('cancel'),NonlocalTransfer()):
            for legacy in (False,True):
                e=Evaluator();h=Host();e.guidance_handler=h.guidance;e.effect_handler=h.effect
                def stop():raise failure
                e.env.bind('stop',Native('stop',stop,e));e.tool_handler=lambda *args:None
                from _bot_reference144 import reference
                with reference() if legacy else nullcontext():
                    with self.assertRaises(type(failure)) as caught:e.run('async fn* stream(){try{yield 1}finally{tool("cleanup",1)}};async fn execute(p,s){let g=stream();await anext(g);stop()};let b=bot("g",fn(s){1},execute,'+VERIFY+');await b()')
                self.assertIs(caught.exception,failure)
                self.assertEqual(h.effects,[('cleanup',1)] if isinstance(failure,NonlocalTransfer) else [])
                self.assertEqual([op for op,p in h.events].count('bot_close'),1)
    def test_every_small_lifecycle_step_budget_and_cancel_threshold_matches_original(self):
        source='let b=bot("g",fn(s){1},fn(p,s){p},'+VERIFY+');(await b()).status'
        total=snapshot(source,legacy=True)[1]
        for limit in range(total+2):
            with self.subTest(limit=limit):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
        for cancel in range(total+2):
            with self.subTest(cancel=cancel):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))
    def test_live_native_bot_capture_graph_padding_matches_original(self):
        # Compare the unchanged program under both fixed real-worker handoffs.
        # Explicit live Task graph coverage is in test_effect_schedule158.
        def setup(e,h):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        for padding in range(64):
            source='''let padding=range(PADDING);let ready=channel(1);let gate=channel();
              var saved=null;let sink=limited(fn(){fn(t){saved=t;null}});
              async fn plan(s){await channel_send(ready,0);await channel_receive(gate);42};
              let b=bot("g",plan,fn(p,s){p},'''+VERIFY+''');let task=b();spawn(task);
              await channel_receive(ready);sink(task);await channel_send(gate,0);(await saved).output'''
            source=source.replace('PADDING',str(padding))
            for effect_policy in ('already_done','first_wait'):
                with self.subTest(padding=padding,effect_policy=effect_policy):self.check(source,42,setup=setup,effect_policy=effect_policy)


if __name__=='__main__':unittest.main()
