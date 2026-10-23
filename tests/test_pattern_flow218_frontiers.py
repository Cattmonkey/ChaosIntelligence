"""Original pattern boundaries under real host failure, poisoned metadata and soft close."""
import unittest
from unittest.mock import patch
from chaos import suspension,capture_frames
from chaos.language import Evaluator,Native,LanguageError,ResourceLimit,ExecutionControl,serializable
from chaos.execution_frames import EvaluationBoundaryFrame,ensure_cursor
from chaos.execution_patterns import PatternSyntaxFrame
from test_callable_execution_frames import owned_frame
from test_operator_execution_frames import active
from test_callable_execution_frames import pending
import _generator_flow208 as streams
import _pattern_flow218 as fixture

class PatternFrontiers218(unittest.TestCase):
    def test_original_match_host_error_identity_cause_and_hard_abort_keep_cleanup_priority(self):
        retained=[]
        for fault_type in (LanguageError,ResourceLimit,ExecutionControl,GeneratorExit,StopIteration,RuntimeError):
            rows=[]
            for target in fixture.TARGETS():
                effects=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:effects.append((n,v))or v)
                cause=ValueError('pattern-cause218');fault=fault_type('pattern-fault218');fault.__cause__=cause;retained.extend((fault,cause))
                def fail():raise fault
                def choose(f):return streams.compile_choice(e,f,target)
                e.env.bind('fail',Native('fail',fail,e,captures=()));e.env.bind('choose',Native('choose',choose,e,captures=()))
                source='let f=choose(fn(){try{match 0{_=>fail()}}finally{tool("exit1",1);tool("exit2",2)}});f()'
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

    def test_pending_pattern_body_scope_and_definition_faults_unwind_original_cleanup_once(self):
        foreign=Evaluator().run('fn foreign(){0}');retained=[]
        def run(target,mode,kind,override):
            notes=[];effects=[];leaves=[];tracked=set();e=Evaluator(timeout=30,step_limit=30000,tool_handler=lambda n,v:v)
            cleanup_fault=LanguageError('cleanup-new218',code='cleanup-new218');fault=ResourceLimit('hard218')if kind=='hard'else GeneratorExit('native-close218')if kind=='close'else LanguageError('incoming218',code='incoming218')
            retained.extend((fault,cleanup_fault))
            def cleanup_fail():raise cleanup_fault
            source='async fn(borrowed,note,cleanup_fail){try{match 0{_=>do{note("prefix");await borrowed;note("forbidden")}}}finally{tool("exit1",1);tool("exit2",2);'+('cleanup_fail()'if override else'null')+'}}'
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
                    pattern=owned_frame(self,cursor,PatternSyntaxFrame);self.assertEqual(pattern.phase,'body')
                    boundaries=tuple(states[0].frames)if states else();tracked.update(id(frame.capture)for frame in boundaries)
                    if states:
                        self.assertEqual(len(states),1);state=states[0];state.validate(full=True);self.assertIs(state.pending_cursor,cursor);self.assertIs(state.pending_request,request);self.assertTrue(fixture.structural(state.program))
                        self.assertTrue(all(type(frame)is EvaluationBoundaryFrame and frame.entered for frame in boundaries))
                        if mode=='container':state.frames=foreign
                        elif mode=='row':state.frames[0]=foreign
                        elif mode=='definition':f.body=replacement.body
                        elif mode=='scope':pattern.scope=foreign.env
                        elif mode=='body':pattern.body=replacement.body
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
                    self.assertEqual(effects,[]if kind in ('hard','close')else[('exit1',1),('exit2',2)]);self.assertEqual(notes,['prefix'])
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

    def test_held_effect_definition_change_observes_atomic_frontier_without_prefix_replay(self):
        from _effect_schedule158 import effect_schedule
        diagnostic=[]
        for target in ('raw','portable'):
            effects=[];functions=[];tasks=[];seen=[];mutated=[]
            fault=LanguageError('definition-frontier218',code='definition_changed')
            def handler(kind,payload,event):
                effects.append(payload['name'])
                if target=='raw'and payload['name']=='rhs_hold':raise fault
                return payload['value']
            e=Evaluator(timeout=30,step_limit=30000,tool_handler=lambda n,v:v,effect_handler=handler)
            replacement=e.run('fn replacement(){tool("new-body-forbidden",0)}')
            def choose(f):answer=streams.compile_choice(e,f,target);functions.append(answer);return answer
            e.env.bind('choose',Native('choose',choose,e,captures=()));e.env.bind('remember',Native('remember',tasks.append,e,captures=()))
            source='var left:Int=1;var right:Int=2;let f=choose(async fn(){try{[left,right]=tool("rhs_hold",[20,22]);await sleep(0);tool("suffix-forbidden",0)}finally{tool("cleanup1",1);tool("cleanup2",2)}});let task=f();remember(task);spawn(task);await task'
            with streams.observe_states()as states,effect_schedule('first_wait')as schedule:
                wait=suspension.wait
                def waiting(futures,*args,**kwargs):
                    futures=tuple(futures)
                    try:
                        task=tasks[0];future=task.runner.future
                        if future is not None and future in futures and future not in seen:
                            seen.append(future);self.assertFalse(future.done())
                            if not mutated:
                                mutated.append(True);self.assertEqual([functions[0].env.get('left'),functions[0].env.get('right')],[1,2])
                                if target!='raw':functions[0].body=replacement.body
                    finally:answer=wait(futures,*args,**kwargs)
                    return answer
                with patch.object(suspension,'wait',waiting):
                    try:e.run(source)
                    except LanguageError as error:
                        self.assertEqual(error.code,'definition_changed')
                        if target=='raw':self.assertIs(error,fault)
                    else:self.fail('definition mutation was not rejected at its explicit subsequent Await')
                cells=[functions[0].env.get('left'),functions[0].env.get('right')];self.assertIn(cells,([1,2],[20,22]))
                self.assertEqual(effects,['rhs_hold','cleanup1','cleanup2']);self.assertEqual(schedule['waitBoundaryReleases'],3);self.assertEqual(schedule['shutdownReleases'],0)
                self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0);self.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers for s in states))
                if target!='raw':self.assertTrue(states);self.assertTrue(fixture.structural(states[0].program))
                diagnostic.append({'target':target,'actualCellsAfterDefinitionFrontier':cells,'effects':effects,'steps':e.steps,'definitionChangedErrorObserved':True,'rawInjectedReferenceError':target=='raw','functionDefinitionMutated':target!='raw'})
        import json
        print('PATTERN_EFFECT_DEFINITION_FRONTIER218 '+json.dumps(diagnostic,sort_keys=True))

    def test_guard_await_future_body_and_scope_are_checked_when_selected_without_replay(self):
        # Validate the future node at selection. A foreign callable is instead
        # checked by the original invocation admission, compared raw-first.
        from chaos.language import Env
        alien=Evaluator();foreign_note=Native('note',lambda value:self.fail('foreign callback executed'),alien,captures=())
        for mode in ('body','scope'):
            reference=None
            for target in (('portable',)if mode=='body'else('raw','portable')):
                with self.subTest(mode=mode,target=target):
                    notes=[];effects=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:v)
                    f=streams.compile_choice(e,e.run('async fn(child,note){try{match 0{_ if do{note("guard");await child}=>do{note("forbidden");42}}}finally{tool("cleanup1",1);tool("cleanup2",2)}}'),target)
                    replacement=e.run('fn replacement(){999}')
                    with active(e),streams.observe_states()as states:
                        def cold():
                            if False:yield None
                            return True
                        borrowed=e.suspension.task(cold());cursor=ensure_cursor(e,e._s_invoke(f,[borrowed,Native('note',notes.append,e,captures=())],force=True))
                        try:
                            request=pending(cursor);self.assertIs(type(request),suspension.Await);self.assertIs(request.task,borrowed);self.assertEqual(notes,['guard'])
                            frame=owned_frame(self,cursor,PatternSyntaxFrame);self.assertEqual(frame.phase,'guard')
                            if mode=='body':frame.body=replacement.body
                            else:
                                scope=Env(frame.scope.parent);scope.values.update(frame.scope.values);scope.values['note']=foreign_note;frame.scope=scope
                            with self.assertRaises(LanguageError)as caught:
                                value=cursor.send(True)
                                while True:
                                    if value is suspension.CHECKPOINT:value=cursor.send(None);continue
                                    self.assertIs(type(value),suspension.Effect);effects.append((value.payload['name'],value.payload['value']));value=cursor.send(value.payload['value'])
                            record=caught.exception.record()
                            if mode=='body':self.assertEqual(caught.exception.code,'ownership_error',repr(record))
                            elif target=='raw':
                                self.assertEqual(caught.exception.code,'runtime_error',repr(record));self.assertEqual(caught.exception.message,'可调用值不能跨执行上下文传递；请传递源代码或数据',repr(record));reference=record
                            else:self.assertEqual(record,reference,repr(record))
                            self.assertEqual(notes,['guard']);self.assertEqual(effects,[('cleanup1',1),('cleanup2',2)])
                            self.assertEqual(borrowed.state,'created');self.assertFalse(borrowed.cancel_event.is_set());self.assertTrue(cursor.closed)
                            if target=='raw':self.assertEqual(states,[])
                            else:self.assertTrue(states);self.assertTrue(all(s.closed and not s.frames and not s._owned_activations and not s._transfers for s in states))
                        finally:cursor.close()
