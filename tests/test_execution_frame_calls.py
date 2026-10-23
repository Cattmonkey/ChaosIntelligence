import unittest
from chaos.language import Evaluator,Native,LanguageError
from chaos.execution_frames import OperandFrame,InvocationFrame,ReturnFrame

class ExecutionFrameCalls(unittest.TestCase):
    def test_earlier_argument_field_is_authoritative_during_later_callback(self):
        e=Evaluator();prefix=[]
        def change():
            c=e._execution_cursor;frame=next(x for x in reversed(c.frames) if isinstance(x,OperandFrame) and x.node[0]=='call' and x.args)
            frame.args[0]=40;return 2
        e.env.bind('change',Native('change',change,e));e.env.bind('prefix',Native('prefix',lambda:prefix.append(1) or 1,e))
        self.assertEqual(e.run('fn sum(a,b){a+b};sum(prefix(),change())'),42);self.assertEqual(prefix,[1])

    def test_return_contract_frame_is_authoritative(self):
        e=Evaluator()
        def narrow():
            frame=next(x for x in reversed(e._execution_cursor.frames) if isinstance(x,ReturnFrame))
            frame.invocation.required['String']=dict(e.current_span);return 42
        e.env.bind('narrow',Native('narrow',narrow,e))
        with self.assertRaises(LanguageError):e.run('fn f(){let value=narrow();value};f()')

    def test_tail_calls_compose_and_result_modes(self):
        e=Evaluator(step_limit=300000);self.assertEqual(e.run('fn f(n){if n==0{return_values(40,99)}else{f(n-1)}};[f(2000),multiple_value_list(fn(){f(2000)}),compose(fn(x){x+2},fn(){return_values(40,99)})()]'),[40,[40,99],42])

    def test_eager_binding_spread_keywords_and_cold_async(self):
        e=Evaluator();self.assertEqual(e.run('fn f(a,b=2,*,c=3){a+b+c};async fn later(x){x};[f(*[37],**{c:3}),type_of(later(42)),await later(42)]'),[42,'Task',42])
