import unittest
from chaos.language import Evaluator,ResourceLimit
class DynamicScheduling(unittest.TestCase):
    def value(self,s):return Evaluator(step_limit=250000,timeout=4).run(s)
    def test_creation_snapshot_and_siblings(self):
        self.assertEqual(self.value('let v=context_var("x",0);async fn f(n){let old=v.get();v.set(n);await sleep(0);[old,v.get()]}v.set(1);let a=f(3);v.set(2);let b=f(4);v.set(9);[await gather([a,b]),v.get()]'),[[[1,3],[2,4]],9])
    def test_foreign_task_token_rejected(self):
        self.assertEqual(self.value('let v=context_var("x",0);let t=v.set(2);async fn f(){try{v.reset(t)}catch e{e.code}}let e=await f();v.reset(t);[e,v.get()]'),['context_token_error',0])
    def test_async_run_selected_context_and_child_fork(self):
        self.assertEqual(self.value('let v=context_var("x",0);let c=context();async fn child(){v.set(99);v.get()}async fn f(){v.set(1);await sleep(0);let n=await child();[n,v.get()]}[c.run(f),c.get(v),v.get()]'),[[99,1],1,0])
    def test_cold_return_not_awaited(self):
        self.assertEqual(self.value('let v=context_var("x",0);let c=context();async fn f(){v.get()}let t=c.run(fn(){v.set(42);f()});[type_of(t),await t,v.get()]'),['Task',42,0])
    def test_generator_run_returns_cold(self):
        self.assertEqual(self.value('let v=context_var("x",0);let c=context();c.run(fn(){v.set(42)});let g=c.run(fn*(){yield v.get()});[type_of(g),next(g).value,v.get()]'),['Generator',42,0])
    def test_generator_scope_retained_and_close(self):
        self.assertEqual(self.value('let v=context_var("x",0);fn* g(){with context_bind([{variable:v,value:7}]) as s{yield v.get();yield v.get()}}v.set(1);let s=g();v.set(2);let a=next(s);v.set(3);let b=next(s);close(s);[a.value,b.value,v.get()]'),[7,7,3])
    def test_async_generator_await_and_scope(self):
        self.assertEqual(self.value('let v=context_var("x",0);async fn* g(){with context_bind([{variable:v,value:7}]) as s{await sleep(0);yield v.get()}}let s=g();let a=await anext(s);await aclose(s);[a.value,v.get()]'),[7,0])
    def test_scope_restores_selected_only(self):
        self.assertEqual(self.value('let a=context_var("a",0);let b=context_var("b",0);let n=with context_bind([{variable:a,value:1}]) as s{a.set(2);b.set(3);a.get()};[n,a.get(),b.get()]'),[2,0,3])
    def test_scope_transactional_validation(self):
        self.assertEqual(self.value('let v=context_var("x",0);try{context_bind([{variable:v,value:1},{variable:42,value:2}])}catch e{null};v.get()'),0)
    def test_scope_return_throw_and_restart(self):
        self.assertEqual(self.value('let v=context_var("x",0);fn f(){with context_bind([{variable:v,value:1}]) as s{return v.get()}}let a=f();let b=restart_case(fn(){with context_bind([{variable:v,value:2}]) as s{invoke_restart("r")}},[{name:"r",function:fn(){v.get()}}]);[a,b,v.get()]'),[1,0,0])
    def test_exit_stack_foreign_and_lifo_preflight(self):
        self.assertEqual(self.value('let v=context_var("x",0);let s=exit_stack();s.enter_context(context_bind([{variable:v,value:2}]));async fn f(){try{s.close()}catch e{e.code}}let a=await f();let b=with context_bind([]) as c{try{s.close()}catch e{e.code}};let n=v.get();s.close();[a,b,n,v.get()]'),['ownership_error','context_error',2,0])
    def test_async_stack_transparent(self):
        self.assertEqual(self.value('let v=context_var("x",0);let s=exit_stack(true);s.enter_context(context_bind([{variable:v,value:2}]));await s.aclose();v.get()'),0)
    def test_fresh_async_protocol_transparent(self):
        self.assertEqual(self.value('let v=context_var("x",0);class C{async fn __aenter__(self){v.set(1);await sleep(0);v.get()}async fn __aexit__(self,e){v.set(2);await sleep(0);null}}let n=async with C() as x{v.get()};[n,v.get()]'),[1,2])
    def test_taskgroup_lexical_body(self):
        self.assertEqual(self.value('let v=context_var("x",0);async fn f(){v.set(9);v.get()}let n=async with task_group() as g{v.set(1);await g.spawn(f());v.get()};[n,v.get()]'),[1,1])
    def test_hard_exit_bookkeeping(self):
        e=Evaluator(step_limit=100)
        with self.assertRaises(ResourceLimit):e.run('let v=context_var("x",0);with context_bind([{variable:v,value:2}]) as s{while true{null}}')
        self.assertEqual(e.suspension.context_values,[])
