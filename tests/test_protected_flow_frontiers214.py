"""Original hard/soft close frontiers and actual owned-protection retirement."""
import copy,unittest
from unittest.mock import patch
from chaos import suspension,capture_frames
from chaos.language import Evaluator,Native,LanguageError,ResourceLimit,ExecutionControl,serializable
from chaos.execution_frames import EvaluationBoundaryFrame,ensure_cursor
from chaos.execution_protection import FinallyFrame
from test_operator_execution_frames import active
from test_callable_execution_frames import pending
import _generator_flow208 as streams
import _protected_flow214 as fixture

class ProtectedFrontiers214(unittest.TestCase):
    def test_both_stream_flavors_keep_yield_throw_and_two_effect_close_inside_actual_protection(self):
        for flavor,nextop,throwop,closeop in [('fn*','next(s)','throw_into(s,43)','close(s)'),('async fn*','await anext(s)','await athrow(s,43)','await aclose(s)')]:
            for operation in ('close','throw'):
                rows=[]
                source='let g=choose('+flavor+'(){try{let feedback=yield 1;yield feedback}catch e{yield e.value}finally{tool("cleanup1",1);tool("cleanup2",2)};return 9});let s=g();mark(s);let first='+nextop+';inspect_stream(s);'
                source+=('let closed='+closeop+';let second='+closeop+';[first,closed,second,'+nextop+']'if operation=='close'else'[first,'+throwop+','+nextop+']')
                for target in streams.targets():
                    checked=[]
                    def inspect(e,stream,states,functions):
                        checked.append(stream);self.assertEqual(stream.state,'suspended')
                        if target in ('raw','interpreter'):self.assertEqual(states,[]);return
                        live=[s for s in states if not s.closed];self.assertEqual(len(live),1);state=live[0]
                        self.assertEqual(state.pending_kind,'yield');self.assertIs(state.pending_cursor,stream.runner.iterator)
                        self.assertIs(type(state.pending_request),suspension.Yield)
                        self.assertTrue(any(type(frame)is FinallyFrame and frame.phase=='body' and frame._flow_region is not None for frame in stream.runner.iterator.frames))
                        self.assertTrue(any(row[0]=='protect'for row in state.program.instructions))
                    with self.subTest(flavor=flavor,operation=operation,target=target):
                        row,states,functions,held=streams.run(source,target,inspect=inspect)
                        self.assertEqual(len(checked),1);streams.assert_compiled(self,target,states,functions)
                        self.assertEqual(row[2],[('cleanup1',1),('cleanup2',2)])
                        expected=([{'done':False,'value':1},None,None,{'done':True,'value':None}]if operation=='close'else[{'done':False,'value':1},{'done':False,'value':43},{'done':True,'value':9}])
                        self.assertEqual(row[0],('value',expected));rows.append(row)
                        self.assertTrue(all(not s._transfers for s in states))
                self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_host_failure_identity_cause_and_hard_abort_cleanup_match_bare_first(self):
        # The original complete public run decides whether a host fault escapes,
        # becomes a language failure, or is latched by the actual Task runner.
        for fault_type in (LanguageError,ResourceLimit,ExecutionControl,GeneratorExit,StopIteration,RuntimeError):
            rows=[];retained=[]
            for target in streams.targets():
                effects=[];functions=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:effects.append((n,v))or v)
                cause=ValueError('protected-cause214');fault=fault_type('protected-fault214');fault.__cause__=cause;retained.extend((fault,cause))
                def fail():raise fault
                def choose(f):
                    chosen=streams.compile_choice(e,f,target);functions.append(chosen);return chosen
                e.env.bind('fail',Native('fail',fail,e,captures=()));e.env.bind('choose',Native('choose',choose,e,captures=()))
                with streams.observe_states()as states:
                    try:outer=('value',serializable(e.run('let f=choose(fn(){try{fail()}finally{tool("cleanup1",1);tool("cleanup2",2)}});f()')))
                    except BaseException as error:
                        retained.extend((error,error.__traceback__))
                        outer=('error',type(error).__name__,str(error),error is fault,error.__cause__ is fault,error.__cause__ is cause)
                self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertIsNone(e.handled_error);self.assertIsNone(e.error_context)
                if fault_type in (ResourceLimit,ExecutionControl,GeneratorExit):self.assertEqual(effects,[])
                elif fault_type is LanguageError:self.assertEqual(effects,[('cleanup1',1),('cleanup2',2)])
                if target not in ('raw','interpreter'):
                    self.assertTrue(states);self.assertTrue(any(row[0]=='protect'for row in functions[0].compiled_plan.flow.instructions))
                    self.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers for s in states))
                else:self.assertEqual(states,[])
                rows.append((outer,effects))
            self.assertTrue(all(row==rows[0]for row in rows),(fault_type,rows))

    def test_corrupt_protected_activation_drains_two_effects_once_and_cleanup_error_overrides_ordinary_fault(self):
        foreign=Evaluator().run('fn foreign(){0}');retained=[]
        def run(target,mode,kind,override):
            notes=[];effects=[];states=[];leaves=[];tracked=set();e=Evaluator(timeout=30,step_limit=30000,tool_handler=lambda name,value:value)
            cleanup_fault=LanguageError('cleanup-new214',code='cleanup-new214');fault=(ResourceLimit('hard214')if kind=='hard'else GeneratorExit('native-close214')if kind=='close'else LanguageError('incoming214',code='incoming214'))
            retained.extend((fault,cleanup_fault))
            def note(value):notes.append(value)
            def cleanup_fail():raise cleanup_fault
            setup='async fn(borrowed,note,cleanup_fail){try{note("prefix");await borrowed;note("forbidden-suffix")}finally{tool("cleanup1",1);tool("cleanup2",2);'+('cleanup_fail()'if override else'42')+'}}'
            f=streams.compile_choice(e,e.run(setup),target)
            original_leave=capture_frames.leave
            def leave(frame,value):
                if frame is not None and id(frame)in tracked:
                    self.assertFalse(any(old is frame for old in leaves));leaves.append(frame)
                return original_leave(frame,value)
            with active(e)as holder,streams.observe_states()as states,patch.object(capture_frames,'leave',leave):
                def cold():
                    notes.append('borrowed-started')
                    if False:yield None
                    return 99
                borrowed=e.suspension.task(cold());before_borrowed=(borrowed.state,borrowed.runner.started,borrowed.cancel_event.is_set(),tuple(borrowed.cancel_sources))
                before=(list(e.frames),e.depth,e.current_span,getattr(holder,'module_scope',None),getattr(holder,'debug_context',None),list(holder.capture_frames))
                cursor=ensure_cursor(e,e._s_invoke(f,[borrowed,Native('note',note,e,captures=()),Native('cleanup_fail',cleanup_fail,e,captures=())],force=True))
                try:
                    request=pending(cursor);self.assertIs(type(request),suspension.Await);self.assertIs(request.task,borrowed);self.assertEqual(notes,['prefix'])
                    boundaries=tuple(states[0].frames)if states else();captures=tuple(frame.capture for frame in boundaries);tracked.update(id(x)for x in captures)
                    if states:
                        self.assertEqual(len(states),1);state=states[0];state.validate(full=True)
                        self.assertIs(state.pending_cursor,cursor);self.assertIs(state.pending_request,request)
                        self.assertTrue(any(row[0]=='protect'for row in state.program.instructions))
                        self.assertTrue(all(type(frame)is EvaluationBoundaryFrame and frame.entered for frame in boundaries))
                        if mode=='container':state.frames=foreign
                        elif mode=='row':state.frames[0]=foreign
                    try:
                        yielded=cursor.throw(fault)
                        while True:
                            if yielded is suspension.CHECKPOINT:yielded=cursor.send(None);continue
                            self.assertIs(type(yielded),suspension.Effect);self.assertEqual(yielded.kind,'tool')
                            effects.append((yielded.payload['name'],yielded.payload['value']));yielded=cursor.send(yielded.payload['value'])
                    except StopIteration as done:outcome=('done',serializable(done.value),False,False,None)
                    except BaseException as error:
                        retained.extend((error,error.__traceback__))
                        outcome=('error',type(error).__name__,error is fault,error is cleanup_fault,getattr(error,'code',None))
                        if kind=='hard':self.assertIs(error,fault)
                        if kind=='ordinary'and override:self.assertIs(error,cleanup_fault)
                        elif kind=='ordinary'and mode!='valid':self.assertEqual(error.code,'ownership_error')
                    self.assertEqual(effects,[]if kind in ('hard','close')else[('cleanup1',1),('cleanup2',2)])
                    self.assertEqual(notes,['prefix']);self.assertTrue(cursor.closed);self.assertEqual(cursor.frames,[])
                    for state in states:
                        self.assertTrue(state.closed);self.assertEqual(state.frames,[]);self.assertFalse(state._owned_activations);self.assertFalse(state._transfers)
                        self.assertIsNone(state.pending_cursor);self.assertIsNone(state.pending_request)
                    self.assertTrue(all(not frame.entered for frame in boundaries));self.assertEqual({id(x)for x in leaves},tracked);self.assertEqual(len(leaves),len(tracked))
                    self.assertEqual(e.frames,before[0]);self.assertEqual(e.depth,before[1]);self.assertIs(e.current_span,before[2]);self.assertIs(getattr(holder,'module_scope',None),before[3]);self.assertIs(getattr(holder,'debug_context',None),before[4]);self.assertEqual(holder.capture_frames,before[5])
                    self.assertEqual((borrowed.state,borrowed.runner.started,borrowed.cancel_event.is_set(),tuple(borrowed.cancel_sources)),before_borrowed)
                    cursor.close();self.assertEqual(notes,['prefix']);self.assertEqual(len(leaves),len(tracked))
                    return outcome,effects
                finally:cursor.close()
        for kind in ('ordinary','hard','close'):
            for override in (False,True):
                with self.subTest(kind=kind,override=override):
                    bare=run('raw','valid',kind,override)
                    for target in ('interpreter','portable'):
                        self.assertEqual(run(target,'valid',kind,override),bare)
                    for mode in ('container','row'):
                        actual=run('portable',mode,kind,override)
                        if kind in ('hard','close')or override:self.assertEqual(actual,bare)
