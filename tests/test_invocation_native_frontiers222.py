"""Keep the actual native dispatch frontier alive after its outer owner closes."""
import copy,json,unittest
from contextlib import nullcontext
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError
from chaos.execution_frames import ensure_cursor,InvocationCloseCursor,MultipleValuesCloseCursor,ProtocolBridge
from chaos.execution_invocation import InvocationDispatchFrame
from chaos.suspension import Effect,CHECKPOINT
import chaos.control_prompts as control
import _invocation_fixture222 as fixture
from _invocation_reference222 import reference
from test_operator_execution_frames import active

class InvocationNativeFrontiers222(unittest.TestCase):
    def test_nested_MV_keeps_original_pending_cursor_and_separate_dispatch_cursor(self):
        source='async fn work(){multiple_value_list(fn(){tool("nested",40);return_values(20,22)})};let task=work();remember222(task,work);await task'
        for legacy in (True,False):
            witnessed=[]
            def inspect(e,task,target,old):
                todo=[task.runner.iterator];visited=set();pairs=[]
                while todo:
                    cursor=todo.pop();self.assertNotIn(id(cursor),visited);visited.add(id(cursor))
                    for bridge in cursor.frames:
                        pending=getattr(bridge,'pending_cursor',None);dispatch=getattr(bridge,'dispatch_cursor',None)
                        for slot,child in (('pending_cursor',pending),('dispatch_cursor',dispatch)):
                            if child is not None:
                                self.assertIs(child.owner,e);self.assertIs(child.parent_bridge,bridge);self.assertIs(getattr(bridge,slot),child);todo.append(child)
                        if type(pending)is MultipleValuesCloseCursor and type(dispatch)is InvocationCloseCursor:
                            self.assertIsNot(pending,dispatch);self.assertIs(type(dispatch.frames[0]),InvocationDispatchFrame)
                            self.assertIs(dispatch.frames[0].owner,e);pairs.append((bridge,pending,dispatch))
                self.assertEqual(bool(pairs),not old)
                witnessed.extend(pairs)
            row,seen,_=fixture.run(self,source,legacy=legacy,pending_check=inspect)
            self.assertEqual(row[0],('value',[20,22]));self.assertEqual(len(row[2]),1)
            if legacy:golden=row
            else:self.assertEqual(row,golden);self.assertTrue(witnessed)
    def test_real_native_dispatch_second_close_send_throw_after_delegate_close(self):
        retained=[];diagnostics=[]
        for failure_kind in ('stop','generator_exit','runtime'):
            for second in ('close','send','throw'):
                with self.subTest(failure=failure_kind,second=second):
                    rows=[]
                    for legacy in (True,False):
                        e=Evaluator();events=[];held=[];errors=[]
                        after=Effect('tool',{'tool':'after-close','value':42})
                        cause=ValueError('native delegate cause')
                        fault=StopIteration(after)if failure_kind=='stop'else GeneratorExit('native delegate exit')if failure_kind=='generator_exit'else RuntimeError('native delegate failure')
                        fault.__cause__=cause;resumed=LanguageError('native resumed',code='native_resume')
                        class RawChild:
                            def __iter__(self):return self
                            def __next__(self):return self.send(None)
                            def send(self,value):events.append(('send',value));return Effect('tool',{'tool':'native-hold','value':40})
                            def throw(self,error):events.append(('throw',type(error).__name__));raise error
                            def close(self):events.append(('close',type(fault).__name__));raise fault
                        raw=RawChild();target=Native('frontier222',lambda:control.Request('frontier',()),e)
                        def resolve(owner,request):self.assertIs(owner,e);self.assertEqual(request.operation,'frontier');events.append(('resolve',));return raw
                        def normal(value):
                            if type(value)is Effect:return('Effect',value.kind,copy.deepcopy(value.payload))
                            if hasattr(value,'cold')and hasattr(value,'value'):return(type(value).__name__,normal(value.value),value.cold)
                            return copy.deepcopy(value)
                        def observe(call):
                            returned=error=None
                            try:returned=call()
                            except BaseException as caught:error=caught;errors.append((caught,caught.__traceback__))
                            actual_cause=getattr(error,'__cause__',None)
                            return (type(error).__name__ if error else None,str(error)if error else None,getattr(error,'code',None),normal(error.value)if type(error)is StopIteration else None,error is fault,error is resumed,actual_cause is fault,actual_cause is cause,normal(returned),copy.deepcopy(events),e.steps,e.depth,len(e.frames))
                        with reference()if legacy else nullcontext(),active(e),patch.object(control,'resolve',resolve):
                            original=e._frame_invocation_dispatch
                            def recording(state):
                                iterator=original(state);held.append((state,iterator));return iterator
                            with patch.object(e,'_frame_invocation_dispatch',recording):
                                outer=ensure_cursor(e,e._s_invoke(target,[]));request=outer.send(None)
                                while request is CHECKPOINT:request=outer.send(None)
                                self.assertIs(type(request),Effect);self.assertEqual(request.payload['tool'],'native-hold')
                                matches=[(state,iterator)for state,iterator in held if state.fn is target]
                                self.assertEqual(len(matches),1);state,thin=matches[0]
                                native=thin
                                owned=None if legacy else next(bridge.dispatch_cursor for bridge in outer.frames if type(bridge)is ProtocolBridge and bridge.iterator is thin)
                                if not legacy:self.assertIs(type(owned),InvocationCloseCursor);self.assertIs(owned.frames[0].state,state)
                                # Compare the same public native generator on
                                # both sides. The actual inner close cursor is
                                # retained as an owner witness, never used to
                                # bypass a possibly closed thin wrapper.
                                retained.append((outer,thin,native,owned,raw,fault,cause,resumed,held,errors))
                                observations=[observe(outer.close)]
                                observations.append(observe(native.close if second=='close'else lambda:native.send(77)if second=='send'else native.throw(resumed)))
                                rows.append(observations)
                                self.assertTrue(outer.closed);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
                                diagnostics.append({'legacy':legacy,'failure':failure_kind,'second':second,'firstError':observations[0][0],'secondError':observations[1][0],'secondReturnedEffect':observations[1][8]==normal(after),'rawCloseCalls':sum(event[0]=='close'for event in events)})
                    self.assertEqual(rows[1],rows[0])
        print('INVOCATION_NATIVE_CLOSE222 '+json.dumps(diagnostics,sort_keys=True))
        for outer,thin,native,*rest in retained:
            for iterator in (native,thin,outer):
                try:iterator.close()
                except BaseException:pass
