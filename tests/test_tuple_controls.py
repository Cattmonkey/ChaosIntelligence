"""Tuple scopes, cooperative controls and nested live-value boundaries."""
import threading
import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, serializable
from chaos.tuple_values import TupleValue
from chaos.channels import create, send, validate_payload
from chaos.suspension import Await, TaskCancelled


class TupleControlTests(unittest.TestCase):
    def test_resource_success_failure_cleanup_and_native_scope_exit(self):
        for prefix,enter,leave in [('with','fn __enter__','fn __exit__'),('async with','async fn __aenter__','async fn __aexit__')]:
            source='''var seen=[];class C{ENTER(s){(20,22)}LEAVE(s,error){seen=append(seen,if error==null{null}else{error.code});false}}
              let value=PREFIX C() as (a,b){a+b};let error=try{PREFIX C() as (a,){0}}catch e{e.code};[value,error,seen]'''.replace('ENTER',enter).replace('LEAVE',leave).replace('PREFIX',prefix)
            with self.subTest(prefix=prefix):self.assertEqual(Evaluator().run(source),[42,'match_error',[None,'match_error']])
        source='let g=task_group();try{async with g as (){42}}catch e{null};task_group_info(g).state'
        self.assertEqual(Evaluator().run(source),'closed')
        self.assertEqual(Evaluator().run('async fn work(){let s=cancel_shield();try{with s as (){42}}catch e{null};cancel_shield_info(s).state};await work()'),'closed')

    def test_sync_async_generator_close_during_assignment_does_not_commit(self):
        for flavor,next_call,close_call in [('fn*','next(g)','close(g)'),('async fn*','await anext(g)','await aclose(g)')]:
            source='var a:Int=1;var b:Int=2;var closed=0;'+flavor+' work(){try{(a,b)=yield ((20,22))}finally{closed+=1}};let g=work();'+next_call+';'+close_call+';[a,b,closed]'
            with self.subTest(flavor=flavor):self.assertEqual(Evaluator().run(source),[1,2,1])

    def test_async_rhs_other_tasks_cannot_observe_torn_commit(self):
        observed=[];threads=set()
        def debug(node,env,frames):
            threads.add(threading.get_ident())
            try:observed.append([env.get('a'),env.get('b')])
            except LanguageError:pass
        source='''var a:Int=1;var b:Int=2;let ready=channel();let release=channel();
          async fn update(){(a,b)=do{await channel_send(ready,());await channel_receive(release);(20,22)};a+b}
          async fn other(){await channel_receive(ready);(a,b)=(9,10);await channel_send(release,());a+b}
          let result=await gather([update(),other()]);[result,a,b]'''
        result=Evaluator(debug_hook=debug).run(source)
        self.assertEqual(result[-2:],[20,22]);self.assertTrue(all(pair in ([1,2],[9,10],[20,22])for pair in observed))
        self.assertEqual(threads,{threading.get_ident()})

    def test_unhandled_control_in_tuple_rhs_leaves_cells_and_skips_handlers(self):
        for failure in (ExecutionControl('host'),ResourceLimit('budget'),KeyboardInterrupt(),SystemExit(7)):
            cells=[];seen=[]
            def debug(node,env,frames):
                if node[0]=='assign_pattern':cells[:]=[env.resolve_cell('a'),env.resolve_cell('b')]
            def effect(kind,payload,cancel):
                seen.append(payload['name'])
                if payload['name']=='stop':raise failure
            ev=Evaluator(debug_hook=debug,tool_handler=lambda *a:None,effect_handler=effect)
            with self.subTest(failure=type(failure).__name__),self.assertRaises(type(failure)) as caught:
                ev.run('var a:Int=1;var b:Int=2;async fn go(){try{(a,b)=(20,tool("stop",null))}catch e{tool("caught",null)}};await go()')
            self.assertIs(caught.exception,failure);self.assertEqual([c.value for c in cells],[1,2]);self.assertEqual(seen,['stop'])
            self.assertFalse(ev.suspension.active);self.assertIsNone(ev.suspension.pool)

    def test_borrowed_iterator_overflow_probe_and_cancellation_keep_control(self):
        ev=Evaluator(step_limit=500000);sources=[]
        def debug(node,env,frames):
            try:sources[:]=[env.get('raw')]
            except LanguageError:pass
        ev.debug_hook=debug
        source='class Source{var n=0;fn __iter__(s){s}fn __next__(s){s.n+=1;{done:false,value:s.n}}};let raw=Source();tuple(raw)'
        with self.assertRaises(ResourceLimit):ev.run(source)
        self.assertEqual(sources[0].fields['n'].value,10001)
        self.assertFalse(ev.suspension.active)
        source='var target=null;async fn go(){class Source{fn __iter__(s){s}fn __next__(s){cancel(target);{done:false,value:1}}};tuple(Source())};target=go();await target'
        with self.assertRaises(TaskCancelled):Evaluator().run(source)

    def test_foreign_nested_callables_objects_and_tasks_rejected_at_boundaries(self):
        foreign=[Evaluator().run('fn(){42}'),Evaluator().run('class C{};C()'),Evaluator().run('async fn f(){42};f()')]
        for child in foreign:
            value=TupleValue([{'nested':TupleValue([child])}])
            for source in ['input[0]','class C{fn __getitem__(s,k){42}};C()[input]','class C{fn __iter__(s){s}fn __next__(s){{done:true,value:input}}};tuple(C())','tool("save",input)','json_stringify(input)']:
                calls=[]
                with self.subTest(child=type(child).__name__,source=source),self.assertRaises(LanguageError) as caught:
                    Evaluator(tool_handler=lambda *a:calls.append(a)).run(source,{'input':value})
                self.assertEqual(caught.exception.code,'runtime_error' if type(child).__name__=='Task' else 'ownership_error');self.assertEqual(calls,[])

    def test_channel_cold_copy_preserves_function_and_copies_children(self):
        ev=Evaluator();s=ev.suspension;s.begin()
        def stub():yield
        try:
            c=create(ev,1);live=s.task(stub());child={'list':[1]};value=TupleValue([child,live]);operation=send(ev,c,value)
            child['list'].append(2)
            def wait():return (yield Await(operation))
            s.drive(wait());stored=c.buffer[0].value
            self.assertIsInstance(stored,TupleValue);self.assertEqual(stored[0],{'list':[1]});self.assertIs(stored[1],live)
            self.assertEqual(s.channel_slots,1)
        finally:s.finish()
        self.assertEqual((s.channel_slots,s.channel_nodes,s.channel_chars),(0,0,0))

    def test_channel_foreign_and_recording_scope_rejected_before_reservation(self):
        ev=Evaluator();s=ev.suspension;s.begin()
        foreign=Evaluator().run('fn(){42}')
        def stub():yield
        try:
            c=create(ev);live=s.task(stub())
            for value in [TupleValue([foreign]),TupleValue([{'x':TupleValue([foreign])}])]:
                before=len(s.tasks)
                with self.assertRaises(LanguageError) as caught:send(ev,c,value)
                self.assertEqual(caught.exception.code,'ownership_error');self.assertEqual((len(s.tasks),s.channel_slots),(before,0))
            live.recording_scope='another'
            with self.assertRaises(LanguageError) as caught:send(ev,c,TupleValue([live]))
            self.assertEqual(caught.exception.code,'mode_error');self.assertEqual(s.channel_slots,0)
        finally:s.finish()

    def test_channel_revalidates_tuple_live_children_when_started(self):
        ev=Evaluator();s=ev.suspension;s.begin()
        def stub():yield
        try:
            c=create(ev,1);live=s.task(stub());operation=send(ev,c,TupleValue([live]));self.assertEqual(s.channel_slots,1)
            live.recording_scope='changed'
            def wait():return (yield Await(operation))
            with self.assertRaises(LanguageError) as caught:s.drive(wait())
            self.assertEqual(caught.exception.code,'mode_error');self.assertEqual((s.channel_slots,len(c.buffer)),(0,0))
        finally:s.finish()

    def test_tuple_constructor_can_suspend_host_work_without_moving_ast_threads(self):
        main=threading.get_ident();asts=set();workers=set()
        def effect(kind,payload,cancel):workers.add(threading.get_ident());return payload['value']
        source='class Source{fn* __iter__(s){yield tool("one",20);yield tool("two",22)}};async fn run(){tuple(Source())};await run()'
        ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect,debug_hook=lambda *a:asts.add(threading.get_ident()))
        self.assertEqual(serializable(ev.run(source)),{'$tuple':[20,22]});self.assertEqual(asts,{main});self.assertTrue(workers);self.assertNotIn(main,workers)

    def test_ordinary_handlers_receive_inert_tuple_callable_and_task_descriptors(self):
        seen=[]
        def handler(name,payload):seen.append(payload);return payload
        source='fn callback(){42};async fn work(){tool("unexpected",null)};let task=work();tool("save",(callback,{task:task}));task_info(task).state'
        self.assertEqual(Evaluator(tool_handler=handler).run(source),'created')
        self.assertEqual(seen,[{'$tuple':[{'$callable':'callback'},{'task':{'$handle':'Task','state':'created'}}]}])
        projected=[]
        def agent(name,config,value):projected.append(value);return value
        result=Evaluator(agent_handler=agent).run('agent worker{};worker((20,22))')
        self.assertEqual(result,{'$tuple':[20,22]});self.assertEqual(projected,[result])

    def test_generators_send_and_async_tasks_preserve_native_tuple(self):
        source='fn* values(){let (a,b)=yield ((1,2));return (a+b,)};let g=values();let before=next(g);let result=send(g,(20,22));[before,result]'
        self.assertEqual(serializable(Evaluator().run(source)),[{'done':False,'value':{'$tuple':[1,2]}},{'done':True,'value':{'$tuple':[42]}}])
        source='async fn work(){await sleep(0);(fn(x){x*2},21)};let (f,value)=await work();f(value)'
        self.assertEqual(Evaluator().run(source),42)

    def test_scoped_cancellation_finishes_whole_tuple_write_before_delivery(self):
        seen=[]
        source='''var a:Int=1;var b:Int=2;var target=null;
          async fn work(){with cancel_shield() as shield{
          (a,b)=do{cancel(target);tool("rhs",null);(20,22)};tool("inside",(a,b))};tool("outside",null)};
          target=work();await target'''
        with self.assertRaises(TaskCancelled):Evaluator(tool_handler=lambda n,v:seen.append((n,v))).run(source)
        self.assertEqual(seen,[('rhs',None),('inside',{'$tuple':[20,22]})])
