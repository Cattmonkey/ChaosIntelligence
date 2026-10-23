"""Complete control activations under real held effects, not reconstructed captures."""
import copy
import unittest
from unittest.mock import patch

from chaos import suspension
from chaos.language import Evaluator, Native, LanguageError, ResourceLimit, serializable
from chaos.compiled_flow_state import FlowState
from chaos.jit import compile_function
from chaos.native_jit import BackendUnavailable
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal


def branch_case(choice):
    body = ('let value=if tool("if_condition",CHOICE){'
            'let answer=tool("if_then",40);answer+2}else{'
            'let answer=tool("if_else",44);answer-2};'
            'return tool("return_value",value)')
    return '', body.replace('CHOICE', 'true' if choice else 'false'), 42, ['if_condition', 'if_then' if choice else 'if_else', 'return_value'], ('return_value',)


def while_case(breaking):
    body = ('var i=0;var total=0;while tool("while_condition",i<3){'
            'i=i+1;let value=tool("while_body",i);'
            'if i==1{tool("before_continue",i);continue}else{null};'
            'if BREAKING && i==2{tool("before_break",i);break}else{null};'
            'total=total+value}else{let extra=tool("while_else",100);total=total+extra};'
            'let answer=tool("after_while",total);return tool("return_value",answer)')
    names = (['while_condition', 'while_body', 'before_continue', 'while_condition', 'while_body', 'before_break'] if breaking else
             ['while_condition', 'while_body', 'before_continue', 'while_condition', 'while_body', 'while_condition', 'while_body', 'while_condition', 'while_else'])
    return '', body.replace('BREAKING', 'true' if breaking else 'false'), 0 if breaking else 105, names+['after_while', 'return_value'], ('return_value',)


def for_case(pattern):
    prefix = ('class Cursor{var n=0;fn __iter__(self){let ignored=tool("for_acquire",0);self}'
              'fn __next__(self){self.n=self.n+1;let ignored=tool("for_step",self.n);'
              '{done:self.n>2,value:ITEM}}};')
    body = ('var total=0;for BINDING in do{tool("for_source",0);Cursor()}{'
            'let value=tool("for_body",TERM);total=total+value}'
            'else{let extra=tool("for_else",1);total=total+extra};'
            'let answer=tool("after_for",total);return answer')
    return (prefix.replace('ITEM', '[self.n,20]' if pattern else 'self.n'),
            body.replace('BINDING', '[a,b]' if pattern else 'item').replace('TERM', 'a+b' if pattern else 'item'),
            44 if pattern else 4,
            ['for_source', 'for_acquire', 'for_step', 'for_body', 'for_step', 'for_body', 'for_step', 'for_else', 'after_for'], ())


NESTED = ('var outer=0;while outer<2{outer=outer+1;for item in [1,2,3]{'
          'let seen=tool("nested_body",item);'
          'if seen==1{tool("before_continue",seen);continue}else{null};'
          'if outer==1{tool("before_break",seen);break}else{null};'
          'return tool("early_return",40)+2};tool("after_inner",outer)};'
          'tool("unreachable",0);-1')


class FlowControlCapture204(unittest.TestCase):
    def run_case(self, case, padding, target):
        assert target in ('bare','interpreter','auto-flow')
        compiled = target == 'auto-flow'
        prefix, body, expected, expected_names, tail_handoffs = case
        self.assertEqual(len(set(tail_handoffs)),len(tail_handoffs))
        self.assertTrue(all(name == expected_names[-1] for name in tail_handoffs))
        effects, trace, debug, states, held, observations = [], [], [], [], [], []
        future_requests, visited, functions = {}, [], []
        base = [None]

        def effect(kind, payload, event):
            self.assertEqual(kind, 'tool')
            self.assertIn(payload['name'], expected_names)
            effects.append((kind, copy.deepcopy(payload)))
            return payload['value']

        e = Evaluator(tool_handler=lambda name,value:value, effect_handler=effect, step_limit=30000, timeout=30)
        def traced(event):
            if base[0] is not None:
                row = copy.deepcopy(event)
                if type(row.get('step')) is int: row['step'] -= base[0]
                trace.append(row)
        e.trace_handler = traced
        e.debug_hook = lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps-base[0])) if base[0] is not None else None

        def choose(function):
            if target == 'bare':
                value = function
            else:
                with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('portable-test')):
                    value = compile_function(e, function, target)
            functions.append(value)
            return value

        def remember(task, pad):
            held.append((task,pad)); base[0] = e.steps; trace.clear(); debug.clear()
        e.env.bind('choose', Native('choose',choose,e,captures=()))
        e.env.bind('remember', Native('remember',remember,e,captures=()))
        source = ('let padding=range('+str(padding)+');'+prefix+
                  'let work=choose(async fn(pad){'+body+'});'
                  'let task=work(padding);remember(task,padding);spawn(task);await task')
        original_init = FlowState.__init__
        def created(state,*args,**kwargs):
            original_init(state,*args,**kwargs); states.append(state)
        recording_seal = observed_seal()
        with effect_schedule('first_wait') as schedule, patch.object(FlowState,'__init__',created):
            controlled_wait = suspension.wait; submit = suspension.ThreadPoolExecutor.submit
            def submitting(pool, function, *args, **kwargs):
                future = submit(pool,function,*args,**kwargs)
                if args and type(args[0]) is suspension.Effect:
                    future_requests[future] = args[0]
                return future
            def waiting(futures,*args,**kwargs):
                futures = tuple(futures)
                try:
                    self.assertEqual(len(held),1)
                    task,pad = held[0]; runner = task.runner; future = runner.future
                    if future is not None and future in futures and future not in visited:
                        request = future_requests[future]; name = request.payload['name']
                        self.assertEqual(name,expected_names[len(visited)])
                        self.assertFalse(future.done()); self.assertEqual(runner.state,'effect')
                        self.assertNotIn(task.state,suspension.TERMINAL)
                        self.assertIs(task.capture_roots[1]['pad'],pad)
                        self.assertEqual(pad,list(range(padding)))
                        if compiled:
                            live = [state for state in states if not state.closed]
                            if name in tail_handoffs:
                                # This exact final Effect is dispatched by the
                                # trampoline after the actual body has retired.
                                self.assertEqual(len(visited),len(expected_names)-1)
                                self.assertEqual(len(states),1); self.assertEqual(live,[])
                                state = states[0]
                                self.assertIs(state.owner,e); self.assertEqual(state.epoch,e.suspension.epoch)
                                self.assertTrue(state.closed); self.assertEqual(state.phase,'closed')
                                self.assertEqual(state.frames,[]); self.assertEqual(state._owned_activations,[])
                                self.assertIsNone(state.pending_cursor); self.assertIsNone(state.pending_request)
                                self.assertIsNone(state.pending_authority); self.assertIsNone(state.pending_pc)
                                self.assertIsNone(state._cursor)
                                state.validate(full=True); self.assertEqual(state.roots(),())
                                self.assertFalse(any(getattr(frame,'compiled_flow_state',None) is state for frame in runner.capture_frames))
                                self.assertFalse(any(row is state for row in getattr(runner.iterator,'_compiled_flows',())))
                                self.assertIs(state.program,functions[0].compiled_plan.flow)
                            else:
                                self.assertEqual(len(live),1); state = live[0]
                                self.assertIs(state.owner,e); self.assertEqual(state.epoch,e.suspension.epoch)
                                self.assertEqual(state.phase,'host'); state.validate(full=True)
                                self.assertIs(state.pending_cursor,runner.iterator)
                                self.assertIs(state.pending_request,request)
                                self.assertIs(state.scope.get('pad'),pad)
                                self.assertTrue(any(getattr(frame,'compiled_flow_state',None) is state for frame in runner.capture_frames))
                                self.assertIs(state.program,functions[0].compiled_plan.flow)
                        else:
                            self.assertEqual(states,[])
                        visited.append(future)
                        result, charge, paths = recording_seal(e,(task,),None)
                        self.assertIsNone(result); self.assertGreater(charge,0)
                        observations.append((name,charge,paths))
                finally:
                    # Real worker release/reap still occurs if an assertion failed.
                    answer = controlled_wait(futures,*args,**kwargs)
                return answer
            with patch.object(suspension.ThreadPoolExecutor,'submit',submitting), patch.object(suspension,'wait',waiting):
                try:
                    result = ('value',serializable(e.run(source)))
                except LanguageError as error:
                    result = ('error',error.code,error.record())
            self.assertEqual([row[0] for row in observations],expected_names)
            self.assertEqual(len(visited),len(expected_names))
            self.assertEqual(schedule['waitBoundaryReleases'],len(expected_names))
            self.assertEqual(schedule['shutdownReleases'],0)
            self.assertEqual([payload['name'] for kind,payload in effects],expected_names)
            self.assertEqual(e.frames,[]); self.assertEqual(e.depth,0)
            if compiled:
                self.assertTrue(states)
                self.assertTrue(all(state.closed and not state.frames and state.pending_cursor is None for state in states))
                self.assertGreater(functions[0].compiled_plan.flow.portable_resumes,0)
        self.assertEqual(result,('value',expected))
        return result,e.steps-base[0],effects,trace,debug,observations

    def all_offsets(self, cases):
        for label,case in cases:
            charges = [[] for name in case[3]]
            for padding in range(65):
                with self.subTest(case=label,padding=padding):
                    bare = self.run_case(case,padding,'bare')
                    old = self.run_case(case,padding,'interpreter')
                    actual = self.run_case(case,padding,'auto-flow')
                    self.assertEqual(old,bare,'explicit interpreter must match genuinely uncompiled function')
                    self.assertEqual(actual,bare,'compiled CFG must match the independent bare-function path')
                    for index,row in enumerate(actual[-1]): charges[index].append(row[1])
            for index,values in enumerate(charges):
                self.assertEqual(len(values),65,(label,index))
                self.assertGreater(len(set(values)),1,(label,case[3][index],'charged wrap boundary must really be crossed'))

    def test_if_condition_both_branches_and_return_all65(self):
        self.all_offsets([('then',branch_case(True)),('else',branch_case(False))])

    def test_while_condition_body_continue_break_and_else_all65(self):
        self.all_offsets([('exhaust',while_case(False)),('break',while_case(True))])

    def test_for_source_acquire_step_body_pattern_and_else_all65(self):
        self.all_offsets([('ordinary',for_case(False)),('pattern',for_case(True))])

    def test_nested_loop_abrupt_retirement_and_early_return_all65(self):
        names = ['nested_body','before_continue','nested_body','before_break','after_inner',
                 'nested_body','before_continue','nested_body','early_return']
        self.all_offsets([('nested',('',NESTED,42,names,()))])

    def test_corrupt_activation_vector_retires_exact_owned_boundaries_before_next_effect(self):
        """Direct embedding Await frontier, distinct from the real-Task65 proofs above.

        The Scheduler fixture supplies current ownership; no fake waiting Future
        is installed. An independently created real cold Task is borrowed as
        the actual Await target and checked before fixture-wide final cleanup.
        """
        from chaos import capture_frames
        from chaos.execution_frames import EvaluationBoundaryFrame, ensure_cursor
        from test_operator_execution_frames import active
        from test_callable_execution_frames import pending
        foreign = Evaluator().run('fn foreign(){0}')

        def run(mode, fault_kind):
            notes, states, leaves, retained = [], [], [], []
            tracked = set()
            e = Evaluator(step_limit=30000,timeout=30)
            plain = e.run('async fn(borrowed,note){note("prefix");await borrowed;note("forbidden-suffix");42}')
            with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('portable-test')):
                function = compile_function(e,plain,'auto-flow')
            note = Native('note',lambda value:notes.append(value),e,captures=())
            fault = (ResourceLimit('hard-fault') if fault_kind == 'hard' else GeneratorExit('close-fault')
                     if fault_kind == 'close' else LanguageError('ordinary-fault',code='injected'))
            original_init, original_leave = FlowState.__init__, capture_frames.leave
            def created(state,*args,**kwargs):
                original_init(state,*args,**kwargs); states.append(state)
            def leave(frame,value):
                if frame is not None and id(frame) in tracked:
                    self.assertFalse(any(old is frame for old in leaves),'actual owned capture must retire once')
                    leaves.append(frame)
                return original_leave(frame,value)
            with active(e) as holder, patch.object(FlowState,'__init__',created), patch.object(capture_frames,'leave',leave):
                def cold():
                    notes.append('borrowed-started')
                    if False: yield None
                    return 99
                borrowed = e.suspension.task(cold())
                before_borrowed = (borrowed.state,borrowed.runner.started,borrowed.runner.iterator.closed,
                                   borrowed.cancel_event.is_set(),tuple(borrowed.cancel_sources))
                before = (list(e.frames),e.depth,e.current_span,getattr(holder,'module_scope',None),
                          getattr(holder,'debug_context',None),list(holder.capture_frames))
                cursor = ensure_cursor(e,e._s_invoke(function,[borrowed,note],force=True))
                try:
                    request = pending(cursor)
                    self.assertIs(type(request),suspension.Await); self.assertIs(request.task,borrowed)
                    self.assertEqual(notes,['prefix']); self.assertEqual(len(states),1)
                    state = states[0]; state.validate(full=True)
                    self.assertIs(state.pending_cursor,cursor); self.assertIs(state.pending_request,request)
                    self.assertIs(type(state.frames),list); self.assertTrue(state.frames)
                    boundaries = tuple(state.frames); retained.extend(boundaries)
                    self.assertTrue(state._owned_activations)
                    self.assertTrue(all(type(frame) is EvaluationBoundaryFrame and frame.entered for frame in boundaries))
                    captures = tuple(frame.capture for frame in boundaries)
                    self.assertTrue(all(frame is not None and frame.owner is e and frame.holder is holder for frame in captures))
                    tracked.update(id(frame) for frame in captures)
                    if mode == 'container': state.frames = foreign
                    elif mode == 'row-foreign': state.frames[0] = foreign
                    elif mode == 'row-none': state.frames[0] = None
                    try:
                        yielded = cursor.throw(fault)
                        while yielded is suspension.CHECKPOINT: yielded = cursor.send(None)
                    except StopIteration as done:
                        outcome = ('done',serializable(done.value),False)
                    except BaseException as error:
                        outcome = ('error',type(error).__name__,error is fault)
                        if mode != 'valid' and fault_kind == 'ordinary':
                            self.assertIsInstance(error,LanguageError); self.assertEqual(error.code,'ownership_error')
                        elif fault_kind == 'hard': self.assertIs(error,fault)
                    else:
                        self.fail('injected failure advanced to another live protocol/effect')
                    self.assertEqual(notes,['prefix'])
                    self.assertTrue(cursor.closed); self.assertEqual(cursor.frames,[])
                    self.assertTrue(state.closed); self.assertEqual(state.frames,[]); self.assertFalse(state._owned_activations)
                    self.assertIsNone(state.pending_cursor); self.assertIsNone(state.pending_request)
                    self.assertTrue(all(not frame.entered for frame in boundaries))
                    self.assertEqual({id(frame) for frame in leaves},tracked)
                    self.assertEqual(len(leaves),len(tracked))
                    self.assertTrue(all(not frame.roots and not frame.identities for frame in captures))
                    self.assertEqual(e.frames,before[0]); self.assertEqual(e.depth,before[1]); self.assertIs(e.current_span,before[2])
                    self.assertIs(getattr(holder,'module_scope',None),before[3]); self.assertIs(getattr(holder,'debug_context',None),before[4])
                    self.assertEqual(holder.capture_frames,before[5]); self.assertIs(e.suspension.current,holder)
                    self.assertEqual((borrowed.state,borrowed.runner.started,borrowed.runner.iterator.closed,
                                      borrowed.cancel_event.is_set(),tuple(borrowed.cancel_sources)),before_borrowed)
                    cursor.close(); self.assertEqual(len(leaves),len(tracked)); self.assertEqual(notes,['prefix'])
                    return outcome
                finally:
                    cursor.close()

        for kind in ('ordinary','hard','close'):
            baseline = run('valid',kind)
            for mode in ('container','row-foreign','row-none'):
                with self.subTest(fault=kind,corruption=mode):
                    outcome = run(mode,kind)
                    if kind in ('hard','close'):
                        self.assertEqual(outcome,baseline,'preserve actual public hard-error/GeneratorExit frontier')
                    else:
                        self.assertEqual(outcome[:2],('error','LanguageError'))
