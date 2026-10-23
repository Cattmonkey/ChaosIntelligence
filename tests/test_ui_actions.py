import threading
import time
import unittest

from chaos.language import Evaluator, LanguageError, Native, TailCall
from chaos.session import Session
from chaos.suspension import TaskCancelled


PREFIX = 'let view=ui_artifact("page","Demo",ui_node("ActionButton",["Run","run",{n:41}]));'
EVENT = '{name:"run",payload:{n:41}}'


class UIActionTests(unittest.TestCase):
    def test_cold_dispatch_and_ast_sync_async_callbacks(self):
        for handler in ('fn(x){x.n+1}', 'async fn(x){await sleep(0);x.n+1}'):
            with self.subTest(handler=handler):
                value = Evaluator().run(PREFIX+'let h=agent_tool("run",'+handler+',input_type="Record",output_type="Int");let job=ui_dispatch(view,'+EVENT+',{run:h});[task_info(job).state,await job]')
                self.assertEqual(value, ['created', 42])

    def test_handler_stays_on_ast_and_has_real_effect_authority(self):
        thread = threading.get_ident(); ast = []; worker = []
        evaluator = Evaluator(tool_handler=lambda name, value: worker.append(threading.get_ident()) or value,
                              debug_hook=lambda *args: ast.append(threading.get_ident()))
        value = evaluator.run(PREFIX+'let h=agent_tool("run",fn(x){tool("test",x.n)+1},capabilities=["test"]);await ui_dispatch(view,'+EVENT+',{run:h})')
        self.assertEqual(value, 42); self.assertTrue(ast); self.assertEqual(set(ast), {thread}); self.assertNotIn(thread, worker)
        self.assertEqual(Evaluator(tool_handler=lambda *args: 42).run(PREFIX+'let h=agent_tool("run",fn(x){tool("test",x.n)});try{await ui_dispatch(view,'+EVENT+',{run:h})}catch e{e.code}'), 'capability_error')

    def test_unknown_forged_events_and_invalid_handlers_no_callback(self):
        expressions = ['ui_dispatch(view,{name:"missing",payload:{}},{})',
            'ui_dispatch(view,{name:"run",payload:{n:42}},{run:h})',
            'ui_dispatch(view,{name:"run",payload:{n:41},extra:1},{run:h})',
            'ui_dispatch(view,'+EVENT+',{run:fn(x){x}})', 'ui_dispatch(view,'+EVENT+',{})']
        for expression in expressions:
            with self.subTest(expression=expression):
                value = Evaluator().run(PREFIX+'var seen=false;let h=agent_tool("run",fn(x){seen=true;x});try{'+expression+'}catch e{null};seen')
                self.assertFalse(value)

    def test_return_contract_and_native_artifact_not_json(self):
        self.assertEqual(Evaluator().run(PREFIX+'let h=agent_tool("run",fn(x){"wrong"},output_type="Int");try{await ui_dispatch(view,'+EVENT+',{run:h})}catch e{e.code}'), 'runtime_error')
        self.assertEqual(Evaluator().run(PREFIX+'let h=agent_tool("run",fn(x){view});try{await ui_dispatch(view,'+EVENT+',{run:h})}catch e{e.code}'), 'agent_tool_error')

    def test_cross_owner_handler_and_epoch_task(self):
        other = Evaluator().run('agent_tool("run",fn(x){x})')
        with self.assertRaises(LanguageError): Evaluator().run(PREFIX+'ui_dispatch(view,'+EVENT+',{run:input})', {'input': other})
        session = Session(); session.evaluate(PREFIX+'let h=agent_tool("run",fn(x){x});let job=ui_dispatch(view,'+EVENT+',{run:h})')
        with self.assertRaises(LanguageError): session.evaluate('await job')

    def test_cancel_cold_task_has_no_callback(self):
        value = Evaluator().run(PREFIX+'var seen=false;let h=agent_tool("run",fn(x){seen=true;x});let job=ui_dispatch(view,'+EVENT+',{run:h});cancel(job);seen')
        self.assertFalse(value)

    def test_generation_is_cold_and_strict_ast_commit(self):
        seen = []
        def effect(kind, payload, cancel):
            seen.append((kind, payload)); return {'source': 'root = Text("generated")'}
        evaluator = Evaluator(effect_handler=effect, agent_handler=lambda *args: None)
        value = evaluator.run('agent a{provider:"model",tools:[]};let job=ui_generate(a,{request:"page"});let before=task_info(job).state;let view=await job;[before,type_of(view),view.root.props.text]')
        self.assertEqual(value, ['created', 'UIArtifact', 'generated']); self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], 'ui'); self.assertEqual(set(seen[0][1]), {'operation', 'agent', 'request', 'kind', 'title', 'previous', 'libraryHash'})

    def test_generation_bad_final_and_title_fail_without_fake_artifact(self):
        evaluator = Evaluator(effect_handler=lambda *args: {'source': 'root = Text("truncated"'}, agent_handler=lambda *args: None)
        with self.assertRaises(LanguageError): evaluator.run('agent a{provider:"model"};await ui_generate(a,"request")')
        seen = []
        evaluator = Evaluator(effect_handler=lambda *args: seen.append(1), agent_handler=lambda *args: None)
        with self.assertRaises(LanguageError): evaluator.run('agent a{provider:"model"};ui_generate(a,"x",title=input)', {'input': '\ud800'})
        self.assertEqual(seen, [])

    def test_pure_generation_requires_host_and_64_native_limit(self):
        self.assertEqual(Evaluator().run('agent a{provider:"model"};try{await ui_generate(a,"x")}catch e{e.code}'), 'capability_error')
        with self.assertRaises(LanguageError): Evaluator().run(PREFIX+'let h=agent_tool("run",fn(x){x});for n in range(65){ui_dispatch(view,'+EVENT+',{run:h})}')

    def test_native_cold_await_cancels_owned_worker_and_drains(self):
        started, finished, cancelled = threading.Event(), threading.Event(), []
        def effect(kind, payload, cancel):
            started.set(); cancelled.append(cancel.wait(1)); finished.set()
            return {'source': 'root = Text("done")'}
        evaluator = Evaluator(effect_handler=effect, agent_handler=lambda *args: None)
        with self.assertRaises(TaskCancelled):
            evaluator.run('agent a{provider:"model"};async fn parent(){await ui_generate(a,"x")};let job=parent();spawn(job);await sleep(0.03);cancel(job);await job')
        self.assertTrue(started.is_set()); self.assertTrue(finished.is_set()); self.assertEqual(cancelled, [True])
        self.assertFalse(any(thread.name.startswith('chaos-effect') and thread.is_alive() for thread in threading.enumerate()))

    def test_spawned_generation_is_borrowed_by_cancelled_waiter(self):
        cancelled = []
        def effect(kind, payload, cancel):
            time.sleep(.08); cancelled.append(cancel.is_set()); return {'source': 'root = Text("done")'}
        evaluator = Evaluator(effect_handler=effect, agent_handler=lambda *args: None)
        result = evaluator.run('agent a{provider:"model"};let shared=ui_generate(a,"x");spawn(shared);async fn waiter(){await shared};let w=waiter();spawn(w);await sleep(0.02);cancel(w);(await shared).root.props.text')
        self.assertEqual(result, 'done'); self.assertEqual(cancelled, [False])

    def test_current_authority_cannot_dispatch_broader_tool(self):
        evaluator = Evaluator(tool_handler=lambda *args: 42)
        limited = Native('limited', lambda fn: TailCall(fn, []), evaluator, capabilities=frozenset())
        result = evaluator.run(PREFIX+'let h=agent_tool("run",fn(x){tool("secret",x)},capabilities=["secret"]);fn go(){ui_dispatch(view,'+EVENT+',{run:h})};try{limited(go)}catch e{e.code}', {'limited': limited})
        self.assertEqual(result, 'capability_error')


if __name__ == '__main__': unittest.main()
