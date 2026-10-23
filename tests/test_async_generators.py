import threading
import time
import unittest

from chaos.language import Evaluator, Parser, LanguageError, ResourceLimit, ExecutionControl
from chaos.session import Session
from chaos.suspension import AsyncGenerator


class StreamProtocolTests(unittest.TestCase):
    def run_code(self, source, **kwargs):
        return Evaluator(**kwargs).run(source)

    def test_sync_send_expression_and_completion(self):
        self.assertEqual(self.run_code('fn* g(){let x=yield 1;yield x;return 9}let s=g();[next(s),send(s,42),next(s),send(s,4)]'),
                         [{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':9},{'done':True,'value':9}])

    def test_sync_initial_send_does_not_start(self):
        self.assertEqual(self.run_code('var n=0;fn* g(){n=1;yield 2}let s=g();let err=try{send(s,3)}catch e{e.code};[n,err,next(s)]'),[0,'argument_error',{'done':False,'value':2}])

    def test_sync_throw_and_closed_throw(self):
        self.assertEqual(self.run_code('fn* g(){try{yield 1}catch e{yield e.value};return 9}let s=g();next(s);let r=throw_into(s,{answer:42});close(s);[r,try{throw_into(s,"closed")}catch e{e.value}]'),[{'done':False,'value':{'answer':42}},'closed'])

    def test_sync_bidirectional_delegation(self):
        self.assertEqual(self.run_code('fn* inner(){try{let v=yield 1;yield v}catch e{yield e.value};return 9}fn* outer(){return yield from inner()}let s=outer();[next(s),send(s,42),throw_into(s,43),next(s)]'),[{'done':False,'value':1},{'done':False,'value':42},{'done':False,'value':43},{'done':True,'value':9}])

    def test_list_delegation_rejects_send_and_propagates_throw(self):
        self.assertEqual(self.run_code('fn* g(){try{yield from [1,2]}catch e{yield e.value}}let a=g();next(a);let first=throw_into(a,42);fn* plain(){yield from [1,2]}let b=plain();next(b);[first,try{send(b,2)}catch e{e.message}]'),[{'done':False,'value':42},'List/String 委托只接受 null'])

    def test_async_cold_and_initial_send(self):
        self.assertEqual(self.run_code('var n=0;async fn* g(){n=1;let x=yield 2;yield x;return 9}let s:AsyncGenerator=g();let t:Task=anext(s);let before=n;let err=try{await asend(s,1)}catch e{e.code};[before,n,err,type_of(s),await t,await asend(s,42),await anext(s)]'),[0,0,'argument_error','AsyncGenerator',{'done':False,'value':2},{'done':False,'value':42},{'done':True,'value':9}])

    def test_async_throw_and_completion_contract(self):
        self.assertEqual(self.run_code('async fn* g()->Int{try{yield 1}catch e{yield e.value};return 7}let s=g();[await anext(s),await athrow(s,42),await anext(s)]'),[{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':7}])
        with self.assertRaises(LanguageError):self.run_code('async fn* g()->Int{yield 1;return "bad"}await acollect(g())')

    def test_sync_async_type_boundaries(self):
        for source in ['async fn* g(){yield 1}next(g())','fn* g(){yield 1}anext(g())','async fn* g(){yield 1}let x:Generator=g()','async fn* g(){yield 1}for x in g(){x}','async for x in [1]{x}','async fn* g(){yield 1}fn* outer(){yield from g()}collect(outer())']:
            with self.subTest(source=source), self.assertRaises(LanguageError):self.run_code(source)
        with self.assertRaises(LanguageError):Parser('fn f(){async for x in g(){x}}').program()

    def test_async_for_break_continue_and_resume(self):
        self.assertEqual(self.run_code('async fn* g(){yield 1;yield 2;yield 3;yield 4}let s=g();var total=0;async for x in s{if x==1{continue}else{null};total=total+x;if x==2{break}else{null}};[total,await anext(s)]'),[2,{'done':False,'value':3}])

    def test_top_level_async_for_uses_effect_worker(self):
        threads=[];main=threading.get_ident()
        def tool(name,value):threads.append(threading.get_ident());return value
        self.assertEqual(self.run_code('async fn* g(){yield tool("read",42)}var total=0;async for x in g(){total=total+x};total',tool_handler=tool),42)
        self.assertEqual(len(threads),1);self.assertNotEqual(threads[0],main)

    def test_async_delegate_both_types_send_throw_close(self):
        for flavor in ['fn*','async fn*']:
            source='var seen=[];'+flavor+' inner(){try{let x=yield 1;yield x}catch e{yield e.value}finally{seen=append(seen,"inner")};return 9}async fn* outer(){try{return yield from inner()}finally{seen=append(seen,"outer")}}let s=outer();let values=[await anext(s),await asend(s,42),await athrow(s,43)];await aclose(s);[values,seen]'
            with self.subTest(flavor=flavor):self.assertEqual(self.run_code(source),[[{'done':False,'value':1},{'done':False,'value':42},{'done':False,'value':43}],['inner','outer']])

    def test_async_cleanup_can_await(self):
        self.assertEqual(self.run_code('var seen=[];async fn mark(){seen=append(seen,2)}async fn* g(){try{yield 1}finally{await mark()}}let s=g();await anext(s);await aclose(s);seen'),[2])

    def test_scope_finalization_awaits_cleanup_effects(self):
        seen=[]
        value=self.run_code('async fn cleanup(){tool("close",42)}async fn* g(){try{yield 1}finally{await cleanup()}}let s=g();await anext(s);7',tool_handler=lambda name,value:seen.append(value))
        self.assertEqual(value,7);self.assertEqual(seen,[42])

    def test_close_unstarted_and_close_yield(self):
        self.assertEqual(self.run_code('var n=0;async fn* g(){n=1;yield 2}let s=g();await aclose(s);[n,await anext(s)]'),[0,{'done':True,'value':None}])
        self.assertEqual(self.run_code('async fn* g(){try{yield 1}finally{yield 2}}let s=g();await anext(s);let err=try{await aclose(s)}catch e{e.message};[err,await anext(s)]'),['关闭生成器期间不能 yield',{'done':True,'value':None}])

    def test_parallel_stream_effects_single_ast_thread(self):
        barrier=threading.Barrier(2);ast=set();workers=set();main=threading.get_ident()
        def effect(name,value):workers.add(threading.get_ident());barrier.wait(timeout=2);return value
        result=self.run_code('async fn* g(x){yield tool("read",x)}await gather([anext(g(41)),anext(g(42))])',tool_handler=effect,debug_hook=lambda *args:ast.add(threading.get_ident()))
        self.assertEqual(result,[{'done':False,'value':41},{'done':False,'value':42}]);self.assertEqual(ast,{main});self.assertEqual(len(workers),2);self.assertNotIn(main,workers)

    def test_overlapping_consumers_fail_without_corrupting_stream(self):
        def tool(name,value):time.sleep(.02);return value
        source='async fn* g(){yield tool("read",42);yield 43}let s=g();async fn attempt(){try{await anext(s)}catch e{e.code}}let results=await gather([attempt(),attempt()]);[results,await anext(s)]'
        self.assertEqual(self.run_code(source,tool_handler=tool),[[{'done':False,'value':42},'argument_error'],{'done':False,'value':43}])

    def test_acollect_holds_consumer_lock(self):
        def tool(name,value):time.sleep(.01);return value
        self.assertEqual(self.run_code('async fn* g(){yield tool("read",1);yield 2}let s=g();async fn other(){try{await anext(s)}catch e{e.code}}await gather([acollect(s),other()])',tool_handler=tool),[[1,2],'argument_error'])

    def test_cancel_operation_isolated_and_worker_drained(self):
        started=threading.Event();finished=[];ev=Evaluator()
        def effect(kind,payload,cancel):
            started.set()
            if payload['value']==1:
                cancel.wait(timeout=2);finished.append(1);return 1
            return 42
        def hook(node,env,frames):
            if started.is_set():
                for task in ev.suspension.tasks:
                    if task.state=='running' and task.id==target[0]:task.cancel_event.set()
        target=[None]
        def trace(item):
            if item['event']=='task.started' and target[0] is None:target[0]=item['taskId']
        ev.effect_handler=effect;ev.tool_handler=lambda *args:None;ev.debug_hook=hook;ev.trace_handler=trace
        # A separate normal task yields checkpoints to deliver cancellation after
        # the first stream starts, while the sibling still completes normally.
        source='var cleaned=0;async fn* g(){try{yield tool("read",1)}finally{cleaned=1}}let s=g();let t=spawn(anext(s));async fn other(){var i=0;while i<100{i=i+1};tool("read",2)}let result=await other();[result,cleaned]'
        self.assertEqual(ev.run(source),[42,0]);self.assertEqual(finished,[1]);self.assertFalse(ev.suspension.active)

    def test_cancel_sync_stream_inside_async_native_callback_unwinds(self):
        started=threading.Event();release=threading.Event();finished=[];ev=Evaluator()
        def effect(*args):
            started.set();release.wait(timeout=2);finished.append(True);return 1
        def cancel():
            if started.wait(timeout=2):
                for task in ev.suspension.tasks:task.cancel_event.set()
                # Keep the host effect pending through the scheduler idle check.
                time.sleep(.04);release.set()
        worker=threading.Thread(target=cancel);worker.start()
        ev.effect_handler=effect;ev.tool_handler=lambda *args:None
        try:
            self.assertEqual(ev.run('fn* g(){try{yield tool("read",1)}finally{null}}let s=g();async fn f(){next(s)}spawn(f());42'),42)
        finally:release.set();worker.join(timeout=3)
        self.assertEqual(finished,[True]);self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)
        self.assertTrue(all(not handle.busy for handle in ev.suspension.generators))

    def test_host_base_exceptions_unwind_nested_stream_drivers(self):
        sources=['fn* g(){yield tool("read",1)}let s=g();async fn f(){next(s)}await f()',
                 'async fn* g(){yield tool("read",1)}await anext(g())']
        for interrupt in (SystemExit,KeyboardInterrupt):
            for source in sources:
                ev=Evaluator(tool_handler=lambda *args:None)
                def effect(*args):raise interrupt('host fixture')
                ev.effect_handler=effect
                with self.subTest(interrupt=interrupt.__name__,source=source),self.assertRaises(interrupt):
                    ev.run(source)
                self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0)
                self.assertFalse(ev.suspension.active);self.assertIsNone(ev.suspension.pool)
                self.assertTrue(all(not handle.busy and handle.runner.depth==0 for handle in ev.suspension.generators))
                self.assertFalse(any(thread.name.startswith('chaos-effect') for thread in threading.enumerate()))

    def test_handles_do_not_escape_epoch(self):
        session=Session();session.evaluate('async fn* g(){yield 1}let s=g();let t=anext(s)')
        for source in ['await anext(s)','await t']:
            with self.assertRaises(LanguageError):session.evaluate(source)
        self.assertEqual(session.evaluate('await acollect(g())'),[1])

    def test_resource_limit_skips_stream_cleanup(self):
        seen=[]
        with self.assertRaises(ResourceLimit):self.run_code('async fn* g(){try{yield 1;while true{1}}finally{tool("cleanup",1)}}let s=g();await anext(s);await anext(s)',step_limit=100,tool_handler=lambda *args:seen.append(1))
        self.assertEqual(seen,[])

    def test_resumed_debug_frames_have_current_operation_task(self):
        ids=[]
        def hook(node,env,frames):
            if node[0]=='yield' and frames:ids.append(frames[-1]['taskId'])
        self.assertEqual(self.run_code('async fn* g(){let x=yield 1;yield x}let s=g();await anext(s);await asend(s,42)',debug_hook=hook),{'done':False,'value':42})
        self.assertEqual(len(set(ids)),2);self.assertTrue(all(ids))

    def test_illegal_cleanup_yield_does_not_corrupt_caller_frames(self):
        for flavor, first, closing in [('fn*','next(s)','close(s)'),('async fn*','await anext(s)','await aclose(s)')]:
            ev=Evaluator()
            source=flavor+' g(){try{yield 1}finally{yield 2}}let s=g();'+first+';try{'+closing+'}catch e{null};fn f(x){x+1}f(41)'
            self.assertEqual(ev.run(source),42)
            self.assertEqual(ev.depth,0);self.assertEqual(ev.frames,[])

    def test_only_static_single_level_macro_target(self):
        self.assertEqual(Parser('L.m!(42)').program()[1][0][1], 'L.m')
        for source in ['L.M.m!(42)','L["m"]!(42)','f().m!(42)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()


if __name__ == '__main__':unittest.main()
