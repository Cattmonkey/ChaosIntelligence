"""General original-language host semantics inside real resumable native CFG."""
import copy
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native,TailCall,Parser,serializable
from chaos.jit import compile_function,compile_info,compile_optimization_info
from chaos.jit_flow import info
from chaos.native_jit import BackendUnavailable
from chaos.suspension import CHECKPOINT,Task,Generator
from test_native_jit import available


def make(source,target='portable'):
    calls=[]
    e=Evaluator(step_limit=3000000,timeout=90,tool_handler=lambda name,value:calls.append((name,value)) or value)
    value=e.run(source)
    fn,args=value if type(value) is list else (value,[])
    if target=='portable':
        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('fixture')):f=compile_function(e,fn,'auto-flow')
    else:f=compile_function(e,fn,target)
    calls.clear();return e,f,args,calls

def outcome(source,target,*,limit=3000000,cancel=None,kwargs=None,trace=False):
    e,f,args,calls=make(source,target);events=[]
    e.steps=0;e.step_limit=limit;e.cancelled=lambda:cancel is not None and e.steps>=cancel
    if trace:e.trace_handler=events.append
    try:result=('value',e.invoke(f,args,kwargs))
    except LanguageError as error:result=('error',serializable(error.record()))
    except ExecutionControl:result=('cancel',)
    if target!='interpreter':assert f.compiled_plan.flow is not None,compile_info(e,f)
    assert e.frames==[] and e.depth==0
    return (result,e.steps,calls,events),e,f

class NativeCompositionTests(unittest.TestCase):
    def compare(self,source,**options):
        expected=outcome(source,'interpreter',**options)[0]
        for target in ('portable','auto-flow'):
            self.assertEqual(outcome(source,target,**options)[0],expected,(source,target))
        return expected

    def test_non_tail_calls_execute_in_original_order(self):
        self.compare('[fn(cb){cb(1);cb(2);42},[fn(n){tool("call",n)}]]')
        self.compare('[fn(cb){let first=cb(20);cb(22)+first},[fn(n){tool("call",n)}]]')
        self.compare('[fn(cb){var total=0;for x in [20,22]{total=total+cb(x)};total},[fn(n){tool("call",n)}]]')

    def test_no_scalar_and_host_only_branches_have_real_flow(self):
        for source in ('[fn(cb){cb()},[fn(){42}]]','[fn(cb){if cb(true){cb(42)}else{cb(0)}},[fn(x){x}]]'):
            self.compare(source)
            e,f,args,_=make(source,'auto-flow');self.assertEqual(e.invoke(f,args),42)
            self.assertGreater(info(e,f)['resumeCalls'],0)
            if available():self.assertGreater(info(e,f)['nativeResumes'],0)
        e,f,args,_=make('[fn(cb){cb()},[fn(){42}]]','auto-flow')
        self.assertEqual(compile_optimization_info(e,f)['kernelCount'],0)
        self.assertFalse(compile_optimization_info(e,f)['enabled'])

    def test_call_positions_in_conditions_short_circuit_pipeline_and_return(self):
        cases=['[fn(cb){if cb(true){return cb(42)}else{cb(0)}},[fn(x){tool("call",x)}]]',
          '[fn(cb){false&&cb(true);true||cb(false);true&&cb(true)},[fn(x){tool("call",x)}]]',
          '[fn(cb){let a=20|>cb;(a+2)|>cb},[fn(x){tool("call",x)}]]',
          '[fn(cb){var n=0;while cb(n<2){cb(n);n=n+1};n},[fn(x){tool("call",x)}]]']
        for source in cases:self.compare(source)

    def test_actual_compiled_tail_recursion_and_return_contracts(self):
        source='var recur=null;recur=compile_function(fn(n:Int,acc:Int)->Int{if n==0{acc}else{recur(n-1,acc+1)}},target=TARGET);[recur(1200,0),compile_flow_info(recur)]'
        for target in ('portable','auto-flow'):
            e=Evaluator(step_limit=1000000,timeout=90)
            context=patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('fixture')) if target=='portable' else __import__('contextlib').nullcontext()
            with context:result=e.run(source.replace('TARGET','"auto-flow"'))
            self.assertEqual(result[0],1200);self.assertGreater(result[1]['resumeCalls'],1200)
            if target=='auto-flow' and available():self.assertGreater(result[1]['nativeResumes'],1200)
        self.compare('[fn(cb)->String{cb()},[fn(){42}]]')

    def test_non_tail_recursion_retains_original_depth_limit(self):
        for target in ('interpreter','auto-flow'):
            e=Evaluator(step_limit=1000000,timeout=90)
            with self.assertRaises(ResourceLimit) as error:
                e.run('var recur=null;recur=compile_function(fn(n){if n==0{0}else{1+recur(n-1)}},target="'+target+'");recur(200)')
            self.assertEqual(error.exception.code,'resource_limit')
            self.assertIn('深度',error.exception.message)
            self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)

    def test_all_runtime_binding_modes_defaults_patterns_and_keywords(self):
        self.compare('[fn(a,/,b=20,*,c=2){a+b+c},[20]]')
        self.compare('[fn(pair as (a,b),*extra,c=1,**options){a+b+length(extra)+c+length(keys(options))},[(20,18),9]]',kwargs={'c':2,'x':1})
        self.compare('[fn(*values,**named){[values,named]},[20,22]]',kwargs={'x':42})
        self.compare('[fn(a,b,c,d,e,f,g,h,i){a+b+c+d+e+f+g+h+i},[1,2,3,4,5,6,7,8,9]]')
        self.compare('[fn(pair as (a,b)){a+b},[(1,)]]')
        self.compare('[fn(a,/,*,b){a+b},[20]]',kwargs={'a':1,'b':22})

    def test_call_spreads_duplicates_and_evaluation_order(self):
        cases=['[fn(cb){cb(*[20],**{right:22})},[fn(left,*,right){left+right}]]',
          '[fn(cb){cb(tool("a",20),right=tool("b",22))},[fn(left,*,right){left+right}]]',
          '[fn(cb){cb(x=tool("one",1),**{x:tool("two",2)})},[fn(**args){args}]]']
        for source in cases:self.compare(source)

    def test_nested_definitions_closures_class_and_generic_dispatch(self):
        cases=['fn(){let offset=20;fn add(x){offset+x};add(22)}',
          'fn(){class C{var n=20;fn add(self,x){self.n+x}}let c=C();c.add(22)}',
          'fn(){let g=generic_method(generic_function("g"),["Int"],fn(x){x+20});g(22)}',
          'data Option{Some(value),None};fn(){match Some(42){Some(x)=>x,None()=>0}}',
          'fn(){let f=fn*(x){yield x};let stream=f(42);next(stream).value}']
        for source in cases:self.compare(source)

    def test_containers_access_and_all_assignment_forms(self):
        cases=['fn(){let xs=[1,22];xs[0]=20;xs[0]+xs[1]}',
          'fn(){class C{var n=20};let c=C();c.n+=22;c.n}',
          'fn(){var a=0;var b=0;[a,b]=[20,22];a+=b;a}',
          'fn(){let xs=[x*2 for x in [10,11]];xs[0]+xs[1]}',
          'fn(){let m=hash_map([("x",20)]);hash_get(hash_set(m,"y",22),"x")+hash_get(hash_set(m,"y",22),"y")}']
        for source in cases:self.compare(source)

    def test_protected_try_cleanup_and_outer_loop_signals(self):
        cases=['[fn(cb){try{cb()}catch error{error.code}finally{tool("cleanup",1)}},[fn(){throw ValueError("fixture")}]]',
          'fn(){var n=0;for x in [1,2,3]{try{if x==1{continue}else{null};if x>2{break}else{null};n=n+x}finally{tool("cleanup",x)}}else{n=n+100};n}',
          '[fn(cb){try{return cb()}finally{tool("cleanup",1)}},[fn(){tool("body",42)}]]',
          'fn(){try{throw ValueError("first")}catch error{throw TypeError("second")}}']
        for source in cases:self.compare(source)

    def test_resource_context_and_conditions_use_original_boundaries(self):
        self.compare('fn(){class C{fn __enter__(self){tool("enter",20)}fn __exit__(self,error){tool("exit",null);null}}with C() as value{value+22}}')
        self.compare('import "std/context" as C;fn(){let v=C.context_var("v",20);with C.context_bind(v,42){C.context_get(v)}}')

    def test_cold_task_generator_and_async_generator_are_not_consumed(self):
        for factory,expected in [('async fn(){tool("effect",42)}',Task),('fn*(){tool("effect",1);yield 42}',Generator),('async fn*(){yield 42}',Generator)]:
            for target in ('portable','auto-flow'):
                e,f,args,calls=make('[fn(cb){cb()},['+factory+']]',target)
                value=e.invoke(f,args);self.assertIsInstance(value,expected);self.assertEqual(value.state,'created');self.assertEqual(calls,[])

    def test_exact_tick_cancel_and_checkpoint_traces_with_calls(self):
        source='[fn(cb){var n=0;for x in [1,2]{n=n+cb(x)};n},[fn(x){tool("call",x)}]]'
        expected,e,f=outcome(source,'interpreter');total=expected[1]
        for boundary in range(total+2):
            for option in ({'limit':boundary},{'cancel':boundary}):
                for target in ('portable','auto-flow'):
                    self.assertEqual(outcome(source,target,**option)[0],outcome(source,'interpreter',**option)[0])
        self.compare(source,trace=True)

    def test_signature_replacement_deopts_but_mutable_default_stays_live(self):
        e,f,args,_=make('fn(values=[20]){values[0]*2}','auto-flow')
        self.assertEqual(e.invoke(f,[]),40)
        f.params[0][3][0]=21;self.assertEqual(e.invoke(f,[]),42)
        self.assertNotEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')
        old=f.params[0];f.params[0]=(old[0],old[1],old[2],[22])
        self.assertEqual(e.invoke(f,[]),44);self.assertEqual(compile_info(e,f)['lastFallbackReason'],'definition_changed')
        e,f,args,_=make('fn(value as (a,b)){a+b}','auto-flow')
        from chaos.tuple_values import TupleValue
        old=f.params[0];pattern=copy.deepcopy(old[4]);pattern[1][0]=('p_literal',20);f.params[0]=old[:4]+(pattern,)
        with self.assertRaises(LanguageError):e.invoke(f,[TupleValue([1,2])])

    def test_pattern_signature_compilation_preserves_budget_and_cancel(self):
        for source in ('fn(value){value}','fn(value as [a]){a}'):
            for target in ('auto-flow','native-flow'):
                e=Evaluator();f=e.run(source);e.step_limit=e.steps
                with self.assertRaises(ResourceLimit):compile_function(e,f,target)
                e=Evaluator();f=e.run(source);e.cancelled=lambda:True
                with self.assertRaises(ResourceLimit) as error:compile_function(e,f,target)
                self.assertIn('取消',error.exception.message)
                def interrupt():raise ExecutionControl()
                e.cancelled=interrupt
                with self.assertRaises(ExecutionControl):compile_function(e,f,target)
        e=Evaluator();f=e.run('fn(value as [a]){a}')
        row=f.params[0];f.params[0]=row[:4]+(('invalid_pattern',),)
        self.assertEqual(compile_info(e,compile_function(e,f,'auto-flow'))['fallbackReason'],'parameter_pattern')

    def test_pattern_inspection_bounds_forged_cycles_and_rechecks(self):
        from chaos.jit_regions import analyze_structured
        from chaos.jit import Unsupported
        e=Evaluator();f=e.run('fn(value as [a]){a}')
        row=f.params[0];children=[];pattern=('p_list',children,None);children.append(pattern)
        f.params[0]=row[:4]+(pattern,)
        with self.assertRaises(Unsupported) as error:analyze_structured(f,control_flow=True,resumable=True)
        self.assertEqual(str(error.exception),'depth_limit')
        self.assertEqual(compile_info(e,compile_function(e,f,'auto-flow'))['fallbackReason'],'depth_limit')

    def test_compilation_never_traverses_default_payload_hooks(self):
        calls=[]
        class Trap:
            def __repr__(self):calls.append('repr');raise AssertionError('hook')
            def __str__(self):calls.append('str');raise AssertionError('hook')
            def __eq__(self,other):calls.append('eq');raise AssertionError('hook')
            def __iter__(self):calls.append('iter');raise AssertionError('hook')
        e=Evaluator();f=e.run('fn(x=null){x}');p=f.params[0];f.params[0]=p[:3]+(Trap(),)
        result=compile_function(e,f,'auto-flow');self.assertIsNotNone(result.compiled_plan.flow);self.assertEqual(calls,[])

    def test_optional_region_proof_width_and_kernel_quota_do_not_reject_flow(self):
        self.compare('[fn(a,b,c,d,e,f,g,h,i){a+b+c+d+e+f+g+h+i},[1,2,3,4,5,6,7,8,9]]')
        if not available():return
        e=Evaluator();f=e.run('fn(x){x+1}');e.native_kernel_count=31
        f=compile_function(e,f,'native-flow');self.assertEqual(e.native_kernel_count,32);self.assertEqual(e.invoke(f,[41]),42)
        self.assertEqual(compile_optimization_info(e,f)['kernelCount'],0)
        e=Evaluator();f=e.run('fn(x){x+1}')
        with patch('chaos.native_jit.compile_kernel',side_effect=BackendUnavailable('scalar fixture')):f=compile_function(e,f,'native-flow')
        self.assertEqual(e.invoke(f,[41]),42);self.assertGreater(info(e,f)['nativeResumes'],0)

    def test_mid_callback_definition_change_is_sticky_and_cleanup_executes(self):
        e=Evaluator();box={};events=[]
        def mutate():box['f'].params[0]=('cb','Any','positional',None);return None
        e.env.bind('mutate',Native('mutate',mutate,e))
        e.env.bind('cleanup',Native('cleanup',lambda:events.append('cleanup'),e))
        value=e.run('[fn(cb){try{cb()}catch error{42}finally{cleanup()}},[fn(){mutate();do{20}}]]')
        box['f']=compile_function(e,value[0],'auto-flow')
        with self.assertRaises(LanguageError) as error:e.invoke(box['f'],value[1])
        self.assertEqual(error.exception.code,'definition_changed');self.assertEqual(events,['cleanup']);self.assertEqual(e.frames,[])

    def test_real_native_branch_chooses_host_call_successor(self):
        if not available():self.skipTest('optional LLVM unavailable')
        e,f,args,calls=make('[fn(cb){if cb(true){cb(42)}else{cb(0)}},[fn(x){tool("call",x)}]]','native-flow')
        flow=f.compiled_plan.flow;self.assertIn('br i1',str(flow.kernel._module))
        self.assertEqual(e.invoke(f,args),42);self.assertEqual(calls,[('call',True),('call',42)])
        self.assertGreater(flow.native_resumes,0);self.assertEqual(flow.portable_resumes,0)

    def test_old_targets_and_suspending_flavor_limits_are_unchanged(self):
        e=Evaluator();plain=e.run('fn(cb){cb()}')
        for target in ('auto','auto-control'):
            self.assertEqual(compile_info(e,compile_function(e,plain,target))['status'],'unsupported')
        # Scalar/control limits remain; flow now deliberately includes both
        # generator flavors while preserving cold creation and explicit consume.
        for source in ('fn*(){yield 42}','async fn*(){yield 42}'):
            for target in ('auto','auto-control'):
                f=compile_function(e,e.run(source),target)
                self.assertEqual(compile_info(e,f)['fallbackReason'],'suspending_function')
                self.assertIsNone(f.compiled_plan.flow)
            with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('generator-contract-portable')):
                f=compile_function(e,e.run(source),'auto-flow')
            self.assertIsNotNone(f.compiled_plan.flow);self.assertEqual(f.compiled_plan.flow.resume_calls,0)
            binding='generator208_async'if source.startswith('async')else'generator208_sync'
            e.env.bind(binding,f)
            first=e.run('let stream208='+binding+'();'+('await anext(stream208)'if source.startswith('async')else'next(stream208)'))
            self.assertEqual(first,{'done':False,'value':42});self.assertGreater(f.compiled_plan.flow.portable_resumes,0)
        for target in ('auto','auto-control'):
            f=compile_function(e,e.run('async fn(){42}'),target)
            self.assertEqual(compile_info(e,f)['status'],'unsupported')
            self.assertEqual(compile_info(e,f)['fallbackReason'],'suspending_function')
        for target in ('native','native-control'):
            with self.assertRaises(LanguageError) as error:compile_function(e,e.run('async fn(){42}'),target)
            self.assertEqual(error.exception.code,'compiler_error')
            self.assertIn('suspending_function',error.exception.message)

        from chaos.compiled_flow_state import FlowState
        from chaos.suspension import Await
        from chaos.native_flow import compile_flow as actual_compile_flow
        source='async fn(value){try{await sleep(0);let answer=tool("body",value);answer+2}finally{tool("cleanup",1)}}'
        targets=('portable','native-flow') if available() else ('portable',)
        for target in targets:
            with self.subTest(target=target):
                calls=[];states=[]
                evaluator=Evaluator(step_limit=30000,timeout=30,tool_handler=lambda name,value:calls.append((name,value)) or value)
                plain=evaluator.run(source)
                compiler=patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('async-contract-portable')) if target=='portable' else patch('chaos.native_flow.compile_flow',wraps=actual_compile_flow)
                with compiler as compilation:
                    function=compile_function(evaluator,plain,'auto-flow' if target=='portable' else 'native-flow')
                self.assertEqual(compilation.call_count,1)
                program=function.compiled_plan.flow
                self.assertIsNotNone(program)
                self.assertEqual(program.resume_calls,0)
                self.assertEqual(calls,[])
                dispatcher=program if target=='portable' else program.kernel
                method='portable' if target=='portable' else 'resume'
                original_dispatch=getattr(dispatcher,method)
                original_init=FlowState.__init__
                def entered(state,*args,**kwargs):
                    original_init(state,*args,**kwargs);states.append(state)
                failure=None
                try:
                    with patch.object(FlowState,'__init__',entered),patch.object(dispatcher,method,wraps=original_dispatch) as dispatch:
                        task=evaluator.invoke(function,[40])
                        self.assertIsInstance(task,Task)
                        self.assertEqual(task.state,'created')
                        self.assertEqual(calls,[])
                        self.assertEqual(states,[])
                        self.assertEqual(program.resume_calls,0)
                        def awaited():
                            return (yield Await(task))
                        self.assertEqual(evaluator.suspension.drive(awaited()),42)
                        self.assertGreater(dispatch.call_count,0)
                        self.assertEqual(dispatch.call_count,program.portable_resumes if target=='portable' else program.native_resumes)
                    self.assertEqual(calls,[('body',40),('cleanup',1)])
                    self.assertTrue(states)
                    self.assertTrue(all(state.asynchronous and state.closed for state in states))
                    self.assertGreater(program.resume_calls,0)
                    if target=='portable':
                        self.assertIsNone(program.kernel);self.assertEqual(program.native_resumes,0)
                    else:
                        self.assertIsNotNone(program.kernel);self.assertEqual(program.portable_resumes,0)
                    self.assertEqual(task.state,'completed')
                    self.assertEqual(task.runner.capture_frames,[])
                    for state in states:
                        self.assertEqual(state.frames,[])
                        self.assertEqual(state._owned_activations,[])
                        self.assertIsNone(state.pending_cursor)
                        self.assertIsNone(state.pending_request)
                        self.assertEqual(state.roots(),())
                    self.assertEqual(evaluator.frames,[]);self.assertEqual(evaluator.depth,0)
                except BaseException as caught:
                    failure=caught;raise
                finally:evaluator.suspension.finish(failure)
        # Strict native flow admits ordinary async_fn on every interpreter;
        # an absent backend is a real backend failure, not the old flavor gate.
        evaluator=Evaluator();plain=evaluator.run('async fn(){42}')
        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('async-contract-backend-required')) as compiler:
            with self.assertRaises(LanguageError) as error:compile_function(evaluator,plain,'native-flow')
        self.assertEqual(compiler.call_count,1)
        self.assertEqual(error.exception.code,'compiler_error')
        self.assertIn('async-contract-backend-required',error.exception.message)

if __name__=='__main__':unittest.main()
