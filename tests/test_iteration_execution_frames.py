"""Complete iteration/stream/comprehension execution against original algorithms."""
import copy
import threading
import unittest
from contextlib import contextmanager,nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native,TailCall,Env
from chaos.suspension import Runner,Task,Generator,AsyncGenerator,Await,Yield,CHECKPOINT,GeneratorClose
from chaos.iteration import Iterator,AsyncIterator,IterationRequest
from chaos.execution_frames import ExecutionCursor,Push,Done,External,InvocationFrame
from chaos.execution_iteration import (ITERATION_FRAMES,IterationFrameRequest,IterationAcquireFrame,
    IterationInvokeFrame,IterationAwaitFrame,IterationStepFrame,IterationCloseFrame,IterationCollectFrame,
    IterationConsumeFrame,IterationDelegateFrame,IterationResolveFrame,StreamResumeFrame,StreamCloseFrame)
from chaos.execution_comprehensions import (COMPREHENSION_FRAMES,ComprehensionState,ComprehensionRequest,
    ComprehensionFrame,ComprehensionWalkFrame)

SYNC='''class Cursor{var n=0;var closes=0;fn __iter__(self){self}
fn __next__(self){self.n=self.n+1;{done:self.n>2,value:self.n}}
fn __close__(self){self.closes=self.closes+1;null}}'''
ASYNC='''class AsyncCursor{var n=0;var closes=0;fn __aiter__(self){self}
async fn __anext__(self){await sleep(0);self.n=self.n+1;{done:self.n>2,value:self.n}}
async fn __aclose__(self){await sleep(0);self.closes=self.closes+1;null}}'''
NODE=('list_comp',('literal',42),[('comp_for','x',('list',[]))])


def snapshot(source,legacy=False,limit=100000,cancel=None,setup=None,seen=None):
    from _iteration_reference149 import reference
    effects,trace,debug,checkpoints=[],[],[],[]
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda name,value:effects.append((name,copy.deepcopy(value))) or value,trace_handler=trace.append)
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    if setup is not None:setup(e)
    original=ExecutionCursor._advance
    def observed(cursor,value,error):
        request=original(cursor,value,error)
        if request is CHECKPOINT:
            s=e.suspension;task=s.current.task if s.current else None
            checkpoints.append((e.steps,dict(e.current_span),task.id if task else None,s.stream.id if s.stream else None))
        return request
    with patch.object(ExecutionCursor,'_advance',observed),reference(seen) if legacy else nullcontext():
        try:outcome=('value',e.run(source))
        except LanguageError as error:outcome=('error',error.record())
        except ExecutionControl as error:outcome=('control',type(error).__name__,str(error))
    return outcome,e.steps,effects,trace,debug,checkpoints


@contextmanager
def active(e,caps=None,team=None):
    s=e.suspension;s.begin();task=Task(s,iter(()))
    runner=Runner(iter(()),task=task);runner.capabilities=caps;runner.team_scope=team
    runner.control_state=s.control_state;runner.context_state=s.context_state;runner.condition_state=s.condition_state;runner.decimal_state=s.decimal_state
    s.current=runner
    try:yield runner
    finally:s.current=None;s.finish()


class IterationExecutionFrames(unittest.TestCase):
    def check(self,source,expected,**options):
        modern=snapshot(source,**options);self.assertEqual(modern[0],('value',expected));self.assertEqual(modern,snapshot(source,legacy=True,**options));return modern
    def own(self,e,frame):frame.owner=e;return SimpleNamespace(owner=e)
    def frame(self,e,cls):return next(f for f in reversed(e._execution_cursor.frames)if type(f) is cls)
    def test_all_compatibility_helpers_are_thin_requests(self):
        e=Evaluator();e._execution_cursor=object();it=e.suspension.iteration
        cases=[it.acquire([]),it._invoke(None,'x',[]),it._await(None),it.step(None),it._step(None),it.close(None),it._close(None),it.collect([]),it.consume([],'expand'),it.delegate([]),e.suspension._s_resume(None),e.suspension._s_close(None)]
        for proxy in cases:self.assertIs(type(next(proxy)),IterationFrameRequest)
        r=IterationRequest('map',[],None);self.assertIs(next(it.resolve(r)),r)
        from chaos.comprehensions import evaluate,traverse
        self.assertIs(type(next(evaluate(e,NODE,Env()))),ComprehensionRequest)
        self.assertIs(type(next(traverse(e,NODE,Env(),[]))),ComprehensionRequest)
        e._execution_cursor=None
        from chaos.execution_frames import ProtocolBridge
        for operation in [it.acquire([]),it.resolve(r),e.suspension._s_resume(None),evaluate(e,NODE,Env()),traverse(e,NODE,Env(),[])]:
            self.assertIs(type(operation),ExecutionCursor);self.assertIs(type(operation.root_bridge),ProtocolBridge)
            self.assertFalse(getattr(operation.root_bridge,'supplementary',False));operation.close()
    def test_exact_old_oracle_all15_boundaries_are_reached(self):
        from _iteration_reference149 import ITERATION_NAMES
        seen=[]
        source=SYNC+ASYNC+'''fn* inner(){yield 1;yield 2}fn* outer(){yield from inner()}
        let g=outer();next(g);close(g);let a=aiter(AsyncCursor());await anext(a);await aclose(a);
        [collect(Cursor()),map(fn(x){x+1},Cursor()),[x for x in [1,2]]]'''
        self.assertEqual(snapshot(source,legacy=True,seen=seen)[0],('value',[[1,2],[2,3],[1,2]]))
        self.assertEqual(set(seen),set(ITERATION_NAMES+['_s_resume','_s_close','evaluate','traverse']))
    def test_iterator_invoke_uses_real_ordinary_invocation_flags(self):
        e=Evaluator();fn=Native('fn',lambda:42,e);f=IterationInvokeFrame(None,'x',[42]);f.phase='access'
        action=f.step(self.own(e,f),fn,None)
        self.assertIs(type(action),Push);self.assertIs(type(action.frame),InvocationFrame)
        self.assertIs(action.frame.fn,fn);self.assertFalse(action.frame.force);self.assertFalse(action.frame._recovery_callback);self.assertFalse(action.frame.all_results)
    def test_native_sources_and_hashmap_context_keys_share_acquire(self):
        self.check('[collect(iter([42])),collect(iter((42,))),collect(iter({42})),collect(iter("x")),collect(iter(hash_map([("x",42)])))]',[[42],[42],[42],['x'],['x']])
        self.check('let v=context_var("x",0);v.set(42);let c=copy_context();collect(iter(c))[0]==v',True)
    def test_temporary_native_acquire_keeps_registry_slots(self):
        e=Evaluator()
        with active(e):
            before=len(e.suspension.iteration.handles);f=IterationAcquireFrame([42],temporary=True)
            action=f.step(self.own(e,f),None,None);self.assertIs(type(action),Done);self.assertEqual(action.value.values,[42]);self.assertEqual(len(e.suspension.iteration.handles),before)
            f=IterationAcquireFrame([42]);f.step(self.own(e,f),None,None);self.assertEqual(len(e.suspension.iteration.handles),before+1)
    def test_custom_sync_factories_cache_terminal_values(self):
        self.check(SYNC+'let raw=Cursor();let a=iter(raw);let b=iter(raw);[a==b,next(a),next(b),next(a),next(b)]',[True,{'done':False,'value':1},{'done':False,'value':2},{'done':True,'value':3},{'done':True,'value':3}])
    def test_custom_async_step_and_close_await_exact_tasks(self):
        self.check(ASYNC+'let raw=AsyncCursor();let a=aiter(raw);let x=await anext(a);await aclose(a);[x,raw.closes,await anext(a)]',[{'done':False,'value':1},1,{'done':True,'value':None}])
    def test_async_factory_is_rejected_without_hidden_await(self):
        source='var started=0;class Bad{async fn __iter__(self){started=started+1;iter([])}}let code=try{iter(Bad())}catch e{e.code};[code,started]'
        self.check(source,['argument_error',0])
    def test_step_record_shape_done_bool_and_live_values_are_rejected(self):
        for expression in ['42','{done:1,value:42}','{done:false,value:42,extra:0}']:
            source='class Bad{fn __iter__(self){self}fn __next__(self){'+expression+'}}next(iter(Bad()))'
            a=snapshot(source);self.assertEqual(a,snapshot(source,legacy=True));self.assertEqual(a[0][0],'error')
        # Any owned language value is valid; compare callable identity locally,
        # then project to ordinary data for the cross-evaluator full snapshot.
        self.check('let f=fn(){42};class Good{fn __iter__(self){self}fn __next__(self){{done:false,value:f}}}let item=next(iter(Good()));[item.done,item.value==f,type_of(item.value),item.value()]',[False,True,'Function',42])
        e=Evaluator();local=e.run('fn(){42}')
        packet=e.suspension.iteration._record({'done':False,'value':{'nested':[local]}})
        self.assertIs(packet['value']['nested'][0],local);self.assertEqual(e.invoke(local,[]),42)
        foreign=Evaluator().run('fn(){42}')
        with self.assertRaises(LanguageError) as caught:e.suspension.iteration._record({'done':False,'value':{'nested':[foreign]}})
        self.assertEqual(caught.exception.code,'ownership_error')
        with active(e):stale=Iterator(e.suspension,values=[])
        with active(e):
            with self.assertRaises(LanguageError) as caught:e.suspension.iteration._record({'done':False,'value':[stale]})
            self.assertEqual(caught.exception.code,'runtime_error')
    def test_custom_send_and_throw_use_original_argument_protocols(self):
        self.check('class C{fn __iter__(self){self}fn __next__(self){{done:false,value:0}}fn __send__(self,x){{done:false,value:x}}fn __throw__(self,x){{done:true,value:x}}}let a=iter(C());[send(a,42),throw_into(a,"x"),next(a)]',[{'done':False,'value':42},{'done':True,'value':'x'},{'done':True,'value':'x'}])
    def test_map_callbacks_remain_cold_and_predicates_strict_bool(self):
        self.check('var n=0;async fn f(x){n=n+1;x*2}let tasks=map(f,[20,21]);let before=n;[before,await gather(tasks),n]',[0,[40,42],2])
        a=snapshot('filter(fn(x){1},[1])');self.assertEqual(a,snapshot('filter(fn(x){1},[1])',legacy=True));self.assertEqual(a[0][0],'error')
    def test_consumer_alias_lock_releases_without_closing_borrow(self):
        self.check(SYNC+'let raw=Cursor();let it=iter(raw);let out=map(fn(x){try{next(raw)}catch e{e.code}},it);[out,raw.closes,next(it)]',[['argument_error','argument_error'],0,{'done':True,'value':3}])
    def test_all_consumers_share_result_projection_and_order(self):
        self.check(SYNC+'[map(fn(x){x*2},Cursor()),filter(fn(x){x==2},Cursor()),fold(fn(a,x){a+x},39,Cursor()),any(fn(x){x==2},Cursor()),all(fn(x){x<3},Cursor())]',[[2,4],[2],42,True,True])
    def test_short_circuit_has_no_extra_probe_or_implicit_close(self):
        self.check(SYNC+'let raw=Cursor();let it=iter(raw);let a=any(fn(x){true},it);[a,raw.n,raw.closes,next(it)]',[True,1,0,{'done':False,'value':2}])
    def test_overflow_probe_commits_item_without_callback(self):
        from _iteration_reference149 import reference
        outcomes=[]
        for legacy in [False,True]:
            e=Evaluator();calls=[]
            with active(e):
                handle=Iterator(e.suspension,values=[1,2,3]);callback=Native('cb',lambda x:calls.append(x) or x,e)
                with reference() if legacy else nullcontext():
                    with self.assertRaises(ResourceLimit):e.suspension.drive(e.suspension.iteration.consume(handle,'map',callback,limit=2))
                outcomes.append((e.steps,calls,handle.index,handle.busy,handle.state))
        self.assertEqual(outcomes[0],outcomes[1]);self.assertEqual(outcomes[0][1:],([1,2],3,False,'suspended'))
    def test_spread_apply_tuple_and_set_share_resolution(self):
        self.check('fn* xs(){yield 20;yield 22}fn sum(a,b,*,extra=0){a+b+extra};[sum(*xs()),apply(sum,xs(),{extra:1}),tuple(xs())==(20,22),contains(set_of(xs()),22)]',[42,43,True,True])
    def test_collect_terminal_final_value_and_close_idempotence(self):
        self.check(SYNC+'let raw=Cursor();let it=iter(raw);let all=collect(it);close(it);close(it);[all,raw.closes,next(it)]',[[1,2],1,{'done':True,'value':3}])
    def test_delegation_forwards_send_throw_and_final_return(self):
        self.check('fn* inner(){let x=yield 1;try{yield x}catch e{yield e.value};return 99}fn* outer(){let r=yield from inner();return r}let g=outer();[next(g),send(g,42),throw_into(g,"caught"),next(g)]',[{'done':False,'value':1},{'done':False,'value':42},{'done':False,'value':'caught'},{'done':True,'value':99}])
    def test_delegation_close_runs_cleanup_once_and_native_send_rejects(self):
        self.check('var seen=[];fn* inner(){try{yield 42}finally{seen=append(seen,"closed")}}fn* outer(){yield from inner()}let g=outer();next(g);close(g);close(g);seen',['closed'])
        self.check('fn* g(){yield from [1,2]}let a=g();next(a);try{send(a,9)}catch e{e.code}','runtime_error')
    def test_stream_cold_close_does_not_enter_body(self):
        self.check('var n=0;fn* g(){n=n+1;yield 42};let s=g();close(s);[n,next(s)]',[0,{'done':True,'value':None}])
    def test_stream_scoped_context_and_exclusive_generator_manager(self):
        self.check('let v=context_var("v",0);fn* g(){with context_bind([{variable:v,value:7}]) as c {yield v.get();yield v.get()}}let s=g();let a=next(s).value;v.set(9);let b=next(s).value;close(s);[a,b,v.get()]',[7,7,9])
        self.check('fn* g(){yield 42};let s=g();let c=generator_context(s);let code=try{next(s)}catch e{e.code};[code,with c as x{x}]',['context_error',42])
    def test_stream_restores_exact_original_state_runner_authority_on_close(self):
        e=Evaluator();original=frozenset({'outside'});team={'role':'outer'}
        with active(e,original,team) as runner:
            task=e.suspension.task(iter(()))
            def body():yield Await(task);yield Yield(42)
            g=e.suspension.generator(body());g.capabilities=frozenset();g.team_scope={'role':'inner'}
            saved=(e.frames,e.depth,e.current_span,e.handled_error,e.error_context,e.suspension.stream)
            f=StreamResumeFrame(g);c=self.own(e,f);action=f.step(c,None,None);self.assertIs(type(action),External)
            self.assertEqual(runner.capabilities,frozenset());self.assertIs(e.suspension.stream,g)
            other=Runner(iter(()));other.capabilities=frozenset({'other'});e.suspension.current=other
            try:
                f.close(c);self.assertIs(runner.capabilities,original);self.assertIs(runner.team_scope,team)
                self.assertEqual(other.capabilities,frozenset({'other'}))
                for actual,expected in zip((e.frames,e.depth,e.current_span,e.handled_error,e.error_context,e.suspension.stream),saved):self.assertIs(actual,expected)
            finally:e.suspension.current=runner
    def test_stream_trace_failure_still_restores_and_keeps_error_priority(self):
        from _iteration_reference149 import reference
        for legacy in [False,True]:
            e=Evaluator();failure=RuntimeError('event failed');original=frozenset({'outside'})
            with active(e,original) as runner:
                def body():raise LanguageError('body');yield
                g=e.suspension.generator(body());g.capabilities=frozenset();saved=(e.frames,e.current_span,e.handled_error,e.error_context,e.suspension.stream)
                def trace(row):
                    if row['event']=='generator.failed':raise failure
                e.trace_handler=trace
                with reference() if legacy else nullcontext():
                    operation=e.suspension._s_resume(g)
                    with self.assertRaises(RuntimeError) as caught:next(operation)
                self.assertIs(caught.exception,failure);self.assertIs(runner.capabilities,original);self.assertFalse(g.busy)
                for actual,expected in zip((e.frames,e.current_span,e.handled_error,e.error_context,e.suspension.stream),saved):self.assertIs(actual,expected)
                e.trace_handler=None
    def test_unstarted_throw_does_not_validate_or_tick(self):
        e=Evaluator();failure=LanguageError('original')
        for frame in [IterationAwaitFrame(None),StreamResumeFrame(None)]:
            before=e.steps
            with self.assertRaises(LanguageError) as caught:frame.step(self.own(e,frame),None,failure)
            self.assertIs(caught.exception,failure);self.assertEqual(e.steps,before)
        from chaos.language import ReturnSignal,BreakSignal,ContinueSignal
        for operation in ('evaluate','traverse'):
            for signal in (ReturnSignal(42),BreakSignal(),ContinueSignal()):
                frame=ComprehensionFrame(NODE,Env(),[],operation);before=e.steps
                with self.assertRaises(type(signal)) as caught:frame.step(self.own(e,frame),None,signal)
                self.assertIs(caught.exception,signal);self.assertEqual(e.steps,before)
    def test_all_five_comprehension_forms_share_traversal(self):
        self.check('async fn* source(){yield 20;yield 22};[[x+1 for x in [20,21]],{str(x):x for x in [1,2]},contains({x for x in [1,2]},2),collect((x for x in [20,22])),await acollect(async (x async for x in source()))]',[[21,22],{'1':1,'2':2},True,[20,22],[20,22]])
    def test_comprehension_record_key_before_value_and_strict_filter(self):
        a=snapshot('{tool("key",1):tool("value",42) for x in [0]}');self.assertEqual(a,snapshot('{tool("key",1):tool("value",42) for x in [0]}',legacy=True));self.assertEqual(a[2],[('key',1)])
        self.check('var seen=[];fn k(x){seen=append(seen,"k");"same"}fn v(x){seen=append(seen,"v");x};let out={k(x):v(x) for x in [1,2]};[out,seen]',[{'same':2},['k','v','k','v']])
    def test_nested_patterns_per_item_closures_and_no_binding_leak(self):
        self.check('let x=99;let fs=[fn(){a+b} for (a,b) in [(20,1),(20,2)] if a==20];[map(fn(f){f()},fs),x]',[[21,22],99])
    def test_lazy_first_source_eager_cells_shared_body_cold(self):
        self.check('var xs=[1];var offset=10;let g=(x+offset for x in xs);xs=[9];offset=20;let before=offset;[before,collect(g)]',[20,[21]])
        self.check(SYNC+'let raw=Cursor();let g=(x for x in raw);let before=raw.n;close(g);[before,raw.n,raw.closes]',[0,0,0])
    def test_lazy_entry_count_advances_only_after_yield_resume(self):
        counts=[]
        def setup(e):
            def probe(x):counts.append(self.frame(e,ComprehensionWalkFrame).state.entries);return x
            e.env.bind('probe',Native('probe',probe,e))
        self.assertEqual(snapshot('let g=(probe(x) for x in [1,2,3]);next(g);next(g);close(g);42',setup=setup)[0],('value',42));self.assertEqual(counts,[0,1])
    def test_comprehension_wrapper_borrows_without_lock_or_close(self):
        self.check(SYNC+'let raw=Cursor();let cursor=iter(raw);let g=(x*10 for x in cursor);let a=next(g);let b=next(cursor);close(g);[a,b,raw.closes,next(cursor)]',[{'done':False,'value':10},{'done':False,'value':2},0,{'done':True,'value':3}])
    def test_async_comprehension_cancellation_releases_pending_anext(self):
        source='''let ready=channel(1);let gate=channel();class Pending{fn __aiter__(self){self}async fn __anext__(self){await channel_send(ready,0);await channel_receive(gate);{done:true,value:null}}}async fn worker(){await acollect(async (x async for x in Pending()))};let t=worker();spawn(t);await channel_receive(ready);cancel(t);try{await t}catch e{e.code}'''
        a=snapshot(source);self.assertEqual(a,snapshot(source,legacy=True));self.assertEqual(a[0][0],'control');self.assertEqual(a[0][1],'TaskCancelled')
    def test_all_small_budget_and_cancel_cutpoints_match_old(self):
        for source in ['map(fn(x){x+1},[1,2])','[x+1 for x in [1,2]]','fn* g(){yield 42}let a=g();next(a);close(a)',ASYNC+'let a=aiter(AsyncCursor());await anext(a);await aclose(a)']:
            total=snapshot(source)[1]
            for boundary in range(total+2):
                with self.subTest(source=source,boundary=boundary):
                    self.assertEqual(snapshot(source,limit=boundary),snapshot(source,legacy=True,limit=boundary))
                    self.assertEqual(snapshot(source,cancel=boundary),snapshot(source,legacy=True,cancel=boundary))
    def test_paused_consumer_arguments_and_accumulator_are_actual(self):
        def setup(e):
            def mutate(x):
                f=self.frame(e,IterationConsumeFrame);f.item['value']=42;return False
            e.env.bind('mutate',Native('mutate',mutate,e))
        self.assertEqual(snapshot('filter(fn(x){mutate(x);true},[0])',setup=setup)[0],('value',[42]))
    def test_paused_clause_key_and_shared_result_are_actual(self):
        def setup(e):
            def mutate(x):self.frame(e,ComprehensionWalkFrame).key='changed';return x
            e.env.bind('mutate',Native('mutate',mutate,e))
        self.assertEqual(snapshot('{"original":mutate(x) for x in [42]}',setup=setup)[0],('value',{'changed':42}))
    def test_every13_frame_declares_foreign_pending_roots(self):
        from chaos.capture_graph import seal
        e=Evaluator();foreign=Evaluator().run('fn(){42}');state=ComprehensionState(e,NODE,Env(),[])
        cases=[(IterationAcquireFrame([]),'result'),(IterationInvokeFrame(None,'x',[]),'args'),(IterationAwaitFrame(None),'result'),
            (IterationStepFrame(None),'sent'),(IterationCloseFrame(None),'result'),(IterationCollectFrame([]),'values'),
            (IterationConsumeFrame([],'fold'),'result'),(IterationDelegateFrame([]),'item'),(IterationResolveFrame(IterationRequest('iter',[])),'result'),
            (StreamResumeFrame(None),'saved_error'),(StreamCloseFrame(None),'context'),(ComprehensionFrame(NODE,Env()),'result'),(ComprehensionWalkFrame(state,0,Env()),'value')]
        self.assertEqual({type(f)for f,k in cases},set(ITERATION_FRAMES+COMPREHENSION_FRAMES))
        for frame,key in cases:
            with self.subTest(frame=type(frame).__name__):
                setattr(frame,key,[foreign]if key in ('args','values')else foreign)
                with self.assertRaises(LanguageError) as caught:seal(e,(ExecutionCursor(e,frame),),None)
                self.assertEqual(caught.exception.code,'ownership_error')
    def test_requests_limits_atomic_alias_caps_and_no_host_hooks(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        for args in [(foreign,),[foreign]]:
            targets=[IterationFrameRequest('invoke',(None,'x',args)),IterationInvokeFrame(None,'x',args)]
            for target in targets:
                if type(target) is IterationInvokeFrame:target.owner=e
                with self.assertRaises(LanguageError):seal(e,(target,),None)
        f=IterationInvokeFrame(None,'x',([None]*10000,[None]*10000));f.owner=e
        with self.assertRaises(ResourceLimit):seal(e,(f,),None)
        deep=None
        for i in range(102):deep=[deep]
        f.args=(deep,)
        with self.assertRaises(ResourceLimit):seal(e,(f,),None)
        token=NextDescriptor(e,None);n=Native('next',lambda:None,e,generic=token,captures=());f.args=(foreign,n)
        with self.assertRaises(LanguageError):seal(e,(f,),frozenset())
        self.assertIsNone(token.capabilities);self.assertIsNone(n.capabilities)
        class Trap:
            def __iter__(self):raise AssertionError('host iteration')
            @property
            def capabilities(self):raise AssertionError('host property')
        f.args=Trap();f.function=Trap();seal(e,(f,),None)
    def test_active_saved_and_caller_floor_capabilities_remain_distinct(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator()
        with active(e,frozenset({'outside'})) as runner:
            inside,outside=NextDescriptor(e,None),NextDescriptor(e,None)
            inner=Native('inside',lambda:None,e,generic=inside,captures=());outer=Native('outside',lambda:None,e,generic=outside,captures=())
            handle=Iterator(e.suspension,values=[]);handle.capabilities=frozenset()
            f=IterationStepFrame(handle,sent=inner);f.handle=handle;f.authority_runner=runner;f.previous_authority=runner.capabilities;runner.value=outer
            seal(e,(ExecutionCursor(e,f),),frozenset({'outside'}));self.assertEqual(inside.capabilities,frozenset());self.assertEqual(outside.capabilities,frozenset({'outside'}))
            seal(e,(ExecutionCursor(e,f),),frozenset());self.assertEqual(outside.capabilities,frozenset())
    def test_empty_fold_initial_retains_caller_authority_until_callback(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator()
        with active(e,frozenset({'outside'})):
            token=NextDescriptor(e,None);initial=Native('initial',lambda:None,e,generic=token,captures=())
            callback=Native('limited',lambda *args:None,e,capabilities=frozenset(),captures=())
            f=IterationConsumeFrame([],'fold',callback,initial);cursor=ExecutionCursor(e,f)
            seal(e,(cursor,),frozenset({'outside'}));self.assertEqual(token.capabilities,frozenset({'outside'}))
            f.callback_called=True;f.arguments=[initial,0];seal(e,(cursor,),frozenset({'outside'}));self.assertEqual(token.capabilities,frozenset())
            from chaos.bot_values import Bot,State
            bot=Bot(e,e.suspension.epoch,State('goal',None,None),None,None,None,capabilities=frozenset())
            second=NextDescriptor(e,None);arg=Native('arg',lambda:None,e,generic=second,captures=())
            call=IterationConsumeFrame([],'map',bot);call.callback_called=True;call.arguments=[arg]
            seal(e,(ExecutionCursor(e,call),),frozenset({'outside'}));self.assertEqual(second.capabilities,frozenset())
            from chaos.device_values import Device,Lease as DeviceLease
            from chaos.computer_values import Computer
            from chaos.robot_values import Robot,Lease as RobotLease
            from chaos.objects import StaticMethod
            from chaos.control_prompts import EscapeContinuation
            callables=[StaticMethod(callback,e,frozenset()),
                       EscapeContinuation(e.suspension,e.suspension.epoch,None,'escape-test',frozenset()),
                       Device(e,DeviceLease(e,e.suspension.epoch,{}),frozenset()),
                       Computer(e,{},e.suspension.epoch,capabilities=frozenset()),
                       Robot(e,RobotLease(e,e.suspension.epoch,{}),frozenset())]
            for callback in callables:
                pending=NextDescriptor(e,None);argument=Native('arg',lambda:None,e,generic=pending,captures=())
                frame=IterationInvokeFrame(None,'call',[argument]);frame.function=callback
                seal(e,(ExecutionCursor(e,frame),),frozenset({'outside'}))
                self.assertEqual(pending.capabilities,frozenset(),type(callback).__name__)
    def test_restricted_cursor_does_not_restrict_broader_caller_callback_or_head(self):
        def setup(e):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        for expression in ['map(fn(x){tool("seen",x*2)},cursor)','[tool("seen",x*2) for x in cursor]']:
            self.check('let cursor=limited(fn(){iter([21])});'+expression,[42],setup=setup)
    def test_all64_live_task_and_stream_capture_graph_residues_match(self):
        def setup(e):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        for padding in range(64):
            source='''let padding=range(PADDING);let ready=channel(1);let gate=channel();var saved=null;let sink=limited(fn(){fn(t){saved=t;null}});
            class Source{var n=0;fn __aiter__(self){self}async fn __anext__(self){await channel_send(ready,0);await channel_receive(gate);self.n=self.n+1;{done:true,value:null}}}
            async fn work(){await acollect(async (x async for x in Source()))};let task=work();spawn(task);await channel_receive(ready);sink(task);await channel_send(gate,0);await saved'''.replace('PADDING',str(padding))
            with self.subTest(padding=padding):self.check(source,[],setup=setup)
            stream_source='''let padding=range(PADDING);let ready=channel(1);let gate=channel();var saved=null;let sink=limited(fn(){fn(t){saved=t;null}});
            async fn* stream(){await channel_send(ready,0);await channel_receive(gate);yield 42}
            async fn work(){let g=stream();let item=await anext(g);await aclose(g);item.value};let task=work();spawn(task);
            await channel_receive(ready);sink(task);await channel_send(gate,0);await saved'''.replace('PADDING',str(padding))
            with self.subTest(padding=padding,kind='actual-stream'):self.check(stream_source,42,setup=setup)


if __name__=='__main__':unittest.main()
