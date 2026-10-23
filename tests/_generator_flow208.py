"""Shared observations of real stream APIs; no replacement semantic interpreter."""
import copy
from contextlib import contextmanager
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError,ExecutionControl,serializable
from chaos.jit import compile_function,compile_info
from chaos.jit_flow import info
from chaos.compiled_flow_state import FlowState
from chaos.native_jit import BackendUnavailable
from _effect_schedule158 import effect_schedule

@contextmanager
def observe_states():
    states=[];original=FlowState.__init__
    def entered(self,*args,**kw):original(self,*args,**kw);states.append(self)
    with patch.object(FlowState,'__init__',entered):yield states

def compile_choice(e,function,target):
    if target=='raw':return function
    if target=='portable':
        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('forced-portable208')):
            return compile_function(e,function,'auto-flow')
    return compile_function(e,function,'native-flow' if target=='native' else 'interpreter')

def targets():
    from test_native_jit import NATIVE
    return ('raw','interpreter','portable')+(('native',)if NATIVE else ())

def run(source,target='portable',limit=30000,cancel=None,inspect=None,budget_details=None):
    effects=[];trace=[];debug=[];functions=[];streams=[];base=[None]
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:effects.append((n,copy.deepcopy(v)))or v)
    e.cancelled=lambda:cancel is not None and base[0]is not None and e.steps-base[0]>=cancel
    def choose(function):
        answer=compile_choice(e,function,target);functions.append(answer);return answer
    def mark(stream):
        # Keep the real cumulative counter; grant identical remaining body budget.
        streams.append(stream);base[0]=e.steps;e.step_limit=e.steps+limit;trace.clear();debug.clear()
        if budget_details is not None:budget_details.append((base[0],e.step_limit))
    def checkpoint(stream):
        if inspect:inspect(e,stream,states,functions)
    def parts(stream):
        from chaos.multiple_values import components
        return list(components(stream.result,e))
    for name,callback in [('choose',choose),('mark',mark),('inspect_stream',checkpoint),('final_parts',parts)]:
        e.env.bind(name,Native(name,callback,e,captures=()))
    def traced(row):
        if base[0]is not None:
            row=copy.deepcopy(row)
            if type(row.get('step'))is int:row['step']-=base[0]
            trace.append(row)
    e.trace_handler=traced
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps-base[0]))if base[0]is not None else None
    failure=None
    with observe_states()as states,effect_schedule('already_done'):
        try:result=('value',serializable(e.run(source)))
        except LanguageError as error:failure=error;result=('error',error.record())
        except ExecutionControl as error:failure=error;result=('control',type(error).__name__,str(error))
    assert base[0]is not None,'fixture must mark the actual cold stream before consuming'
    row=(result,e.steps-base[0],effects,trace,debug,e.depth,len(e.frames),tuple(s.busy for s in streams))
    return row,states,functions,streams

def assert_compiled(test,target,states,functions):
    if target in ('raw','interpreter'):test.assertEqual(states,[]);return
    test.assertTrue(states,'a compiled stream body must actually execute, not fall back')
    test.assertTrue(all(s.generating and s.flavor in ('gen_fn','async_gen_fn')for s in states))
    test.assertTrue(all(s.closed and not s.frames and not s._owned_activations and s.pending_cursor is None and s.pending_request is None for s in states))
    for f in functions:
        test.assertIsNotNone(f.compiled_plan.flow);test.assertGreater(info(f.owner,f)['resumeCalls'],0)
        test.assertEqual(len(info(f.owner,f)),11);test.assertEqual(len(compile_info(f.owner,f)),12)
        # All shared parity runs install trace/debug hooks: the documented
        # permanent debug fallback is itself observable and must be portable.
        test.assertGreater(info(f.owner,f)['portableResumes'],0)
        test.assertEqual(info(f.owner,f)['nativeResumes'],0)
