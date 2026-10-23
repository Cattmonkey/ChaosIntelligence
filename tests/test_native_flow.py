"""Differential semantic events for actual resumable LLVM structural CFG."""
import copy
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError
from chaos.jit import compile_function,compile_info,compile_optimization_info
from chaos.jit_flow import info
from chaos.native_jit import BackendUnavailable
from chaos.suspension import CHECKPOINT
from test_native_jit import NATIVE
SRC='fn(n){var i=0;var total=0;while i<n{total=total+i;i=i+1};total}'
def make(src=SRC,target='auto-flow'):
 e=Evaluator(step_limit=1000000);return e,compile_function(e,e.run(src),target)
def outcome(e,f,args,limit=1000000,cancel=None):
 e.steps=0;e.step_limit=limit;e.cancelled=lambda:cancel is not None and e.steps>=cancel
 try:value=('value',e.invoke(f,args))
 except LanguageError as error:value=('error',error.record())
 return value,e.steps

def schedule(e,f,args):
 e.steps=0;events=[];it=e._s_invoke(f,args)
 while True:
  try:
   event=next(it);assert event is CHECKPOINT;events.append((e.steps,dict(e.current_span)))
  except StopIteration as done:return done.value,events,e.steps
class FlowPortable(unittest.TestCase):
 def test_actual_result_and_old_metadata_shapes(self):
  e,f=make();self.assertEqual(e.invoke(f,[4]),6)
  self.assertEqual(len(compile_info(e,f)),12);self.assertEqual(len(compile_optimization_info(e,f)),13)
  q=info(e,f);self.assertEqual(len(q),11);self.assertEqual(q['abi'],'pc-host-events/1')
  self.assertGreater(q['resumeCalls'],0);self.assertEqual(q['definitionPolicy'],'stable-during-invocation')
 def test_control_completion_and_scope(self):
  cases=[SRC,
   'fn(n){var i=0;var total=0;while i<n{i=i+1;if i<3{continue}else{null};if i>5{break}else{null};total=total+i}else{total=total+100};total}',
   'fn(n){var i=0;while i<n{if i>1{return i*2}else{null};i=i+1};-1}',
   'fn(n){var i=0;var sum=0;while i<n{var j=0;while j<3{j=j+1;if j>1{break}else{null};sum=sum+i};i=i+1};sum}',
   'fn(n){var i=0;while i<n{let i=40;i=i+1};i}',
   'fn(n)->String{var i=0;while i<n{i=i+1};i}',
   'fn(n){var i:Int=0;while i<n{i=true};i}']
  for src in cases:
   a,f=make(src);b,g=make(src,'interpreter')
   for n in (0,1,7):self.assertEqual(outcome(a,f,[n]),outcome(b,g,[n]),src)
 def test_budget_cancel_and_scheduler_each_boundary(self):
  a,f=make();b,g=make(target='interpreter')
  self.assertEqual(schedule(a,f,[2]),schedule(b,g,[2]));total=outcome(b,g,[2])[1]
  for limit in range(total+2):self.assertEqual(outcome(a,f,[2],limit),outcome(b,g,[2],limit))
  for cancel in range(total+2):self.assertEqual(outcome(a,f,[2],cancel=cancel),outcome(b,g,[2],cancel=cancel))
 def test_debug_trace_enable_at_each_real_checkpoint(self):
  for instrument in ('debug','trace'):
   for at in range(4):
    results=[]
    for target in ('auto-flow','interpreter'):
     e,f=make(target=target);e.steps=0;it=e._s_invoke(f,[2]);events=[]
     for _ in range(at):next(it)
     if instrument=='debug':e.debug_hook=lambda node,env,frames:events.append((node[0],dict(e.current_span),e.steps))
     else:e.trace_handler=events.append
     while True:
      try:next(it)
      except StopIteration as done:results.append((done.value,e.steps,events));break
    self.assertEqual(results[0],results[1])
 def test_stable_definition_before_and_during_invocation(self):
  e,f=make();f.body[1][-1]=copy.deepcopy(f.body[1][-1])
  self.assertEqual(e.invoke(f,[3]),3);self.assertEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')
  e,f=make();it=e._s_invoke(f,[2]);next(it);next(it);next(it)
  f.body[1][-1]=copy.deepcopy(f.body[1][-1])
  with self.assertRaises(LanguageError) as caught:next(it)
  self.assertEqual(caught.exception.code,'definition_changed')
 def test_backend_missing_strict_and_quota_no_allocation(self):
  e=Evaluator();plain=e.run(SRC)
  with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('disabled')):
   f=compile_function(e,plain,'auto-flow');self.assertEqual(e.invoke(f,[4]),6);self.assertGreater(info(e,f)['portableResumes'],0)
   with self.assertRaises(LanguageError) as caught:compile_function(e,plain,'native-flow')
   self.assertEqual(caught.exception.code,'compiler_error')
  e=Evaluator();plain=e.run(SRC);e.native_kernel_count=32
  with patch('chaos.native_flow.compile_flow',side_effect=AssertionError('allocated')):
   f=compile_function(e,plain,'auto-flow');self.assertEqual(compile_info(e,f)['fallbackReason'],'kernel_limit')
 def test_unsupported_remains_original_function(self):
  e,f=make('fn(xs){'+'0;'*130+'var total=0;for x in xs{total=total+x};total}')
  self.assertEqual(compile_info(e,f)['status'],'unsupported');self.assertEqual(e.invoke(f,[[1,2,3]]),6)
 def test_query_is_inert_and_fresh_and_old_targets_unchanged(self):
  e,f=make();before=e.steps;q=info(e,f);q['lastPC']=999;self.assertEqual(e.steps,before);self.assertNotEqual(info(e,f)['lastPC'],999)
  e,f=make(target='auto');self.assertEqual(compile_info(e,f)['status'],'unsupported');self.assertEqual(info(e,f)['status'],'not_compiled')
@unittest.skipUnless(NATIVE,'optional LLVM backend unavailable')
class FlowNative(unittest.TestCase):
 def test_true_llvm_branch_saved_pc_and_bigint_leaf_fallback(self):
  e,f=make(target='native-flow');program=f.compiled_plan.flow;module=str(program.kernel._module)
  self.assertIn('switch i32',module);self.assertIn('br i1',module)
  seen=[];original=program.kernel.resume
  def resume(pc,flags):
   answer=original(pc,flags);seen.append((pc,answer));return answer
  program.kernel.resume=resume
  self.assertEqual(e.invoke(f,[3]),3);self.assertGreater(len(set(pc for pc,_ in seen)),3)
  self.assertEqual(info(e,f)['nativeResumes'],len(seen));self.assertGreater(info(e,f)['nativeResumes'],1)
  src='fn(n){var i=0;var total=n;while i<2{total=total+1;i=i+1};total}'
  a,f=make(src,'native-flow');b,g=make(src,'interpreter');self.assertEqual(outcome(a,f,[2**100]),outcome(b,g,[2**100]))
 def test_no_c_callback_drives_protocol_yield(self):
  src='class C{fn __add__(self,x){x+2}}[C(),fn(n){var i=0;var total=n;while i<1{total=total+1;i=i+1};total}]'
  a=Evaluator();aa,plain=a.run(src);f=compile_function(a,plain,'native-flow')
  b=Evaluator();bb,plain=b.run(src);g=compile_function(b,plain,'interpreter')
  self.assertEqual(schedule(a,f,[aa]),schedule(b,g,[bb]))
