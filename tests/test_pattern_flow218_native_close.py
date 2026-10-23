"""Actual compiled-pattern native close frontier, with the bare route first.

Only the selected original leaf's delegate is replaced by a hostile iterator.
The real invocation cursor, PatternCloseCursor, public native generator and
second-close/send/throw transport remain unchanged and strongly retained.
"""
import copy
import unittest
from unittest.mock import patch
from chaos.language import Evaluator, Cell, LanguageError
from chaos.execution_patterns import PatternSyntaxFrame
from chaos.execution_frames import ensure_cursor, PatternCloseCursor
from chaos.suspension import Effect
from test_callable_execution_frames import pending
from test_operator_execution_frames import active
import _generator_flow208 as streams


class PatternFlowNativeClose218(unittest.TestCase):
    def compare(self, scenarios):
        retained=[]
        for kind,source,phase,completion,failure_kind in scenarios:
            for second in ('close','send','throw'):
                expected=None
                for target in streams.targets():
                    with self.subTest(kind=kind,phase=phase,completion=completion,failure=failure_kind,second=second,target=target):
                        e=Evaluator(timeout=30,tool_handler=lambda n,v:v)
                        if kind=='assign_pattern':e.env.bind('a',Cell(1,'Int'))
                        function=streams.compile_choice(e,e.run('fn(){'+source+'}'),target)
                        base=e.steps;events=[];frames=[];native=[];children=[];errors=[]
                        cause=ValueError('original-pattern-close-cause218')
                        fault=StopIteration(copy.deepcopy(completion))if failure_kind=='stop'else RuntimeError('raw-pattern-close218')
                        if failure_kind=='runtime':fault.__cause__=cause
                        resumed_fault=LanguageError('second-native-resume218',code='second-native-resume218')
                        class RawChild:
                            def __iter__(self):return self
                            def __next__(self):return self.send(None)
                            def send(self,value):events.append(('raw-send',value));return Effect('tool',{'name':'raw-pattern-pending218','value':42})
                            def throw(self,error):raise error
                            def close(self):events.append(('raw-close',type(fault).__name__));raise fault
                        raw=RawChild();original_leaf=PatternSyntaxFrame.leaf;original_execute=e._s_execute
                        def leaf(frame,iterator):
                            if frame.kind==kind and frame.phase==phase and not frames:
                                frames.append(frame);children.append(iterator)
                                events.append(('selected-leaf',phase))
                                return original_leaf(frame,raw)
                            return original_leaf(frame,iterator)
                        def execute(node,env,*args,**kwargs):
                            result=original_execute(node,env,*args,**kwargs)
                            if node[0]in PatternSyntaxFrame.KINDS:native.append(result)
                            return result
                        with active(e),streams.observe_states()as states,patch.object(PatternSyntaxFrame,'leaf',leaf),patch.object(e,'_s_execute',execute):
                            outer=ensure_cursor(e,e._s_invoke(function,[],force=True,all_results=True))
                            request=pending(outer)
                            self.assertIs(type(request),Effect);self.assertEqual(request.payload['name'],'raw-pattern-pending218')
                            self.assertEqual(len(frames),1);frame=frames[0];self.assertEqual(len(native),1)
                            public_native=native[0]
                            # The actual native close cursor is held by the real
                            # outer bridge, not a reconstructed test cursor.
                            owned=[getattr(row,'pending_cursor',None)for row in outer.frames]
                            close_cursor=next((row for row in owned if type(row)is PatternCloseCursor),None)
                            self.assertIsNotNone(close_cursor);self.assertIs(close_cursor.frames[0],frame)
                            self.assertIs(close_cursor.owner,e);self.assertIs(close_cursor.ambient_cursor,None)
                            if target in('raw','interpreter'):self.assertEqual(states,[])
                            else:
                                self.assertEqual(len(states),1);state=states[0];state.validate(full=True)
                                self.assertIs(state._cursor,outer);self.assertIs(state.pending_cursor,outer)
                                self.assertIs(frame._flow_boundary.flow_state,state)
                                self.assertEqual(frame._flow_pattern_segment,(phase,frame.index-1 if phase in('guard','body')else 0))
                                program=function.compiled_plan.flow
                                self.assertTrue(any(row[0]=='pattern'and row[1]is frame.node for row in program.instructions))
                                self.assertGreater(program.resume_calls,0)
                                self.assertGreater(program.native_resumes if target=='native'else program.portable_resumes,0)
                            observations=[]
                            for index in range(2):
                                error=None;returned=None
                                try:
                                    if index==0:returned=outer.close()
                                    elif second=='close':returned=public_native.close()
                                    elif second=='send':returned=public_native.send(42)
                                    else:returned=public_native.throw(resumed_fault)
                                except BaseException as caught:error=caught;errors.append((caught,caught.__traceback__))
                                actual_cause=getattr(error,'__cause__',None)
                                cell=(e.env.resolve_cell('a')if kind=='assign_pattern'else frame.env.values.get('a'))
                                observations.append({'index':index,'errorType':type(error).__name__ if error is not None else None,
                                    'errorMessage':str(error)if error is not None else None,'errorCode':getattr(error,'code',None),
                                    'errorIsFault':error is fault,'errorIsResumeFault':error is resumed_fault,
                                    'causeIsFault':actual_cause is fault,'causeIsOriginalCause':actual_cause is cause,
                                    'stopValue':copy.deepcopy(error.value)if type(error)is StopIteration else None,
                                    'returned':returned,'events':copy.deepcopy(events),'rawCloseCount':sum(row[0]=='raw-close'for row in events),
                                    'aExists':cell is not None,'aIsCell':type(cell)is Cell,'aValue':copy.deepcopy(cell.value if type(cell)is Cell else cell),
                                    'steps':e.steps-base,'depth':e.depth,'frames':len(e.frames),'outerClosed':outer.closed,
                                    'nativeClosed':public_native.gi_frame is None})
                            retained.append((e,outer,public_native,close_cursor,raw,fault,cause,resumed_fault,errors,children,frames,states))
                            self.assertEqual(observations[0]['rawCloseCount'],1)
                            if failure_kind=='runtime':
                                self.assertTrue(observations[0]['errorIsFault']);self.assertTrue(observations[0]['causeIsOriginalCause'])
                            if kind!='match'and observations[0]['errorType']is None:
                                self.assertTrue(observations[0]['aExists']);self.assertEqual(observations[0]['aValue'],42)
                                self.assertEqual(observations[0]['aIsCell'],kind!='let_pattern')
                            self.assertTrue(outer.closed);self.assertEqual(outer.frames,[])
                            for state in states:
                                self.assertTrue(state.closed);self.assertEqual(state.frames,[]);self.assertFalse(state._owned_activations)
                                self.assertFalse(state._transfers);self.assertIsNone(state.pending_request);self.assertIsNone(state.pending_cursor)
                                self.assertEqual(state.roots(),())
                            if expected is None:expected=observations
                            else:self.assertEqual(observations,expected)
                            # Retain both real frontiers until all observations;
                            # explicit final cleanup is after the comparison.
                            try:public_native.close()
                            except BaseException as cleanup:errors.append((cleanup,cleanup.__traceback__))
                            outer.close()

    def test_lawful_native_leaf_completion_commits_and_keeps_two_resume_frontiers(self):
        self.compare([
            ('let_pattern','let [a]=[0]','rhs',[42],'stop'),
            ('var_pattern','var [a]=[0]','rhs',[42],'stop'),
            ('assign_pattern','[a]=[0]','rhs',[42],'stop'),
            ('match','match 0{x=>tool("chosen",x)}','subject',42,'stop'),
            ('match','match 42{x if true=>tool("chosen",x);_=>tool("fallback",0)}','guard',True,'stop'),
            ('match','match 42{x if true=>tool("chosen",x);_=>tool("fallback",0)}','guard',False,'stop'),
            ('match','match 42{x if true=>0;_=>0}','guard',1,'stop'),
            ('match','match 42{x=>0}','body',42,'stop'),
        ])

    def test_original_close_error_and_cause_survive_second_close_send_throw(self):
        self.compare([('match','match 42{x if true=>0;_=>0}','guard',None,'runtime')])
