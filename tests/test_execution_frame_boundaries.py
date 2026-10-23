import unittest
from chaos.language import Evaluator,Native,TailCall,LanguageError
from chaos.execution_frames import ExecutionCursor

class ExecutionFrameBoundaries(unittest.TestCase):
    def test_same_cursor_protocol_callback_and_prompt_cleanup(self):
        e=Evaluator();self.assertEqual(e.run('var n=0;let p=prompt_tag();let x=call_prompt(p,fn(){dynamic_wind(fn(){n=n+1},fn(){abort_prompt(p,40)},fn(){n=n+2})});[x,n,map(fn(x){x+2},[40])]'),[40,3,[42]])

    def test_task_stream_and_groups_keep_independent_cursors(self):
        e=Evaluator();self.assertEqual(e.run('async fn* g(){yield 40;return return_values(42,99)};let stream=g();let first=await anext(stream);await anext(stream);[first.value,multiple_value_list(stream),await gather([async fn(){42}()])]'),[40,[42,99],[42]])
        self.assertTrue(all(type(x.runner.iterator) is ExecutionCursor for x in e.suspension.tasks))

    def test_native_foreign_reentrant_drive_preserves_outer_state(self):
        e=Evaluator();e.env.bind('inner',Native('inner',lambda fn:e.invoke(fn,[40]),e));self.assertEqual(e.run('1+inner(fn(x){x+1})'),42)

    def test_close_preserves_real_opaque_cleanup_failure_and_releases_frames(self):
        from chaos.execution_frames import ensure_cursor
        from chaos.suspension import CHECKPOINT
        failure=RuntimeError('original cleanup failure')
        def operation():
            try:yield CHECKPOINT
            finally:raise failure
        baseline=operation();next(baseline)
        with self.assertRaises(RuntimeError) as original:baseline.close()
        self.assertIs(original.exception,failure)
        e=Evaluator();cursor=ensure_cursor(e,operation());next(cursor)
        with self.assertRaises(RuntimeError) as actual:cursor.close()
        self.assertIs(actual.exception,failure);self.assertEqual(cursor.frames,[]);self.assertIsNone(getattr(e,'_execution_cursor',None))

    def test_seal_reads_replaced_authoritative_argument_fields(self):
        from chaos.capture_graph import seal
        from chaos.execution_frames import OperandFrame
        e=Evaluator();other=Evaluator();foreign=Native('foreign',lambda:42,other)
        def replace():
            frame=next(x for x in reversed(e._execution_cursor.frames) if isinstance(x,OperandFrame) and x.node[0]=='call' and x.args)
            frame.args=[foreign]
            seal(e,(e.suspension.current,),None)
            return 2
        e.env.bind('replace_frame_args',Native('replace_frame_args',replace,e))
        with self.assertRaises(LanguageError) as caught:e.run('fn sum(x,y){42};sum(1,replace_frame_args())')
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_selected_argument_survives_live_future_ast_mutation(self):
        from chaos.language import Parser,Node
        e=Evaluator();tree=Parser('fn pair(a,b){[a,b]};pair(a=mutate(),b=2)').program();call=tree[1][-1]
        def mutate():
            call[2][0]=('keyword','b',Node(('literal',99)))
            return 1
        e.env.bind('mutate',Native('mutate',mutate,e));e.suspension.begin()
        try:self.assertEqual(e.suspension.drive(e._s_evaluate(tree,e.env)),[1,2])
        finally:e.suspension.finish()

    def test_selected_record_key_survives_live_pair_mutation(self):
        from chaos.language import Parser,Node
        e=Evaluator();tree=Parser('{a:mutate()}').program();record=tree[1][0]
        def mutate():
            record[1][0]=('b',Node(('literal',99)))
            return 1
        e.env.bind('mutate',Native('mutate',mutate,e));e.suspension.begin()
        try:self.assertEqual(e.suspension.drive(e._s_evaluate(tree,e.env)),{'a':1})
        finally:e.suspension.finish()

    def test_unknown_invoke_option_rejects_before_execution(self):
        e=Evaluator();seen=[];e.env.bind('hit',Native('hit',lambda:seen.append(1),e));fn=e.run('fn(){hit()}');steps=e.steps
        with self.assertRaises(TypeError):e.invoke(fn,[],bogus=True)
        self.assertEqual(seen,[]);self.assertEqual(e.steps,steps)

    def test_real_capture_limit_keeps_hard_error_and_skips_wind_after(self):
        from chaos.language import ResourceLimit
        e=Evaluator(step_limit=1000000,timeout=120);after=[]
        e.env.bind('capture_after',Native('capture_after',lambda:after.append(1),e))
        source='class C{var n=0;fn __iter__(self){self}fn __next__(self){self.n=self.n+1;{done:self.n>10000,value:self.n}}};dynamic_wind(fn(){null},fn(){map(fn(x){[x]},C())},fn(){capture_after()})'
        with self.assertRaises(ResourceLimit) as caught:e.run(source)
        self.assertEqual(caught.exception.code,'resource_limit')
        self.assertIn('Active evaluation capture roots exceed 20000',str(caught.exception))
        self.assertEqual(after,[])
        self.assertTrue(all(not t.runner.frames and not t.runner.capture_frames and not t.runner.iterator.frames for t in e.suspension.tasks))

    def test_real_capture_limit_near_boundary_success(self):
        e=Evaluator(step_limit=1000000,timeout=120)
        source='class C{var n=0;fn __iter__(self){self}fn __next__(self){self.n=self.n+1;{done:self.n>9900,value:self.n}}};let result=map(fn(x){[x]},C());[length(result),result[9899]]'
        self.assertEqual(e.run(source),[9900,[9900]])
        self.assertTrue(all(not t.runner.capture_frames and not t.runner.iterator.frames for t in e.suspension.tasks))
