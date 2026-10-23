"""Exact old multiple-value algorithms versus authoritative typed receivers."""
import copy
import unittest
from contextlib import nullcontext
from unittest.mock import patch
import chaos.multiple_values as mv
from chaos.language import Evaluator,Native,LanguageError,TailCall
from chaos.suspension import Effect,CHECKPOINT
from test_operator_execution_frames import active
from _multiple_value_reference220 import reference,observe,snapshot


class MultipleValueFrontiers220(unittest.TestCase):
    def check(self,source,expected=None,**kwargs):
        raw=snapshot(source,legacy=True,**kwargs)
        modern=snapshot(source,**kwargs)
        self.assertEqual(modern[0],raw[0])
        self.assertEqual(raw[1],{})
        if raw[3]['resolve']:
            self.assertGreater(modern[1].get('MultipleValueResolveFrame',0),0)
            self.assertGreater(modern[1].get('MultipleValueReceiveFrame',0),0)
        if expected is not None:self.assertEqual(modern[0][0],('value',expected))
        return modern

    def test_all_five_receivers_zero_null_secondary_and_order(self):
        self.check('var seen=[];let result=multiple_value_list(fn(){multiple_value_call(return_values,fn(){seen=append(seen,1);return_values(1,2)},fn(){seen=append(seen,2);return_values()},fn(){seen=append(seen,3);return_values(null,4)})});[result,seen,nth_value(8,fn(){return_values(1,2)}),multiple_value_bind(3,fn(){return_values(1,2)},fn(a,b,c){[a,b,c]}),multiple_value_list(fn(){multiple_value_prog1(fn(){return_values(7,8)},fn(){tool("after",1);return_values(99)},fn(){return_values()})}),multiple_value_list(fn(){return_values()}),multiple_value_list(fn(){return_values(null)})]',[[1,2,None,4],[1,2,3],None,[1,2,None],[7,8],[],[None]])

    def test_bind_forces_context_and_call_keeps_cold_async_tail(self):
        self.check('var seen=[];let bound=multiple_value_bind(2,async fn(){await sleep(0);return_values(20,22)},async fn(a,b){seen=append(seen,"consumer");await sleep(0);a+b});let cold=multiple_value_call(async fn(*xs){seen=append(seen,"tail");xs},fn(){return_values(1,2)});let before=seen;[bound,type_of(cold),before,await cold,seen]',[42,'Task',['consumer'],[1,2],['consumer','tail']])

    def test_tail_completion_cleanup_and_errors_keep_full_original_vector(self):
        for source in (
            'fn f(n){if n==0{return_values(7,"secondary")}else{return f(n-1)}};multiple_value_list(fn(){f(250)})',
            'var e=[];multiple_value_list(fn(){try{multiple_value_prog1(fn(){return_values(1,2)},fn(){throw "stop"},fn(){e=append(e,"never")})}finally{tool("cleanup",1)}})',
            'multiple_value_bind(2,fn(){return_values(1,2)},fn(a,b){throw [a,b]})',
            'multiple_value_call(fn(a:Int){a},fn(){return_values("wrong")})'):
            with self.subTest(source=source):self.check(source)

    def test_every_secondary_is_validated_before_nth_or_zero_bind_discard(self):
        def setup(e,held):
            other=Evaluator();foreign=Native('foreign',lambda:42,other)
            held.extend((other,foreign));e.env.bind('foreign',foreign)
        for source in ('return_values(1,foreign)','nth_value(0,fn(){return_values(1,foreign)})','multiple_value_bind(0,fn(){return_values(1,foreign)},fn(){tool("forbidden",1)})','multiple_value_call(fn(*xs){tool("forbidden",1)},fn(){return_values(1,foreign)})'):
            with self.subTest(source=source):
                row=self.check(source,setup=setup)
                self.assertEqual(row[0][0][0],'error');self.assertEqual(row[0][0][1]['code'],'ownership_error');self.assertEqual(row[0][2],[])

    def test_narrow_secondary_authority_cannot_escape_projection(self):
        def setup(e,held):
            f=e.run('fn(){tool("secret",null)}');held.append(f)
            e.env.bind('locked',Native('locked',lambda:mv.Completion(e,(1,f)),e,capabilities=frozenset(),captures=(f,)))
        row=self.check('let values=multiple_value_list(locked);values[1]()',setup=setup)
        self.assertEqual(row[0][0][0],'error');self.assertEqual(row[0][0][1]['code'],'capability_error');self.assertEqual(row[0][2],[])

    def test_all_cumulative_budget_and_cancel_frontiers_match_without_counter_reset(self):
        source='var seen=[];multiple_value_list(fn(){multiple_value_call(return_values,fn(){seen=append(seen,1);tool("first",1);return_values(20,22)},fn(){seen=append(seen,2);return_values()},fn(){tool("last",2);return_values(3)})})'
        for limit in (1,32,63,64,65,127,128,129,255,511,1023,4096):
            with self.subTest(limit=limit):self.check(source,limit=limit)
        for cancel in (0,1,32,64,128,256):
            with self.subTest(cancel=cancel):self.check(source,cancel=cancel)
        row=self.check('multiple_value_bind(10001,fn(){tool("never",0)},fn(*xs){xs})')
        self.assertEqual(row[0][0][0],'error');self.assertEqual(row[0][2],[])

    def test_real_pending_args_are_authoritative_and_borrowed_handles_are_not_consumed(self):
        from chaos.execution_multiple_values import MultipleValueResolveFrame
        original=MultipleValueResolveFrame.step;changed=[]
        def step(frame,cursor,value,error):
            if frame.phase=='producer' and frame.operation=='call' and frame.index==1 and error is None:
                self.assertEqual(frame.args,[20]);frame.args[0]=40;changed.append(frame)
            return original(frame,cursor,value,error)
        with patch.object(MultipleValueResolveFrame,'step',step):
            row=snapshot('multiple_value_call(fn(a,b){a+b},fn(){return_values(20)},fn(){return_values(2)})')
        self.assertEqual(row[0][0],('value',42));self.assertEqual(len(changed),1)
        self.check('var seen=0;async fn work(){seen+=1;return_values(1,2)};let t=work();await t;[multiple_value_list(t),nth_value(1,t),multiple_value_bind(3,t,fn(a,b,c){[a,b,c]}),seen]',[[1,2],2,[1,2,None],1])

    def test_original_native_two_close_send_throw_complete_frontiers(self):
        retained=[]
        for operation in ('receive','list','nth','bind','prog1','call'):
            for failure_kind in ('stop','generator_exit','runtime'):
                for second in ('close','send','throw'):
                    with self.subTest(operation=operation,failure=failure_kind,second=second):
                        rows=[]
                        for legacy in (True,False):
                            e=Evaluator();events=[];errors=[];resumed=LanguageError('resume',code='fixture_resume');cause=ValueError('original cause')
                            completion=mv.Completion(e,(20,22));fault=StopIteration(completion) if failure_kind=='stop' else GeneratorExit('raw') if failure_kind=='generator_exit' else RuntimeError('raw failure')
                            fault.__cause__=cause
                            callback=Native('producer',lambda:0,e);consumer=Native('consumer',lambda *xs:42,e)
                            class RawChild:
                                def __iter__(self):return self
                                def __next__(self):return self.send(None)
                                def send(self,value):events.append(('send',value));return Effect('tool',{'name':'raw-hold','value':42})
                                def throw(self,error):events.append(('throw',type(error).__name__));raise error
                                def close(self):events.append(('close',type(fault).__name__));raise fault
                            raw=RawChild();children=[]
                            def invoke(f,args,**kwargs):events.append(('invoke',f.name,copy.deepcopy(args),dict(kwargs)));children.append(raw);return raw
                            def normal(value):
                                if type(value)is mv.Completion:return('Completion',tuple(normal(x)for x in value.items))
                                if type(value)is TailCall:return('TailCall',value.fn.name if hasattr(value,'fn')else value.function.name,normal(value.args),value.ordinary_call,dict(value.span))
                                if type(value)is Effect:return('Effect',value.kind,copy.deepcopy(value.payload))
                                if type(value)is list:return[normal(x)for x in value]
                                if type(value)is tuple:return tuple(normal(x)for x in value)
                                if type(value)is Native:return('Native',value.name)
                                return value
                            with active(e,None),reference() if legacy else nullcontext(),observe() as (counts,frames),patch.object(e,'_s_invoke',invoke):
                                request=mv.Request(e,operation,consumer,(callback,callback)if operation=='prog1'else(callback,),0 if operation=='nth'else 2 if operation=='bind'else None)
                                iterator=mv.receive(e,callback)if operation=='receive'else mv.resolve(e,request)
                                first=iterator.send(None)
                                while first is CHECKPOINT:first=iterator.send(None)
                                self.assertIs(type(first),Effect);self.assertEqual(first.payload['name'],'raw-hold')
                                self.assertEqual(counts,{})if legacy else self.assertGreater(sum(counts.values()),0)
                                outcomes=[]
                                for index in range(2):
                                    error=None;returned=None
                                    try:returned=iterator.close()if index==0 or second=='close'else iterator.send(77)if second=='send'else iterator.throw(resumed)
                                    except BaseException as caught:error=caught;errors.append((caught,caught.__traceback__))
                                    actual_cause=getattr(error,'__cause__',None)
                                    outcomes.append((type(error).__name__ if error else None,str(error)if error else None,getattr(error,'code',None),normal(error.value)if type(error)is StopIteration else None,error is fault,error is resumed,actual_cause is fault,actual_cause is cause,normal(returned),copy.deepcopy(events),e.steps,e.depth,len(e.frames)))
                                rows.append(outcomes);retained.append((iterator,raw,fault,cause,resumed,errors,children,frames))
                        self.assertEqual(rows[0],rows[1])
        # Preserve all original frontiers and tracebacks through every comparison.
        for iterator,*_ in retained:
            try:iterator.close()
            except BaseException:pass
