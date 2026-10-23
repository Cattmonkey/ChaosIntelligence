import unittest
from chaos.language import Evaluator,Parser,Env,LanguageError
from chaos.execution_frames import ExecutionCursor,OperandFrame,InvocationFrame
from chaos.suspension import CHECKPOINT

class ExecutionFrames(unittest.TestCase):
    def test_default_source_uses_typed_frames_and_full_values(self):
        e=Evaluator();seen=[]
        def trace(event):
            c=getattr(e,'_execution_cursor',None)
            if c:seen.extend(type(x).__name__ for x in c.frames)
        e.trace_handler=trace
        self.assertEqual(e.run('let f=fn(x){x+2};[f(40),multiple_value_list(fn(){return_values(null,42)})]'),[42,[None,42]])
        self.assertIn('OperandFrame',seen);self.assertIn('InvocationFrame',seen)

    def test_direct_iterator_keeps_scheduler_events_and_completion(self):
        e=Evaluator();e.suspension.begin()
        try:
            c=e._s_evaluate(Parser('while false{}else{42}').program()[1][0],Env(e.env));self.assertIs(type(c),ExecutionCursor);events=[]
            try:
                while True:events.append(next(c))
            except StopIteration as done:self.assertEqual(done.value,42)
            self.assertEqual(events,[CHECKPOINT,CHECKPOINT]);self.assertEqual(c.frames,[])
        finally:e.suspension.finish()

    def test_live_block_statement_list_is_read_after_checkpoint(self):
        e=Evaluator();fn=e.run('fn(x){x+2}');c=e._s_invoke(fn,[40]);self.assertIs(next(c),CHECKPOINT)
        replacement=Evaluator().run('fn(x){x+3}');fn.body[1][:]=replacement.body[1]
        with self.assertRaises(StopIteration) as done:next(c)
        self.assertEqual(done.exception.value,43);self.assertEqual(c.frames,[])

    def test_literal_collections_binding_and_boolean_context(self):
        e=Evaluator();self.assertEqual(e.run('var x:Int=1;let y={a:[x,2],b:tuple([3,4])};[y.a[0],if true{42}else{0},true&&return_values(true,false),!false,-2]'),[1,42,True,True,-2])

    def test_terminal_failure_clears_cursor_and_capture_state(self):
        e=Evaluator();e.suspension.begin();c=e._s_evaluate(Parser('let x=1;throw 42').program(),Env(e.env))
        try:
            with self.assertRaises(LanguageError):
                while True:next(c)
            self.assertEqual(c.frames,[]);self.assertIsNone(getattr(e,'_execution_cursor',None))
        finally:e.suspension.finish()

    def test_unstarted_throw_preserves_identity_without_entry(self):
        e=Evaluator();seen=[];e.trace_handler=seen.append;c=e._s_evaluate(Parser('42').program(),Env(e.env));failure=LanguageError('original')
        with self.assertRaises(LanguageError) as caught:c.throw(LanguageError,failure)
        self.assertIs(caught.exception,failure);self.assertEqual(seen,[]);self.assertEqual(e.steps,0);self.assertEqual(c.frames,[])
