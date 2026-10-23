"""Actual native close frontiers; no Python-version or message normalization."""
import copy
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError
from chaos.suspension import Effect,Await,Yield,Task,CHECKPOINT
from chaos.execution_frames import ExecutionCursor,EvaluationRequest,InvocationRequest,ProtectedValueRequest,gateway
from _suspension_reference201 import reference
from test_suspension_syntax_frames201 import observe
from test_operator_execution_frames import active

class SuspensionNativeClose201(unittest.TestCase):
    def test_native_completion_and_raw_await_yield_frontier_survive_second_operation(self):
        held_all=[]
        for kind in ('await','yield','yield_from','throw'):
            for failure_kind in ('stop','generator_exit','runtime'):
                for second in ('close','send','throw'):
                    with self.subTest(kind=kind,failure=failure_kind,second=second):
                        outcomes=[]
                        for legacy in (True,False):
                            e=Evaluator();events=[];errors=[];held=[];children=[];resumed=LanguageError('resume',code='fixture_resume')
                            with active(e,None),reference(held)if legacy else nullcontext()as reached,observe()as seen:
                                def cold():
                                    if False:yield None
                                    return 42
                                completion=e.suspension.task(cold(),capture_roots=())if kind=='await'else[1,2]if kind=='yield_from'else{'answer':42}if kind=='throw'else 42
                                cause=ValueError('original explicit cause')
                                fault=StopIteration(completion)if failure_kind=='stop'else GeneratorExit('raw')if failure_kind=='generator_exit'else RuntimeError('raw failure')
                                if failure_kind=='runtime':fault.__cause__=cause
                                operand=('literal',0);node=(kind,operand)
                                class RawChild:
                                    def __iter__(self):return self
                                    def __next__(self):return self.send(None)
                                    def send(self,value):events.append(('raw.send',value));return Effect('tool',{'name':'raw-hold','value':42})
                                    def throw(self,error):raise error
                                    def close(self):events.append(('raw.close',type(fault).__name__));raise fault
                                raw=RawChild();original=e._s_evaluate
                                def evaluate(actual,env,*args,**kwargs):
                                    events.append(('evaluate',actual[0],actual is operand))
                                    iterator=raw if actual is operand else original(actual,env,*args,**kwargs);children.append(iterator);return iterator
                                with patch.object(e,'_s_evaluate',evaluate):
                                    iterator=e._s_execute(node,e.env);native=iterator.iterator if legacy else iterator
                                    held_all.append((iterator,native,raw,fault,cause,errors,held,children,seen))
                                    request=iterator.send(None);self.assertIs(type(request),Effect);self.assertEqual(request.payload['name'],'raw-hold')
                                    if legacy:self.assertGreater(reached[kind],0);self.assertEqual(seen['steps'],{});self.assertTrue(any(actual is native and actual.gi_code is code for _,actual,code in held))
                                    else:self.assertGreater(seen['steps'].get(kind,0),0);self.assertEqual(seen['frames'][-1][0].phase,'operand')
                                    def normal(value):
                                        if type(value)is Await:return('Await',value.task is completion,value.task.id,value.task.state)
                                        if type(value)is Yield:return('Yield',normal(value.value))
                                        if type(value)is Effect:return('Effect',value.kind,copy.deepcopy(value.payload))
                                        if type(value)is Task:return('Task',value is completion,value.id,value.state)
                                        return copy.deepcopy(value)
                                    rows=[]
                                    for index in range(2):
                                        error=None;returned=None
                                        try:
                                            returned=iterator.close()if index==0 or second=='close'else iterator.send(77)if second=='send'else iterator.throw(resumed)
                                        except BaseException as caught:error=caught;errors.append((caught,caught.__traceback__))
                                        actual_cause=getattr(error,'__cause__',None)
                                        rows.append({'errorType':type(error).__name__ if error else None,'message':str(error)if error else None,'code':getattr(error,'code',None),'value':normal(error.value)if type(error)is StopIteration else None,'isRawFault':error is fault,'isResumeFault':error is resumed,'causeType':type(actual_cause).__name__ if actual_cause else None,'causeIsRaw':actual_cause is fault,'causeIsExplicit':actual_cause is cause,'returned':normal(returned),'events':copy.deepcopy(events),'steps':e.steps,'depth':e.depth,'frames':len(e.frames),'nativeClosed':native.gi_frame is None})
                                    outcomes.append(rows)
                                    if failure_kind=='runtime':self.assertTrue(rows[0]['isRawFault']);self.assertTrue(rows[0]['causeIsExplicit'])
                        self.assertEqual(outcomes[0],outcomes[1])
        for iterator,native,raw,fault,cause,errors,held,children,seen in held_all:
            if native.gi_frame is not None:
                try:iterator.close()
                except BaseException as error:errors.append((error,error.__traceback__))

    def test_rethrow_preserves_exact_error_cause_and_cold_stopiteration_boundary(self):
        for original_error in (LanguageError('same',code='same_error'),RuntimeError('same'),StopIteration(42),GeneratorExit('same')):
            rows=[]
            for legacy in (True,False):
                e=Evaluator();held=[];cause=ValueError('cause');original_error.__cause__=cause
                with active(e,None),reference(held)if legacy else nullcontext():
                    e.handled_error=original_error;iterator=e._s_execute(('rethrow',),e.env)
                    try:iterator.send(None)
                    except BaseException as error:rows.append((type(error).__name__,str(error),error is original_error,error.__cause__ is original_error,error.__cause__ is cause,e.steps))
                    else:self.fail('rethrow cannot silently complete')
            self.assertEqual(rows[0],rows[1])
