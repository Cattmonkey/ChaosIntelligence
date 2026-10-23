"""Authoritative native control state and the exact pre-frame source oracle."""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from chaos.language import (Evaluator, Env, ExecutionControl, LanguageError,
                            Native, Parser, ResourceLimit, TailCall)
from chaos.execution_frames import (ExecutionCursor, InvocationFrame, Done, Push,
                                    ProtocolBridge, frame_roots)
from chaos.execution_control import (CallbackRequest, PromptHandlerRequest,
                                     ControlCallbackFrame, PromptHandlerFrame,
                                     PromptCallFrame, ControlBarrierFrame,
                                     DynamicWindFrame)
from chaos.execution_scopes import PromptScopeFrame
from chaos.control_prompts import (Request, PromptScope, PromptTransfer,
                                   tag, callback, handled, resolve)
from chaos.multiple_values import Completion, components
from chaos.suspension import CHECKPOINT, Runner, GeneratorClose, TaskCancelled


def snapshot(source, legacy=False, limit=100000, cancel=None, setup=None, lexical=False):
    from _control_reference143 import reference
    effects, trace, debug = [], [], []
    e = Evaluator(step_limit=limit, timeout=30,
                  tool_handler=lambda name, value: effects.append((name, value)) or value,
                  trace_handler=trace.append)
    if setup is not None:setup(e)
    e.cancelled = lambda: cancel is not None and e.steps >= cancel
    e.debug_hook = lambda node, env, frames: debug.append((node[0], dict(e.current_span), e.steps))
    with reference(e, lexical=lexical) if legacy else nullcontext():
        try:
            result = ('value', e.run(source))
        except LanguageError as error:
            result = ('error', error.record())
        except ExecutionControl as error:
            result = ('control', type(error).__name__, str(error))
    return result, e.steps, effects, trace, debug


@contextmanager
def runner_scope(e, capabilities=None):
    e.suspension.begin()
    runner = Runner(iter(()))
    runner.capabilities = capabilities
    runner.control_state = e.suspension.control_state
    runner.context_state = e.suspension.context_state
    runner.condition_state = e.suspension.condition_state
    runner.decimal_state = e.suspension.decimal_state
    e.suspension.current = runner
    try:
        yield runner
    finally:
        e.suspension.current = None
        e.suspension.finish()


class NativeControlFrames(unittest.TestCase):
    def active(self, e, cls):
        return next(frame for frame in reversed(e._execution_cursor.frames) if type(frame) is cls)

    def direct(self, e, frame):
        frame.owner = e
        return SimpleNamespace(owner=e)

    def assert_oracle(self, source, expected):
        modern, old = snapshot(source), snapshot(source, legacy=True)
        self.assertEqual(modern[0], ('value', expected))
        self.assertEqual(modern, old)

    def test_compatibility_entries_are_only_typed_gateway_requests(self):
        e = Evaluator()
        e._execution_cursor = object()
        function = Native('unused', lambda: self.fail('gateway executed callback'), e)
        request = Request('barrier', (function,))
        self.assertIs(next(resolve(e, request)), request)
        cb = next(callback(e, function, (20, 22), frozenset()))
        self.assertIs(type(cb), CallbackRequest)
        self.assertIs(cb.function, function)
        self.assertEqual(cb.args, (20, 22))
        scope, signal = object(), object()
        hr = next(handled(e, scope, function, signal))
        self.assertIs(type(hr), PromptHandlerRequest)
        self.assertEqual((hr.scope, hr.handler, hr.signal), (scope, function, signal))

    def test_callback_pushes_real_invocation_with_original_force_flags(self):
        e = Evaluator()
        function = Native('unused', lambda: 42, e)
        frame = ControlCallbackFrame(function, (20, 22), frozenset({'one'}))
        with runner_scope(e, frozenset({'one', 'two'})) as runner:
            action = frame.step(self.direct(e, frame), None, None)
            self.assertIs(type(action), Push)
            self.assertIs(type(action.frame), InvocationFrame)
            self.assertIs(action.frame.fn, function)
            self.assertEqual(action.frame.args, [20, 22])
            self.assertTrue(action.frame.force)
            self.assertTrue(action.frame._context_callback)
            self.assertTrue(action.frame.all_results)
            self.assertEqual(runner.capabilities, frozenset({'one'}))
            self.assertEqual(e.steps, 0)
            frame.close(SimpleNamespace(owner=e))

    def test_callback_done_restores_exact_original_runner_and_capability_object(self):
        e = Evaluator()
        original = frozenset({'one', 'two'})
        with runner_scope(e, original) as runner:
            frame = ControlCallbackFrame(None, authority=frozenset({'one'}))
            cursor = self.direct(e, frame)
            frame.step(cursor, None, None)
            other = Runner(iter(()))
            other.capabilities = frozenset({'other'})
            e.suspension.current = other
            completion = Completion(e, (20, 22))
            action = frame.step(cursor, completion, None)
            self.assertIs(type(action), Done)
            self.assertIs(action.value, completion)
            self.assertIs(runner.capabilities, original)
            self.assertEqual(other.capabilities, frozenset({'other'}))

    def test_callback_error_and_hard_close_restore_authority_without_replay(self):
        e = Evaluator()
        calls = []
        for failure in (LanguageError('ordinary'), ResourceLimit('hard'), GeneratorExit()):
            with self.subTest(failure=type(failure).__name__), runner_scope(e, frozenset({'saved'})) as runner:
                original = runner.capabilities
                frame = ControlCallbackFrame(Native('body', lambda: calls.append(1), e), authority=frozenset())
                cursor = self.direct(e, frame)
                frame.step(cursor, None, None)
                with self.assertRaises(type(failure)) as caught:
                    frame.step(cursor, None, failure)
                self.assertIs(caught.exception, failure)
                self.assertIs(runner.capabilities, original)
                frame.close(cursor)
                self.assertIs(runner.capabilities, original)
        self.assertEqual(calls, [])

    def test_prompt_and_escape_keep_full_zero_null_many_completions(self):
        self.assert_oracle('''let p=prompt_tag();[
          multiple_value_list(fn(){call_prompt(p,fn(){return_values(20,22)})}),
          multiple_value_list(fn(){call_prompt(p,fn(){abort_prompt(p)})}),
          multiple_value_list(fn(){call_escape(fn(exit){exit(null)})}),
          multiple_value_list(fn(){call_escape(fn(exit){exit(20,22)})})]''',
          [[20,22],[],[None],[20,22]])

    def test_callbacks_force_async_body_and_share_logical_context(self):
        self.assert_oracle('''let key=context_var("key",1);let p=prompt_tag();
          with key.set(40) as token{let value=call_prompt(p,async fn(){
            await sleep(0);abort_prompt(p,key.get())},async fn(n){await sleep(0);n+2});
            [value,key.get(),prompt_available(p)]}''', [42,40,False])

    def test_generator_callbacks_remain_cold_and_result_handles_stay_single(self):
        self.assert_oracle('''var count=0;fn* body(){count+=1;yield 42};
          async fn task(){count+=1;40};let g=call_prompt(prompt_tag(),body);
          let t=call_escape(fn(exit){task()});let before=count;
          [type_of(g),type_of(t),before,next(g).value,await t,count]''',
          ['Generator','Task',0,42,40,2])

    def test_native_and_lexical_handlers_use_same_actual_handler_frame(self):
        e, seen = Evaluator(), []
        def probe(kind):
            frame = self.active(e, PromptHandlerFrame)
            self.assertEqual(frame.phase, 'handler')
            self.assertEqual(frame.scope.state, 'closed')
            self.assertIsNone(frame.scope.handler)
            self.assertTrue(frame.authority_entered)
            self.assertIs(frame.owner, e)
            seen.append(kind)
        e.env.bind('probe', Native('probe', probe, e))
        value = e.run('''let p=prompt_tag();[
          call_prompt(p,fn(){abort_prompt(p,20)},fn(n){probe("native");n+22}),
          with prompt_scope(p,fn(n){probe("lexical");n+2}) as s{abort_prompt(p,40)}]''')
        self.assertEqual(value, [42,42])
        self.assertEqual(seen, ['native','lexical'])

    def test_handler_none_prepares_completion_under_all_three_ceilings(self):
        e = Evaluator()
        function = e.run('fn(){42}')
        with runner_scope(e, frozenset({'a','b'})) as runner:
            original = runner.capabilities
            scope = PromptScope(e, tag(e))
            scope.capabilities = frozenset({'a','c'})
            signal = PromptTransfer(scope, Completion(e, (function,)), frozenset({'b','c'}))
            frame = PromptHandlerFrame(scope, None, signal)
            action = frame.step(self.direct(e, frame), None, None)
            self.assertIs(type(action), Done)
            self.assertEqual(frame.ceiling, frozenset())
            self.assertEqual(components(action.value, e)[0].capabilities, frozenset())
            self.assertIs(runner.capabilities, original)

    def test_handler_authority_spans_async_pause_and_restores_saved_runner(self):
        e = Evaluator()
        seen = []
        def probe():
            frame = self.active(e, PromptHandlerFrame)
            self.assertTrue(frame.authority_entered)
            self.assertEqual(frame.ceiling, frozenset())
            self.assertEqual(e.suspension.current.capabilities, frozenset())
            seen.append(frame)
        e.env.bind('probe', Native('probe', probe, e))
        e.env.bind('limited', Native('limited', lambda f: TailCall(f, []), e, capabilities=frozenset()))
        self.assertEqual(e.run('''let p=prompt_tag();call_prompt(p,
          fn(){limited(fn(){abort_prompt(p,40)})},async fn(n){await sleep(0);probe();n+2})'''),42)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].authority_entered)

    def test_handler_close_restores_authority_even_after_current_runner_changes(self):
        e = Evaluator()
        with runner_scope(e, frozenset({'outer'})) as runner:
            original = runner.capabilities
            scope = PromptScope(e, tag(e))
            signal = PromptTransfer(scope, Completion(e, (42,)), frozenset())
            frame = PromptHandlerFrame(scope, Native('unused', lambda n: n, e), signal)
            cursor = self.direct(e, frame)
            action = frame.step(cursor, None, None)
            self.assertIs(type(action.frame), ControlCallbackFrame)
            self.assertTrue(frame.authority_entered)
            other = Runner(iter(()))
            other.capabilities = frozenset({'other'})
            e.suspension.current = other
            frame.close(cursor)
            self.assertIs(runner.capabilities, original)
            self.assertEqual(other.capabilities, frozenset({'other'}))

    def test_pending_prompt_handler_field_changes_actual_invocation(self):
        e = Evaluator()
        replacement = e.run('fn(n){n+2}')
        def replace_handler():
            frame = self.active(e, PromptCallFrame)
            self.assertEqual(frame.phase, 'body')
            frame.handler = replacement
        e.env.bind('replace_handler', Native('replace_handler', replace_handler, e))
        self.assertEqual(e.run('''let p=prompt_tag();call_prompt(p,
          fn(){replace_handler();abort_prompt(p,40)},fn(n){n+1})'''),42)

    def test_pending_wind_after_and_full_result_fields_drive_resumption(self):
        e = Evaluator()
        def replace_after():
            frame = self.active(e, DynamicWindFrame)
            self.assertEqual(frame.phase, 'body')
            frame.after = replacement
        def mutate_result():
            frame = self.active(e, DynamicWindFrame)
            self.assertEqual(frame.phase, 'after_success')
            self.assertEqual(components(frame.result, e), (20,22))
            frame.result = Completion(e, (40,2))
        e.env.bind('replace_after', Native('replace_after', replace_after, e))
        e.env.bind('mutate_result', Native('mutate_result', mutate_result, e))
        replacement = e.run('fn(){mutate_result();null}')
        self.assertEqual(e.run('''multiple_value_list(fn(){dynamic_wind(fn(){null},
          fn(){replace_after();return_values(20,22)},fn(){throw "old after"})})'''),[40,2])

    def test_callback_args_are_authoritative_before_child_invocation(self):
        e = Evaluator()
        original = ControlCallbackFrame.step
        def step(frame, cursor, value, error):
            if frame.phase == 'start' and frame.args == (40,):
                frame.args = (41,)
            return original(frame, cursor, value, error)
        with patch.object(ControlCallbackFrame, 'step', step):
            self.assertEqual(e.run('let p=prompt_tag();call_prompt(p,fn(){abort_prompt(p,40)},fn(n){n+1})'),42)

    def test_recursive_matching_scopes_release_before_outer_handler_transfer(self):
        self.assert_oracle('''var events=[];let p=prompt_tag();call_prompt(p,fn(){
          call_prompt(p,fn(){abort_prompt(p,20)},fn(n){events=append(events,prompt_available(p));
            abort_prompt(p,n+22)})},fn(n){[n,events,prompt_available(p)]})''', [42,[True],False])

    def test_barrier_state_blocks_only_outward_transfer(self):
        e, seen = Evaluator(), []
        def probe():
            frame = self.active(e, ControlBarrierFrame)
            self.assertTrue(frame.entered)
            self.assertEqual(frame.manager.state, 'active')
            self.assertIn(frame.manager, frame_roots(frame))
            seen.append(frame)
        e.env.bind('probe', Native('probe', probe, e))
        self.assertEqual(e.run('''let p=prompt_tag();call_prompt(p,fn(){control_barrier(fn(){probe();[
          prompt_available(p),try{abort_prompt(p,0)}catch e{e.code},
          call_prompt(p,fn(){abort_prompt(p,42)})]})})'''),[False,'control_barrier_error',42])
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0].manager.state,'closed')
        self.assertFalse(seen[0].entered)

    def test_escape_typed_state_preserves_owner_recording_and_dynamic_extent(self):
        e = Evaluator()
        with runner_scope(e) as runner:
            frame = PromptCallFrame(Request('escape',(Native('unused',lambda exit:42,e),)))
            cursor = self.direct(e,frame)
            frame.step(cursor,None,None)
            self.assertEqual(frame.args,(frame.escape,))
            self.assertIs(frame.escape.scope,frame.manager)
            other=e.suspension.task(iter(()))
            other.recording_scope='foreign-recording'
            e.suspension.current=other.runner
            from chaos.control_prompts import invoke
            with self.assertRaises(LanguageError) as caught:invoke(e,frame.escape,[42])
            self.assertEqual(caught.exception.code,'mode_error')
            e.suspension.current=runner
            escape=frame.escape
            frame.close(cursor)
            with self.assertRaises(LanguageError) as caught:invoke(e,escape,[42])
            self.assertEqual(caught.exception.code,'escape_error')

    def test_cross_task_escape_keeps_parent_scope_unavailable_to_child(self):
        self.assert_oracle('''call_escape(async fn(exit){async fn child(){
          try{exit(9)}catch e{e.code}};[await child(),exit.available]})''', ['escape_error',True])

    def test_wind_stores_body_error_and_restores_outer_context_after_cleanup(self):
        e, seen = Evaluator(), []
        def probe():
            frame = self.active(e, DynamicWindFrame)
            self.assertEqual(frame.phase,'after_error')
            self.assertIs(e.handled_error,frame.pending_error)
            self.assertIs(e.error_context,frame.pending_error)
            self.assertTrue(frame.error_context_entered)
            seen.append(frame)
        e.env.bind('probe',Native('probe',probe,e))
        self.assertEqual(e.run('''try{dynamic_wind(fn(){null},fn(){throw "body"},
          async fn(){await sleep(0);probe();null})}catch e{e.value}'''),'body')
        self.assertEqual(len(seen),1)
        self.assertFalse(seen[0].error_context_entered)
        self.assertIsNone(e.handled_error)
        self.assertIsNone(e.error_context)

    def test_wind_after_error_replaces_body_error_and_preserves_exception_chain(self):
        self.assert_oracle('''try{dynamic_wind(fn(){null},fn(){throw "body"},
          fn(){throw "after"})}catch e{[e.value,current_error().context.value]}''',['after','body'])
        self.assert_oracle('''try{dynamic_wind(fn(){null},fn(){throw "body"},fn(){throw})}
          catch e{e.value}''','body')

    def test_wind_before_failure_skips_body_and_cleanup(self):
        self.assert_oracle('''var calls=[];let value=try{dynamic_wind(fn(){throw "before"},
          fn(){calls=append(calls,"body")},fn(){calls=append(calls,"after")})}catch e{e.value};
          [value,calls]''',['before',[]])

    def test_wind_hard_body_failure_is_exact_and_skips_after(self):
        for failure in (ResourceLimit('hard'),ExecutionControl('cancel'),KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                e,calls=Evaluator(),[]
                def stop():raise failure
                e.env.bind('stop',Native('stop',stop,e))
                e.env.bind('mark',Native('mark',lambda:calls.append(1),e))
                with self.assertRaises(type(failure)) as caught:
                    e.run('dynamic_wind(fn(){null},stop,fn(){mark()})')
                self.assertIs(caught.exception,failure)
                self.assertEqual(calls,[])

    def test_wind_forced_close_during_after_error_restores_exact_context(self):
        e=Evaluator()
        frame=DynamicWindFrame(None,None,None)
        cursor=self.direct(e,frame)
        original_error,original_context=LanguageError('outer'),LanguageError('context')
        e.handled_error,e.error_context=original_error,original_context
        frame.phase='body'
        pending=LanguageError('body')
        action=frame.step(cursor,None,pending)
        self.assertIs(type(action.frame),ControlCallbackFrame)
        self.assertIs(e.handled_error,pending)
        frame.close(cursor)
        self.assertIs(e.handled_error,original_error)
        self.assertIs(e.error_context,original_context)
        self.assertFalse(frame.error_context_entered)

    def test_forced_cursor_close_retires_all_control_scopes_without_user_cleanup(self):
        e=Evaluator()
        calls=[]
        e.env.bind('mark',Native('mark',lambda:calls.append(1),e))
        tree=Parser('''call_prompt(prompt_tag(),fn(){control_barrier(fn(){dynamic_wind(
          fn(){null},fn(){mark();42},fn(){mark()})})})''').program()
        e.suspension.begin()
        cursor=e._s_evaluate(tree,Env(e.env))
        try:
            for _ in range(100):
                request=next(cursor)
                if any(type(f) is DynamicWindFrame and f.phase=='body' for f in cursor.frames):break
            else:self.fail('did not pause inside wind body')
            self.assertIs(request,CHECKPOINT)
            prompts=[f for f in cursor.frames if type(f) is PromptCallFrame]
            barriers=[f for f in cursor.frames if type(f) is ControlBarrierFrame]
            self.assertTrue(prompts and barriers)
            cursor.close()
            self.assertEqual(calls,[])
            self.assertTrue(all(f.manager.state=='closed' for f in prompts+barriers))
            self.assertEqual(e.suspension.control_state.stack,[])
            self.assertEqual(cursor.frames,[])
            self.assertEqual(e.depth,0)
        finally:
            cursor.close()
            e.suspension.finish()

    def test_pending_callback_and_wind_saved_error_roots_reject_foreign_functions(self):
        from chaos.capture_graph import seal
        foreign=Evaluator().run('fn(){42}')
        for kind in ('callback-result','handler-result','wind-result','wind-previous-error'):
            with self.subTest(kind=kind):
                e=Evaluator()
                with runner_scope(e):
                    if kind=='callback-result':frame=ControlCallbackFrame(None);frame.result=foreign
                    elif kind=='handler-result':
                        scope=PromptScope(e,tag(e));signal=PromptTransfer(scope,42,None)
                        frame=PromptHandlerFrame(scope,None,signal);frame.result=foreign
                    else:
                        frame=DynamicWindFrame(None,None,None)
                        if kind=='wind-result':frame.result=foreign
                        else:frame.previous_error=LanguageError('saved',value=foreign)
                    cursor=ExecutionCursor(e,frame)
                    with self.assertRaises(LanguageError) as caught:seal(e,(cursor,),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
                    cursor.close()

    def test_every_new_frame_root_schema_rejects_foreign_owner(self):
        from chaos.capture_graph import seal
        e,other=Evaluator(),Evaluator()
        frames=[ControlCallbackFrame(None),PromptHandlerFrame(None,None,None),
                PromptCallFrame(Request('prompt',(None,None,None))),
                ControlBarrierFrame(None),DynamicWindFrame(None,None,None)]
        for frame in frames:
            with self.subTest(frame=type(frame).__name__):
                frame.owner=other
                with self.assertRaises(LanguageError) as caught:seal(e,(frame,),None)
                self.assertEqual(caught.exception.code,'ownership_error')

    def test_active_and_saved_root_edges_respect_caller_authority_without_hooks(self):
        from chaos.capture_graph import seal
        from chaos.capture_frames import Edge
        from chaos.generic_functions import NextDescriptor
        e=Evaluator()
        active=NextDescriptor(e,None);saved=NextDescriptor(e,None)
        a=Native('a',lambda:0,e,generic=active,captures=())
        b=Native('b',lambda:0,e,generic=saved,captures=())
        frame=ControlCallbackFrame(a,authority=frozenset())
        frame.owner=e
        frame.previous_authority=frozenset({'saved'})
        roots=frame_roots(frame)
        self.assertTrue(all(type(root) is Edge for root in roots[:2]))
        frame.result=a
        wind=DynamicWindFrame(None,None,None);wind.owner=e
        wind.previous_error=LanguageError('saved',value=b)
        seal(e,(frame,wind),frozenset({'saved','extra'}))
        self.assertEqual(active.capabilities,frozenset())
        self.assertEqual(saved.capabilities,frozenset({'saved','extra'}))
        seal(e,(wind,),frozenset())
        self.assertEqual(saved.capabilities,frozenset())
        # The old graph remains charged in its original DFS order. New frame
        # state is fully validated without adding representation-only steps.
        shared=NextDescriptor(e,None)
        token=Native('shared',lambda:0,e,generic=shared,captures=())
        charged=[token]+list(range(128))
        start=e.steps
        seal(e,(charged,),frozenset({'saved'}))
        charged_steps=e.steps-start
        supplemental=ControlCallbackFrame(None,(token,),frozenset())
        supplemental.owner=e
        start=e.steps
        seal(e,(charged,supplemental),frozenset({'saved'}))
        self.assertEqual(e.steps-start,charged_steps)
        self.assertEqual(shared.capabilities,frozenset())
        # Even an all-supplementary walk retains the initial/final checks.
        supplemental.close(SimpleNamespace(owner=e))
        self.assertIsNone(supplemental.args)
        start=e.steps
        seal(e,(supplemental,),None)
        self.assertEqual(e.steps-start,2)
        # Supplemental nodes still consume the complete node and depth bounds.
        for args in (([None]*10000,[None]*10000),):
            oversized=ControlCallbackFrame(None,args)
            oversized.owner=e
            with self.assertRaises(ResourceLimit):seal(e,(oversized,),None)
        deep=None
        for _ in range(102):deep=[deep]
        oversized=ControlCallbackFrame(None,(deep,));oversized.owner=e
        with self.assertRaises(ResourceLimit):seal(e,(oversized,),None)
        # Stage an alias update before the later foreign argument fails. No
        # capsule may change until both charged and supplemental walks finish.
        atomic=NextDescriptor(e,None)
        pending=Native('pending',lambda:0,e,generic=atomic,captures=())
        foreign=Evaluator().run('fn(){42}')
        for args in ((foreign,pending),[foreign,pending]):
            bad=ControlCallbackFrame(None,args,frozenset());bad.owner=e
            with self.assertRaises(LanguageError) as caught:seal(e,(bad,),None)
            self.assertEqual(caught.exception.code,'ownership_error')
            self.assertIsNone(atomic.capabilities)
            self.assertIsNone(pending.capabilities)
            prompt=PromptCallFrame(Request('escape',(None,)))
            prompt.owner=e;prompt.args=args
            with self.assertRaises(LanguageError) as caught:seal(e,(prompt,),None)
            self.assertEqual(caught.exception.code,'ownership_error')
            self.assertIsNone(atomic.capabilities)
            self.assertIsNone(pending.capabilities)
        class HostTrap:
            @property
            def captures(self):self.fail('host hook traversed')
            def __getattr__(self,name):raise AssertionError('host introspection')
            def __iter__(self):raise AssertionError('host iteration')
        wind.pending_error=HostTrap()
        seal(e,(wind,),None)
        supplemental.args=HostTrap()
        seal(e,(supplemental,),None)

    def test_foreign_control_request_payloads_are_checked_by_capture_graph(self):
        from chaos.capture_graph import seal
        foreign=Evaluator().run('fn(){42}')
        e=Evaluator()
        with runner_scope(e):
            scope=PromptScope(e,tag(e));signal=PromptTransfer(scope,42,None)
            requests=[CallbackRequest(foreign,(1,),None),
                      CallbackRequest(None,(foreign,),None),
                      CallbackRequest(None,[foreign],None),
                      PromptHandlerRequest(scope,foreign,signal),Request('wind',(None,foreign,None))]
            for request in requests:
                with self.subTest(request=type(request).__name__),self.assertRaises(LanguageError) as caught:
                    seal(e,(request,),None)
                self.assertEqual(caught.exception.code,'ownership_error')

    def test_secondary_payload_authority_and_handler_none_keep_narrow_projection(self):
        e=Evaluator()
        effects=[]
        e.tool_handler=lambda name,value:effects.append(name) or 42
        wide=e.run('fn(){tool("secret",null)}')
        e.env.bind('wide',wide)
        e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        self.assertEqual(e.run('''let p=prompt_tag();let packet=multiple_value_list(fn(){
          call_prompt(p,fn(){limited(fn(){abort_prompt(p,1,wide)})})});
          [packet[0],try{packet[1]()}catch e{e.code}]'''),[1,'capability_error'])
        self.assertEqual(effects,[])

    def test_every_native_family_budget_and_cancel_threshold_matches_original_oracle(self):
        sources=[
            'let p=prompt_tag();call_prompt(p,fn(){abort_prompt(p,40)},fn(n){n+2})',
            'call_escape(fn(exit){exit(42)})',
            'control_barrier(fn(){20+22})',
            'dynamic_wind(fn(){null},fn(){20+22},fn(){null})',
        ]
        for source in sources:
            total=snapshot(source,legacy=True)[1]
            for limit in range(total+2):
                with self.subTest(source=source,limit=limit):
                    self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
            for cancel in range(total+2):
                with self.subTest(source=source,cancel=cancel):
                    self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))

    def test_differential_cleanup_scheduler_and_barrier_effect_order(self):
        cases=[
            ('''var seen=[];let p=prompt_tag();let result=call_prompt(p,fn(){dynamic_wind(
              fn(){seen=append(seen,"before")},fn(){tool("body",0);abort_prompt(p,42)},
              async fn(){await sleep(0);seen=append(seen,"after")})},
              fn(n){seen=append(seen,"handler");n});[result,seen]''', [42,['before','after','handler']]),
            ('''let p=prompt_tag();call_prompt(p,async fn(){async fn child(){prompt_available(p)};
              async with task_group() as g{let child_value=await g.spawn(child());
                abort_prompt(p,child_value,42)}},fn(child,value){[child,value]})''', [False,42]),
            ('''var seen=[];let result=call_escape(fn(exit){try{control_barrier(fn(){
              try{exit(0)}catch e{seen=append(seen,e.code)}});exit(42)}
              finally{tool("cleanup",0);seen=append(seen,"finally")}});[result,seen]''',
              [42,['control_barrier_error','finally']]),
        ]
        for source,expected in cases:
            with self.subTest(source=source):self.assert_oracle(source,expected)
        # Live deferred cursors force capture_graph through the actual control
        # frames, including a full pending result retained while after awaits.
        def setup(e):
            e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        prefix='''let base=generic_method(generic_function("g"),["Any"],fn(x){tool("fixture",x)});
          let g=generic_method(base,["Int"],fn(next,x){next},with_next=true);
          var saved=null;let sink=limited(fn(){fn(item){saved=item;0}});
          let ready=channel(1);let gate=channel();'''
        bodies=['''call_prompt(prompt_tag(),async fn(){let token=g(42);
                  await channel_send(ready,0);await channel_receive(gate);token})''',
                '''dynamic_wind(fn(){null},fn(){g(42)},async fn(){
                  await channel_send(ready,0);await channel_receive(gate);null})''']
        suffix='''let pending=work();spawn(pending);await channel_receive(ready);sink(pending);
          await channel_send(gate,0);let result=await saved;try{result()}catch e{e.code}'''
        for body in bodies:
            source=prefix+'async fn work(){'+body+'};'+suffix
            with self.subTest(live_body=body):
                modern=snapshot(source,setup=setup)
                self.assertEqual(modern[0],('value','capability_error'))
                self.assertEqual(modern,snapshot(source,legacy=True,setup=setup))
        # Under the empty caller ceiling each captured padding list expands
        # exactly once, so lengths 0..63 cover every graph-tick residue. This
        # oracle restores the original lexical handled algorithm as well.
        lexical_body='''let p=prompt_tag();async fn work(){with prompt_scope(p,
          async fn(n){let token=g(42);await channel_send(ready,0);
            await channel_receive(gate);token}) as s{abort_prompt(p,0)}};'''
        for padding in range(64):
            source=prefix+'let padding=range('+str(padding)+');'+lexical_body+suffix
            with self.subTest(lexical_padding=padding):
                modern=snapshot(source,setup=setup)
                self.assertEqual(modern[0],('value','capability_error'))
                self.assertEqual(modern,snapshot(source,legacy=True,setup=setup,lexical=True))


if __name__=='__main__':unittest.main()
