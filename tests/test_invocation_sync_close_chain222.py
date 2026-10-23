"""A later synchronous Native leaf must feed the same public close transport."""
import copy,json,sys,unittest
from contextlib import nullcontext
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError
from chaos.execution_frames import ensure_cursor,InvocationCloseCursor,ProtocolBridge
from chaos.suspension import Effect,CHECKPOINT
import chaos.control_prompts as control
import chaos.conditions as conditions
from _invocation_reference222 import reference
from test_operator_execution_frames import active

class InvocationSynchronousCloseChain222(unittest.TestCase):
    def test_control_close_completion_then_sync_recovery_leaf_reaches_original_effect_frontier(self):
        retained=[];diagnostics=[]
        for completion_mode in ('close','send'):
            for second in ('send','throw','close'):
                with self.subTest(completion=completion_mode,second=second):
                    rows=[]
                    for legacy in (True,False):
                        e=Evaluator();events=[];held=[];errors=[]
                        after=Effect('tool',{'name':'sync-after-close','value':42})
                        request=conditions.Request('fixture-sync-recovery',40)
                        cause=ValueError('control delegate original cause')
                        fault=StopIteration(request);fault.__cause__=cause
                        resumed=LanguageError('chain resumed',code='chain_resume')
                        class RawChild:
                            def __init__(self):self.sends=0
                            def __iter__(self):return self
                            def __next__(self):return self.send(None)
                            def send(self,value):
                                self.sends+=1;events.append(('raw-send',self.sends,value))
                                if self.sends==1:return Effect('tool',{'name':'control-chain-hold','value':40})
                                raise StopIteration(request)
                            def throw(self,error):events.append(('raw-throw',type(error).__name__));raise error
                            def close(self):events.append(('raw-close',));raise fault
                        raw=RawChild();target=Native('chain222',lambda:control.Request('fixture-control-chain',()),e)
                        def control_leaf(owner,value):
                            self.assertIs(owner,e);self.assertEqual(value.operation,'fixture-control-chain')
                            events.append(('control-resolve',));return raw
                        def recovery_leaf(owner,value):
                            self.assertIs(owner,e);self.assertIs(value,request)
                            events.append(('recovery-resolve',))
                            if not legacy:
                                state,thin=held[0]
                                cursor=owned
                                self.assertIs(cursor.parent_bridge,dispatch_bridge)
                                self.assertIs(dispatch_bridge.iterator,thin)
                                self.assertIs(dispatch_bridge.dispatch_cursor,owned)
                                self.assertIs(type(cursor),InvocationCloseCursor)
                                self.assertIs(cursor.frames[0].state,state)
                                self.assertEqual(cursor.frames[0].native_stage,9,'Recovery branch must be entered after Control, not restarted')
                            if False:yield None
                            return after
                        def normal(value):
                            if type(value)is Effect:return('Effect',value.kind,copy.deepcopy(value.payload))
                            if hasattr(value,'cold')and hasattr(value,'value'):return(type(value).__name__,normal(value.value),value.cold)
                            return copy.deepcopy(value)
                        def observe(call):
                            value=error=None
                            try:value=call()
                            except BaseException as caught:error=caught;errors.append((caught,caught.__traceback__))
                            actual_cause=getattr(error,'__cause__',None)
                            return {'errorType':type(error).__name__ if error else None,'errorMessage':str(error)if error else None,'errorCode':getattr(error,'code',None),'stopValue':normal(error.value)if type(error)is StopIteration else None,'errorIsDelegateFault':error is fault,'errorIsResumeFault':error is resumed,'causeIsDelegateFault':actual_cause is fault,'causeIsExplicitCause':actual_cause is cause,'returned':normal(value),'events':copy.deepcopy(events),'steps':e.steps,'depth':e.depth,'frameCount':len(e.frames)}
                        with reference()if legacy else nullcontext(),active(e),patch.object(control,'resolve',control_leaf),patch.object(conditions,'resolve',recovery_leaf):
                            original=e._frame_invocation_dispatch
                            def recording(state):
                                thin=original(state)
                                if state.fn is target:held.append((state,thin))
                                return thin
                            with patch.object(e,'_frame_invocation_dispatch',recording):
                                outer=ensure_cursor(e,e._s_invoke(target,[]));pending=outer.send(None)
                                while pending is CHECKPOINT:pending=outer.send(None)
                                self.assertIs(type(pending),Effect);self.assertEqual(pending.payload['name'],'control-chain-hold')
                                self.assertEqual(len(held),1);state,thin=held[0]
                                dispatch_bridge=None if legacy else next(frame for frame in outer.frames if type(frame)is ProtocolBridge and frame.iterator is thin)
                                owned=None if legacy else dispatch_bridge.dispatch_cursor
                                if not legacy:self.assertIs(type(owned),InvocationCloseCursor);self.assertIs(owned.frames[0].state,state)
                                retained.append((outer,thin,owned,raw,fault,cause,resumed,held,errors))
                                observations=[]
                                if completion_mode=='send':
                                    observations.append(observe(lambda:outer.send(0)))
                                    self.assertEqual(observations[-1]['returned'],normal(after),'both actual resolver leaves must complete to the second Effect')
                                observations.append(observe(outer.close))
                                close_recovery_calls=sum(event[0]=='recovery-resolve'for event in events)
                                self.assertEqual(sum(event[0]=='control-resolve'for event in events),1)
                                if completion_mode=='send':self.assertEqual(close_recovery_calls,1)
                                if completion_mode=='close'and close_recovery_calls:
                                    self.assertEqual(close_recovery_calls,1);self.assertIs(state.value,after)
                                    self.assertIsNotNone(thin.gi_frame,'the actual public generator must still own its next Effect')
                                    if not legacy:self.assertIs(owned.pending_request,after);self.assertTrue(owned.close_pending)
                                observations.append(observe(thin.close if second=='close'else lambda:thin.send(77)if second=='send'else thin.throw(resumed)))
                                self.assertTrue(outer.closed);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
                                rows.append((observations,copy.deepcopy(events)))
                                diagnostics.append({'hostVersion':sys.version.split()[0],'actualReached':bool(close_recovery_calls),'legacy':legacy,'completion':completion_mode,'second':second,'recoveryCallsAfterFirstClose':close_recovery_calls,'firstCloseError':observations[-2]['errorType'],'secondError':observations[-1]['errorType'],'secondStopValue':observations[-1]['stopValue']})
                    self.assertEqual(rows[1],rows[0])
        # This exposes the actual interpreter-specific close frontier. The
        # send-completion control always proves both real leaves were reached;
        # close-completion claims only what the exact original generator did.
        print('INVOCATION_SYNC_CLOSE_CHAIN222 '+json.dumps(diagnostics,sort_keys=True))
        for outer,thin,owned,*rest in retained:
            for iterator in (thin,owned,outer):
                if iterator is not None:
                    try:iterator.close()
                    except BaseException:pass
