"""Resource leases under real host failure, poisoned metadata and soft close."""
import unittest
from unittest.mock import patch
from chaos import suspension,capture_frames
from chaos.language import Evaluator,Native,LanguageError,ResourceLimit,ExecutionControl,serializable
from chaos.execution_frames import EvaluationBoundaryFrame,ensure_cursor
from chaos.execution_resources import ContextFrame
from test_operator_execution_frames import active
from test_callable_execution_frames import pending
import _generator_flow208 as streams
import _resource_flow217 as fixture

class ResourceFrontiers217(unittest.TestCase):
    def test_host_error_identity_cause_and_hard_abort_use_original_exit_frontier(self):
        retained=[]
        for fault_type in (LanguageError,ResourceLimit,ExecutionControl,GeneratorExit,StopIteration,RuntimeError):
            rows=[]
            for target in fixture.TARGETS():
                effects=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:effects.append((n,v))or v)
                cause=ValueError('resource-cause217');fault=fault_type('resource-fault217');fault.__cause__=cause;retained.extend((fault,cause))
                def fail():raise fault
                def choose(f):return streams.compile_choice(e,f,target)
                e.env.bind('fail',Native('fail',fail,e,captures=()));e.env.bind('choose',Native('choose',choose,e,captures=()))
                source='class C{fn __enter__(s){0}fn __exit__(s,error){tool("exit1",1);tool("exit2",2);null}};let f=choose(fn(){with C() as value{fail()}});f()'
                with streams.observe_states()as states:
                    try:outer=('value',serializable(e.run(source)))
                    except BaseException as error:
                        retained.extend((error,error.__traceback__));outer=('error',type(error).__name__,str(error),error is fault,error.__cause__ is fault,error.__cause__ is cause)
                self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0);self.assertIsNone(e.handled_error);self.assertIsNone(e.error_context)
                if fault_type in (ResourceLimit,ExecutionControl,GeneratorExit):self.assertEqual(effects,[])
                elif fault_type is LanguageError:self.assertEqual(effects,[('exit1',1),('exit2',2)])
                if target not in ('raw','interpreter'):
                    self.assertTrue(states);self.assertTrue(fixture.structural(states[0].program))
                    self.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers for s in states))
                else:self.assertEqual(states,[])
                rows.append((outer,effects))
            self.assertTrue(all(row==rows[0]for row in rows),(fault_type,rows))

    def test_owned_resource_unwinds_once_after_container_row_or_definition_change_without_cancelling_borrowed(self):
        foreign=Evaluator().run('fn foreign(){0}');retained=[]
        def run(target,mode,kind,override):
            notes=[];effects=[];leaves=[];tracked=set();e=Evaluator(timeout=30,step_limit=30000,tool_handler=lambda n,v:v)
            cleanup_fault=LanguageError('exit-new217',code='exit-new217');fault=ResourceLimit('hard217')if kind=='hard'else GeneratorExit('native-close217')if kind=='close'else LanguageError('incoming217',code='incoming217')
            retained.extend((fault,cleanup_fault))
            def cleanup_fail():raise cleanup_fault
            source='async fn(borrowed,note,cleanup_fail){class Lease{fn __enter__(s){0}fn __exit__(s,error){tool("exit1",1);tool("exit2",2);'+('cleanup_fail()'if override else'null')+'}};with Lease() as value{note("prefix");await borrowed;note("forbidden")}}'
            f=streams.compile_choice(e,e.run(source),target);replacement=e.run('fn replacement(){999}')
            original_leave=capture_frames.leave
            def leave(frame,value):
                if frame is not None and id(frame)in tracked:self.assertFalse(any(old is frame for old in leaves));leaves.append(frame)
                return original_leave(frame,value)
            with active(e)as holder,streams.observe_states()as states,patch.object(capture_frames,'leave',leave):
                def cold():
                    notes.append('borrowed-started')
                    if False:yield None
                    return 99
                borrowed=e.suspension.task(cold());before_borrowed=(borrowed.state,borrowed.runner.started,borrowed.cancel_event.is_set(),tuple(borrowed.cancel_sources))
                before=(list(e.frames),e.depth,e.current_span,getattr(holder,'module_scope',None),getattr(holder,'debug_context',None),list(holder.capture_frames))
                cursor=ensure_cursor(e,e._s_invoke(f,[borrowed,Native('note',notes.append,e,captures=()),Native('cleanup_fail',cleanup_fail,e,captures=())],force=True))
                try:
                    request=pending(cursor);self.assertIs(type(request),suspension.Await);self.assertIs(request.task,borrowed);self.assertEqual(notes,['prefix'])
                    resources=[frame for frame in cursor.frames if type(frame)is ContextFrame];self.assertEqual(len(resources),1);entry=resources[0].entry;self.assertIsNotNone(entry);self.assertFalse(entry.released)
                    boundaries=tuple(states[0].frames)if states else();tracked.update(id(frame.capture)for frame in boundaries)
                    if states:
                        self.assertEqual(len(states),1);state=states[0];state.validate(full=True);self.assertIs(state.pending_cursor,cursor);self.assertIs(state.pending_request,request);self.assertTrue(fixture.structural(state.program))
                        self.assertTrue(all(type(frame)is EvaluationBoundaryFrame and frame.entered for frame in boundaries))
                        if mode=='container':state.frames=foreign
                        elif mode=='row':state.frames[0]=foreign
                        elif mode=='definition':f.body=replacement.body
                    try:
                        yielded=cursor.throw(fault)
                        while True:
                            if yielded is suspension.CHECKPOINT:yielded=cursor.send(None);continue
                            self.assertIs(type(yielded),suspension.Effect);self.assertEqual(yielded.kind,'tool');effects.append((yielded.payload['name'],yielded.payload['value']));yielded=cursor.send(yielded.payload['value'])
                    except StopIteration as done:outcome=('done',serializable(done.value),False,False,None)
                    except BaseException as error:
                        retained.extend((error,error.__traceback__));outcome=('error',type(error).__name__,error is fault,error is cleanup_fault,getattr(error,'code',None))
                        if kind=='hard':self.assertIs(error,fault)
                        if kind=='ordinary'and override:self.assertIs(error,cleanup_fault)
                        elif kind=='ordinary'and mode!='valid':self.assertEqual(error.code,'definition_changed'if mode=='definition'else'ownership_error')
                    self.assertEqual(effects,[]if kind in ('hard','close')else[('exit1',1),('exit2',2)]);self.assertEqual(notes,['prefix']);self.assertTrue(entry.released)
                    self.assertTrue(cursor.closed);self.assertEqual(cursor.frames,[])
                    for state in states:
                        self.assertTrue(state.closed);self.assertEqual(state.frames,[]);self.assertFalse(state._owned_activations);self.assertFalse(state._transfers);self.assertIsNone(state.pending_cursor);self.assertIsNone(state.pending_request)
                    self.assertTrue(all(not frame.entered for frame in boundaries));self.assertEqual({id(x)for x in leaves},tracked);self.assertEqual(len(leaves),len(tracked))
                    self.assertEqual((e.frames,e.depth),(before[0],before[1]));self.assertIs(e.current_span,before[2]);self.assertIs(getattr(holder,'module_scope',None),before[3]);self.assertIs(getattr(holder,'debug_context',None),before[4]);self.assertEqual(holder.capture_frames,before[5])
                    self.assertEqual((borrowed.state,borrowed.runner.started,borrowed.cancel_event.is_set(),tuple(borrowed.cancel_sources)),before_borrowed)
                    cursor.close();self.assertEqual(notes,['prefix']);self.assertEqual(len(leaves),len(tracked));return outcome,effects
                finally:cursor.close()
        for kind in ('ordinary','hard','close'):
            for override in (False,True):
                with self.subTest(kind=kind,override=override):
                    bare=run('raw','valid',kind,override)
                    for target in ('interpreter','portable'):self.assertEqual(run(target,'valid',kind,override),bare)
                    for mode in ('container','row','definition'):
                        actual=run('portable',mode,kind,override)
                        if kind in ('hard','close')or override:self.assertEqual(actual,bare)
