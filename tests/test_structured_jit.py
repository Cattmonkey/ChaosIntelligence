"""Native regions preserve interpreter scopes, ticks and suspension boundaries."""
import copy
import unittest
from unittest import mock

from chaos.jit import compile_function, compile_info, Unsupported
from chaos.jit_regions import analyze_structured
from chaos.language import Evaluator, LanguageError, Node, ResourceLimit
from chaos.native_jit import BackendUnavailable
from chaos.suspension import CHECKPOINT
from test_native_jit import NATIVE

BODY = '''fn(x:Int,y:Int=2)->Int{
 let doubled=x*y;
 if doubled>=0{
  let answer=doubled+2;
  if answer>10{answer}else{answer*2}
 }else{return -doubled+2}
}'''


def function(source=BODY,target='auto'):
    e=Evaluator(step_limit=1000000)
    return e,compile_function(e,e.run(source),target)


def record(e,f,values,limit=1000000,cancelled=None):
    e.steps=0;e.step_limit=limit;e.cancelled=cancelled or (lambda:False)
    try: answer=('value',e.invoke(f,values))
    except LanguageError as error: answer=('error',error.record())
    except (RecursionError,ValueError) as error:answer=('host',type(error).__name__,str(error))
    return answer,e.steps


class StructuredPortableTests(unittest.TestCase):
    def test_local_bindings_and_selected_nested_branches(self):
        e,f=function()
        for value,expected in ((20,42),(-20,42),(3,16)):
            self.assertEqual(e.invoke(f,[value]),expected)
        info=compile_info(e,f)
        self.assertEqual(info['status'],'native' if NATIVE else 'interpreter')
        self.assertEqual(info['nativeCalls'],3 if NATIVE else 0)
        self.assertEqual(info['interpreterCalls'],0 if NATIVE else 3)
        self.assertEqual(len(info),12)

    def test_bool_local_is_exact_bool_and_never_native_int_operand(self):
        for source,values,expected in (
            ('fn(x,y){let flag=x<y;if flag{x+1}else{y-1}}',[3,4],4),
            ('fn(x,y){let flag=x<y;flag}',[3,4],True),
            ('fn(x,y){let flag=x<y;flag}',[4,3],False)):
            with self.subTest(source=source,values=values):
                e,f=function(source)
                actual=e.invoke(f,values)
                self.assertIs(type(actual),type(expected));self.assertEqual(actual,expected)
                if NATIVE:
                    self.assertEqual(compile_info(e,f)['nativeCalls'],1)
                    self.assertEqual(compile_info(e,f)['interpreterCalls'],1)

    def test_boolean_operands_preserve_original_type_error(self):
        for expression in ('(x<y)<3','(x<y)+3','(x<y)*3','-(x<y)'):
            source='fn(x,y){let z=1;'+expression+'}'
            native_e,native=function(source)
            plain_e,plain=function(source,'interpreter')
            self.assertEqual(compile_info(native_e,native)['status'],'unsupported')
            self.assertEqual(record(native_e,native,[1,2]),record(plain_e,plain,[1,2]))
            self.assertEqual(record(native_e,native,[1,2])[0][0],'error')

    def test_scopes_shadowing_and_immutable_binding_errors(self):
        cases=[('fn(x){let y=x+2;if x>0{let y=1;y}else{y}}',[40],1),
               ('fn(x){let y=x+2;if x>0{let y=1;y}else{0};y}',[40],42),
               ('fn(x){let y=x+2;let y=x+3;y}',[40],None)]
        for source,values,expected in cases:
            a,f=function(source);b,g=function(source,'interpreter')
            self.assertEqual(record(a,f,values),record(b,g,values))
            if expected is not None:self.assertEqual(a.invoke(f,values),expected)

    def test_unsupported_mutation_calls_free_variables_and_boolean_operators(self):
        for source in ('fn(x){var y=x;y+1}', 'fn(x){let y=x;print(y);y}',
                       'fn(x){let y=x;while x>0{x};y}', 'fn(x){let y=x;x==y}',
                       'fn(x){let y=x;x>0&&y>0}', 'fn(x){let y=x;!false}',
                       'let outer=2;fn(x){let y=x;outer+y}'):
            with self.subTest(source=source):
                e,f=function(source)
                self.assertEqual(compile_info(e,f)['status'],'unsupported')

    def test_structured_analysis_is_bounded_and_immutable_fingerprint(self):
        e=Evaluator();f=e.run('fn(x){let y=x+2;if y>0{y}else{-y}}')
        first=analyze_structured(f)
        self.assertEqual(first.fingerprint,analyze_structured(f).fingerprint)
        self.assertEqual(first.node_count,14)
        f.body[1].extend(f.body[1]*200)
        with self.assertRaises(Unsupported):analyze_structured(f)
        self.assertLess(first.node_count,128)

    def test_partial_backend_failure_accounts_each_retained_allocation(self):
        calls=[]
        def partial(expression,prefix,*,code_cache=None):
            self.assertIs(code_cache,e.native_code_cache)
            calls.append(expression)
            if len(calls)==2:raise BackendUnavailable('disabled')
            return object()
        e=Evaluator();plain=e.run('fn(x){let y=x+2;y*2}')
        with mock.patch('chaos.native_jit.compile_kernel',side_effect=partial):
            f=compile_function(e,plain)
        self.assertEqual(len(calls),2)
        self.assertEqual(e.native_kernel_count,1)
        self.assertEqual(compile_info(e,f)['status'],'interpreter')
        self.assertEqual(compile_info(e,f)['fallbackReason'],'disabled')
        self.assertEqual(e.invoke(f,[19]),42)

    def test_quota_rejects_allocation_before_partial_compile(self):
        e=Evaluator();plain=e.run('fn(x){let y=x+2;if y>0{y}else{-y}}')
        e.native_kernel_count=30
        with mock.patch('chaos.native_jit.compile_kernel',side_effect=AssertionError('must not allocate')):
            f=compile_function(e,plain)
        self.assertEqual(e.native_kernel_count,30)
        self.assertEqual(compile_info(e,f)['fallbackReason'],'kernel_limit')
        self.assertEqual(e.invoke(f,[40]),42)


@unittest.skipUnless(NATIVE,'optional LLVM backend unavailable')
class StructuredNativeTests(unittest.TestCase):
    def test_all_four_ordered_comparisons_return_exact_bool(self):
        for op in ('<','<=','>','>='):
            for values in ((1,2),(2,2),(3,2),(-2,1)):
                e,f=function('fn(x,y){x'+op+'y}','native')
                p,g=function('fn(x,y){x'+op+'y}','interpreter')
                native=e.invoke(f,list(values));plain=p.invoke(g,list(values))
                self.assertIs(type(native),bool);self.assertIs(native,plain)
                self.assertEqual(compile_info(e,f)['nativeCalls'],1)

    def test_step_budgets_match_every_node_and_operation_in_selected_paths(self):
        for values in ([20],[-20],[3]):
            e,f=function(target='native');p,g=function(target='interpreter')
            self.assertEqual(record(e,f,values),record(p,g,values))
            total=e.steps
            for limit in range(total+2):
                with self.subTest(values=values,limit=limit):
                    self.assertEqual(record(e,f,values,limit),record(p,g,values,limit))

    def test_cancellation_matches_every_tick_and_location(self):
        for values in ([20],[-20],[3]):
            e,f=function(target='native');p,g=function(target='interpreter')
            total=record(p,g,values)[1]
            for threshold in range(total+2):
                with self.subTest(values=values,threshold=threshold):
                    self.assertEqual(record(e,f,values,cancelled=lambda:e.steps>=threshold),
                                     record(p,g,values,cancelled=lambda:p.steps>=threshold))

    def test_original_checkpoints_and_ticks_preserved(self):
        def schedule(e,f):
            e.steps=0;events=[];iterator=e._s_invoke(f,[3])
            while True:
                try:
                    self.assertIs(next(iterator),CHECKPOINT)
                    events.append((e.steps,dict(e.current_span)))
                except StopIteration as stop:return events,stop.value,e.steps
        e,f=function(target='native');p,g=function(target='interpreter')
        self.assertEqual(schedule(e,f),schedule(p,g))
        self.assertEqual(len(schedule(e,f)[0]),3)

    def test_host_tick_errors_at_every_location_match_interpreter(self):
        for factory in (lambda:ValueError('failed'),lambda:RecursionError('failed'),lambda:ResourceLimit('failed')):
            for threshold in range(1,32):
                outcomes=[]
                for target in ('native','interpreter'):
                    e,f=function(target=target);base=e.tick
                    def tick():
                        base()
                        if e.steps==threshold:raise factory()
                    e.tick=tick;outcomes.append(record(e,f,[3]))
                with self.subTest(error=factory,threshold=threshold):self.assertEqual(outcomes[0],outcomes[1])

    def test_large_local_only_current_region_falls_back(self):
        e,f=function('fn(x:Int)->Int{let square=x*x;square*square}','native')
        self.assertEqual(e.invoke(f,[60000]),12960000000000000000)
        info=compile_info(e,f)
        self.assertEqual((info['nativeCalls'],info['interpreterCalls']),(1,1))
        self.assertEqual(info['lastFallbackReason'],'argument_guard')
        self.assertEqual(e.invoke(f,[2]),16)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],None)

    def test_exact_parameter_guards_exclude_bool_subclasses_float_bigint(self):
        class Integer(int):pass
        for value in (True,Integer(20),20.0,2**100):
            e,f=function('fn(x){let y=x*2;if y>0{y+2}else{-y}}','native')
            p,g=function('fn(x){let y=x*2;if y>0{y+2}else{-y}}','interpreter')
            self.assertEqual(record(e,f,[value]),record(p,g,[value]))
            self.assertEqual(compile_info(e,f)['nativeCalls'],0)
            self.assertEqual(compile_info(e,f)['interpreterCalls'],1)

    def test_debugger_or_trace_at_nested_checkpoint_does_not_replay_bindings(self):
        for instrumentation in ('debug','trace'):
            outcomes=[]
            for target in ('native','interpreter'):
                e,f=function('fn(x){let y=x+2;if y>0{let y=40;y+2}else{0}}',target)
                e.steps=0;it=e._s_invoke(f,[1]);self.assertIs(next(it),CHECKPOINT)
                self.assertIs(next(it),CHECKPOINT);events=[]
                if instrumentation=='debug':e.debug_hook=lambda node,env,frames:events.append((node[0],dict(env.values)))
                else:e.trace_handler=events.append
                with self.assertRaises(StopIteration) as done:next(it)
                outcomes.append((done.exception.value,events,e.steps))
                if target=='native':
                    self.assertEqual(compile_info(e,f)['nativeCalls'],1)
                    self.assertEqual(compile_info(e,f)['interpreterCalls'],1)
            self.assertEqual(outcomes[0],outcomes[1])

    def test_new_ast_node_at_branch_checkpoint_invalidates_remaining_native(self):
        outcomes=[]
        for target in ('native','interpreter'):
            e,f=function('fn(x){let y=x+2;if y>0{y+2}else{0}}',target)
            e.steps=0;it=e._s_invoke(f,[1]);self.assertIs(next(it),CHECKPOINT)
            self.assertIs(next(it),CHECKPOINT)
            branch=f.body[1][1][2];old=branch[1][0]
            branch[1][0]=Node(('binary','+',old[2],Node(('literal',5),dict(old[3].span))),dict(old.span))
            with self.assertRaises(StopIteration) as done:next(it)
            outcomes.append((done.exception.value,e.steps))
            if target=='native':
                self.assertEqual(compile_info(e,f)['nativeCalls'],1)
                self.assertEqual(compile_info(e,f)['interpreterCalls'],1)
                self.assertEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')
        self.assertEqual(outcomes[0],outcomes[1]);self.assertEqual(outcomes[0][0],8)

    def test_exception_thrown_into_nested_checkpoint_has_original_location(self):
        for factory in (lambda:LanguageError('failed'),lambda:ValueError('failed'),lambda:RecursionError('failed')):
            outcomes=[]
            for target in ('native','interpreter'):
                e,f=function(target=target);it=e._s_invoke(f,[3])
                self.assertIs(next(it),CHECKPOINT);self.assertIs(next(it),CHECKPOINT)
                with self.assertRaises(LanguageError) as caught:it.throw(factory())
                outcomes.append(caught.exception.record())
            self.assertEqual(outcomes[0],outcomes[1])

    def test_same_semantic_ast_replacement_has_truthful_fallback_counters(self):
        e,f=function('fn(x){let y=x+2;if y>0{y+2}else{0}}','native')
        f.body=copy.deepcopy(f.body)
        self.assertEqual(e.invoke(f,[1]),5)
        info=compile_info(e,f)
        self.assertEqual((info['nativeCalls'],info['interpreterCalls']),(0,1))
        self.assertEqual(info['lastFallbackReason'],'definition_changed')
        e,f=function('fn(x){let y=x+2;if y>0{y+2}else{0}}','native')
        it=e._s_invoke(f,[1]);self.assertIs(next(it),CHECKPOINT)
        self.assertIs(next(it),CHECKPOINT)
        branch=f.body[1][1][2];branch[1][0]=copy.deepcopy(branch[1][0])
        with self.assertRaises(StopIteration) as done:next(it)
        self.assertEqual(done.exception.value,5)
        info=compile_info(e,f)
        self.assertEqual((info['nativeCalls'],info['interpreterCalls']),(1,1))
        self.assertEqual(info['lastFallbackReason'],'definition_changed')

    def test_trace_disable_after_deoptimization_never_reenters_native(self):
        e,f=function('fn(x){let y=x+2;if y>0{if y>2{y+2}else{0}}else{0}}','native')
        it=e._s_invoke(f,[1]);self.assertIs(next(it),CHECKPOINT)
        self.assertIs(next(it),CHECKPOINT)
        before=compile_info(e,f)['nativeCalls'];events=[];e.trace_handler=events.append
        self.assertIs(next(it),CHECKPOINT)
        e.trace_handler=None
        with self.assertRaises(StopIteration) as done:next(it)
        self.assertEqual(done.exception.value,5);self.assertTrue(events)
        self.assertEqual(compile_info(e,f)['nativeCalls'],before)
        self.assertEqual(compile_info(e,f)['lastFallbackReason'],'expression_trace')
        self.assertEqual(compile_info(e,f)['interpreterCalls'],1)

    def test_body_replacement_at_entry_keeps_captured_original_body(self):
        outcomes=[]
        for target in ('native','interpreter'):
            e,f=function('fn(x){let y=x+2;if y>0{y+2}else{0}}',target)
            e.steps=0;it=e._s_invoke(f,[1]);self.assertIs(next(it),CHECKPOINT)
            f.body=Evaluator().run('fn(x){let y=x+3;if y>0{y+3}else{0}}').body
            while True:
                try:self.assertIs(next(it),CHECKPOINT)
                except StopIteration as done:
                    outcomes.append((done.value,e.steps));break
        self.assertEqual(outcomes[0],outcomes[1])

    def test_return_contract_and_selected_early_return_are_original(self):
        e,f=function('fn(x)->String{let y=x*2;if y>0{return y}else{y+2}}','native')
        p,g=function('fn(x)->String{let y=x*2;if y>0{return y}else{y+2}}','interpreter')
        for value in (20,-20):self.assertEqual(record(e,f,[value]),record(p,g,[value]))

    def test_region_context_does_not_leak_to_other_function_calls(self):
        result=Evaluator().run('let f=compile_function(fn(x){let y=x*2;if y>0{y+2}else{-y+2}},"native");async fn work(x){f(x)};let values=await gather([work(20),work(-20)]);[values,compile_info(f)]')
        self.assertEqual(result[0],[42,42]);self.assertEqual(result[1]['nativeCalls'],2)
        self.assertEqual(result[1]['interpreterCalls'],0)


if __name__=='__main__':unittest.main()
