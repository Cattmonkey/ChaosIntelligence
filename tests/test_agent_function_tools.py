"""Language closures remain in their evaluator while Agent drivers suspend."""
import threading
import unittest
from chaos import Framework, Registry, ProviderSpec, ToolSpec, ToolCall, Final, Settings
from chaos.language import Evaluator, LanguageError, Native


class AgentFunctionToolsTests(unittest.TestCase):
    def run_program(self, source, *, tools=(), provider=None, capabilities=()):
        registry=Registry()
        registry.register_provider(ProviderSpec('choose',provider or (lambda request,context:
            ToolCall('work',request.input) if not request.history else Final(request.history[-1]['output']))))
        for tool in tools:registry.register_tool(tool)
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('tools',source=source,capabilities=list(capabilities))
            return app.start('tools',1,None,mode='source').result(10)

    def test_public_metadata_copy_and_cold_call(self):
        value=Evaluator().run('''agent a{provider:"local",tools:[]};let tool=agent_tool("work",fn(x){x},"doc","Int","Int");
        let b=agent_bind(value=a,tools=[tool]);let info=agent_tool_info(value=tool);let changed=set(info,"description","changed");
        [agent_tool_info(tool),type_of(b),type_of(b(1)),agent_binding_info(value=b)["agent"].name]''')
        self.assertEqual(value,[{'name':'work','description':'doc','inputType':'Int','outputType':'Int','capabilities':[]},'Function','Task','a'])

    def test_contracts_accept_only_eight_json_types(self):
        for kind in ('Any','Number','Int','String','Bool','List','Record','Null'):
            self.assertEqual(Evaluator().run('agent_tool_info(agent_tool("work",fn(x){x},input_type=input)).inputType',{'input':kind}),kind)
        for kind in ('Function','Task','Generator','AgentTool','Unknown'):
            with self.subTest(kind=kind),self.assertRaises(LanguageError):
                Evaluator().run('agent_tool("work",fn(x){x},output_type=input)',{'input':kind})

    def test_invalid_descriptor_contracts_have_no_effects(self):
        cases=['agent_tool("bad name",fn(x){x})','agent_tool("x",42)',
               'agent_tool("x",fn(x){x},capabilities=null)', 'agent_tool("x",fn(x){x},capabilities=["x","x"])',
               'agent_tool("x",fn(x){x},description=42)']
        for source in cases:
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)

    def test_duplicate_collision_and_capability_subset_rejected(self):
        prefix='agent a{tools:["host"]};'
        for expression in ['agent_bind(a,[agent_tool("host",fn(x){x})])',
                           'agent_bind(a,[agent_tool("x",fn(x){x}),agent_tool("x",fn(x){x})])',
                           'agent_bind(a,[agent_tool("x",fn(x){x},capabilities=["other"])])']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):Evaluator().run(prefix+expression)

    def test_binding_public_definition_and_opaque_identity(self):
        with self.assertRaises(LanguageError):Evaluator().run('agent a{apiKey:"private"};agent_bind(a,[])')
        self.assertEqual(Evaluator().run('agent a{};let f=fn(x){x};let t=agent_tool("work",f);[t==t,t==agent_tool("work",f),agent_bind(a,[t])==agent_bind(a,[t])]'),[True,False,False])
        for operation in ('agent_info(b)','agent_source(b)','b.handler','t.handler'):
            with self.subTest(operation=operation),self.assertRaises(LanguageError):
                Evaluator().run('agent a{};let t=agent_tool("work",fn(x){x});let b=agent_bind(a,[t]);'+operation)

    def test_cross_evaluator_descriptors_are_rejected(self):
        first=Evaluator();tool=first.run('agent_tool("work",fn(x){x})')
        with self.assertRaises(LanguageError):Evaluator().run('agent_tool_info(input)',{'input':tool})

    def test_sync_async_class_and_bound_agent_handlers(self):
        handlers=['fn work(x){x+1}', 'async fn work(x){await sleep(0);x+1}',
                  'class Work{fn __call__(self,x){x+1}};let work=Work()']
        for handler in handlers:
            with self.subTest(handler=handler):
                result=self.run_program(handler+';agent a{provider:"choose"};await agent_bind(a,[agent_tool("work",work)])(41)')
                self.assertEqual(result,42)

    def test_handler_task_is_awaited_exactly_once(self):
        source='''async fn inner(){42}async fn work(x){inner()};agent a{provider:"choose"};
        try{await agent_bind(a,[agent_tool("work",work)])(0)}catch e{e.code}'''
        self.assertEqual(self.run_program(source),'agent_tool_error')

    def test_closure_mutation_and_lexical_cleanup_complete_before_model_continues(self):
        source='''var values=[];fn work(x){with exit_stack() as s{s.callback(fn(){values=append(values,"closed")});values=append(values,x);x}}
        agent a{provider:"choose"};let b=agent_bind(a,[agent_tool("work",work)]);[await b(21),values]'''
        self.assertEqual(self.run_program(source),[21,[21,'closed']])

    def test_input_contract_precedes_callback_and_output_contract_precedes_model(self):
        source='''var entered=false;fn work(x){entered=true;"bad"};agent a{provider:"choose"};
        let b=agent_bind(a,[agent_tool("work",work,input_type="Int",output_type="Int")]);
        let result=try{await b("bad")}catch e{e.code};[entered,result]'''
        self.assertEqual(self.run_program(source),[False,'runtime_error'])
        source=source.replace('b("bad")','b(1)')
        self.assertEqual(self.run_program(source),[True,'runtime_error'])

    def test_non_json_returns_never_turn_into_handle_descriptions(self):
        for expression in ('fn(y){y}','exit_stack()','[fn(y){y}]'):
            with self.subTest(expression=expression):
                source='agent a{provider:"choose"};try{await agent_bind(a,[agent_tool("work",fn(x){'+expression+'})])(1)}catch e{e.code}'
                self.assertEqual(self.run_program(source),'agent_tool_error')

    def test_four_parallel_agents_release_all_workers_before_tool_effects(self):
        models=threading.Barrier(4);effects=threading.Barrier(4)
        def provider(request,context):
            if request.history:return Final(request.history[-1]['output'])
            models.wait(3);return ToolCall('work',request.input)
        def host(value,context):effects.wait(3);return value*2
        source='''agent a{provider:"choose",tools:["echo"]};let b=agent_bind(a,[agent_tool("work",fn(x){tool("echo",x)},capabilities=["echo"])]);
        await gather([b(1),b(2),b(3),b(4)])'''
        self.assertEqual(self.run_program(source,provider=provider,tools=[ToolSpec('echo',host)],capabilities=['echo']),[2,4,6,8])

    def test_deferred_task_generator_stack_and_iterator_keep_empty_authority(self):
        prefixes=[('async fn later(){tool("secret",1)}','later()','await saved'),
                  ('fn* later(){yield tool("secret",1)}','later()','next(saved)'),
                  ('fn later(){let s=exit_stack();s.callback(fn(){tool("secret",1)});s}','later()','saved.close()'),
                  ('class Cursor{fn __iter__(self){self}fn __next__(self){return {done:false,value:tool("secret",1)}}}','iter(Cursor())','next(saved)')]
        for definition,create,consume in prefixes:
            calls=[]
            source=definition+';var saved=null;fn work(x){saved='+create+';0};agent a{provider:"choose",tools:["secret"]};await agent_bind(a,[agent_tool("work",work)])(0);try{'+consume+'}catch e{e.code}'
            with self.subTest(consume=consume):
                self.run_program(source,tools=[ToolSpec('secret',lambda value,context:calls.append(value))],capabilities=['secret'])
                self.assertEqual(calls,[])

    def test_borrowed_broader_task_is_not_started_or_cancelled(self):
        source='''async fn outside(){42};let borrowed=outside();fn work(x){borrowed};agent a{provider:"choose"};
        let result=try{await agent_bind(a,[agent_tool("work",work)])(0)}catch e{e.code};
        [result,task_info(borrowed).state,task_info(borrowed).cancellationRequested,await borrowed]'''
        self.assertEqual(self.run_program(source),['capability_error','created',False,42])

    def test_empty_binding_is_valid_and_local_invocation_is_explicitly_rejected(self):
        self.assertEqual(Evaluator().run('agent a{};agent_binding_info(agent_bind(a,[])).tools'),[])
        result=self.run_program('agent a{provider:"local"};try{await agent_bind(a,[])(0)}catch e{e.code}')
        self.assertEqual(result,'provider_error')

    def test_native_batch_contract_failure_precedes_every_tool(self):
        from chaos.harness import NativeHarness, HarnessError
        from tests.test_harness_runtime import FakeClient, call, message, CONFIG
        client=FakeClient([message(calls=[call(args='{"input":1}'),call(args='{"input":"wrong"}',ident='b')])])
        harness=NativeHarness(CONFIG,client_factory=lambda config:client);effects=[]
        with self.assertRaises(HarnessError):
            harness.run('',None,[{'name':'work','wrapped':True,'inputType':'Int'}],lambda *args:effects.append(args))
        self.assertEqual(effects,[]);self.assertTrue(client.closed)

    def test_native_client_close_preserves_hard_error_identity(self):
        from chaos.harness import NativeHarness
        from chaos.language import ResourceLimit
        from tests.test_harness_runtime import FakeClient, call, message, CONFIG
        original=ResourceLimit('original')
        client=FakeClient([message(calls=[call()])])
        def close():raise ValueError('incidental cleanup')
        client.close=close
        def invoke(*args):raise original
        with self.assertRaises(ResourceLimit) as caught:
            NativeHarness(CONFIG,client_factory=lambda config:client).run('',None,[{'name':'work'}],invoke)
        self.assertIs(caught.exception,original)

    def test_native_late_resource_limit_outranks_task_local_cancel(self):
        from chaos.harness import NativeHarness
        from chaos.language import ResourceLimit
        from chaos.suspension import TaskCancelled
        from tests.test_harness_runtime import FakeClient, call, message, CONFIG
        original=TaskCancelled('cancel',_cancel_event=threading.Event());late=ResourceLimit('late')
        client=FakeClient([message(calls=[call()])])
        def close():raise late
        client.close=close
        def invoke(*args):raise original
        with self.assertRaises(ResourceLimit) as caught:
            NativeHarness(CONFIG,client_factory=lambda config:client).run('',None,[{'name':'work'}],invoke)
        self.assertIs(caught.exception,late)

    def test_transferred_stack_and_new_acquisitions_retain_captured_authority(self):
        for operation in ('saved.pop_all().close()', 'saved.enter_context(Gate())'):
            calls=[]
            source='''class Gate{fn __enter__(self){tool("secret",1)}fn __exit__(self,error){null}}
            var saved=null;fn work(x){saved=exit_stack();saved.callback(fn(){tool("secret",1)});0}
            agent a{provider:"choose",tools:["secret"]};await agent_bind(a,[agent_tool("work",work)])(0);
            try{'''+operation+'''}catch e{e.code}'''
            with self.subTest(operation=operation):
                try:self.run_program(source,tools=[ToolSpec('secret',lambda value,context:calls.append(value))],capabilities=['secret'])
                except Exception:pass  # Remaining registered callbacks also fail closed at the epoch barrier.
                self.assertEqual(calls,[])

    def test_group_cancellation_closes_native_driver_before_continuation(self):
        from unittest.mock import patch
        import time
        from tests.test_harness_runtime import FakeClient,call,message
        for awaiting in (False,True):
            with self.subTest(awaiting=awaiting):
                entered=threading.Event();ended=threading.Event();client=FakeClient([message(calls=[call('tool_1','{"input":0}')])])
                registry=Registry()
                def block(value,context):
                    entered.set()
                    try:
                        while True:context.checkpoint();time.sleep(.002)
                    finally:ended.set()
                def fail(value,context):
                    if not entered.wait(3):raise RuntimeError('callback was not entered')
                    raise LanguageError('sibling failure')
                registry.register_tool(ToolSpec('block',block));registry.register_tool(ToolSpec('fail',fail))
                registry.register_tool(ToolSpec('inspect',lambda value,context:[ended.is_set(),client.closed]))
                settings=Settings(':memory:');settings.update({'providers':{'text':{'apiKey':'test'}},'roles':{'executor':{'model':'test'}}},1)
                body='group.spawn(bad());await bound(0)' if awaiting else 'group.spawn(bound(0));group.spawn(bad())'
                source='''agent a{provider:"model",tools:["block"]};let bound=agent_bind(a,[agent_tool("work",fn(x){tool("block",x)},capabilities=["block"])]);
                async fn bad(){tool("fail",null)};try{async with task_group() as group{'''+body+'''}}catch e{null};tool("inspect",null)'''
                with patch.object(settings,'client',return_value=client),Framework(registry=registry,settings=settings) as app:
                    app.create_project('group',source=source,capabilities=['block','fail','inspect'])
                    self.assertEqual(app.start('group',1,None,mode='source').result(6),[True,True])

    def test_cancelled_start_response_closes_undelivered_invocation(self):
        import time
        from unittest.mock import patch
        from chaos.runtime import Context
        from tests.test_harness_runtime import FakeClient,call,message
        handed=threading.Event();client=FakeClient([message(calls=[call(args='{"input":0}')])])
        original=Context.event
        def event(context,kind,payload):
            original(context,kind,payload)
            if kind=='agent.language_tool':
                handed.set();deadline=time.monotonic()+3
                while not context.cancel.is_set() and time.monotonic()<deadline:time.sleep(.002)
                self.assertTrue(context.cancel.is_set())
        def fail(value,context):
            self.assertTrue(handed.wait(3));raise LanguageError('handoff failure')
        registry=Registry();registry.register_tool(ToolSpec('fail',fail))
        registry.register_tool(ToolSpec('inspect',lambda value,context:client.closed))
        settings=Settings(':memory:');settings.update({'providers':{'text':{'apiKey':'test'}},'roles':{'executor':{'model':'test'}}},1)
        source='''var hits=0;agent a{provider:"model"};let bound=agent_bind(a,[agent_tool("work",fn(x){hits=hits+1;x})]);
        async fn bad(){tool("fail",null)};try{async with task_group() as group{group.spawn(bad());await bound(0)}}catch e{null};[tool("inspect",null),hits]'''
        with patch.object(Context,'event',event),patch.object(settings,'client',return_value=client),Framework(registry=registry,settings=settings) as app:
            app.create_project('handoff',source=source,capabilities=['fail','inspect'])
            self.assertEqual(app.start('handoff',1,None,mode='source').result(6),[True,0])

    def test_native_bookkeeping_close_reports_its_own_resource_limit(self):
        from chaos.harness import NativeHarness
        from chaos.language import ResourceLimit
        from tests.test_harness_runtime import FakeClient,call,message,CONFIG
        client=FakeClient([message(calls=[call()])]);late=ResourceLimit('native close')
        def close():raise late
        client.close=close
        driver=NativeHarness(CONFIG,client_factory=lambda config:client).actions('',None,[{'name':'work'}])
        self.assertEqual(next(driver),{'name':'work','input':{}})
        with self.assertRaises(ResourceLimit) as caught:driver.close()
        self.assertIs(caught.exception,late)
