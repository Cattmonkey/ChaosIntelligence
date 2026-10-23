"""A real native-close completion marker gates an unentered compiled segment."""
import unittest
from unittest.mock import patch
from chaos.language import Evaluator, LanguageError
from chaos.execution_patterns import PatternSyntaxFrame
from chaos.execution_frames import ensure_cursor, PatternCloseCursor
from chaos.suspension import Effect
from test_callable_execution_frames import pending
from test_operator_execution_frames import active
import _generator_flow208 as streams


class PatternCloseCompletion245(unittest.TestCase):
    def scenario(self,remove_completion):
        e=Evaluator(timeout=20,tool_handler=lambda name,value:value)
        function=streams.compile_choice(e,e.run('fn(){let [answer]=[0]}'),'portable')
        held=[];children=[];closed=[];markers=[];failures=[]
        class RawChild:
            def __iter__(self):return self
            def __next__(self):return self.send(None)
            def send(self,value):return Effect('tool',{'name':'native-close245','value':42})
            def throw(self,error):raise error
            def close(self):closed.append('close');raise StopIteration([42])
        original_leaf=PatternSyntaxFrame.leaf;original_step=PatternSyntaxFrame.step
        def leaf(frame,iterator):
            if frame.phase=='rhs' and not held:
                held.append(frame);children.append(iterator)
                return original_leaf(frame,RawChild())
            return original_leaf(frame,iterator)
        def step(frame,cursor,value,error):
            if held and frame is held[0] and getattr(cursor,'close_signal',None) is not None and cursor.close_completed is True:
                self.assertIs(type(cursor),PatternCloseCursor)
                self.assertIs(cursor.frames[0],frame)
                self.assertEqual(frame._flow_pattern_segment,('rhs',0))
                markers.append(True)
                if remove_completion:cursor.close_completed=False
            return original_step(frame,cursor,value,error)
        with active(e),streams.observe_states() as states,patch.object(PatternSyntaxFrame,'leaf',leaf),patch.object(PatternSyntaxFrame,'step',step):
            outer=ensure_cursor(e,e._s_invoke(function,[],force=True,all_results=True))
            self.assertIs(type(pending(outer)),Effect)
            frame=held[0];state=states[0];entry=state._event_index
            self.assertEqual(state.program.instructions[entry][0],'enter')
            self.assertEqual(state.program.instructions[entry][1][0],'list')
            self.assertFalse(any(boundary.flow_index==entry for boundary in state._owned_activations))
            failure=None
            try:outer.close()
            except BaseException as error:failure=error;failures.append((error,error.__traceback__))
            self.assertEqual(closed,['close'])
            self.assertTrue(outer.closed);self.assertEqual(outer.frames,[])
            self.assertTrue(state.closed);self.assertEqual(state.frames,[])
            self.assertEqual(state.roots(),())
            answer=frame.env.values.get('answer')
            # Keep the real cold native child through the observation, then
            # explicitly retire that test-owned alias without evaluating it.
            for child in children:child.close()
            return bool(markers),failure,answer,closed

    def test_missing_real_completion_marker_cannot_skip_structural_RHS_or_commit(self):
        normal=self.scenario(False);missing=self.scenario(True)
        if normal[0]:
            self.assertIsNone(normal[1]);self.assertEqual(normal[2],42)
            self.assertTrue(missing[0]);self.assertIsInstance(missing[1],LanguageError)
            self.assertEqual(missing[1].code,'ownership_error')
            self.assertIsNone(missing[2])
        else:
            # Host native-close versions which do not lawfully complete this
            # delegate cannot enter the opt-in path. Preserve their real error
            # frontier exactly, without treating StopIteration alone as proof.
            self.assertFalse(missing[0]);self.assertIsNotNone(normal[1])
            self.assertEqual((type(missing[1]),str(missing[1]),missing[2],missing[3]),
                             (type(normal[1]),str(normal[1]),normal[2],normal[3]))
