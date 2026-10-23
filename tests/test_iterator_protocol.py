"""User iteration uses the language scheduler, descriptors and scope ownership."""
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, serializable
from chaos.iteration import Iterator, AsyncIterator
from chaos.session import Session

SYNC = '''class C{var n=0;fn __iter__(self){self}
fn __next__(self){self.n=self.n+1;{done:self.n>2,value:self.n}}}'''
ASYNC = '''class C{var n=0;fn __aiter__(self){self}
async fn __anext__(self){self.n=self.n+1;{done:self.n>2,value:self.n}}}'''


class IteratorProtocolTests(unittest.TestCase):
    def run_code(self, source, bindings=None, **kwargs): return Evaluator(**kwargs).run(source, bindings)

    def test_native_sequence_cursor_and_keywords(self):
        self.assertEqual(self.run_code('let x:Iterator=iter(value="😀x");let y=iter([1,2]);[next(handle=x),next(x),next(x),collect(handle=y),type_of(x)]'),[{'done':False,'value':'😀'},{'done':False,'value':'x'},{'done':True,'value':None},[1,2],'Iterator'])
        self.assertEqual(self.run_code('let xs=[1,2];[collect(xs),collect(xs)]'),[[1,2],[1,2]])
        for source in ['iter({x:1})','aiter([1])','next([1])','class C{}iter(C())']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_long_string_and_shared_dag_for_compatibility(self):
        self.assertEqual(self.run_code('var out=null;for x in input{out=x;break}out',bindings={'input':'x'*15000}), 'x')
        source='fn nest(n){if n==0{[1]}else{let a=nest(n-1);[a,a]}}let value=nest(35);var count=0;for x in [value]{count=count+1;break}count'
        self.assertEqual(self.run_code(source,step_limit=2000),1)

    def test_factory_reuse_and_self_iterator_shared_completion(self):
        self.assertEqual(self.run_code('class Box{fn* __iter__(self){yield 40;yield 42}}let b=Box();[collect(b),collect(b)]'),[[40,42],[40,42]])
        self.assertEqual(self.run_code(SYNC+'let c=C();let a=iter(c);let b=iter(c);[a==b,next(c),next(a),next(b),next(c),c.n]'),[True,{'done':False,'value':1},{'done':False,'value':2},{'done':True,'value':3},{'done':True,'value':3},3])

    def test_factory_runs_each_time_but_next_skips_it(self):
        self.assertEqual(self.run_code('var calls=0;'+SYNC.replace('fn __iter__(self){self}','fn __iter__(self){calls=calls+1;self}')+'let c=C();next(c);let a=iter(c);let b=iter(c);[calls,a==b,next(a)]'),[2,True,{'done':False,'value':2}])
        self.assertEqual(self.run_code('class C{fn __next__(self){{done:true,value:42}}}let c=C();[next(c),try{iter(c)}catch e{e.code}]'),[{'done':True,'value':42},'argument_error'])

    def test_generator_factories_and_handle_identity(self):
        self.assertEqual(self.run_code('class C{fn* __iter__(self){yield 42}async fn* __aiter__(self){yield 41}}let c=C();let s=iter(c);let a=aiter(c);[type_of(s),iter(s)==s,type_of(a),aiter(a)==a,collect(s),await acollect(a)]'),['Generator',True,'AsyncGenerator',True,[42],[41]])
        for source in ['class C{async fn __iter__(self){null}}iter(C())','class C{async fn __aiter__(self){null}}aiter(C())','class C{fn __iter__(self){[1]}}iter(C())']:
            with self.subTest(source=source),self.assertRaises(LanguageError): self.run_code(source)

    def test_async_cold_and_exact_result(self):
        self.assertEqual(self.run_code(ASYNC+'let c=C();let a:AsyncIterator=aiter(value=c);let t=anext(handle=c);let before=c.n;[before,type_of(a),await t,await anext(a),await anext(c),await anext(a),c.n]'),[0,'AsyncIterator',{'done':False,'value':1},{'done':False,'value':2},{'done':True,'value':3},{'done':True,'value':3},3])
        self.assertEqual(self.run_code(ASYNC+'let c=C();let t=acollect(c);let before=c.n;[before,await t]'),[0,[1,2]])

    def test_malformed_step_result_fails_and_caches_terminal(self):
        for expression in ['null','42','{done:1,value:42}','{done:false}','{done:false,value:42,extra:1}']:
            source='class C{var n=0;fn __next__(self){self.n=self.n+1;'+expression+'}}let c=C();let error=try{next(c)}catch e{e.code};[error,next(c),c.n]'
            with self.subTest(expression=expression):self.assertEqual(self.run_code(source),['argument_error',{'done':True,'value':None},1])
        for source in ['class C{fn __anext__(self){{done:false,value:42}}}await anext(C())','class C{async fn __next__(self){{done:false,value:42}}}next(C())','class C{let __next__=42}next(C())']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_step_contract_and_user_error_propagate(self):
        self.assertEqual(self.run_code('class C{fn __next__(self){throw {reason:"read"}}}let c=C();[try{next(c)}catch e{e.value},next(c)]'),[{'reason':'read'},{'done':True,'value':None}])
        with self.assertRaises(LanguageError):self.run_code('class C{fn __next__(self)->Int{{done:true,value:1}}}next(C())')

    def test_special_descriptor_mro_and_dynamic_slots(self):
        source='''class D{fn __get__(self,obj,owner){fn(){{done:true,value:42}}}}class A{let __next__=D()}class B{}class C(A,B){}next(C())'''
        self.assertEqual(self.run_code(source),{'done':True,'value':42})
        self.assertEqual(self.run_code('class C{var __next__=fn(){{done:false,value:1}}}let c=C();c.__next__=fn(){{done:false,value:0}};let a=next(c);C.__next__=fn(){{done:true,value:2}};[a,next(c)]'),[{'done':False,'value':1},{'done':True,'value':2}])
        self.assertEqual(self.run_code('class C{let __next__=property(fn(self){throw "binding"})}try{next(C())}catch e{e.value}'),'binding')

    def test_sync_optional_send_throw_and_completion(self):
        source='''class C{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}fn __send__(self,value){{done:false,value:value}}fn __throw__(self,value){{done:true,value:value}}}
let c=C();let i=iter(c);[send(handle=c,value=null),send(i,42),throw_into(c,{x:43}),next(i),try{throw_into(i,"finished")}catch e{e.value}]'''
        self.assertEqual(self.run_code(source),[{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':{'x':43}},{'done':True,'value':{'x':43}},'finished'])

    def test_missing_send_throw_do_not_advance_or_terminate(self):
        self.assertEqual(self.run_code(SYNC+'let c=C();let err=try{send(c,42)}catch e{e.code};let thrown=try{throw_into(c,43)}catch e{e.value};[err,thrown,next(c)]'),['argument_error',43,{'done':False,'value':1}])

    def test_async_optional_send_throw_close(self):
        source='''var closed=0;class C{fn __aiter__(self){self}async fn __anext__(self){{done:false,value:1}}async fn __asend__(self,value){{done:false,value:value}}async fn __athrow__(self,value){{done:true,value:value}}async fn __aclose__(self){closed=closed+1;null}}
let c=C();let i=aiter(c);let a=await asend(c,null);let b=await asend(i,42);let d=await athrow(c,43);await aclose(c);await aclose(i);[a,b,d,await anext(i),closed]'''
        self.assertEqual(self.run_code(source),[{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':43},{'done':True,'value':43},1])

    def test_loop_break_return_and_error_leave_cursor(self):
        self.assertEqual(self.run_code(SYNC+'let c=C();for x in c{break};[next(c),next(c)]'),[{'done':False,'value':2},{'done':True,'value':3}])
        self.assertEqual(self.run_code(SYNC+'let c=C();fn first(){for x in c{return x}}let x=first();[x,next(c)]'),[1,{'done':False,'value':2}])
        self.assertEqual(self.run_code(ASYNC+'let c=C();try{async for x in c{throw 42}}catch e{null};await anext(c)'),{'done':False,'value':2})

    def test_sync_delegate_bidirectional_and_close(self):
        source='''var closed=0;class C{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}fn __send__(self,x){{done:false,value:x}}fn __throw__(self,x){{done:true,value:x}}fn __close__(self){closed=closed+1;null}}
fn* g(){return yield from C()}let s=g();let a=next(s);let b=send(s,42);let c=throw_into(s,43);let t=g();next(t);close(t);[a,b,c,closed]'''
        self.assertEqual(self.run_code(source),[{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':43},1])

    def test_async_delegate_prefers_async_and_forwards_all(self):
        source='''var closed=0;class C{fn __iter__(self){throw "wrong factory"}fn __aiter__(self){self}async fn __anext__(self){{done:false,value:1}}async fn __asend__(self,x){{done:false,value:x}}async fn __athrow__(self,x){{done:true,value:x}}async fn __aclose__(self){closed=closed+1;null}}
async fn* g(){return yield from C()}let s=g();let a=await anext(s);let b=await asend(s,42);let c=await athrow(s,43);let t=g();await anext(t);await aclose(t);[a,b,c,closed]'''
        self.assertEqual(self.run_code(source),[{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':43},1])
        self.assertEqual(self.run_code(SYNC+'async fn* g(){return yield from C()}await acollect(g())'),[1,2])

    def test_plain_language_error_delegation_not_user_throw(self):
        source='class C{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}fn __throw__(self,x){tool("unexpected",x);{done:true,value:null}}}fn* g(){yield from C()}g()'
        ev=Evaluator(tool_handler=lambda *args:self.fail('ordinary LanguageError became __throw__'))
        # Exercise the interpreter suspension point directly with a typed error.
        ev.suspension.begin()
        from chaos.language import Env,Parser
        scope=Env(ev.env)
        for node in Parser(source).program()[1]: value=ev.resolve(ev.evaluate(node,scope))
        ev.suspension.next(value)
        error=LanguageError('fixture',code='fixture_error')
        try:
            with self.assertRaises(LanguageError) as caught:ev.suspension.drive(ev.suspension._s_resume(value,error=error))
            self.assertIs(caught.exception,error)
        finally:ev.suspension.finish(error)

    def test_normal_finish_closes_created_completed_failed_exactly_once(self):
        seen=[]
        source='''class C{var fail=false;fn __iter__(self){self}fn __next__(self){if self.fail{throw 42}else{{done:true,value:9}}}fn __close__(self){tool("close",self.fail);null}}
let a=iter(C());let b=C();next(b);let c=C();c.fail=true;try{next(c)}catch e{null};let d=C();close(d);close(d);42'''
        self.assertEqual(self.run_code(source,tool_handler=lambda n,v:seen.append(v)),42)
        self.assertEqual(seen,[False,False,False,True])

    def test_cleanup_error_precedence_and_no_retry(self):
        seen=[]
        src='class C{fn __next__(self){{done:true,value:null}}fn __close__(self){tool("closed",1);throw "cleanup"}}let c=C();next(c);'
        with self.assertRaises(LanguageError) as caught:self.run_code(src+'42',tool_handler=lambda n,v:seen.append(v))
        self.assertEqual(caught.exception.value,'cleanup');self.assertEqual(seen,[1])
        with self.assertRaises(LanguageError) as caught:self.run_code(src+'throw "primary"')
        self.assertEqual(caught.exception.value,'primary')
        self.assertEqual(self.run_code(src+'try{close(c)}catch e{null};42',tool_handler=lambda *args:None),42)

    def test_hard_limit_skips_cleanup_and_releases_busy(self):
        seen=[];ev=Evaluator(tool_handler=lambda n,v:seen.append(n),step_limit=160)
        with self.assertRaises(ResourceLimit):ev.run('class C{fn __next__(self){while true{1}}fn __close__(self){tool("cleanup",1);null}}next(C())')
        self.assertEqual(seen,[]);self.assertTrue(all(not x.busy and x.close_attempted for x in ev.suspension.iteration.handles));self.assertEqual(ev.frames,[])

    def test_owner_epoch_and_surviving_instance(self):
        s=Session();s.evaluate(SYNC+'let c=C();let old=iter(c);next(old)')
        with self.assertRaises(LanguageError):s.evaluate('next(old)')
        self.assertEqual(s.evaluate('next(c)'),{'done':False,'value':2})
        foreign=Evaluator().run('iter([1])')
        with self.assertRaises(LanguageError):Evaluator().run('next(input)',{'input':foreign})
        instance=Evaluator().run(SYNC+'C()')
        with self.assertRaises(LanguageError):Evaluator().run('next(input)',{'input':instance})

    def test_inert_serialization_and_host_payload(self):
        seen=[]
        result=self.run_code('let i=iter([42]);tool("snapshot",{i:i});[type_of(i),i]',tool_handler=lambda n,v:seen.append(v))
        self.assertEqual(seen,[{'i':{'$handle':'Iterator','state':'created'}}])
        self.assertEqual(serializable(result),['Iterator',{'$handle':'Iterator','state':'closed'}])
        with self.assertRaises(LanguageError):self.run_code('next(parse_json("{\\"$handle\\":\\"Iterator\\",\\"state\\":\\"created\\"}"))')

    def test_alias_reentrant_binding_and_whole_collect_lock(self):
        self.assertEqual(self.run_code('class D{fn __get__(self,obj,owner){next(obj);fn(){{done:true,value:null}}}}class C{let __next__=D()}try{next(C())}catch e{e.code}'),'argument_error')
        source='''var c=null;class C{var n=0;fn __iter__(self){self}fn __next__(self){self.n=self.n+1;let err=try{next(c)}catch e{e.code};{done:self.n>1,value:err}}}c=C();collect(c)'''
        self.assertEqual(self.run_code(source),['argument_error'])

    def test_async_alias_busy_and_independent_parallel_effects(self):
        barrier=threading.Barrier(2);threads=[]
        def effect(kind,payload,cancel):threads.append(threading.get_ident());barrier.wait(timeout=2);return payload['value']
        source='class C{var x=0;fn __init__(self,x){self.x=x;null}fn __aiter__(self){self}async fn __anext__(self){{done:true,value:tool("read",self.x)}}}await gather([anext(C(41)),anext(C(42))])'
        self.assertEqual(self.run_code(source,tool_handler=lambda *a:None,effect_handler=effect),[{'done':True,'value':41},{'done':True,'value':42}]);self.assertEqual(len(set(threads)),2)
        self.assertEqual(self.run_code(ASYNC+'let c=C();let i=aiter(c);async fn read(v){try{await anext(v)}catch e{e.code}}await gather([read(c),read(i)])'),[{'done':False,'value':1},'argument_error'])

    def test_async_finish_cleanup_and_cancellation_drains(self):
        seen=[];main=threading.get_ident()
        source='class C{fn __aiter__(self){self}async fn __anext__(self){{done:true,value:1}}async fn __aclose__(self){tool("cleanup",1);null}}let i=aiter(C());42'
        self.assertEqual(self.run_code(source,tool_handler=lambda n,v:seen.append(threading.get_ident())),42)
        self.assertEqual(len(seen),1);self.assertNotEqual(seen[0],main)
        started=threading.Event();finished=[];ev=Evaluator(tool_handler=lambda *args:None)
        def effect(kind,payload,cancel):
            started.set();cancel.wait(timeout=2);finished.append(True);return {'done':False,'value':1}
        target=[None]
        def trace(event):
            if event['event']=='task.started' and target[0] is None: target[0]=event['taskId']
        def hook(node,env,frames):
            if started.is_set():
                for task in ev.suspension.tasks:
                    if task.state=='running' and task.id==target[0]:task.cancel_event.set()
        ev.effect_handler=effect;ev.debug_hook=hook;ev.trace_handler=trace
        source='class C{async fn __anext__(self){tool("read",1)}fn __aclose__(self){throw "cleanup forbidden"}}let c=C();spawn(anext(c));async fn other(){var i=0;while i<100{i=i+1}}await other();42'
        self.assertEqual(ev.run(source),42);self.assertEqual(finished,[True]);self.assertTrue(all(not h.busy for h in ev.suspension.iteration.handles));self.assertIsNone(ev.suspension.pool)

    def test_async_collection_lock_and_missing_optional_hooks(self):
        self.assertEqual(self.run_code(ASYNC+'let c=C();async fn probe(){try{await anext(c)}catch e{e.code}}await gather([acollect(c),probe()])'),[[1,2],'argument_error'])
        self.assertEqual(self.run_code(ASYNC+'let c=C();let x=try{await asend(c,42)}catch e{e.code};let y=try{await athrow(c,43)}catch e{e.value};[x,y,await anext(c)]'),['argument_error',43,{'done':False,'value':1}])

    def test_close_contract_failure_is_terminal_and_no_implicit_await(self):
        for declaration,call in [('fn __close__(self){42}','close(c)'),('async fn __close__(self){null}','close(c)'),('fn __aclose__(self){null}','await aclose(c)'),('async fn __aclose__(self){42}','await aclose(c)')]:
            step='async fn __anext__(self){{done:false,value:1}}' if 'aclose' in call else 'fn __next__(self){{done:false,value:1}}'
            source='class C{'+step+declaration+'}let c=C();let failure=try{'+call+'}catch e{e.code};[failure,'+call+']'
            with self.subTest(declaration=declaration):self.assertEqual(self.run_code(source),['argument_error' if declaration.startswith('fn __aclose__') else 'runtime_error',None])

    def test_factory_return_is_validated_once_and_not_recursively_acquired(self):
        self.assertEqual(self.run_code('class Target{fn __iter__(self){throw "recursive"}fn __next__(self){{done:true,value:42}}}class Box{fn __iter__(self){Target()}}next(iter(Box()))'),{'done':True,'value':42})
        self.assertEqual(self.run_code('class Target{fn __aiter__(self){throw "recursive"}async fn __anext__(self){{done:true,value:42}}}class Box{fn __aiter__(self){Target()}}await anext(aiter(Box()))'),{'done':True,'value':42})

    def test_hard_failure_in_generator_cleanup_stops_later_adapter_hooks(self):
        from chaos.suspension import TaskCancelled
        seen=[]
        def effect(name,value):
            seen.append(name)
            if name=='stop':raise TaskCancelled('cleanup cancelled')
        source='class C{fn __iter__(self){self}fn __next__(self){{done:true,value:null}}fn __close__(self){tool("adapter",null);null}}let it=iter(C());fn* g(){try{yield 1}finally{tool("stop",null)}}let gen=g();next(gen);42'
        ev=Evaluator(tool_handler=effect)
        with self.assertRaises(TaskCancelled):ev.run(source)
        self.assertEqual(seen,['stop']);self.assertTrue(all(not h.busy and h.close_attempted for h in ev.suspension.iteration.handles))
        seen.clear()
        with self.assertRaises(LanguageError) as caught:Evaluator(tool_handler=effect).run(source[:-2]+'throw \"primary\"')
        self.assertEqual(caught.exception.value,'primary');self.assertEqual(seen,['stop'])

    def test_limits_and_list_specialized_builtins_unchanged(self):
        with self.assertRaises(ResourceLimit):self.run_code('class C{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}}collect(C())',step_limit=1000000)
        self.assertEqual(self.run_code('map(fn(x){x},iter([1]))'),[1])
        for call in ['sum(iter([1]))','head(iter([1]))','take(1,iter([1]))']:
            with self.subTest(call=call),self.assertRaises(LanguageError):self.run_code(call)
        self.assertEqual(self.run_code('var out=[];for x in "😀x"{out=append(out,x)}out'),['😀','x'])
