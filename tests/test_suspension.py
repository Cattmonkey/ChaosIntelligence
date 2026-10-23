import threading
import time
import unittest

from chaos.language import Evaluator, Parser, LanguageError, ResourceLimit, ExecutionControl
from chaos.session import Session
from chaos.suspension import TaskCancelled


class SuspensionTests(unittest.TestCase):
    def run_code(self, source, **kwargs):
        return Evaluator(**kwargs).run(source)

    def test_generator_is_lazy_and_resumes(self):
        self.assertEqual(self.run_code('var n=0;fn* g(){n=n+1;yield n;n=n+1;yield n;return 7}let x=g();let before=n;let a=next(x);let b=next(x);let c=next(x);[before,a,b,c,n]'), [0,{'done':False,'value':1},{'done':False,'value':2},{'done':True,'value':7},2])

    def test_yield_from_and_for(self):
        self.assertEqual(self.run_code('fn* a(){yield 2;return 9}fn* b(){yield 1;let result=yield from a();yield result;yield from [3,4]}var total=0;for x in b(){total=total+x}total'),19)

    def test_generator_close_finally_nested(self):
        self.assertEqual(self.run_code('var seen=[];fn* a(){try{yield 1;yield 2}finally{seen=append(seen,"inner")}}fn* b(){try{yield from a()}finally{seen=append(seen,"outer")}}let x=b();next(x);close(x);seen'),['inner','outer'])

    def test_generator_close_cleanup_failure(self):
        self.assertEqual(self.run_code('fn* g(){try{yield 1}finally{throw "cleanup"}}let x=g();next(x);try{close(x)}catch e{e.value}'),'cleanup')

    def test_cold_task(self):
        self.assertEqual(self.run_code('var n=0;async fn f(){n=n+1;n}let t=f();let before=n;let result=await t;[before,result,n,type_of(t)]'),[0,1,1,'Task'])

    def test_async_effects_overlap_and_ast_one_thread(self):
        ast_threads=set();effect_threads=set();barrier=threading.Barrier(2)
        def tool(name,value):
            effect_threads.add(threading.get_ident());barrier.wait(timeout=2);return value*2
        main=threading.get_ident()
        value=self.run_code('async fn f(x){tool("test",x)}await gather([f(3),f(4)])',tool_handler=tool,debug_hook=lambda *a:ast_threads.add(threading.get_ident()))
        self.assertEqual(value,[6,8]);self.assertEqual(ast_threads,{main});self.assertEqual(len(effect_threads),2);self.assertNotIn(main,effect_threads)

    def test_source_order_and_gather_order(self):
        self.assertEqual(self.run_code('var xs=[];async fn f(n){xs=append(xs,n);n}let a=f(1);let b=f(2);let r=await gather([a,b]);[r,xs]'),[[1,2],[1,2]])

    def test_cancel_does_not_run_finally(self):
        self.assertEqual(self.run_code('var n=0;async fn f(){try{n=1}finally{n=2}}let t=f();cancel(t);n'),0)
        with self.assertRaises(ExecutionControl):self.run_code('async fn f(){1}let t=f();cancel(t);await t')

    def test_task_error_catch_and_finally(self):
        self.assertEqual(self.run_code('var n=0;async fn f(){try{throw "bad"}finally{n=4}}let value=try{await f()}catch e{e.value};[value,n]'),['bad',4])

    def test_async_and_generator_contracts(self):
        self.assertEqual(self.run_code('async fn f()->Int{2}fn* g()->Int{yield "text";return 3}let a:Task=f();let b:Generator=g();[await a,next(b),next(b)]'),[2,{'done':False,'value':'text'},{'done':True,'value':3}])
        with self.assertRaises(LanguageError):self.run_code('async fn f()->Int{"bad"}await f()')
        with self.assertRaises(LanguageError):self.run_code('fn* g()->Int{yield 1;return "bad"}collect(g())')

    def test_native_callbacks_can_await_indirectly(self):
        self.assertEqual(self.run_code('async fn f(x){x+1}async fn wrapper(){map(fn(x){f(x)},[1,2])}let tasks=await wrapper();await gather(tasks)'),[2,3])

    def test_task_and_generator_context_epoch(self):
        session=Session();session.evaluate('async fn f(){1}fn* g(){yield 1}let t=f();let x=g()')
        self.assertEqual(session.evaluate('await f()'),1)
        with self.assertRaises(LanguageError):session.evaluate('await t')
        with self.assertRaises(LanguageError):session.evaluate('next(x)')
        one=Evaluator();handle=one.run('async fn f(){1}f()')
        with self.assertRaises(LanguageError):Evaluator().run('await input',{'input':handle})

    def test_tail_calls_and_boolean_contract_preserved(self):
        self.assertEqual(self.run_code('fn f(n,a){if n==0{a}else{f(n-1,a+1)}}f(1000,0)',step_limit=100000),1000)
        with self.assertRaises(LanguageError):self.run_code('fn f()->Bool{true && apply(fn(){1},[])}f()')

    def test_shared_budget(self):
        with self.assertRaises(ResourceLimit):self.run_code('async fn loop(){while true{1}}await gather([loop(),loop()])',step_limit=200)

    def test_finish_drains_effect_worker(self):
        done=threading.Event()
        def effect(kind,payload,cancel):
            time.sleep(.03);done.set();return 1
        self.run_code('async fn f(){tool("test",1)}spawn(f());0',tool_handler=lambda *a:None,effect_handler=effect)
        # The task may be cancelled before its first effect; if started it must finish.
        self.assertFalse(any(t.name.startswith('chaos-effect') and t.is_alive() for t in threading.enumerate()))

    def test_contextual_words_old_programs(self):
        self.assertEqual(self.run_code('let async=1;let yield=2;let await=3;let quote=4;let macro=5;async+yield+await+quote+macro'),15)

    def test_await_not_normal_fn(self):
        with self.assertRaises(LanguageError):Parser('fn f(){await x}').program()


    def test_contextual_await_callable_and_parenthesized_operator(self):
        self.assertEqual(self.run_code('let await=fn(x){x};let yield=fn(x){x+1};[await(42),yield(2)]'),[42,3])
        self.assertEqual(self.run_code('let await=3;await'),3)
        self.assertEqual(self.run_code('let await=3;[await!=2,await>2,await%2,await]'),[True,True,1,3])
        self.assertEqual(self.run_code('async fn f(){42}await (f())'),42)
        self.assertEqual(Parser('rec{async fn* good(){yield 1}}').program()[1][0][1][0][2][0], 'async_gen_fn')

    def test_unobserved_started_failure_fails_scope(self):
        events=[]
        with self.assertRaises(LanguageError):self.run_code('async fn bad(){throw "bad"}spawn(bad());0',trace_handler=events.append)
        self.assertTrue(any(e['event']=='task.failed' for e in events))

    def test_auto_close_and_hard_failure(self):
        events=[]
        self.run_code('fn* g(){try{yield 1}finally{tool("cleanup",1)}}let x=g();next(x);0',tool_handler=lambda n,v:events.append(n))
        self.assertEqual(events,['cleanup'])
        events.clear()
        with self.assertRaises(ResourceLimit):self.run_code('fn* g(){try{yield 1}finally{tool("cleanup",1)}}let x=g();next(x);while true{1}',tool_handler=lambda n,v:events.append(n),step_limit=100)
        self.assertEqual(events,[])

    def test_close_rejects_yield_and_really_closes(self):
        self.assertEqual(self.run_code('fn* g(){try{yield 1}finally{yield 2}}let x=g();next(x);let error=try{close(x)}catch e{e.code};[error,next(x)]'),['runtime_error',{'done':True,'value':None}])

    def test_generator_break_allows_resume(self):
        self.assertEqual(self.run_code('fn* g(){yield 1;yield 2}let x=g();for n in x{break}next(x)'),{'done':False,'value':2})

    def test_cancel_one_effect_does_not_cancel_sibling(self):
        entered=threading.Event();finished=threading.Event()
        def effect(kind,payload,cancel):
            if payload['value']==1:
                entered.set()
                cancel.wait(1)
                finished.set()
                return 100
            entered.wait(1)
            return 20
        value=self.run_code('async fn slow(x){tool("test",x)}let one=spawn(slow(1));let two=spawn(slow(2));cancel(one);await two',tool_handler=lambda *a:None,effect_handler=effect)
        self.assertEqual(value,20)
        self.assertTrue(finished.is_set())

    def test_debug_frames_task_identity(self):
        frames=[]
        def hook(node,env,stack):
            if stack: frames.append([(f['id'],f['taskId'],f['name']) for f in stack])
        self.run_code('fn helper(x){x+1}async fn f(x){helper(x)}await gather([f(1),f(2)])',debug_hook=hook)
        ids={f[1] for stack in frames for f in stack if f[1]}
        self.assertEqual(len(ids),2)
        per_id={}
        for stack in frames:
            for ident,task,name in stack:per_id.setdefault(ident,set()).add(task)
        self.assertTrue(all(len(tasks)==1 for tasks in per_id.values()))

    def test_await_protocol_error_is_catchable(self):
        self.assertEqual(self.run_code('try{await 42}catch e{e.code}'),'argument_error')
        self.assertEqual(self.run_code('var a=null;async fn f(){try{await a}catch e{e.message}}a=f();await a'),'Task 不能等待自身')

    def test_mutual_wait_deadlock_is_catchable(self):
        self.assertEqual(self.run_code('var a=null;var b=null;async fn fa(){await b}async fn fb(){await a}a=fa();b=fb();try{await a}catch e{e.code}'),'task_deadlock')

    def test_host_exception_context_does_not_change_scope_outcome(self):
        seen=[]
        try:raise ResourceLimit('host error')
        except ResourceLimit:
            self.run_code('fn* g(){try{yield 1}finally{tool("cleanup",1)}}let x=g();next(x);0',tool_handler=lambda n,v:seen.append(n))
            with self.assertRaises(LanguageError):self.run_code('async fn f(){throw "failed"}spawn(f());0')
            session=Session(tool_handler=lambda n,v:seen.append(n))
            session.evaluate('fn* g(){try{yield 1}finally{tool("repl",1)}}let x=g();next(x);0')
        self.assertEqual(seen,['cleanup','repl'])

    def test_gather_observes_all_children_failures(self):
        self.assertEqual(self.run_code('async fn bad(n){throw n}try{await gather([bad(1),bad(2)])}catch e{e.value}'),1)

    def test_cleanup_created_tasks_are_scoped(self):
        events=[]
        self.run_code('async fn worker(){tool("work",1)}fn* g(){try{yield 1}finally{spawn(worker())}}let x=g();next(x);0',tool_handler=lambda *a:1,trace_handler=events.append)
        created=[e['taskId'] for e in events if e['event']=='task.created']
        ended=[e['taskId'] for e in events if e['event'] in ('task.completed','task.cancelled','task.failed')]
        self.assertEqual(created,ended)
        self.assertFalse(any(t.name.startswith('chaos-effect') and t.is_alive() for t in threading.enumerate()))

    def test_effect_permissions_and_error_codes(self):
        self.assertEqual(self.run_code('async fn f(){tool("x",1)}try{await f()}catch e{e.code}'),'runtime_error')
        def broken(*args):raise ValueError('unavailable')
        self.assertEqual(self.run_code('async fn f(){tool("x",1)}try{await f()}catch e{e.code}',tool_handler=broken),'tool_error')
        self.assertEqual(self.run_code('agent A {};async fn f(){A(1)}try{await f()}catch e{e.code}',agent_handler=broken),'provider_error')


if __name__=='__main__':unittest.main()
