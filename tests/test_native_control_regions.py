"""Original AST loops with per-entry native scalar guards and actual snapshots."""
import copy
import unittest
from unittest.mock import patch
from chaos.language import Evaluator, LanguageError
from chaos.jit import compile_function, compile_info, compile_optimization_info
from chaos.native_jit import BackendUnavailable
from chaos.suspension import CHECKPOINT
from test_native_jit import NATIVE

WHILE='fn(n){var i:Int=0;var total:Int=0;while i<n{total=total+i;i=i+1};total}'
FOR='fn(values){var total=0;for item in values{total=total+item};total}'
def make(source=WHILE,target='auto-control'):
 e=Evaluator(step_limit=1000000);return e,compile_function(e,e.run(source),target)
def record(e,f,args,limit=1000000,threshold=None):
 e.steps=0;e.step_limit=limit;e.cancelled=lambda:threshold is not None and e.steps>=threshold
 try:result=('value',e.invoke(f,args))
 except LanguageError as error:result=('error',error.record())
 return result,e.steps

def schedule(e,f,args):
 e.steps=0;events=[];it=e._s_invoke(f,args)
 while True:
  try:
   assert next(it) is CHECKPOINT;events.append((e.steps,dict(e.current_span)))
  except StopIteration as done:return done.value,events,e.steps

class ControlPortable(unittest.TestCase):
 def test_opt_in_keeps_legacy_profile_and_exact_info_shapes(self):
  for src in (WHILE,FOR,'fn(x){var y=x;y+1}'):
   e,old=make(src,'auto');self.assertEqual(compile_info(e,old)['status'],'unsupported')
   e,new=make(src);self.assertEqual(len(compile_info(e,new)),12);self.assertEqual(compile_info(e,new)['parameterGuards'],[])
   self.assertEqual(len(compile_optimization_info(e,new)),13)
 def test_while_zero_and_changing_values(self):
  e,f=make();self.assertEqual([e.invoke(f,[n]) for n in (0,1,6)],[0,0,15])
 def test_for_uses_actual_iterable_parameter_not_global_int_guard(self):
  e,f=make(FOR);self.assertEqual(e.invoke(f,[[1,2,3]]),6)
  self.assertEqual(compile_info(e,f)['parameterGuards'],[])
 def test_break_continue_normal_else_nested_and_return(self):
  cases=[('fn(n){var i=0;var sum=0;while i<n{i=i+1;if i<3{continue}else{null};if i>5{break}else{null};sum=sum+i}else{sum=sum+100};sum}',7,12),
   ('fn(n){var i=0;while i<n{i=i+1}else{i=i+100};i}',3,103),
   ('fn(n){var i=0;while i<n{if i>1{return i*2}else{null};i=i+1};-1}',4,4),
   ('fn(n){var total=0;for a in [1,2]{for b in [2,3]{if b>2{break}else{null};total=total+a*b}else{total=total+100}};total}',0,6)]
  for src,arg,expected in cases:
   a,f=make(src);b,g=make(src,'interpreter');self.assertEqual(record(a,f,[arg]),record(b,g,[arg]));self.assertEqual(a.invoke(f,[arg]),expected)
 def test_augmented_rhs_original_operation_and_pattern_bindings(self):
  for src,args,expected in [('fn(xs){var total=0;for (a,b) in xs{total+=(a+1)*(a+1)+b};total}',[[[1,2]]],None),
   ('fn(xs){var total=0;for [a,b] in xs{total+=(a+1)*(a+1)+b};total}',[[[1,2],[2,3]]],18),
   ('fn(n){var [a,b]=[0,n];while a<b{[a,b]=[a+1,b]};a}',[3],3)]:
   a,f=make(src);b,g=make(src,'interpreter');self.assertEqual(record(a,f,args),record(b,g,args))
   if expected is not None:self.assertEqual(a.invoke(f,args),expected)
 def test_lazy_boolean_and_equality_are_original_selected_controls(self):
  src='fn(n){var i=0;while i!=n && i<3{i=i+1};if !(i==0) || false{i}else{42}}'
  a,f=make(src);b,g=make(src,'interpreter')
  for n in (0,1,5):self.assertEqual(record(a,f,[n]),record(b,g,[n]))
 def test_shadowing_immutable_writes_and_contract_errors(self):
  for src in ('fn(n){var i=0;while i<n{let i=40;i=i+1};i}',
   'fn(n){var i:Int=0;while i<n{i=true};i}',
   'fn(n)->String{var i=0;while i<n{i=i+1};i}'):
   a,f=make(src);b,g=make(src,'interpreter');self.assertEqual(record(a,f,[1]),record(b,g,[1]))
 def test_free_mutable_capture_rejected_without_write(self):
  e,f=make('var outside=0;fn(n){outside=n;outside}')
  self.assertEqual(compile_info(e,f)['status'],'unsupported')
 def test_backend_strict_profile_and_partial_allocation_quota(self):
  e=Evaluator();plain=e.run(WHILE)
  with patch('chaos.native_jit.compile_kernel',side_effect=BackendUnavailable('disabled')):
   with self.assertRaises(LanguageError) as caught:compile_function(e,plain,'native-control')
   self.assertEqual(caught.exception.code,'compiler_error')
   f=compile_function(e,plain,'auto-control');self.assertEqual(e.invoke(f,[3]),3)
  e=Evaluator();plain=e.run(WHILE);e.native_kernel_count=32
  with patch('chaos.native_jit.compile_kernel',side_effect=AssertionError('allocated')):
   f=compile_function(e,plain,"auto-control");self.assertEqual(compile_info(e,f)['fallbackReason'],'kernel_limit')
   with self.assertRaises(LanguageError):compile_function(e,plain,'native-control')

@unittest.skipUnless(NATIVE,'optional LLVM backend unavailable')
class ControlNative(unittest.TestCase):
 def test_real_per_iteration_argument_snapshots_not_initializer_counter(self):
  e,f=make(FOR,'native-control');seen=[]
  for item in f.compiled_plan.regions:
   if item.region.analysis and len(item.region.analysis.parameters)==2:
    original=item.kernel.invoke
    def invoke(args,checkpoint,original=original):seen.append(list(args));return original(args,checkpoint)
    item.kernel.invoke=invoke
  self.assertEqual(e.invoke(f,[[1,2,3]]),6);self.assertEqual(seen,[[0,1],[1,2],[3,3]])
  self.assertEqual(compile_info(e,f)['interpreterCalls'],0)
 def test_budget_cancel_and_yield_locations_every_boundary(self):
  a,f=make(target='native-control');b,g=make(target='interpreter')
  self.assertEqual(schedule(a,f,[2]),schedule(b,g,[2]));total=record(b,g,[2])[1]
  for limit in range(total+2):self.assertEqual(record(a,f,[2],limit),record(b,g,[2],limit))
  for threshold in range(total+2):self.assertEqual(record(a,f,[2],threshold=threshold),record(b,g,[2],threshold=threshold))
 def test_current_bigint_guard_fallback_does_not_replay_and_native_resumes(self):
  src='fn(xs){var last=0;for x in xs{last=x*x};last}'
  e,f=make(src,'native-control');p,g=make(src,'interpreter')
  self.assertEqual(record(e,f,[[2,2**100,3]]),record(p,g,[[2,2**100,3]]));self.assertEqual(e.invoke(f,[[2,2**100,3]]),9)
  info=compile_info(e,f);self.assertEqual((info['nativeCalls'],info['interpreterCalls']),(2,2))
 def test_debug_trace_and_ast_adoption_at_loop_safepoint(self):
  for instrument in ('debug','trace','replace'):
   outcomes=[]
   for target in ('native-control','interpreter'):
    e,f=make(target=target);e.steps=0;it=e._s_invoke(f,[2]);next(it);next(it);events=[]
    if instrument=='debug':e.debug_hook=lambda node,env,frames:events.append((node[0],repr(env.values)))
    elif instrument=='trace':e.trace_handler=events.append
    else:f.body[1][-1]=copy.deepcopy(f.body[1][-1])
    while True:
     try:next(it)
     except StopIteration as done:outcomes.append((done.value,e.steps,[(x.get('kind'),x.get('step')) for x in events] if instrument=='trace' else len(events)));break
   self.assertEqual(outcomes[0],outcomes[1])
 def test_actual_loop_rhs_has_optimized_kernel_and_inert_query(self):
  e,f=make('fn(xs){var total=0;for x in xs{total=total+(x+1)*(x+1)};total}','native-control')
  before=compile_info(e,f);q=compile_optimization_info(e,f)
  self.assertTrue(q['enabled']);self.assertGreater(q['commonSubexpressions'],0);self.assertLess(q['emittedOperations'],q['originalOperations'])
  self.assertEqual(compile_info(e,f),before);self.assertEqual(e.invoke(f,[[0,1,2]]),14)
  self.assertEqual(compile_info(e,f)['nativeCalls'],1)
