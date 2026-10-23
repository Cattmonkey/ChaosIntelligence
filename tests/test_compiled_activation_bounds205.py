"""Legal analyzed depth and authoritative compiled activation metadata."""
import json
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError
from chaos.jit import compile_function, compile_info
from chaos.native_jit import BackendUnavailable
from chaos.suspension import Await, CHECKPOINT
from chaos.execution_frames import ensure_cursor
from chaos.compiled_flow_state import FlowState
from chaos import compiled_flow_frames as activations
from chaos.program import parse_program, format_program
from test_native_jit import available
from _effect_schedule158 import effect_schedule


def nested_source(flavor):
    #31for bodies reachdepth62; this scalar-Bool if and its empty blocks
    #reach64 without opaque(name)->string crossing the analyzer's limit.
    body='if true{}else{}'
    for _ in range(31):body='for x in xs{'+body+'}'
    return flavor+'(xs){tool("before",1);'+body+';tool("after",2);42}'


def invoke_case(source,flavor,route,target):
    calls=[];states=[];maximum=[0]
    e=Evaluator(step_limit=30000,timeout=30,tool_handler=lambda name,value:calls.append((name,value)) or value)
    if route=='source':plain=e.run(source)
    elif route=='IR':plain=e.run(format_program(parse_program(source)))
    elif route=='Code':plain=e.run('function_from(code_from(input))',{'input':source})
    else:raise AssertionError(route)
    if target=='raw':function=plain
    elif target=='portable':
        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('depth-repair-portable')):
            function=compile_function(e,plain,'auto-flow')
    else:function=compile_function(e,plain,target)
    program=getattr(function.compiled_plan,'flow',None)if function.compiled_plan else None
    original=activations.entered
    def entered(boundary,cursor):
        original(boundary,cursor)
        state=getattr(boundary,'flow_state',None)
        if state is not None:
            if not any(state is prior for prior in states):states.append(state)
            maximum[0]=max(maximum[0],len(state.frames))
    failure=None;e.steps=0
    try:
        with patch.object(activations,'entered',entered),effect_schedule('already_done'):
            value=e.invoke(function,[[0]])
            if flavor=='async fn':
                def awaited():return (yield Await(value))
                value=e.suspension.drive(awaited())
        outcome=(value,e.steps,calls,e.depth,len(e.frames))
    except BaseException as caught:failure=caught;raise
    finally:e.suspension.finish(failure)
    return outcome,states,maximum[0],program


class CompiledActivationBounds205(unittest.TestCase):
    def test_source_IR_Code_legal_depth64_runs_all65_actual_activations(self):
        for flavor in ('fn','async fn'):
            source=nested_source(flavor)
            for route in ('source','IR','Code'):
                expected,raw_states,_,_=invoke_case(source,flavor,route,'raw')
                self.assertEqual(raw_states,[])
                self.assertEqual(expected[0],42)
                self.assertEqual(expected[2],[('before',1),('after',2)])
                targets=('interpreter','portable','native-flow')if available()else('interpreter','portable')
                for target in targets:
                    with self.subTest(flavor=flavor,route=route,target=target):
                        observed,states,maximum,program=invoke_case(source,flavor,route,target)
                        self.assertEqual(observed,expected)
                        if target=='interpreter':self.assertEqual(states,[]);continue
                        self.assertIsNotNone(program,'legal source must compile, not fallback')
                        self.assertEqual(maximum,65)
                        self.assertGreater(program.resume_calls,0)
                        if target=='portable':self.assertGreater(program.portable_resumes,0)
                        else:self.assertGreater(program.native_resumes,0)
                        self.assertTrue(states)
                        for state in states:
                            self.assertTrue(state.closed)
                            self.assertEqual(state.frames,[])
                            self.assertEqual(state._owned_activations,[])
                            self.assertEqual(state.roots(),())

    def test_depth65_keeps_honest_fallback_and_strict_rejection(self):
        for flavor in ('fn','async fn'):
            snapshots=[]
            for target in ('raw','interpreter','portable','native-flow'):
                with self.subTest(flavor=flavor,target=target):
                    e=Evaluator();plain=e.run(flavor+'(){}');body=('block',[])
                    for _ in range(65):body=('block',[body])
                    plain.body=body
                    if target=='native-flow':
                        with self.assertRaises(LanguageError)as error:compile_function(e,plain,target)
                        self.assertEqual(error.exception.code,'compiler_error')
                        self.assertIn('depth_limit',error.exception.message)
                        continue
                    if target=='raw':function=plain
                    elif target=='portable':
                        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('depth-limit-portable')):
                            function=compile_function(e,plain,'auto-flow')
                        self.assertIsNone(function.compiled_plan.flow)
                        self.assertEqual(compile_info(e,function)['fallbackReason'],'depth_limit')
                    else:function=compile_function(e,plain,target)
                    e.steps=0;failure=None
                    try:
                        result=e.invoke(function,[])
                        if flavor=='async fn':
                            def awaited():return (yield Await(result))
                            result=e.suspension.drive(awaited())
                        snapshots.append((result,e.steps,e.depth,len(e.frames)))
                    except BaseException as caught:failure=caught;raise
                    finally:e.suspension.finish(failure)
            self.assertEqual(snapshots,[snapshots[0]]*3)
            self.assertIsNone(snapshots[0][0])

    def test_duplicate_alias_and_oversized_vectors_reject_before_next_effect(self):
        for damage in ('duplicate','aliased-event','oversized'):
            with self.subTest(damage=damage):
                calls=[];states=[]
                e=Evaluator(tool_handler=lambda name,value:calls.append(name)or value)
                plain=e.run('async fn(t){await t;tool("forbidden",0);42}')
                with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('metadata-portable')):
                    function=compile_function(e,plain,'auto-flow')
                def cold():
                    yield CHECKPOINT
                    return None
                borrowed=e.suspension.task(cold())
                original=FlowState.__init__
                def entered(state,*args,**kwargs):original(state,*args,**kwargs);states.append(state)
                iterator=ensure_cursor(e,e._s_invoke(function,[borrowed],force=True))
                failure=None
                try:
                    with patch.object(FlowState,'__init__',entered):
                        while True:
                            request=iterator.send(None)
                            if request is not CHECKPOINT:break
                    self.assertIs(type(request),Await)
                    self.assertIs(request.task,borrowed)
                    self.assertEqual(len(states),1);state=states[0]
                    self.assertGreaterEqual(len(state.frames),2)
                    if damage=='duplicate':state.frames.append(state.frames[-1])
                    elif damage=='oversized':state.frames=state.frames*(len(state.program.instructions)+1)
                    else:
                        first,last=state.frames[0],state.frames[-1]
                        self.assertIsNot(first,last)
                        last.flow_index=first.flow_index;last.flow_event=first.flow_event
                        last.node=first.node;last.flow_structural=first.flow_structural
                    with self.assertRaises(LanguageError)as error:iterator.send(None)
                    failure=error.exception
                    self.assertEqual(error.exception.code,'ownership_error')
                    self.assertEqual(calls,[])
                    self.assertEqual(borrowed.state,'created')
                    self.assertTrue(state.closed)
                    self.assertEqual(state.frames,[])
                    self.assertEqual(state._owned_activations,[])
                    self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
                finally:
                    iterator.close();e.suspension.finish(failure)
