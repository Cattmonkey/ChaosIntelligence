"""Differential tests for real LLVM/portable iterator control flow."""
import copy
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ExecutionControl,Native
from chaos.jit import compile_function,compile_info
from chaos.jit_flow import info
from chaos.native_jit import BackendUnavailable
from test_native_jit import available
from chaos.suspension import CHECKPOINT
from chaos.tuple_values import TupleValue

SUM='fn(xs){var total=0;for x in xs{total=total+x};total}'

def make(source=SUM,target='portable'):
    e=Evaluator(step_limit=5000000,timeout=90);f=e.run(source)
    if target=='portable':
        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('fixture')):f=compile_function(e,f,'auto-flow')
    else:f=compile_function(e,f,target)
    return e,f

def outcome(e,f,args,limit=5000000,cancel=None):
    e.steps=0;e.step_limit=limit;e.cancelled=lambda:cancel is not None and e.steps>=cancel
    try:result=('value',e.invoke(f,args))
    except LanguageError as error:result=('error',error.record())
    except ExecutionControl:result=('cancel',)
    return result,e.steps

def schedule(e,f,args):
    e.steps=0;e.suspension.begin();it=e._s_invoke(f,args);events=[]
    try:
        while True:
            try:request=next(it);assert request is CHECKPOINT;events.append((e.steps,dict(e.current_span)))
            except StopIteration as result:return result.value,events,e.steps
    finally:it.close()

class NativeIterationTests(unittest.TestCase):
    def compare(self,source,args):
        expected=None
        for target in ('interpreter','portable','auto-flow'):
            with self.subTest(target=target,source=source):
                e,f=make(source,target);actual=outcome(e,f,args)
                if expected is None:expected=actual
                self.assertEqual(actual,expected)

    def test_previous_unsupported_for_is_real_cfg(self):
        for target in ('portable','auto-flow'):
            e,f=make(target=target);self.assertIsNotNone(f.compiled_plan.flow)
            self.assertEqual(e.invoke(f,[[20,22]]),42)
            self.assertGreater(info(e,f)['resumeCalls'],0);self.assertEqual(len(info(e,f)),11)

    def test_nested_loops_completion_break_continue_return_and_scope(self):
        cases=[(SUM,[[1,2,3]]),
          ('fn(xs){var n=0;for x in xs{if x<2{continue}else{null};if x>3{break}else{null};n=n+x}else{n=n+100};n}',[[1,2,3,4]]),
          ('fn(xs){var n=0;for x in xs{n=n+x}else{n=n+100};n}',[[]]),
          ('fn(xs){var n=0;for x in xs{var i=0;while i<3{i=i+1;if i>1{continue}else{null};n=n+x}else{n=n+10}}else{n=n+100};n}',[[1,2]]),
          ('fn(xs){var i=0;var n=0;while i<2{i=i+1;for x in xs{if x>2{break}else{null};n=n+x}else{n=n+10}};n}',[[1,2,3]]),
          ('fn(xs){var x=20;for x in xs{let x=40;x};x}',[[1,2]]),
          ('fn(xs){for x in xs{if x>2{return x*2}else{null}};-1}',[[1,3,5]]),
          ('fn(xs){var total:Int=0;for x in xs{total=x};total}',[[True]])]
        for source,args in cases:self.compare(source,args)

    def test_patterns_and_atomic_mismatch(self):
        cases=[('fn(xs){var n=0;for (a,b) in xs{n=n+a+b}else{n=n+1};n}',[[TupleValue([1,2]),TupleValue([3,4])]]),
          ('fn(xs){var n=0;for {a:x,b:(y,z)} in xs{n=n+x+y+z};n}',[[{'a':1,'b':TupleValue([2,3])}]]),
          ('fn(xs){var n=0;for (a,b) in xs{n=n+a+b};n}',[[TupleValue([1,2]),TupleValue([3])]])]
        for source,args in cases:self.compare(source,args)

    def test_pattern_constructor_uses_outer_scope(self):
        for target in ('interpreter','auto-flow'):
            self.assertEqual(Evaluator().run('data Pair{PairOf(a,b)};fn sum(xs){var n=0;for PairOf(a,b) in xs{n=n+a+b};n};let f=compile_function(sum,target="'+target+'");f([PairOf(20,22)])'),42)

    def test_exact_budget_and_cancel_boundaries(self):
        e,f=make(target='interpreter');_,total=outcome(e,f,[[1,2]])
        for target in ('portable','auto-flow'):
            for boundary in range(total+2):
                e,f=make(target=target);a,p=make(target='interpreter')
                self.assertEqual(outcome(e,f,[[1,2]],boundary),outcome(a,p,[[1,2]],boundary))
                e,f=make(target=target);a,p=make(target='interpreter')
                self.assertEqual(outcome(e,f,[[1,2]],cancel=boundary),outcome(a,p,[[1,2]],cancel=boundary))
                self.assertTrue(all(not t.runner.capture_frames for t in e.suspension.tasks))

    def test_original_checkpoint_sequence(self):
        for target in ('portable','auto-flow'):
            e,f=make(target=target);a,p=make(target='interpreter')
            self.assertEqual(schedule(e,f,[[1,2]]),schedule(a,p,[[1,2]]))

    def test_debug_and_trace_switch_at_each_checkpoint(self):
        for mode in ('debug','trace'):
            for at in range(4):
                results=[]
                for target in ('auto-flow','interpreter'):
                    e,f=make(target=target);e.steps=0;e.suspension.begin();it=e._s_invoke(f,[[1,2]]);events=[]
                    for _ in range(at):self.assertIs(next(it),CHECKPOINT)
                    if mode=='debug':e.debug_hook=lambda node,env,frames:events.append((node[0],e.steps,dict(e.current_span)))
                    else:e.trace_handler=events.append
                    while True:
                        try:next(it)
                        except StopIteration as result:results.append((result.value,e.steps,events));break
                self.assertEqual(results[0],results[1])

    def test_source_once_order_no_extra_read_or_close(self):
        source='''var log=[];class Cursor{var n=0;fn __iter__(self){log=append(log,"iter");self}fn __next__(self){self.n=self.n+1;log=append(log,self.n);{done:self.n>5,value:self.n}}fn __close__(self){log=append(log,"close");null}}let cursor=Cursor();fn source(){log=append(log,"source");cursor}fn f(){var n=0;for x in source(){n=n+x;if x>1{break}else{null}}else{n=n+100};n};let fast=compile_function(f,target=TARGET);let n=fast();[n,log,cursor.n,next(iter(cursor)).value]'''
        for target in ('interpreter','auto-flow'):
            self.assertEqual(Evaluator().run(source.replace('TARGET','"'+target+'"')),[3,['source','iter',1,2],2,3])

    def test_std_iter_infinite_stream_truncation(self):
        source='''import "std/iter" as I;fn sum(xs){var n=0;for x in I.take(xs,3){n=n+x}else{n=n+0};n}let f=compile_function(sum,target=TARGET);f(I.map(fn(x){x*2},I.count(6)))'''
        for target in ('interpreter','auto-flow'):
            self.assertEqual(Evaluator().run(source.replace('TARGET','"'+target+'"')),42)

    def test_generator_borrowed_after_return(self):
        source='''var closed=0;fn* values(){try{yield 20;yield 22;yield 99}finally{closed=closed+1}}let xs=values();fn first(xs){for x in xs{return x};0}let f=compile_function(first,target=TARGET);let a=f(xs);[a,next(xs).value,closed]'''
        for target in ('interpreter','auto-flow'):
            self.assertEqual(Evaluator().run(source.replace('TARGET','"'+target+'"')),[20,22,0])

    def test_source_loop_signals_target_outer_loop_and_run_cleanup(self):
        for signal,expected in [('break',1),('continue',3)]:
            source='fn(xs){var n=0;var i=0;while i<3{i=i+1;for x in do{try{'+signal+'}finally{n=n+1};xs}{n=n+100}}else{n=n+0};n}'
            self.compare(source,[[1]])
            e,f=make(source);self.assertEqual(e.invoke(f,[[1]]),expected)
        self.compare('fn(xs){for x in do{try{return 42}finally{null};xs}{x};0}',[[1]])

    def test_protocol_error_order(self):
        source='''var log=[];class C{var n=0;fn __iter__(self){log=append(log,"iter");self}fn __next__(self){self.n=self.n+1;log=append(log,self.n);if self.n==2{throw ValueError("stop")}else{{done:false,value:1}}}}fn sum(xs){var n=0;for x in xs{n=n+x}else{n=n+100};n}let f=compile_function(sum,target=TARGET);[try{f(C())}catch e{[e.code,e.message]},log]'''
        answers=[Evaluator().run(source.replace('TARGET','"'+target+'"')) for target in ('interpreter','auto-flow')]
        self.assertEqual(answers[0],answers[1])

    def test_foreign_and_stale_source_rejection(self):
        for target in ('interpreter','auto-flow'):
            foreign=Evaluator().run('fn* g(){yield 1}g()');e,f=make(target=target)
            with self.assertRaises(LanguageError):e.invoke(f,[foreign])
            e=Evaluator();old=e.run('fn* g(){yield 1}g()');e.run('0');f=compile_function(e,e.run(SUM),target)
            with self.assertRaises(LanguageError):e.invoke(f,[old])

    def test_definition_change_before_entry_and_during_iteration(self):
        e,f=make(target='auto-flow');f.body[1][-1]=copy.deepcopy(f.body[1][-1])
        self.assertEqual(e.invoke(f,[[20,22]]),42);self.assertEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')
        e,f=make(target='auto-flow');e.suspension.begin();it=e._s_invoke(f,[[20,22]])
        next(it);next(it);next(it);f.body[1][-1]=copy.deepcopy(f.body[1][-1])
        with self.assertRaises(LanguageError) as caught:next(it)
        self.assertEqual(caught.exception.code,'definition_changed');self.assertEqual(e.depth,0)

    def test_flow_capture_roots_are_bounded_in_while_and_for(self):
        from chaos.capture_frames import holder
        for loop in ('while i<400{let discarded=n+1;i=i+1}','for i in xs{let discarded=n+1}'):
            e=Evaluator(step_limit=1000000);counts=[]
            def sample():
                counts.append(sum(len(f.roots) for f in holder(e).capture_frames));return None
            e.env.bind('sample',Native('sample',sample,e))
            source='class C{fn __add__(self,x){sample();[x]}}let n=C();fn f(n,xs){var i=0;'+loop+';42};let fast=compile_function(f,target="auto-flow");fast(n,range(0,400))'
            self.assertEqual(e.run(source),42);self.assertLess(max(counts)-min(counts),10);self.assertLess(max(counts),80)

    def test_public_long_while_no_false_capture_limit(self):
        e=Evaluator(step_limit=5000000,timeout=90)
        obj,plain=e.run('class C{fn __add__(self,x){[x]}}[C(),fn(n){var i=0;while i<20010{let discarded=n+1;i=i+1};i}]')
        f=compile_function(e,plain,'auto-flow');self.assertEqual(e.invoke(f,[obj]),20010)
        self.assertTrue(all(not task.runner.capture_frames for task in e.suspension.tasks))

    def test_control_module_scope_and_span_restore(self):
        expected=None
        for target in ('interpreter','auto-flow'):
            e,f=make(target=target);seen=[]
            def mark(node):
                if isinstance(node,tuple):
                    if hasattr(node,'__dict__'):node._module_scope='scope-'+node[0]
                    for child in node:mark(child)
                elif isinstance(node,list):
                    for child in node:mark(child)
            mark(f.body);f=compile_function(e,f,target)
            e.debug_hook=lambda node,env,frames:seen.append((node[0],e.suspension.current.module_scope))
            self.assertEqual(e.invoke(f,[[20,22]]),42)
            if expected is None:expected=seen
            self.assertEqual(seen,expected)

    def test_caught_definition_change_cannot_resume_cfg_and_cleanup_runs(self):
        for hard in (False,True):
            e=Evaluator();box={};events=[]
            def mutate():
                body=box['fn'].body;body[1][-1]=copy.deepcopy(body[1][-1]);return None
            def cleanup():
                events.append('cleanup')
                if hard:raise ExecutionControl('cleanup cancelled')
                return None
            e.env.bind('mutate',Native('mutate',mutate,e));e.env.bind('cleanup',Native('cleanup',cleanup,e))
            plain=e.run('fn(xs){var n=0;for x in do{try{mutate();do{xs}}catch error{xs}finally{cleanup()}}{n=n+x};n}')
            box['fn']=compile_function(e,plain,'auto-flow')
            with self.assertRaises(ExecutionControl if hard else LanguageError) as error:e.invoke(box['fn'],[[42]])
            if not hard:self.assertEqual(error.exception.code,'definition_changed')
            self.assertEqual(events,['cleanup']);self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
            self.assertTrue(all(not task.runner.capture_frames for task in e.suspension.tasks))

    def test_source_cursor_item_are_explicit_capture_roots(self):
        from chaos.capture_frames import holder
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.iteration import Iterator
        e=Evaluator();token=NextDescriptor(e,None);item=Native('resume',lambda:42,e,generic=token)
        source=[item];e.env.bind('source',Native('source',lambda:source,e));seen=[]
        f=compile_function(e,e.run('fn(){var n=0;for x in source(){n=n+1};n}'),'auto-flow')
        def inspect(node,env,frames):
            if node[0]!='block':return
            active=[frame for frame in holder(e).capture_frames if frame.kind=='for']
            if not active:return
            frame=active[-1]
            if env.values.get('x') is not item:return
            self.assertTrue(any(root is source for root in frame.roots))
            self.assertTrue(any(type(root) is Iterator for root in frame.roots))
            self.assertTrue(any(root is item for root in frame.roots))
            seal(e,[frame],frozenset());seen.append(True)
        e.debug_hook=inspect
        self.assertEqual(e.invoke(f,[]),1);self.assertEqual(seen,[True]);self.assertEqual(token.capabilities,frozenset())

    def test_saved_module_scope_is_an_isolated_capture_root_after_reset(self):
        from chaos.capture_frames import holder
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.module_values import Scope
        e=Evaluator();token=NextDescriptor(e,None);resume=Native('resume',lambda:42,e,generic=token)
        saved=object.__new__(Scope);saved.evaluator=e;saved.bindings=(('hidden',resume),);saved.cache={}
        f=compile_function(e,e.run(SUM),'auto-flow');f.body._module_scope=saved;seen=[]
        def inspect(node,env,frames):
            if node[0]=='for':
                # Remove the incidental Function.body edge. The live saved
                # structural-frame slot must retain provenance independently.
                del f.body._module_scope
            if node[0]=='block' and env.values.get('x')==22:
                frame=next(frame for frame in holder(e).capture_frames if frame.kind=='for')
                self.assertTrue(any(root is saved for root in frame.roots))
                seal(e,[frame],frozenset());seen.append(True)
        e.debug_hook=inspect
        self.assertEqual(e.invoke(f,[[20,22]]),42);self.assertEqual(seen,[True]);self.assertEqual(token.capabilities,frozenset())

    def test_old_target_and_unsupported_body_call(self):
        e,f=make('fn(xs){var n=0;for x in xs{n=n+(fn(x){x})(x)};n}','auto-flow')
        self.assertIsNotNone(f.compiled_plan.flow);self.assertEqual(e.invoke(f,[[20,22]]),42)
        e,f=make('fn(xs){'+'0;'*130+'var n=0;for x in xs{n=n+x};n}','auto-flow')
        self.assertEqual(compile_info(e,f)['status'],'unsupported');self.assertEqual(e.invoke(f,[[20,22]]),42)
        e,f=make(target='auto-control');self.assertEqual(e.invoke(f,[[20,22]]),42)

    def test_real_llvm_branch_and_resume_evidence(self):
        if not available():self.skipTest('optional LLVM unavailable')
        e,f=make(target='native-flow');flow=f.compiled_plan.flow;seen=[];resume=flow.kernel.resume
        def observed(pc,flags):
            result=resume(pc,flags);seen.append((pc,result));return result
        flow.kernel.resume=observed
        self.assertEqual(e.invoke(f,[[20,22]]),42)
        self.assertIn('switch i32',str(flow.kernel._module));self.assertIn('br i1',str(flow.kernel._module))
        self.assertEqual(info(e,f)['nativeResumes'],len(seen));self.assertGreater(len({pc for pc,_ in seen}),4)

if __name__=='__main__':unittest.main()
