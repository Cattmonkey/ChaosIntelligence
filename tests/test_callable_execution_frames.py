"""Complete callable formation and generic-combination state, against actual old generators."""
import copy
from contextlib import ExitStack, nullcontext, contextmanager
from dataclasses import replace
import unittest
from unittest.mock import patch
from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, Native, TailCall, Parser, serializable
from chaos.execution_frames import ExecutionCursor, ensure_cursor
from chaos.execution_callables import CALLABLE_FRAMES, CallableFrameRequest, GenericResolveFrame, FunctionBuildFrame, CallableSyntaxFrame
from _callable_reference191 import reference, TARGETS, ORIGINALS, KINDS
from _effect_schedule158 import effect_schedule
from test_operator_execution_frames import active
WITNESS = 'var events=[];\nfn note(label,value){events=append(events,label);value}\nlet plain=fn(x:Int=note("plain",40))->Int{x+2};\nlet task=async fn(x=note("async",41)){await sleep(0);x+1};\nlet stream=fn*(x=note("gen",42)){yield x};\nlet asyncstream=async fn*(x=note("asyncgen",42)){yield x};\nfn named(x=note("named",42)){x}\nrec{fn even(n){if n==0{true}else{odd(n-1)}};fn odd(n){if n==0{false}else{even(n-1)}}};\nagent declared{provider:"local",tools:[]};\nlet built=function_from(code_from("fn(x=seed){x+1}"),{seed:41});\nvar work=generic_method(generic_function("work"),["Any"],fn(x){events=append(events,"base");x+1});\nwork=generic_method(work,["Int"],fn(next,x){events=append(events,"int");next()+1},with_next=true);\nwork=generic_method(work,["Any"],fn(x){events=append(events,"before");null},"before");\nwork=generic_method(work,["Any"],fn(x){events=append(events,"after");null},"after");\nwork=generic_method(work,["Any"],fn(next,x){events=append(events,"around");let value=next();events=append(events,"end");value},"around");\n[plain(),await task(),collect(stream()),await acollect(asyncstream()),named(),even(4),built(),work(40),type_of(declared),events]'
WITNESS_EXPECTED = [42, 42, [42], [42], 42, True, 42, 42, 'Function', ['plain', 'async', 'gen', 'asyncgen', 'named', 'around', 'before', 'int', 'base', 'after', 'end']]

def snapshot(source, legacy=False, limit=200000, cancel=None, setup=None, effect_policy='already_done', inputs=None):
    effects = []
    trace = []
    debug = []

    def effect(kind, payload, event):
        effects.append((kind, copy.deepcopy(payload)))
        return payload['value'] if kind == 'tool' else payload.get('input')
    e = Evaluator(step_limit=limit, timeout=30, tool_handler=lambda n, v: v, effect_handler=effect, trace_handler=trace.append)
    e.cancelled = lambda : cancel is not None and e.steps >= cancel
    e.debug_hook = lambda node, env, frames: debug.append((node[0], dict(e.current_span), e.steps))
    if setup:
        setup(e)
    with effect_schedule(effect_policy) as schedule, reference() if legacy else nullcontext():
        try:
            result = ('value', serializable(e.run(source, inputs)))
        except LanguageError as error:
            result = ('error', error.record())
        except ExecutionControl as error:
            result = ('control', type(error).__name__, str(error))
    return (result, e.steps, effects, trace, debug, e.depth, len(e.frames), dict(schedule))

def pending(cursor):
    from chaos.suspension import CHECKPOINT
    value = cursor.send(None)
    while value is CHECKPOINT:
        value = cursor.send(None)
    return value

@contextmanager
def observe_callable_entries():
    steps={}
    with ExitStack()as stack:
        for cls in CALLABLE_FRAMES:
            original=cls.step
            def step(frame,cursor,value,error,_original=original):
                name=type(frame).__name__;steps[name]=steps.get(name,0)+1
                return _original(frame,cursor,value,error)
            stack.enter_context(patch.object(cls,'step',step))
        yield steps

def owned_frame(testcase,root,cls):
    """Find the actual pending frame through declared owned cursor links."""
    owner=root.owner;pending_cursors=[root];visited=set();matches=[]
    while pending_cursors:
        current=pending_cursors.pop()
        testcase.assertIsInstance(current,ExecutionCursor)
        testcase.assertIs(current.owner,owner)
        testcase.assertNotIn(id(current),visited,'owned cursor graph cannot cycle or alias a child')
        visited.add(id(current))
        for frame in current.frames:
            if type(frame)is cls:matches.append(frame)
            child=getattr(frame,'pending_cursor',None)
            if child is not None:
                testcase.assertIsInstance(child,ExecutionCursor)
                testcase.assertIs(child.owner,owner)
                testcase.assertIs(child.parent_bridge,frame)
                testcase.assertIs(frame.pending_cursor,child)
                pending_cursors.append(child)
    testcase.assertEqual(len(matches),1,'one authoritative pending frame must own this algorithm')
    return matches[0]

class CallableExecutionFrames(unittest.TestCase):

    def assert_generic_route(self,legacy,reached,steps):
        if legacy:
            self.assertGreater(reached['generic.resolve'],0)
            self.assertEqual(steps,{})
        else:
            self.assertGreater(steps.get('GenericResolveFrame',0),0)

    def check(self, source, expected=None, **options):
        modern = snapshot(source, **options)
        old = snapshot(source, legacy=True, **options)
        self.assertEqual(modern, old)
        if expected is not None:
            self.assertEqual(modern[0], ('value', expected))
        return modern

    def test_all_nine_original_entry_algorithms_have_real_first_advance_witnesses(self):
        frames = set()
        with reference() as reached, ExitStack() as stack:
            for cls in CALLABLE_FRAMES:
                original = cls.step

                def step(frame, cursor, value, error, _original=original):
                    frames.add(type(frame))
                    return _original(frame, cursor, value, error)
                stack.enter_context(patch.object(cls, 'step', step))
            self.assertEqual(snapshot(WITNESS)[0], ('value', WITNESS_EXPECTED))
        self.assertEqual(frames, set())
        self.assertEqual(set(reached), set(TARGETS))
        self.assertTrue(all(reached.values()), reached)
        self.assertEqual(len(ORIGINALS), 3)
        self.assertEqual(len(KINDS), 7)

    def test_all_three_actual_frames_retain_current_runner_bridge_lineage(self):
        seen = set()
        runners = {}

        def lineage(cursor):
            runner = cursor.owner.suspension.current
            root = runner.iterator
            previous = runners.get(id(runner))
            if previous:
                self.assertIs(previous[0], runner)
                self.assertIs(previous[1], root)
            else:
                runners[id(runner)] = (runner, root)
            todo = [(root, ())]
            visited = set()
            while todo:
                (current, chain) = todo.pop()
                self.assertNotIn(id(current), visited)
                visited.add(id(current))
                self.assertIs(current.owner, cursor.owner)
                if current is cursor:
                    for (child, bridge) in chain:
                        self.assertIs(child.parent_bridge, bridge)
                        self.assertIs(bridge.pending_cursor, child)
                    return
                for frame in current.frames:
                    child = getattr(frame, 'pending_cursor', None)
                    if child is not None:
                        todo.append((child, chain + ((child, frame),)))
            self.fail('callable frame detached from actual Runner iterator')
        with ExitStack() as stack:
            for cls in CALLABLE_FRAMES:
                original = cls.step

                def step(frame, cursor, value, error, _original=original):
                    seen.add(type(frame))
                    lineage(cursor)
                    return _original(frame, cursor, value, error)
                stack.enter_context(patch.object(cls, 'step', step))
            self.assertEqual(snapshot(WITNESS)[0], ('value', WITNESS_EXPECTED))
        self.assertEqual(seen, set(CALLABLE_FRAMES))
        self.assertEqual(len(seen), 3)
        self.assertTrue(runners)

    def test_definition_time_defaults_all_flavors_and_generic_order(self):
        self.check(WITNESS, WITNESS_EXPECTED)

    def test_default_contract_failure_preserves_left_to_right_effect_prefix(self):
        result = self.check('async fn define(){fn bad(a:Int=tool("first",40),b:String=tool("second",42),c=tool("never",0)){null};bad()};await define()')
        self.assertEqual(result[0][0], 'error')
        self.assertEqual([x[1]['name'] for x in result[2]], ['first', 'second'])

    def test_default_values_keep_original_mutable_aliases_and_closure_snapshot(self):
        self.check('var count=40;let original=[1];fn f(x=original){[x==original,count]};count=42;[f(),f(),original]', [[True, 42], [True, 42], [1]])

    def test_parameter_modes_contracts_patterns_and_defaults_share_original_binder(self):
        self.check('fn f(a:Int=20,/,b:Int=20,*tail,extra:Int=2,**kw){[a+b+extra,tail,kw]};f(20,20,9,extra=2,label="ok")', [42, [9], {'label': 'ok'}])

    def test_rec_shared_snapshot_and_self_recursion_remain_original(self):
        self.check('let outer=42;rec{fn even(n){if n==0{outer}else{odd(n-1)}};fn odd(n){even(n-1)}};do{let outer=0;even(6)}', 42)

    def test_rec_conflict_duplicate_and_partial_publication_match_original(self):
        for source in ['let a=0;rec{fn a(){1};fn b(){2}}', 'rec{fn a(){1};fn a(){2}}', 'rec{fn first(x=tool("first",1)){x};fn bad(x=do{throw "bad"}){x}}']:
            with self.subTest(source=source):
                self.assertEqual(snapshot(source), snapshot(source, legacy=True))
        outcomes = []
        for legacy in (False, True):
            e = Evaluator()
            node = Parser('rec{fn a(){1};fn b(){2}}').program()[1][0]
            # The parser correctly rejects duplicate source declarations. This
            # trusted direct-AST case reaches the original two publication loops.
            second=node[1][1]
            node[1][1]=type(second)(('def','a',second[2]),dict(second.span))
            with active(e, None), reference() if legacy else nullcontext():
                with self.assertRaises(LanguageError):
                    e.suspension.drive(e._s_execute(node, e.env))
                a = e.env.values['a']
                outcomes.append(([e.invoke(a, []), e.invoke(a.env.values['a'], [])], e.steps, e.depth, len(e.frames)))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0], [1, 2])

    def test_code_build_explicit_bindings_identity_and_distinct_constructions(self):
        self.check('class Box{var answer=42};let box=Box();let code=code_from("fn(){value}");let a=function_from(code,{value:box});let b=function_from(code,{value:box});[a()==box,b()==box,a()!=null,a==b]', [True, True, True, False])

    def test_generated_code_flavors_defaults_and_source_failures_match_original(self):
        for source in ['let f=function_from(code_from("fn(x=tool(\\"build\\",42)){x}"));f()', 'let f=function_from(code_from("async fn(x=41){await sleep(0);x+1}"));await f()', 'let f=function_from(code_from("fn*(x=42){yield x}"));collect(f())', 'let f=function_from(code_from("async fn*(x=42){yield x}"));await acollect(f())']:
            with self.subTest(source=source):
                self.check(source, 42 if 'collect' not in source else [42])

    def test_agents_are_formed_from_actual_configuration_without_model_execution(self):
        self.check('var calls=[];fn config(){calls=append(calls,"config");{provider:"local",tools:[]}};agent a{provider:config().provider,tools:[]};[type_of(a),calls]', ['Function', ['config']])

    def test_generic_persistent_table_keywords_and_repeated_primary_next(self):
        self.check('var n=0;let base=generic_method(generic_function("f"),["Any"],fn(x,*,offset=0){n=n+1;x+offset});let work=generic_method(base,["Int"],fn(next,x,*,offset=0){next()+next()},with_next=true);let saved=generic_method(work,["Int"],fn(next,x,*,offset=0){next},"around")(20,offset=1);let changed=generic_method(work,["Any"],fn(x,*,offset=0){100});[saved(),n,work(20,offset=1),n]', [42, 2, 42, 4])

    def test_generic_preflight_blocks_every_auxiliary_effect(self):
        self.check('var seen=[];var f=generic_function("f");f=generic_method(f,["Any"],fn(next,x){seen=append(seen,1);42},"around");[try{f(1)}catch e{e.code},seen]', ['no_applicable_method', []])

    def test_primary_async_and_stream_results_remain_cold_after_auxiliary(self):
        self.check('var seen=[];var f=generic_method(generic_function("f"),["Int"],async fn(x){seen=append(seen,"body");x});f=generic_method(f,["Int"],fn(x){seen=append(seen,"after");null},"after");let t=f(42);let before=seen;[task_info(t).state,before,await t,seen]', ['created', ['after'], 42, ['after', 'body']])

    def test_unknown_auxiliary_cold_result_is_preserved_not_awaited(self):
        self.check('var seen=0;async fn cold(){seen=seen+1;42};var f=generic_method(generic_function("f"),["Int"],fn(x){seen=99;42});f=generic_method(f,["Int"],fn(x){cold()},"before");try{f(0)}catch e{let state=task_info(e.value).state;cancel(e.value);[e.code,state,seen]}', ['generic_protocol_error', 'created', 0])

    def test_generic_multiple_values_primary_projection_and_after_order(self):
        self.check('var seen=[];var f=generic_method(generic_function("f"),["Int"],fn(x){return_values(x,x+1)});f=generic_method(f,["Int"],fn(x){seen=append(seen,"after");null},"after");[multiple_value_list(fn(){f(41)}),f(41),seen]', [[41, 42], 41, ['after', 'after']])

    def test_real_first_wait_defaults_generic_callbacks_and_code_build(self):
        for source in ['fn f(x=tool("default",42)){x};f()', 'function_from(code_from("fn(x=tool(\\"default\\",42)){x}"))()', 'var f=generic_method(generic_function("f"),["Int"],fn(x){tool("primary",x)});f=generic_method(f,["Int"],fn(x){tool("before",x);null},"before");f=generic_method(f,["Int"],fn(x){tool("after",x);null},"after");f(42)']:
            with self.subTest(source=source):
                actual='async fn execute(){'+source+'};await execute()'
                result = self.check(actual, 42, effect_policy='first_wait')
                self.assertGreater(result[-1]['waitBoundaryReleases'], 0)

    def test_pending_default_contract_is_the_actual_next_type_check(self):
        from chaos.suspension import Effect
        from chaos.language import check_type
        e = Evaluator(tool_handler=lambda n, v: v)
        node = Parser('fn(x:Int=tool("wait",42)){x}').program()[1][0]
        with active(e, None):
            cursor = ensure_cursor(e, e._s_execute(node, e.env))
            self.assertIs(type(pending(cursor)), Effect)
            frame = next((f for f in cursor.frames if type(f) is CallableSyntaxFrame))
            self.assertEqual(frame.phase, 'default')
            frame.contract = 'String'
            with self.assertRaises(LanguageError) as original_error:
                check_type(42,'String')
            with self.assertRaises(LanguageError) as error:
                cursor.send(42)
            self.assertEqual((error.exception.code,error.exception.message),
                             (original_error.exception.code,original_error.exception.message))

    def test_pending_generic_combination_controls_future_after_callbacks(self):
        from chaos.generic_functions import GenericRequest, resolve
        from chaos.suspension import Effect
        calls = []
        e = Evaluator(tool_handler=lambda n, v: calls.append(n) or v)
        fn = e.run('var f=generic_method(generic_function("f"),["Int"],fn(x){x});f=generic_method(f,["Int"],fn(x){tool("before",0);null},"before");f=generic_method(f,["Int"],fn(x){tool("after",0);null},"after");f')
        with active(e, None):
            cursor = ensure_cursor(e, resolve(e, GenericRequest(fn.generic, (42,), {})))
            self.assertIs(type(pending(cursor)), Effect)
            frame = owned_frame(self,cursor,GenericResolveFrame)
            frame.combination = replace(frame.combination, after=())
            value = e.suspension.drive(cursor)
            self.assertIs(type(value), TailCall)
            self.assertEqual(e.resolve(value), 42)
            self.assertEqual(calls, [])

    def test_pending_build_environment_remains_a_checked_live_root(self):
        from chaos.code_values import request, construct
        from chaos.capture_graph import seal
        from chaos.suspension import Effect
        e = Evaluator(tool_handler=lambda n, v: v)
        code = e.run('code_from("fn(x=tool(\\"wait\\",42)){x}")')
        with active(e, None):
            cursor = ensure_cursor(e, construct(e, request(e, code, {})))
            self.assertIs(type(pending(cursor)), Effect)
            frame = next((f for f in cursor.frames if type(f) is FunctionBuildFrame))
            frame.environment.bind('foreign', Evaluator().run('fn(){1}'))
            with self.assertRaises(LanguageError) as error:
                seal(e, (cursor,), None)
            self.assertEqual(error.exception.code, 'ownership_error')
            cursor.close()

    def test_cold_throw_before_first_advance_has_no_scope_or_publication(self):
        from chaos import generic_functions
        from chaos import code_values
        for legacy in (False, True):
            e = Evaluator()
            fn = e.run('generic_method(generic_function("f"),["Int"],fn(x){x})')
            code = e.run('code_from("fn(x=42){x}")')
            node = Parser('fn f(x=42){x}').program()[1][0]
            with active(e, None), reference() if legacy else nullcontext() as reached, observe_callable_entries() as steps:
                for generator in [generic_functions.resolve(e, generic_functions.GenericRequest(fn.generic, (42,), {})), code_values.construct(e, code_values.request(e, code, {})), e._s_execute(node, e.env)]:
                    before = e.steps
                    failure = ResourceLimit('cold callable throw')
                    with self.assertRaises(ResourceLimit) as caught:
                        generator.throw(failure)
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(e.steps, before)
                self.assertNotIn('f', e.env.values)
                self.assertEqual(steps,{})
                if legacy:self.assertTrue(all(count==0 for count in reached.values()))

    def test_pending_close_restores_original_scope_and_never_runs_after(self):
        from chaos import generic_functions
        from chaos.suspension import Effect
        outcomes = []
        for legacy in (False, True):
            calls = []
            e = Evaluator(tool_handler=lambda n, v: calls.append(n) or v)
            fn = e.run('var f=generic_method(generic_function("f"),["Int"],fn(x){x});f=generic_method(f,["Int"],fn(x){tool("before",0);null},"before");f=generic_method(f,["Int"],fn(x){tool("never",0);null},"after");f')
            with active(e, frozenset({'before', 'never'})) as runner, reference() if legacy else nullcontext() as reached, observe_callable_entries() as steps:
                previous = runner.capabilities
                cursor = ensure_cursor(e, generic_functions.resolve(e, generic_functions.GenericRequest(fn.generic, (42,), {})))
                self.assertIs(type(pending(cursor)), Effect)
                cursor.close()
                self.assertIs(runner.capabilities, previous)
                self.assertEqual(calls, [])
                self.assertEqual(e.depth, 0)
                self.assertEqual(e.frames, [])
                outcomes.append((e.steps, e.depth, len(e.frames), len(runner.capture_frames)))
                self.assert_generic_route(legacy,reached,steps)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_host_scope_suppression_preserves_original_with_suite_resume(self):
        from chaos import agent_tools
        from chaos import generic_functions
        original = agent_tools.scope
        outcomes = []
        for legacy in (False, True):
            e = Evaluator()
            fn = e.run('var f=generic_method(generic_function("f"),["Int"],fn(x){42});f=generic_method(f,["Int"],fn(x){throw "bad"},"before");f=generic_method(f,["Int"],fn(x){null},"after");f')
            events = []
            level = [0]

            class Scope:

                def __init__(self, e, c):
                    self.cm = original(e, c)

                def __enter__(self):
                    level[0] += 1
                    self.outer = level[0] == 1
                    events.append(('enter', level[0]))
                    return self.cm.__enter__()

                def __exit__(self, kind, error, tb):
                    events.append(('exit', level[0], kind.__name__ if kind else None))
                    level[0] -= 1
                    result = self.cm.__exit__(kind, error, tb)
                    return bool(result or (self.outer and isinstance(error, LanguageError)))
            with active(e, None), reference() if legacy else nullcontext() as reached, observe_callable_entries() as steps, patch.object(agent_tools, 'scope', Scope):
                try:
                    value = ('value', e.suspension.drive(generic_functions.resolve(e, generic_functions.GenericRequest(fn.generic, (42,), {}))))
                except BaseException as error:
                    value = ('error', type(error).__name__, str(error))
                self.assertEqual(level, [0])
                outcomes.append((value, e.steps, events, e.depth, len(e.frames)))
                self.assert_generic_route(legacy,reached,steps)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertTrue(any((x[0] == 'exit' and x[-1] is not None for x in outcomes[0][2])))

    def test_errors_cancellation_and_small_step_budgets_match_complete_original_observation(self):
        source = 'var f=generic_method(generic_function("f"),["Int"],fn(x){tool("wait",x)});f=generic_method(f,["Int"],fn(x){null},"before");fn apply(x=tool("default",42)){f(x)};apply()'
        for limit in (1, 8, 31, 64, 128, 512):
            with self.subTest(limit=limit):
                self.assertEqual(snapshot(source, limit=limit), snapshot(source, legacy=True, limit=limit))
        for threshold in (0, 8, 32, 64, 128):
            with self.subTest(cancel=threshold):
                self.assertEqual(snapshot(source, cancel=threshold), snapshot(source, legacy=True, cancel=threshold))

    def test_new_request_foreign_nested_roots_and_opaque_host_values(self):
        from chaos.capture_graph import seal
        from chaos.code_values import FunctionBuildRequest
        from chaos.tuple_values import TupleValue
        e = Evaluator()
        foreign = Evaluator().run('fn(){0}')
        code = e.run('code_from("fn(){value}")')

        class Opaque:

            @property
            def dangerous(self):
                raise AssertionError('opaque introspection')
        seal(e, (CallableFrameRequest('build', (FunctionBuildRequest(code, (('value', Opaque()),)),)),), None)
        for value in [TupleValue((foreign,)), {'foreign': foreign}]:
            with self.subTest(value=type(value).__name__), self.assertRaises(LanguageError):
                seal(e, (CallableFrameRequest('build', (FunctionBuildRequest(code, (('value', value),)),)),), None)

    def test_capture_failure_never_commits_earlier_tentative_next_alias(self):
        from chaos.capture_graph import seal
        from chaos.code_values import FunctionBuildRequest
        from chaos.generic_functions import NextDescriptor
        e = Evaluator()
        code = e.run('code_from("fn(){value}")')
        token = NextDescriptor(e, None)
        native = Native('next', lambda : None, e, generic=token, captures=())
        good = CallableFrameRequest('build', (FunctionBuildRequest(code, (('value', native),)),))
        bad = CallableFrameRequest('build', (FunctionBuildRequest(code, (('value', Evaluator().run('fn(){0}')),)),))
        with self.assertRaises(LanguageError):
            seal(e, (good, bad), frozenset())
        self.assertIsNone(native.capabilities)
        self.assertIsNone(token.capabilities)

    def test_original_capture_node_and_depth_limits_still_apply_to_new_roots(self):
        from chaos.capture_graph import seal
        from chaos.code_values import FunctionBuildRequest
        from chaos.tuple_values import TupleValue
        e = Evaluator()
        code = e.run('code_from("fn(){value}")')
        wide = TupleValue(([None] * 10000, [None] * 10000))
        with self.assertRaises(ResourceLimit):
            seal(e, (CallableFrameRequest('build', (FunctionBuildRequest(code, (('value', wide),)),)),), None)
        deep = 0
        for _ in range(110):
            deep = TupleValue((deep,))
        with self.assertRaises(ResourceLimit):
            seal(e, (CallableFrameRequest('build', (FunctionBuildRequest(code, (('value', deep),)),)),), None)

    def test_all64_real_pending_default_capture_charge_alignments_match_original(self):
        from chaos.capture_graph import seal
        from chaos.suspension import Task
        charges = []
        # Preserve all64 alignments and include the wrap point. A base exactly
        # divisible by64 may have the same floor charge for offsets0..63.
        for padding in range(65):
            witnesses = []

            def setup(e):

                def capture(task,padding_values):
                    self.assertIs(type(task), Task)
                    self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                    self.assertEqual(padding_values,list(range(padding)))
                    phases=[]
                    seen = []
                    import chaos.execution_frames as ef
                    original = ef.frame_roots

                    def roots(frame):
                        seen.append(type(frame))
                        phases.append((type(frame),getattr(frame,'phase',None)))
                        return original(frame)
                    before = e.steps
                    with patch.object(ef, 'frame_roots', roots):
                        seal(e, (task,padding_values), None)
                    witnesses.append((set(seen), e.steps - before, phases))
                    return None
                e.env.bind('capture_probe191', Native('capture_probe191', capture, e, captures=()))
            source = 'let padding=range(PADDING);let ready=channel(1);async fn build(){fn f(x=do{spawn(channel_send(ready,0));tool("hold",42)}){x};f()};let task=build();spawn(task);await channel_receive(ready);capture_probe191(task,padding);await task'.replace('PADDING', str(padding))
            with self.subTest(padding=padding):
                modern = snapshot(source, setup=setup, effect_policy='first_wait')
                old = snapshot(source, legacy=True, setup=setup, effect_policy='first_wait')
                self.assertEqual(modern[0], ('value', 42))
                self.assertEqual(modern, old)
                self.assertEqual(len(witnesses), 2)
                self.assertEqual(witnesses[0][1], witnesses[1][1])
                self.assertIn(CallableSyntaxFrame, witnesses[0][0])
                self.assertIn((CallableSyntaxFrame,'default'),witnesses[0][2])
                self.assertTrue(set(CALLABLE_FRAMES).isdisjoint(witnesses[1][0]))
                charges.append(witnesses[0][1])
        self.assertGreater(len(set(charges)), 1)

    def test_uninstrumented_std_swarm_and_snapshot_match_original(self):
        source = 'import "std/swarm" as S;var log=[];\nlet base=generic_method(generic_function("work"),["Int"],async fn(x){log=append(log,x);x+1});\nlet work=generic_method(base,[generic_eql(40)],async fn(next,x){let n=await next();n+1},with_next=true);\nlet old=S.create({a:40,b:41});let changed=await S.step(old,{a:work,b:work},prepare=fn(context){context.state});\n[old.states,changed.states,changed.tick,sort(log)]'
        expected = [{'a': 40, 'b': 41}, {'a': 42, 'b': 42}, 1, [40, 41]]
        for legacy in (False, True):
            with reference() if legacy else nullcontext():
                self.assertEqual(Evaluator(step_limit=30000, timeout=20).run(source), expected)
        self.check(source, expected)

    def test_original_module_match_and_class_frames_interoperate_without_claimed_migration(self):
        self.check('class Box{let n=40};let code=code_from("fn(x){match x{Box::{n:n}=>n+2;_=>0}}");let f=function_from(code,{Box:Box});f(Box())', 42)

    def test_wide_caller_alias_and_narrow_generic_next_never_gain_authority(self):
        calls = []

        def setup(e):
            e.env.bind('limited', Native('limited', lambda f: TailCall(f, []), e, capabilities=frozenset()))
        self.check('fn secret(x){tool("secret",x)};let base=generic_method(generic_function("f"),["Int"],secret);let work=generic_method(base,["Int"],fn(next,x){next},"around");let saved=limited(fn(){work(42)});try{saved()}catch e{e.code}', 'capability_error', setup=setup)

    def test_generatorexit_scope_suppression_preserves_first_close_and_retained_cursor_order(self):
        from chaos import agent_tools
        from chaos import generic_functions
        from chaos.suspension import Effect
        original = agent_tools.scope
        outcomes = []
        retained = []
        for legacy in (False, True):
            e = Evaluator(tool_handler=lambda n, v: v)
            fn = e.run('var f=generic_method(generic_function("f"),["Int"],fn(x){42});f=generic_method(f,["Any"],fn(x){tool("second",0);null},"before");f=generic_method(f,["Int"],fn(x){tool("first",0);null},"before");f')
            events = []
            level = [0]
            scopes = []
            original_iterators = []

            class Scope:

                def __init__(self, e, c):
                    self.cm = original(e, c)
                    scopes.append(self)

                def __enter__(self):
                    level[0] += 1
                    self.outer = level[0] == 1
                    events.append(('enter', level[0]))
                    return self.cm.__enter__()

                def __exit__(self, kind, error, tb):
                    events.append(('exit', level[0], kind.__name__ if kind else None))
                    level[0] -= 1
                    result = self.cm.__exit__(kind, error, tb)
                    return bool(result or (self.outer and isinstance(error, GeneratorExit)))
            with active(e, None), reference(retained=original_iterators) if legacy else nullcontext() as reached, observe_callable_entries() as steps, patch.object(agent_tools, 'scope', Scope):
                iterator = generic_functions.resolve(e, generic_functions.GenericRequest(fn.generic, (42,), {}))
                cursor = ensure_cursor(e, iterator)
                request = pending(cursor)
                self.assertIs(type(request), Effect)
                self.assertEqual(request.payload['name'], 'first')
                # Keep actual host scopes and exception tracebacks alive across
                # both observations. Refcount/GC must not manufacture a matching
                # exit order after a generator rejects GeneratorExit suppression.
                held = {'iterator': iterator, 'cursor': cursor, 'scopes': scopes,
                        'original_iterators': original_iterators,
                        'first_error': None, 'first_traceback': None,
                        'second_error': None, 'second_traceback': None}
                retained.append(held)
                try:
                    cursor.close()
                    first = ('closed',)
                except BaseException as error:
                    held['first_error'] = error
                    held['first_traceback'] = error.__traceback__
                    self.assertIsNotNone(held['first_traceback'])
                    first = ('error', type(error).__name__)
                first_events = list(events)
                first_level = level[0]
                first_scope_count = len(scopes)
                self.assertIs(held['iterator'], iterator)
                self.assertIs(held['cursor'], cursor)
                self.assertIs(held['scopes'], scopes)
                self.assertTrue(scopes)
                self.assert_generic_route(legacy,reached,steps)
                if legacy:
                    self.assertEqual(len(original_iterators),1)
                    self.assertEqual(original_iterators[0][0],'generic.resolve')
                    self.assertIs(original_iterators[0][1],iterator)
                    self.assertIs(iterator.iterator,original_iterators[0][2])
                    self.assertIs(iterator.iterator.gi_code,original_iterators[0][3])
                if held['first_error'] is not None:
                    self.assertIs(held['first_error'].__traceback__, held['first_traceback'])
                try:
                    iterator.close()
                    second = ('closed',)
                except BaseException as error:
                    held['second_error'] = error
                    held['second_traceback'] = error.__traceback__
                    self.assertIsNotNone(held['second_traceback'])
                    second = ('error', type(error).__name__)
                if legacy:
                    self.assertIs(iterator.iterator,original_iterators[0][2])
                    self.assertIs(iterator.iterator.gi_code,original_iterators[0][3])
                outcomes.append((first, first_events, first_level, first_scope_count,
                                 second, list(events), level[0], len(scopes), e.depth, len(e.frames)))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(len(retained), 2)
