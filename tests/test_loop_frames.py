"""Typed loop state is authoritative; historical generator path is the oracle."""
from contextlib import contextmanager
from unittest.mock import patch
import unittest

from chaos.language import Evaluator,Env,Parser,Native,LanguageError,BreakSignal,ContinueSignal,ResourceLimit
from chaos.execution_frames import ExecutionCursor,LoopFrame,LoopCompletionFrame,frame_roots
from chaos.suspension import CHECKPOINT,Await,TaskCancelled


@contextmanager
def legacy():
    with patch.object(LoopFrame,'KINDS',set()),patch.object(LoopCompletionFrame,'KINDS',set()):yield


class LoopFrames(unittest.TestCase):
    def observation(self,source,*,limit=10000,cancel_at=None,old=False):
        seen=[];trace=[];debug=[];e=Evaluator(step_limit=limit,timeout=30)
        original=e.tick
        def tick():
            if cancel_at is not None and e.steps==cancel_at:raise TaskCancelled('fixture cancel')
            original()
        e.tick=tick;e.tool_handler=lambda n,v:seen.append((n,v)) or v
        e.trace_handler=lambda event:trace.append(dict(event)) if event.get('event')=='expression' else None
        e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
        def run():
            try:return ('value',e.run(source))
            except BaseException as error:return ('error',type(error).__name__,getattr(error,'code',None),str(error),getattr(error,'location',None))
        if old:
            with legacy():result=run()
        else:result=run()
        return result,e.steps,seen,trace,debug

    def test_loop_family_and_completion_match_reference(self):
        sources=[
            'var n=0;var out=[];while n<4{n+=1;if n==2{continue}else{null};out=append(out,n);if n==3{break}else{null}}else{out=append(out,99)};[n,out]',
            'var out=[];for x in [1,2,3]{if x==2{continue}else{null};out=append(out,x)}else{out=append(out,42)};out',
            'var out=[];for [a,b] in [[20,21],[21,22]]{out=append(out,a+b)}else{out=append(out,99)};out',
            'async fn* source(){yield ([20,21]);yield ([21,22])};var out=[];async for [a,b] in source(){out=append(out,a+b)}else{out=append(out,99)};out',
            'async fn* source(){yield 1;yield 2};var out=[];async for x in source(){out=append(out,x);break}else{out=append(out,99)};out',
            'multiple_value_list(fn(){while false{}else{return_values(20,22)}})',
            'fn first(){for x in [42,43]{return return_values(x,"return")}};multiple_value_list(first)',
        ]
        expected_results=[[3,[1,3]],[1,3,42],[41,43,99],[41,43,99],[1],[20,22],[42,'return']]
        for source,expected in zip(sources,expected_results):
            with self.subTest(source=source):
                actual=self.observation(source)
                self.assertEqual(actual[0],('value',expected))
                self.assertEqual(actual,self.observation(source,old=True))

    def test_every_step_budget_and_cancel_threshold_preserves_order_and_locations(self):
        source='var n=0;while n<2{n+=1;tool("body",n)}else{tool("else",n)};for [a,b] in [[20,22]]{tool("pair",a+b)};42'
        total=self.observation(source)[1]
        for threshold in range(total+2):
            with self.subTest(kind='budget',threshold=threshold):self.assertEqual(self.observation(source,limit=threshold),self.observation(source,limit=threshold,old=True))
            with self.subTest(kind='cancel',threshold=threshold):self.assertEqual(self.observation(source,cancel_at=threshold),self.observation(source,cancel_at=threshold,old=True))

    def test_direct_loop_checkpoints_and_full_completion_are_unchanged(self):
        for old in (False,True):
            e=Evaluator();e.suspension.begin()
            try:
                def drive():
                    c=e._s_evaluate(Parser('while false{}else{42}').program()[1][0],Env(e.env));events=[]
                    try:
                        while True:events.append(next(c))
                    except StopIteration as done:self.assertEqual(done.value,42)
                    self.assertEqual(events,[CHECKPOINT,CHECKPOINT]);self.assertEqual(c.frames,[])
                if old:
                    with legacy():drive()
                else:drive()
            finally:e.suspension.finish()

    def test_default_frames_hold_actual_loop_cursor_and_iterator_can_be_replaced(self):
        e=Evaluator();held=[]
        def switch():
            frame=next(f for f in e._execution_cursor.frames if type(f)is LoopFrame)
            self.assertEqual(frame.phase,'for_body');self.assertIsNotNone(frame.scope)
            self.assertIn(frame.scope,frame_roots(frame));held.append(frame)
            if len(held)==1:frame.handle=e.suspension.iteration._register(values=[99])
            return None
        e.env.bind('switch',Native('switch',switch,e))
        self.assertEqual(e.run('var out=[];for x in [1,2,3]{switch();out=append(out,x)};out'),[1,99])
        self.assertEqual(len(held),2);self.assertIsNone(held[-1].scope);self.assertIsNone(held[-1].item)

    def test_default_loop_family_never_enters_legacy_execute_branch(self):
        e=Evaluator();original=e._s_execute
        def execute(node,*args,**kwargs):
            self.assertNotIn(node[0],LoopFrame.KINDS|LoopCompletionFrame.KINDS)
            return original(node,*args,**kwargs)
        e._s_execute=execute
        source='var n=0;while n<1{n+=1}else{n+=1};for x in [1]{n+=x};for [a,b] in [[1,2]]{n+=a+b};async fn* source(){yield ([1,2])};async for [a,b] in source(){n+=a+b};async fn* single(){yield 1};async for x in single(){n+=x};n'
        self.assertEqual(e.run(source),10)

    def test_capture_graph_follows_new_frame_values_without_native_reflection(self):
        from types import SimpleNamespace
        from chaos.capture_graph import seal
        e=Evaluator();other=Evaluator();foreign=Native('foreign',lambda:42,other)
        boundary=SimpleNamespace(node=('for','x',('literal',[]),('block',[])),env=Env(),options={},capture=None,tail=False)
        frame=LoopFrame(boundary);frame.owner=e;frame.items=foreign
        with self.assertRaises(LanguageError) as error:seal(e,[frame],None)
        self.assertEqual(error.exception.code,'ownership_error')
        completion=LoopCompletionFrame(boundary);completion.owner=e;completion.value=foreign
        with self.assertRaises(LanguageError) as error:seal(e,[completion],None)
        self.assertEqual(error.exception.code,'ownership_error')

    def test_condition_and_next_transfers_are_not_body_breaks(self):
        for transfer in (BreakSignal,ContinueSignal):
            for source in ('while signal(){}else{42}', 'class C{fn __iter__(self){self}fn __next__(self){signal()}};for x in C(){}else{42}'):
                observations=[]
                for old in (False,True):
                    e=Evaluator();failure=transfer();e.env.bind('signal',Native('signal',lambda:(_ for _ in ()).throw(failure),e))
                    try:
                        if old:
                            with legacy():e.run(source)
                        else:e.run(source)
                    except BaseException as error:observations.append((type(error),getattr(error,'code',None),str(error)))
                self.assertEqual(observations[0],observations[1]);self.assertEqual(len(observations),2)

    def test_borrowed_iterator_is_not_closed_at_break_or_return(self):
        source='var closed=0;class C{var n=0;fn __iter__(self){self}fn __next__(self){self.n+=1;{done:self.n>3,value:self.n}}fn __close__(self){closed+=1;null}};let c=C();for x in c{break};let before=closed;let step=next(c);fn first(){for x in c{return x}};let value=first();[before,step,value,closed]'
        self.assertEqual(Evaluator().run(source),[0,{'done':False,'value':2},3,0])
        self.assertEqual(self.observation(source),self.observation(source,old=True))

    def test_pending_async_next_is_explicit_root_and_cancelled_on_throw(self):
        e=Evaluator();e.suspension.begin();c=None
        try:
            source='async fn* source(){yield 42};async for x in source(){x}'
            c=e._s_evaluate(Parser(source).program(),Env(e.env))
            while True:
                request=next(c)
                if type(request)is Await:break
                self.assertIs(request,CHECKPOINT)
            loop=next(f for f in c.frames if type(f)is LoopFrame)
            self.assertIs(loop.operation,request.task);self.assertIn(request.task,frame_roots(loop))
            failure=LanguageError('pending cancelled',code='fixture')
            with self.assertRaises(LanguageError) as caught:c.throw(failure)
            self.assertIs(caught.exception,failure);self.assertTrue(request.task.cancel_event.is_set());self.assertIsNone(loop.operation);self.assertEqual(c.frames,[])
        finally:
            if c is not None:c.close()
            e.suspension.finish()

    def test_cursor_close_cancels_pending_async_operation_without_closing_iterator(self):
        e=Evaluator();e.suspension.begin();c=None
        try:
            c=e._s_evaluate(Parser('async fn* source(){yield 42};async for x in source(){x}').program(),Env(e.env))
            while True:
                request=next(c)
                if type(request)is Await:break
            loop=next(f for f in c.frames if type(f)is LoopFrame);handle=loop.handle
            c.close();self.assertTrue(request.task.cancel_event.is_set());self.assertNotEqual(handle.state,'closed');self.assertEqual(c.frames,[])
        finally:
            if c is not None:c.close()
            e.suspension.finish()

    def test_live_while_body_is_read_after_condition_suspends(self):
        e=Evaluator();e.suspension.begin();scope=Env(e.env);node=Parser('while true{break}').program()[1][0]
        try:
            c=e._s_evaluate(node,scope);self.assertIs(next(c),CHECKPOINT)
            # Body's source sequence is read only when its own checkpoint resumes.
            self.assertIs(next(c),CHECKPOINT)
            node[2][1][:]=Parser('throw "new body"').program()[1]
            with self.assertRaises(LanguageError) as error:next(c)
            self.assertEqual(error.exception.value,'new body');self.assertEqual(c.frames,[])
        finally:e.suspension.finish()

    def test_loop_completion_frame_is_real_and_does_not_capture_outer_collector(self):
        e=Evaluator();seen=[]
        def inspect():
            frames=e._execution_cursor.frames
            loops=[f for f in frames if type(f)is LoopFrame]
            completions=[f for f in frames if type(f)is LoopCompletionFrame]
            seen.append((len(loops),len(completions),loops[-1].options.get('_completion') if loops else None))
            return 42
        e.env.bind('inspect',Native('inspect',inspect,e))
        self.assertEqual(e.run('while false{}else{for x in [1]{inspect()}}'),None)
        self.assertEqual(seen,[(1,1,None)])

    def test_async_generator_body_and_cleanup_resume_once(self):
        source='var events=[];async fn* source(){try{yield 20;yield 22}finally{events=append(events,"source")}};async fn* output(){try{async for x in source(){yield x}}finally{events=append(events,"output")}};let s=output();let first=await anext(s);let second=await anext(s);await aclose(s);[first,second,events]'
        self.assertEqual(self.observation(source),self.observation(source,old=True))

    def test_pattern_failure_and_nested_finally_remain_reference_identical(self):
        for source in ('try{for [a,b] in [[1]]{42}else{99}}catch e{e.code}', 'var out=[];while true{try{for x in [1]{continue};break}finally{out=append(out,42)}}else{out=append(out,99)};out'):
            self.assertEqual(self.observation(source),self.observation(source,old=True))
