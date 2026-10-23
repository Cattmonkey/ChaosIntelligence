"""Actual native delegate.close boundary, using this interpreter's old oracle."""
import copy
from contextlib import nullcontext
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Parser,Cell
from chaos.execution_frames import ensure_cursor
from chaos.execution_patterns import PatternSyntaxFrame
from chaos.suspension import Effect
from _pattern_reference196 import reference
from test_operator_execution_frames import active
from test_callable_execution_frames import pending,owned_frame
from test_pattern_execution_frames import observe_pattern_entries

class PatternChildClose196(unittest.TestCase):
    def test_actual_child_close_preserves_original_pep479_and_generatorexit_consumption(self):
        sources={'match':'match 42{x=>x}','let_pattern':'let [a]=[42]','var_pattern':'var [a]=[42]','assign_pattern':'[a]=[42]'}
        retained=[]
        for kind,source in sources.items():
            for error_type in (StopIteration,GeneratorExit):
                outcomes=[]
                # The unchanged native algorithm defines the exact boundary on
                # the current interpreter. Do not assume3.9 close behavior on3.13.
                for legacy in (True,False):
                    e=Evaluator();e.env.bind('a',Cell(1,'Int')) if kind=='assign_pattern'else None
                    node=Parser(source).program()[1][0];child=node[1]if kind=='match'else node[2]if kind=='assign_pattern'else node[3]
                    sentinel=object();fault=error_type(sentinel);events=[];held=[]
                    class RawChild:
                        def __iter__(self):return self
                        def __next__(self):return self.send(None)
                        def send(self,value):events.append(('send',value));return Effect('tool',{'name':'raw-child','value':42})
                        def throw(self,error):raise error
                        def close(self):events.append(('close',error_type.__name__));raise fault
                    raw=RawChild();original=e._s_evaluate
                    def evaluate(actual,env,*args,**kwargs):
                        events.append(('evaluate',actual[0],actual is child))
                        return raw if actual is child else original(actual,env,*args,**kwargs)
                    with self.subTest(kind=kind,error=error_type.__name__,legacy=legacy),active(e,None),reference(retained=held)if legacy else nullcontext()as reached,observe_pattern_entries()as steps,patch.object(e,'_s_evaluate',evaluate):
                        cursor=ensure_cursor(e,e._s_execute(node,e.env));self.assertIsInstance(pending(cursor),Effect)
                        if legacy:self.assertGreater(reached[kind],0);self.assertEqual(steps,{})
                        else:self.assertIs(owned_frame(self,cursor,PatternSyntaxFrame).node,node);self.assertGreater(steps.get('PatternSyntaxFrame',0),0)
                        closes=[];errors=[]
                        for _ in range(2):
                            error=None;returned=None
                            try:returned=cursor.close()
                            except BaseException as caught:error=caught;errors.append((caught,caught.__traceback__))
                            # Record before assertions: a genuine negative must
                            # retain both observations, not produce IndexError.
                            cause=getattr(error,'__cause__',None)
                            closes.append({'errorType':type(error).__name__ if error is not None else None,
                                           'errorMessage':str(error)if error is not None else None,
                                           'causeType':type(cause).__name__ if cause is not None else None,
                                           'causeIsOriginalFault':cause is fault,
                                           'causeValueIsOriginalSentinel':getattr(cause,'value',None)is sentinel,
                                           'returned':returned,'events':copy.deepcopy(events),
                                           'steps':e.steps,'depth':e.depth,'frames':len(e.frames),
                                           'closed':cursor.closed,'cursorFrames':len(cursor.frames),
                                           'aExists':'a'in e.env.values,
                                           'aValue':e.env.values['a'].value if kind=='assign_pattern'else e.env.values.get('a')})
                            if cause is not None:self.assertIs(cause,fault)
                        outcomes.append(closes)
                        self.assertEqual(sum(v[0]=='close'for v in events),1)
                        self.assertTrue(cursor.closed);self.assertEqual(cursor.frames,[])
                        if legacy:
                            self.assertTrue(held);self.assertTrue(all(iterator.gi_code is code for _,iterator,code in held))
                        retained.append((cursor,raw,fault,errors,held))
                self.assertEqual(len(outcomes),2)
                with self.subTest(kind=kind,error=error_type.__name__,comparison='modern_vs_current_interpreter_original'):
                    self.assertEqual(outcomes[1],outcomes[0])
        # Explicitly retire any strongly retained original generator left at an
        # ignored-close frontier, after preserving the observable two-close data.
        for cursor,raw,fault,errors,held in retained:
            for proxy,iterator,code in held:
                if iterator.gi_frame is not None:
                    try:iterator.close()
                    except BaseException as error:errors.append((error,error.__traceback__))
