"""Actual public stream close through the original name-assignment leaf."""
import unittest
from unittest.mock import patch

from chaos.execution_class_creation import ClassSyntaxEvaluationFrame
import _generator_flow208 as streams


class NameAssignmentClose246(unittest.TestCase):
    def test_yielding_assignment_rhs_close_keeps_cell_and_runs_finally_once(self):
        for flavor,nextop,closeop in [('fn*','next(stream)','close(stream)'),('async fn*','await anext(stream)','await aclose(stream)')]:
            source='var retained=0;let work=choose('+flavor+'(){try{retained=do{let value=yield 7;tool("after",value);return_values(value,99)}}finally{tool("cleanup",1)}});let stream=work();mark(stream);let first='+nextop+';'+closeop+';'+closeop+';[first,'+nextop+',retained]'
            rows=[];phase_rows=[]
            for target in ('raw','portable'):
                observed=[];original=ClassSyntaxEvaluationFrame.advance
                def advance(frame,cursor,value,error):
                    if frame.node[0]=='assign' and frame.node[1]=='retained':
                        observed.append((frame.phase,frame in cursor.frames,getattr(frame,'_flow_access_region',None) is not None))
                    return original(frame,cursor,value,error)
                with self.subTest(flavor=flavor,target=target),patch.object(ClassSyntaxEvaluationFrame,'advance',advance):
                    row,states,functions,_=streams.run(source,target)
                self.assertEqual(row[0],('value',[{'done':False,'value':7},{'done':True,'value':None},0]))
                self.assertEqual(row[2],[('cleanup',1)])
                self.assertTrue(observed);self.assertEqual(observed[0],('start',True,target=='portable'))
                self.assertTrue(all(owned and compiled==(target=='portable') for phase,owned,compiled in observed))
                phase_rows.append([(phase,owned) for phase,owned,compiled in observed])
                streams.assert_compiled(self,target,states,functions)
                if target=='portable':
                    self.assertTrue(any(row[0]=='access' and row[1][0]=='assign' for function in functions for row in function.compiled_plan.flow.instructions))
                rows.append(row)
            self.assertEqual(rows[0],rows[1]);self.assertEqual(phase_rows[0],phase_rows[1])
