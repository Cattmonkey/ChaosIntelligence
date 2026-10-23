"""Protected control uses real cursor state; historical code is a test oracle."""
from contextlib import contextmanager
from types import MethodType,SimpleNamespace
from unittest.mock import patch
import unittest

from chaos.language import (Evaluator,Env,Parser,Native,LanguageError,ResourceLimit,
                            ExecutionControl,ReturnSignal,BreakSignal,ContinueSignal,
                            TailCall,check_type,validate_try_cases,_Completion)
from chaos.execution_frames import (ExecutionCursor,ProtectedValueFrame,TryFrame,
                                    FinallyFrame,TryCompletionFrame,ProtocolBridge,frame_roots)
from chaos.suspension import CHECKPOINT,Await,TaskCancelled,GeneratorClose


def _reference_protected(self, node, env):
    """Finish pending calls before leaving a handler/cleanup dynamic scope."""
    try:
        return (yield from self._s_resolve((yield from self._s_evaluate(node, env, True, all_results=True)),all_results=True))
    except ReturnSignal as signal:
        raise ReturnSignal((yield from self._s_resolve(signal.value,all_results=True)))


def _reference_execute(self,node,env,tail=False,*,all_results=False,_completion=None,_compiled_regions=None):
    if node[0] not in ('try','try_cases','finally','try_else'):
        return (yield from self._reference_base_execute(node,env,tail,all_results=all_results,_completion=_completion,_compiled_regions=_compiled_regions))
    self.tick();k=node[0]
    if k in ('loop_else', 'try_else'):
        allowed = ('while', 'for', 'async_for', 'for_pattern', 'async_for_pattern') if k == 'loop_else' else ('try', 'try_cases', 'try_group')
        if len(node) != 3 or not isinstance(node[1], tuple) or not node[1] or node[1][0] not in allowed or not isinstance(node[2], tuple) or not node[2] or node[2][0] != 'block':
            raise LanguageError('正常完成分支需要对应控制结构和 else 块', code='syntax_error')
        # Never catch signals around the whole child: only its historical
        # body/catch boundaries determine a normal, broken or handled exit.
        # Re-enter _s_evaluate so the old child's trace, span, debugger and
        # scheduler checkpoints are retained. Nested calls get no collector.
        outcome = _Completion()
        value = yield from self._s_evaluate(node[1], env, tail, all_results=all_results, _completion=outcome, _compiled_regions=_compiled_regions)
        if outcome.state == 'normal':
            return (yield from self._s_evaluate(node[2], env, tail, all_results=all_results, _compiled_regions=_compiled_regions))
        if outcome.state not in ('broken', 'handled'):
            raise LanguageError('无效内部完成状态')
        return value
    if k in ('try', 'try_cases'):
        if k == 'try_cases': validate_try_cases(node, tick=self.tick)
        try:
            value = yield from self._s_protected_value(node[1], env)
        except ResourceLimit:
            raise
        except LanguageError as error:
            previous_error, previous_context = self.handled_error, self.error_context
            self.handled_error = self.error_context = error
            try:
                cases = [(('p_bind', node[2]), None, node[3])] if k == 'try' else node[2]
                for pattern, guard, handler in cases:
                    # Each rejected arm discards its copied record as well as
                    # its local bindings. Effects and shared handles persist.
                    from chaos.exception_classes import match as match_exception
                    bindings = {}
                    if not match_exception(self, pattern, error, env, bindings): continue
                    scope = Env(env)
                    for name, item in bindings.items(): scope.bind(name, item)
                    if guard is not None and not check_type((yield from self._s_evaluate(guard, scope)), 'Bool'):
                        continue
                    value = yield from self._s_evaluate(handler, scope, tail, all_results=all_results)
                    if _completion is not None: _completion.state = 'handled'
                    return value
                raise error
            except LanguageError as failure:
                from chaos.errors import observe
                raise observe(self, failure)
            finally:
                self.handled_error, self.error_context = previous_error, previous_context
        else:
            if _completion is not None: _completion.state = 'normal'
        return value
    if k == 'finally':
        from chaos.task_groups import GroupAbort
        from chaos.control_prompts import NonlocalTransfer
        try:
            value = (yield from self._s_protected_value(node[1], env))
        except (ResourceLimit, ExecutionControl):
            raise
        except LanguageError as error:
            previous_error, previous_context = self.handled_error, self.error_context
            self.handled_error = self.error_context = error
            try:
                yield from self._s_protected_value(node[2], env)
            finally:
                self.handled_error, self.error_context = previous_error, previous_context
            raise
        except (ReturnSignal, BreakSignal, ContinueSignal, GeneratorClose, GroupAbort, NonlocalTransfer):
            yield from self._s_protected_value(node[2], env)
            raise
        yield from self._s_protected_value(node[2], env)
        return value



@contextmanager
def reference(evaluator):
    original_execute=evaluator._s_execute;original_protected=evaluator._s_protected_value
    evaluator._reference_base_execute=original_execute
    evaluator._s_execute=MethodType(_reference_execute,evaluator)
    evaluator._s_protected_value=MethodType(_reference_protected,evaluator)
    try:
        with patch.object(TryFrame,'KINDS',set()),patch.object(FinallyFrame,'KINDS',set()),patch.object(TryCompletionFrame,'KINDS',set()):yield
    finally:
        evaluator._s_execute=original_execute;evaluator._s_protected_value=original_protected
        del evaluator._reference_base_execute


class ProtectedFrames(unittest.TestCase):
    def observation(self,source,*,old=False,limit=10000,cancel_at=None):
        e=Evaluator(step_limit=limit,timeout=30);effects=[];trace=[];debug=[];original=e.tick
        def tick():
            if cancel_at is not None and e.steps==cancel_at:raise TaskCancelled('fixture cancel')
            original()
        e.tick=tick;e.tool_handler=lambda name,value:effects.append((name,value)) or value
        e.trace_handler=lambda event:trace.append(dict(event)) if event.get('event')=='expression' else None
        e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
        def run():
            try:return ('value',e.run(source))
            except BaseException as error:return ('error',type(error).__name__,getattr(error,'code',None),str(error),getattr(error,'location',None))
        if old:
            with reference(e):result=run()
        else:result=run()
        self.assertIsNone(e.handled_error);self.assertIsNone(e.error_context)
        self.assertIsNone(getattr(e,'_execution_cursor',None));self.assertEqual(e.frames,[])
        return result,e.steps,effects,trace,debug

    def test_semantic_matrix_matches_original_steps_effects_trace_and_debug(self):
        cases=[
            ('try{20}catch e{0}else{42}finally{null}',42),
            ('try{throw 42}catch {value:n,.._} if n<0{0}catch {value:n,.._}{n}else{99}',42),
            ('try{try{throw 7}catch e if do{throw}{0}catch later{99}}catch outer{outer.value}',7),
            ('try{try{1}catch e{99}else{throw 42}}catch outer{outer.value}',42),
            ('var x=1;fn read(){x};fn f(){try{return read()}finally{x=2}};[f(),x]',[1,2]),
            ('fn f(){try{throw 42}finally{return 7}};f()',7),
            ('var n=0;while n<2{n+=1;try{break}finally{continue}}else{42}',42),
            ('fn again(){throw};try{try{throw 42}finally{again()}}catch e{e.value}',42),
            ('var calls=0;async fn cold(){calls+=1;42};let pending=try{cold()}catch e{0}else{cold()};let before=calls;[before,await pending,calls]',[0,42,1]),
            ('var out=[];try{tool("body",1);throw 7}catch e if do{tool("guard",e.value);true}{tool("handler",42)}finally{tool("cleanup",0)}',42),
        ]
        for source,expected in cases:
            with self.subTest(source=source):
                actual=self.observation(source);self.assertEqual(actual,self.observation(source,old=True))
                self.assertEqual(actual[0],('value',expected))

    def test_every_budget_and_local_cancel_threshold_matches_original(self):
        source='try{tool("body",1);throw 7}catch e if do{tool("guard",e.value);true}{tool("handler",42)}else{tool("wrong",99)}finally{tool("cleanup",0)}'
        total=self.observation(source)[1]
        for threshold in range(total+2):
            with self.subTest(budget=threshold):self.assertEqual(self.observation(source,limit=threshold),self.observation(source,old=True,limit=threshold))
            with self.subTest(cancel=threshold):self.assertEqual(self.observation(source,cancel_at=threshold),self.observation(source,old=True,cancel_at=threshold))

    def test_default_protected_syntax_never_enters_legacy_execute(self):
        e=Evaluator();original=e._s_execute
        def execute(node,*args,**kwargs):
            self.assertNotIn(node[0],{'try','try_cases','finally','try_else'})
            return original(node,*args,**kwargs)
        e._s_execute=execute
        self.assertEqual(e.run('[try{1}catch e{0}else{42},try{throw 42}catch {value:n,.._} if n>0{n}finally{null}]'),[42,42])

    def test_protected_tail_resolution_preserves_keywords_contracts_and_returns(self):
        sources=[
            'fn add(*,value:Int)->Int{value+1};try{add(value=41)}catch e{0}',
            'fn bad()->Bool{42};try{bad()}catch e{e.code}',
            'fn again(value=null){throw};fn f(){try{throw 42}catch e{return null |> again}};try{f()}catch out{out.value}',
            'fn recur(n){if n==0{return_values(42,"done")}else{recur(n-1)}};multiple_value_list(fn(){try{return recur(600)}finally{null}})',
        ]
        for source in sources:
            with self.subTest(source=source):self.assertEqual(self.observation(source,limit=100000),self.observation(source,old=True,limit=100000))

    def test_zero_and_multiple_completions_preserve_original_preparation_budget(self):
        for producer in ('return_values()','return_values(null,42)','return_values(42)'):
            for expression in ('try{'+producer+'}finally{return_values(99,100)}',
                               'try{throw 7}catch e{'+producer+'}',
                               'try{1}catch e{0}else{'+producer+'}'):
                source='multiple_value_list(fn(){'+expression+'})'
                with self.subTest(expression=expression):self.assertEqual(self.observation(source),self.observation(source,old=True))

    def test_paused_guard_has_real_scope_error_and_authoritative_arm(self):
        e=Evaluator();e.suspension.begin();c=None
        try:
            node=Parser('try{throw 42}catch e if do{await sleep(0);true}{1}catch later{0}').program()[1][0]
            c=e._s_evaluate(node,Env(e.env))
            while type(next(c)) is not Await:pass
            frame=next(f for f in c.frames if type(f)is TryFrame)
            self.assertEqual(frame.phase,'guard');self.assertEqual(frame.error.value,42)
            self.assertIs(e.handled_error,frame.error);self.assertIn(frame.scope,frame_roots(frame))
            replacement=Parser('99').program();frame.case=(frame.case[0],frame.case[1],replacement)
            try:
                request=c.send(None)
                while True:request=c.send(None)
            except StopIteration as done:self.assertEqual(done.value,99)
            self.assertIsNone(e.handled_error);self.assertIsNone(frame.scope);self.assertIsNone(frame.error)
            self.assertEqual(c.frames,[])
        finally:
            if c is not None:c.close()
            e.suspension.finish()

    def test_cleanup_pending_value_is_actual_state_across_await(self):
        e=Evaluator();e.suspension.begin();c=None
        try:
            c=e._s_evaluate(Parser('try{42}finally{await sleep(0);null}').program()[1][0],Env(e.env))
            while type(next(c)) is not Await:pass
            frame=next(f for f in c.frames if type(f)is FinallyFrame)
            self.assertEqual((frame.phase,frame.value),('cleanup',42));frame.value=99
            try:
                while True:c.send(None)
            except StopIteration as done:self.assertEqual(done.value,99)
            self.assertIsNone(frame.value);self.assertIsNone(frame.pending_error)
        finally:
            if c is not None:c.close()
            e.suspension.finish()

    def test_forced_close_restores_active_error_without_user_cleanup(self):
        e=Evaluator();e.suspension.begin();c=None;seen=[];e.tool_handler=lambda *a:seen.append(a)
        try:
            c=e._s_evaluate(Parser('try{throw 42}finally{await sleep(0);tool("cleanup",null)}').program()[1][0],Env(e.env))
            while type(next(c)) is not Await:pass
            frame=next(f for f in c.frames if type(f)is FinallyFrame)
            self.assertEqual(e.handled_error.value,42);self.assertTrue(frame.active)
            c.close();self.assertEqual(c.frames,[]);self.assertEqual(seen,[])
            self.assertIsNone(e.handled_error);self.assertIsNone(e.error_context);self.assertFalse(frame.active)
        finally:
            if c is not None:c.close()
            e.suspension.finish()

    def test_forced_close_of_guard_restores_prior_error_context(self):
        e=Evaluator();e.suspension.begin();c=None
        previous=LanguageError('outer',value=20);e.handled_error=e.error_context=previous
        try:
            c=e._s_evaluate(Parser('try{throw 42}catch e if do{await sleep(0);true}{99}').program()[1][0],Env(e.env))
            while type(next(c)) is not Await:pass
            frame=next(f for f in c.frames if type(f)is TryFrame)
            self.assertEqual(e.handled_error.value,42)
            c.close();self.assertIs(e.handled_error,previous);self.assertIs(e.error_context,previous)
            self.assertFalse(frame.active);self.assertIsNone(frame.scope);self.assertIsNone(frame.error)
        finally:
            if c is not None:c.close()
            e.handled_error=e.error_context=None;e.suspension.finish()

    def test_tail_completion_secondary_owned_values_are_not_discarded(self):
        for old in (False,True):
            e=Evaluator();foreign=Evaluator();value=Native('foreign',lambda:42,foreign)
            e.env.bind('foreign',value)
            source='fn produce(){return_values(42,foreign)};try{produce()}catch e{e.code}'
            with self.subTest(old=old):
                if old:
                    with reference(e):result=e.run(source)
                else:result=e.run(source)
                self.assertEqual(result,'ownership_error')
                self.assertIsNone(e.handled_error);self.assertIsNone(e.error_context)

    def test_capture_graph_traverses_all_new_frame_pending_values(self):
        from chaos.capture_graph import seal
        e=Evaluator();other=Evaluator();foreign=Native('foreign',lambda:42,other)
        boundary=SimpleNamespace(node=('try',('block',[]),'e',('block',[])),env=Env(),options={},capture=None,tail=False)
        for cls in (TryFrame,FinallyFrame,TryCompletionFrame):
            frame=cls(boundary);frame.owner=e
            if cls is TryFrame:frame.error=LanguageError('held',value=foreign)
            else:frame.value=foreign
            with self.subTest(frame=cls.__name__),self.assertRaises(LanguageError) as error:seal(e,[frame],None)
            self.assertEqual(error.exception.code,'ownership_error')
        frame=ProtectedValueFrame(('literal',None),Env());frame.owner=e;frame.signal=ReturnSignal(foreign)
        with self.assertRaises(LanguageError) as error:seal(e,[frame],None)
        self.assertEqual(error.exception.code,'ownership_error')

    def test_soft_transfer_cleanup_and_hard_failure_skip_are_identical(self):
        for failure in (ResourceLimit('limit'),ExecutionControl('host'),TaskCancelled('foreign'),KeyboardInterrupt(),SystemExit(7)):
            for old in (False,True):
                seen=[]
                def tool(name,value):
                    seen.append(name)
                    if name=='stop':raise failure
                e=Evaluator(tool_handler=tool)
                source='try{tool("stop",null)}catch e{tool("caught",null)}else{tool("else",null)}finally{tool("cleanup",null)}'
                with self.subTest(failure=type(failure).__name__,old=old),self.assertRaises(type(failure)) as caught:
                    if old:
                        with reference(e):e.run(source)
                    else:e.run(source)
                self.assertIs(caught.exception,failure);self.assertEqual(seen,['stop']);self.assertIsNone(e.handled_error)

    def test_dynamic_escape_runs_cleanup_and_cannot_be_caught(self):
        source='var seen=[];let result=call_escape(fn(exit){try{exit(42)}catch e{0}finally{seen=append(seen,"cleanup")}});[result,seen]'
        actual=self.observation(source);self.assertEqual(actual[0],('value',[42,['cleanup']]))
        self.assertEqual(actual,self.observation(source,old=True))

    def test_interleaved_async_guards_retain_distinct_rethrow_contexts(self):
        source='fn current(){try{throw}catch e{e.value}};async fn work(n){try{throw n}catch {value:x,.._} if do{await sleep(0);current()==x}{let before=current();await sleep(0);[before,current()]}};await gather([work(20),work(22)])'
        actual=self.observation(source);self.assertEqual(actual[0],('value',[[20,20],[22,22]]))
        self.assertEqual(actual,self.observation(source,old=True))

    def test_sync_and_async_stream_close_run_cleanup_once_without_context_leak(self):
        for flavor,step,close in [('fn*','next','close'),('async fn*','await anext','await aclose')]:
            source='var seen=[];'+flavor+' stream(){try{throw 42}catch e{try{yield e.value}finally{seen=append(seen,42)}}};let g=stream();'+step+'(g);'+close+'(g);[seen,try{throw}catch e{e.code}]'
            with self.subTest(flavor=flavor):
                actual=self.observation(source);self.assertEqual(actual[0],('value',[[42],'rethrow_error']))
                self.assertEqual(actual,self.observation(source,old=True))
