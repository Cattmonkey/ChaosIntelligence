"""Original execution observation only; does not replace multiple-value semantics."""
from contextlib import contextmanager,ExitStack
from unittest.mock import patch
from chaos.execution_multiple_values import MULTIPLE_VALUE_FRAMES
from chaos.execution_frames import ExecutionCursor
SOURCE="var calls=0;var order=[];async fn work(){calls+=1;await sleep(0);return_values(20,22)};let task=work();let rejected=try{multiple_value_list(task)}catch e{e.code};let returned=multiple_value_list(fn(){task});let cold=[type_of(returned[0]),calls];let awaited=multiple_value_list(async fn(){await task});fn* stream(){yield 1;return_values(7,8)};let g=stream();next(g);next(g);async fn* astream(){yield 2;return_values(9,10)};let ag=astream();await anext(ag);await anext(ag);let joined=multiple_value_list(fn(){multiple_value_call(return_values,fn(){order=append(order,1);return_values(20,22)},fn(){order=append(order,2);return_values()})});let bound=multiple_value_bind(3,fn(){return_values(1,2)},fn(a,b,c){[a,b,c]});let kept=multiple_value_list(fn(){multiple_value_prog1(fn(){return_values(5,6)},fn(){order=append(order,3);return_values(99)})});[rejected,cold,awaited,multiple_value_list(task),multiple_value_list(g),multiple_value_list(ag),joined,nth_value(1,fn(){return_values(20,22)}),bound,kept,order,calls]"
EXPECTED=["multiple_value_state_error",["Task",0],[20,22],[20,22],[7,8],[9,10],[20,22],22,[1,2,None],[5,6],[1,2,3],1]
FRAME_NAMES={'MultipleValueReceiveFrame','MultipleValueResolveFrame'}
@contextmanager
def observe_frames():
    seen=[]
    def wrap(original,name):
        def step(frame,cursor,value,error):
            seen.append((name,frame,cursor,frame in cursor.frames))
            return original(frame,cursor,value,error)
        return step
    with ExitStack()as stack:
        for cls in MULTIPLE_VALUE_FRAMES:
            stack.enter_context(patch.object(cls,'step',wrap(cls.step,cls.__name__)))
        yield seen
def assert_frames(test,seen):
    test.assertEqual({row[0]for row in seen},FRAME_NAMES)
    test.assertTrue(all(isinstance(row[2],ExecutionCursor)and row[1].owner is row[2].owner and row[3]for row in seen))
