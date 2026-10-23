"""Host audit policy observes real typed failures before user catch consumes them."""
from types import SimpleNamespace
import unittest

from chaos.language import Evaluator,LanguageError,Native
from chaos.evaluation import evaluate,_FixtureEvaluator


class InvocationFailureObserver(unittest.TestCase):
    def denied(self,work):
        source='agent a{provider:"model",tools:["secret"]};'+work+';await agent_bind(a,[agent_tool("double",work)])(21)'
        fixtures={'agents':[{'definition':{'kind':'agent','name':'a','config':{'provider':'model','tools':['secret']}},
                            'input':21,'actions':[{'tool':'double','input':21,'expect':1},{'final':42}]}]}
        with self.assertRaises(LanguageError) as caught:
            evaluate(source,fixtures=fixtures,capabilities=['secret'],tool_descriptors={'secret':{}})
        error=caught.exception
        self.assertEqual(error.code,'fixture_error')
        self.assertFalse(error.fixture_evidence['passed'])
        # Catch remains effective: the real language handler returned 1 and the
        # fixture reached its final action, while its audit retains the denial.
        self.assertEqual(error.fixture_evidence['agents'][0]['completed'],1)
        return error

    def test_plain_call_denial_is_sticky_after_catch(self):
        self.denied('fn work(x){try{let denied=tool("secret",x);denied}catch e{1}}')

    def test_protected_tail_call_denial_is_sticky_after_catch(self):
        self.denied('fn work(x){try{tool("secret",x)}catch e{1}}')

    def test_try_else_preserves_sticky_failure_while_catch_completes(self):
        self.denied('fn work(x){try{tool("secret",x)}catch e{1}else{0}}')

    def test_group_handler_cannot_erase_invocation_denial(self):
        self.denied('fn work(x){try{tool("secret",x)}catch* _ as group{1}}')

    def test_suspended_guard_inner_catch_cannot_erase_denial(self):
        self.denied('async fn work(x){try{throw x}catch e if do{await sleep(0);try{tool("secret",x)}catch denied{true}}{1}}')

    def test_finally_inner_catch_cannot_erase_denial(self):
        self.denied('fn work(x){try{1}finally{try{tool("secret",x)}catch denied{null}}}')

    def test_ordinary_evaluator_still_allows_catching_capability_error(self):
        error=LanguageError('fixture-independent denial',code='capability_error')
        def denied():raise error
        e=Evaluator();e.env.bind('deny',Native('deny',denied,e))
        self.assertEqual(e.run('try{deny()}catch e{42}'),42)
        self.assertIsNone(e.handled_error);self.assertIsNone(e.error_context)

    def test_shared_observation_helper_keeps_first_failure_idempotent(self):
        from chaos.evaluation import FixtureHost
        host=SimpleNamespace(failure=None)
        import threading
        host.lock=threading.RLock();host.latch=lambda error:FixtureHost.latch(host,error)
        e=_FixtureEvaluator();e.fixture_host=host
        first=LanguageError('first',code='capability_error');second=LanguageError('second',code='fixture_error')
        e._frame_invocation_error(first);e._observe_fixture_error(first);e._observe_fixture_error(second)
        self.assertEqual(host.failure,str(first))
        e._frame_invocation_error(LanguageError('ordinary',code='runtime_error'))
        self.assertEqual(host.failure,str(first))

    def test_actual_failure_hook_runs_after_invocation_restores_context(self):
        observed=[]
        class Host(Evaluator):
            def _frame_invocation_error(self,error):
                observed.append((error,self.handled_error,self.error_context,tuple(self.frames)))
        e=Host();failure=LanguageError('denied',code='capability_error')
        def denied():raise failure
        e.env.bind('deny',Native('deny',denied,e))
        self.assertEqual(e.run('try{deny()}catch e{42}'),42)
        self.assertEqual(len(observed),1);self.assertIs(observed[0][0],failure)
        self.assertIsNone(observed[0][1]);self.assertIsNone(observed[0][2])
        self.assertEqual(observed[0][3],())
