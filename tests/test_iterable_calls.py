"""Synchronous iterable composition shares the cooperative iterator scheduler."""
import threading
import unittest

from chaos.iteration import IterationRequest
from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.session import Session
from chaos.suspension import TaskCancelled


CURSOR = '''class Cursor{var n=0;fn __iter__(self){self}
fn __next__(self){self.n=self.n+1;{done:self.n>3,value:self.n}}}'''
SOURCES = {
    'list': '[1,2,3]',
    'generator': '(fn*(){yield 1;yield 2;yield 3})()',
    'iterator': 'iter([1,2,3])',
    'user': 'Cursor()',
}


class IterableCalls(unittest.TestCase):
    def run_code(self, source, **kwargs):
        return Evaluator(**kwargs).run(source)

    def test_consumers_share_source_protocol(self):
        # A named factory also verifies __iter__ returning a generator handle.
        prefix = CURSOR + 'class Factory{fn* __iter__(self){yield 1;yield 2;yield 3}}'
        sources = dict(SOURCES, factory='Factory()')
        for label, source in sources.items():
            cases = [
                ('map(fn(x){x*2},VALUE)', [2,4,6]),
                ('filter(fn(x){x>1},VALUE)', [2,3]),
                ('fold(fn(a,x){a+x},10,VALUE)', 16),
                ('any(fn(x){x==2},VALUE)', True),
                ('all(fn(x){x<4},VALUE)', True),
                ('apply(fn(*xs){xs},VALUE)', [1,2,3]),
                ('(fn(*xs){xs})(*VALUE)', [1,2,3]),
            ]
            for expression, expected in cases:
                with self.subTest(source=label, expression=expression):
                    self.assertEqual(self.run_code(prefix + expression.replace('VALUE',source)), expected)

    def test_string_unicode_and_existing_parameter_names(self):
        self.assertEqual(self.run_code('map(fn=upper,xs="a😀b")'), ['A','😀','B'])
        self.assertEqual(self.run_code('filter(fn=fn(x){x!="😀"},xs="a😀b")'), ['a','b'])
        self.assertEqual(self.run_code('fold(fn=fn(a,x){a+x},initial="",items="a😀b")'), 'a😀b')
        self.assertEqual(self.run_code('any(f=fn(x){x=="😀"},xs="a😀b")'), True)
        self.assertEqual(self.run_code('all(f=fn(x){length(x)==1},xs="a😀b")'), True)
        self.assertEqual(self.run_code('apply(fn=fn(*xs,**kw){[xs,kw]},args="a😀b",kwargs={key:42})'), [['a','😀','b'],{'key':42}])
        self.assertEqual(self.run_code('(fn(*xs){xs})(*"a😀b")'), ['a','😀','b'])

    def test_empty_input_never_invokes_invalid_callback(self):
        for source in ['[]','""','iter([])','(fn*(){return 42})()']:
            with self.subTest(source=source):
                self.assertEqual(self.run_code('let xs='+source+';[map(42,xs),filter(42,xs),fold(42,7,xs),any(42,xs),all(42,xs)]'), [[],[],7,False,True])
        self.assertEqual(self.run_code('fn f(*xs,**kw){[xs,kw]}apply(f,iter([]),{x:1})'), [[],{'x':1}])

    def test_async_and_record_sources_are_rejected_without_running(self):
        prefix = '''async fn work(){tool("unexpected",null);[1]}
async fn* stream(){tool("unexpected",null);yield 1}
class AsyncSource{fn __aiter__(self){self}async fn __anext__(self){{done:true,value:null}}}
class AsyncFactory{async fn __iter__(self){tool("unexpected",null);iter([])}}'''
        calls = ['map(fn(x){x},VALUE)','filter(fn(x){true},VALUE)',
                 'fold(fn(a,x){a},0,VALUE)','any(fn(x){true},VALUE)',
                 'all(fn(x){true},VALUE)','apply(fn(*xs){xs},VALUE)',
                 '(fn(*xs){xs})(*VALUE)']
        for source in ['{x:1}','work()','stream()','aiter(AsyncSource())','AsyncSource()','AsyncFactory()','channel()']:
            for call in calls:
                seen = []
                with self.subTest(source=source, call=call), self.assertRaises(LanguageError):
                    self.run_code(prefix+call.replace('VALUE',source),tool_handler=lambda *args:seen.append(args))
                self.assertEqual(seen, [])

    def test_map_keeps_async_callbacks_cold_and_predicates_require_bool(self):
        source = '''var started=0;async fn work(x){started=started+1;x*2}
fn* values(){yield 1;yield 2}let tasks=map(work,values());let before=started;
[before,map(task_info,tasks),await gather(tasks),started]'''
        result = self.run_code(source)
        self.assertEqual(result[0],0)
        self.assertEqual([item['state'] for item in result[1]],['created','created'])
        self.assertEqual(result[2:],[[2,4],2])
        for call in ['filter(work,it)','any(work,it)','all(work,it)']:
            source='var started=0;async fn work(x){started=started+1;true}let it=iter([1,2]);let error=try{'+call+'}catch e{e.code};[error,started,next(it)]'
            with self.subTest(call=call):
                self.assertEqual(self.run_code(source),['runtime_error',0,{'done':False,'value':2}])

    def test_short_circuit_does_not_prefetch_or_close(self):
        source = '''var closed=0;var read=[];class C{var n=0;fn __iter__(self){self}
fn __next__(self){self.n=self.n+1;read=append(read,self.n);{done:false,value:self.n}}
fn __close__(self){closed=closed+1;null}}
let c=C();let it=iter(c);let yes=any(fn(x){x==2},it);let a=next(c);
let no=all(fn(x){x<5},c);let b=next(it);[yes,a,no,b,read,closed]'''
        self.assertEqual(self.run_code(source),[True,{'done':False,'value':3},False,{'done':False,'value':6},[1,2,3,4,5,6],0])
        self.assertEqual(self.run_code('var closed=0;fn* g(){try{yield 1;yield 2}finally{closed=closed+1}}let it=g();let first=any(fn(x){true},it);[first,closed,next(it)]'),[True,0,{'done':False,'value':2}])

    def test_callback_failure_commits_item_and_releases_cursor(self):
        for consumer in ['map(callback,it)','filter(callback,it)','fold(fn(a,x){callback(x)},0,it)','any(callback,it)','all(callback,it)']:
            # Choose the nondecisive predicate result so failure is reached.
            truth = 'true' if consumer.startswith('all') else 'false'
            source=CURSOR+'let it=iter(Cursor());fn callback(x){if x==2{throw "callback"}else{'+truth+'}}let failure=try{'+consumer+'}catch e{e.value};[failure,next(it)]'
            with self.subTest(consumer=consumer):
                self.assertEqual(self.run_code(source),['callback',{'done':False,'value':3}])

    def test_alias_cursor_is_locked_for_callbacks_and_released_afterward(self):
        source=CURSOR+'''let raw=Cursor();let it=iter(raw);
let errors=map(fn(x){[try{next(raw)}catch e{e.code},try{close(it)}catch e{e.code},try{map(fn(y){y},it)}catch e{e.code}]},it);
[errors,next(raw)]'''
        self.assertEqual(self.run_code(source),[[['argument_error']*3]*3,{'done':True,'value':4}])
        self.assertEqual(self.run_code('fn* g(){yield 1;yield 2}let it=g();let values=map(fn(x){try{next(it)}catch e{e.code}},it);[values,next(it)]'),[['argument_error','argument_error'],{'done':True,'value':None}])

    def test_spread_and_apply_effect_order_and_partial_consumption(self):
        seen=[]
        source='''class C{var n=0;fn __iter__(self){tool("iter",null);self}
fn __next__(self){self.n=self.n+1;tool("next",self.n);{done:self.n>1,value:self.n}}}
fn target(*xs,**kw){tool("body",null);[xs,kw]}
fn callee(){tool("callee",null);target}
callee()(tool("arg",0),*C(),tool("after",3),key=tool("keyword",4))'''
        self.assertEqual(self.run_code(source,tool_handler=lambda name,value:(seen.append((name,value)),value)[1]),[[0,1,3],{'key':4}])
        self.assertEqual(seen,[('callee',None),('arg',0),('iter',None),('next',1),('next',2),('after',3),('keyword',4),('body',None)])
        self.assertEqual(self.run_code('let it=iter([1,2]);fn f(a){a}let error=try{apply(f,it)}catch e{e.code};[error,next(it)]'),['argument_error',{'done':True,'value':None}])
        seen=[]
        source='fn* g(){yield tool("yield",1);throw "source"}fn f(*xs){tool("body",null)}try{f(*g(),tool("later",2))}catch e{e.value}'
        self.assertEqual(self.run_code(source,tool_handler=lambda name,value:(seen.append(name),value)[1]),'source')
        self.assertEqual(seen,['yield'])

    def test_callbacks_and_generator_steps_never_reenter_host_invoke(self):
        ev=Evaluator()
        def forbidden(*args,**kwargs):self.fail('consumer reentered Evaluator.invoke')
        ev.invoke=forbidden
        self.assertEqual(ev.run('fn* g(){yield 1;yield 2}let xs=map(fn(x){x+1},g());[filter(fn(x){true},xs),fold(fn(a,x){a+x},0,iter(xs)),any(fn(x){true},g()),all(fn(x){true},g()),apply(fn(*x){x},g()),(fn(*x){x})(*g())]'),[[2,3],5,True,True,[1,2],[1,2]])

    def test_distinct_consumers_cooperate_on_effects(self):
        for expression in ['map(fn(x){tool("callback",x)},iter([n]))','map(fn(x){x},(fn*(){yield tool("source",n)})())']:
            barrier=threading.Barrier(2);threads=set()
            def effect(kind,payload,cancel):
                threads.add(threading.get_ident());barrier.wait(timeout=3);return payload['value']
            source='async fn consume(n){'+expression+'}await gather([consume(1),consume(2)])'
            with self.subTest(expression=expression):
                self.assertEqual(self.run_code(source,tool_handler=lambda *args:None,effect_handler=effect),[[1],[2]])
                self.assertEqual(len(threads),2)

    def test_shared_cursor_stays_locked_while_callback_effect_is_pending(self):
        release=threading.Event()
        def effect(kind,payload,cancel):
            if payload['name']=='callback':
                if not release.wait(3):raise AssertionError('concurrent probe did not run')
            else:release.set()
            return payload['value']
        source='''let it=iter([1]);async fn consume(){map(fn(x){tool("callback",x)},it)}
async fn probe(){let error=try{next(it)}catch e{e.code};tool("release",null);error}
[await gather([consume(),probe()]),next(it)]'''
        self.assertEqual(self.run_code(source,tool_handler=lambda *args:None,effect_handler=effect),[[[1],'argument_error'],{'done':True,'value':None}])

    def test_finite_consumption_limit_counts_discarded_items_and_one_probe(self):
        for operation,callback in [('filter','false'),('any','false'),('all','true'),('map','x'),('fold','x')]:
            seen={'source':0,'callback':0}
            def effect(name,value):seen[name]+=1;return value
            args='fn(a,x){tool("callback",x);x},0' if operation=='fold' else 'fn(x){tool("callback",x);'+callback+'}'
            source='fn* values(){var i=0;while true{i=i+1;tool("source",i);yield i}}'+operation+'('+args+',values())'
            ev=Evaluator(step_limit=1000000,tool_handler=effect)
            with self.subTest(operation=operation),self.assertRaises(ResourceLimit):ev.run(source)
            self.assertEqual(seen,{'source':10001,'callback':10000})
            self.assertTrue(all(not handle.busy for handle in ev.suspension.generators))

    def test_exact_limit_and_aggregate_call_budget(self):
        self.assertEqual(self.run_code('fn* values(){for x in range(10000){yield x}}length(map(fn(x){x},values()))',step_limit=1000000),10000)
        self.assertEqual(self.run_code('fn* values(){for x in range(10000){yield x}}apply(fn(*xs){length(xs)},values())',step_limit=1000000),10000)
        for expression,expected in [
            ('(fn(*xs,**kw){0})(*values(),extra=1)',10001),
            ('(fn(*xs){0})(1,*values())',10000),
            ('apply(fn(*xs,**kw){0},values(),{extra:1})',10000),
        ]:
            seen=[]
            source='fn* values(){var i=0;while true{i=i+1;tool("item",i);yield i}}'+expression
            with self.subTest(expression=expression),self.assertRaises(ResourceLimit):
                self.run_code(source,step_limit=1000000,tool_handler=lambda name,value:seen.append(value))
            self.assertEqual(len(seen),expected)

    def test_native_consumption_does_not_exhaust_persistent_iterator_slots(self):
        self.assertEqual(self.run_code('length(map(fn(x){map(fn(y){y},[])},range(10000)))',step_limit=1000000,timeout=30),10000)
        for expression in ['map(str,[])', 'map(str,"")', 'sum([])']:
            with self.subTest(expression=expression):
                source='var n=0;while n<10001{'+expression+';n=n+1}n'
                self.assertEqual(self.run_code(source,step_limit=1000000,timeout=30),10001)
        for expression in ['apply(loop,[n-1])','loop(*[n-1])']:
            with self.subTest(expression=expression):
                source='fn loop(n){if n==0{42}else{'+expression+'}}loop(10001)'
                self.assertEqual(self.run_code(source,step_limit=1000000,timeout=30),42)
        for expression in ['iter([])','collect([])']:
            with self.subTest(persistent=expression),self.assertRaises(ResourceLimit):
                self.run_code('var n=0;while n<10001{'+expression+';n=n+1}n',step_limit=1000000,timeout=30)

    def test_stale_foreign_and_recording_scope_handles_rejected(self):
        calls=['map(fn(x){x},old)','filter(fn(x){true},old)','fold(fn(a,x){a},0,old)',
               'any(fn(x){true},old)','all(fn(x){true},old)','apply(fn(*xs){xs},old)','(fn(*xs){xs})(*old)']
        foreign=Evaluator().run('iter([1])')
        for call in calls:
            with self.subTest(call=call,kind='foreign'),self.assertRaises(LanguageError):
                Evaluator().run(call,{'old':foreign})
            session=Session();session.evaluate('let old=iter([1])')
            with self.subTest(call=call,kind='stale'),self.assertRaises(LanguageError):session.evaluate(call)
        ev=Evaluator();scheduler=ev.suspension;scheduler.begin()
        try:
            handle=scheduler.drive(scheduler.iteration.acquire([1]))
            handle.recording_scope='another-recording'
            with self.assertRaises(LanguageError) as caught:
                scheduler.drive(scheduler.iteration.resolve(IterationRequest('map',handle,None)))
            self.assertEqual(caught.exception.code,'mode_error')
            self.assertFalse(handle.busy);self.assertEqual(handle.index,0)
        finally:scheduler.finish()

    def test_cancellation_drains_pending_effect_and_releases_borrow(self):
        for expression in ['map(fn(x){tool("read",x)},iter([1]))',
                           'apply(fn(*xs){xs},(fn*(){yield tool("read",1)})())']:
            started=threading.Event();finished=threading.Event();seen=[]
            ev=Evaluator(tool_handler=lambda *args:None)
            def effect(kind,payload,cancel):
                seen.append(payload['name']);started.set();cancel.wait(3);finished.set();return 1
            ev.effect_handler=effect
            def cancel():
                if started.wait(3):
                    for task in ev.suspension.tasks:
                        if task.state=='running':
                            task.cancel_event.set();return
            worker=threading.Thread(target=cancel);worker.start()
            try:
                with self.subTest(expression=expression),self.assertRaises(TaskCancelled):
                    ev.run('async fn consume(){'+expression+'}await consume()')
            finally:worker.join(4)
            self.assertTrue(finished.is_set());self.assertEqual(seen,['read']);self.assertIsNone(ev.suspension.pool)
            self.assertTrue(all(not h.busy for h in ev.suspension.iteration.handles+ev.suspension.generators))
            self.assertEqual(ev.frames,[])


if __name__ == '__main__':
    unittest.main()
