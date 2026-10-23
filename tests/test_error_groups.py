"""Native exception trees retain topology, authority and scheduler ownership."""
import threading
import unittest
from chaos import Framework, Registry, Settings, ProviderSpec, Final
from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, Native, Parser, serializable, TupleValue, TailCall
from chaos.errors import GroupedError, leaves
from chaos.error_values import Error, capture
from chaos.suspension import TaskCancelled

TREE='error_group("root",[error_group("left",[error(1),error(2)]),error(3)])'

class ErrorGroups(unittest.TestCase):
    def run_code(self, source, **kwargs): return Evaluator(**kwargs).run(source)

    def test_native_error_and_legacy_catch_contract(self):
        self.assertEqual(self.run_code('let e=error(42);[type_of(e),e.code,e.message,e.value,length(e.exceptions),current_error(),try{throw e}catch caught{keys(caught)}]'),
            ['Error','user_throw','程序抛出错误',42,0,None,['code','message','value','location']])

    def test_scoped_rethrow_preserves_tree_and_identity_for_unchanged_payloads(self):
        ev=Evaluator()
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        source='fn work(){try{try{throw '+TREE+'}catch* {value:1,.._} as g{throw g}}catch e{e.value}};[work(),limited(work)]'
        direct,scoped=ev.run(source)
        self.assertEqual(direct,scoped)

    def test_multiple_authority_projections_keep_rethrow_topology(self):
        ev=Evaluator()
        for name,caps in [('one',{'a','b'}),('two',{'a'})]:
            ev.env.bind(name,Native(name,lambda f:TailCall(f,[]),ev,capabilities=frozenset(caps)))
        self.assertEqual(ev.run('fn f(){0};try{try{throw error_group("orig",[error(f),error(2)])}catch* {value:x,.._} if type_of(x)=="Function" as g{one(fn(){try{throw g}catch caught{two(fn(){throw current_error()})}})}}catch e{e.message}'),'orig')

    def test_guard_bare_rethrow_does_not_duplicate_leaf(self):
        for expression in ('throw','throw current_error()'):
            with self.subTest(expression=expression),self.assertRaises(GroupedError) as caught:
                self.run_code('try{throw '+TREE+'}catch* _ if do{'+expression+'} as g{0}')
            self.assertEqual(caught.exception.message,'root')
            self.assertEqual([leaf.value for _,leaf in leaves(caught.exception)],[1,2,3])

    def test_scoped_error_payload_remains_restricted_after_rethrow(self):
        seen=[];ev=Evaluator(tool_handler=lambda *args:seen.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        source='fn secret(){tool("secret",0)};let original=error_group("root",[error(secret),error(2)]);fn work(){try{throw original}catch* {value:f,.._} if type_of(f)=="Function" as g{throw g}};try{limited(work)}catch* _ as g{try{g.exceptions[0].value()}catch e{e.code}}'
        self.assertEqual(ev.run(source),'capability_error');self.assertEqual(seen,[])

    def test_member_projection_restricts_nested_callback(self):
        seen=[];ev=Evaluator(tool_handler=lambda *args:seen.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('fn secret(){tool("secret",0)};let source=error({f:secret});let f=limited(fn(){source.value.f});try{f()}catch e{e.code}'),'capability_error')
        self.assertEqual(seen,[])

    def test_group_value_contains_actual_immutable_errors(self):
        value=self.run_code('let e=error_group("batch",[error(42)]);[type_of(e.exceptions),type_of(e.exceptions[0]),e.exceptions[0].value]')
        self.assertEqual(value,['Tuple','Error',42])
        self.assertEqual(self.run_code('let e=error(42);try{setattr(e,"value",0)}catch c{c.code}'),'attribute_error')

    def test_nested_partition_preserves_shape_and_location(self):
        value=self.run_code('var out=[];try{throw '+TREE+'}catch* {value:n,.._} if n!=2 as g{out=append(out,[g.message,g.exceptions[0].message,g.exceptions[0].exceptions[0].value,g.exceptions[1].value])}catch* _ as g{out=append(out,[g.message,g.exceptions[0].message,g.exceptions[0].exceptions[0].value])};out')
        self.assertEqual(value,[['root','left',1,3],['root','left',2]])

    def test_rethrow_original_occurrences_reconstructs_original_identity(self):
        ev=Evaluator();original=None
        def save():
            nonlocal original
            original=ev.handled_error
        ev.env.bind('save',Native('save',save,ev))
        with self.assertRaises(GroupedError) as caught:
            ev.run('try{throw '+TREE+'}catch e{save();try{throw}catch* {value:1,.._} as group{throw}catch* {value:3,.._} as group{throw}}')
        self.assertIs(caught.exception,original)

    def test_duplicate_leaf_occurrences_are_not_deduplicated(self):
        value=self.run_code('let e=error(7);let tree=error_group("root",[e,error_group("inner",[e])]);var n=0;try{throw tree}catch* _ if do{n=n+1;n==1} as g{null}catch* _ as g{[n,g.exceptions[0].message,g.exceptions[0].exceptions[0].value]}')
        self.assertEqual(value,[2,'inner',7])

    def test_unmatched_error_is_same_original(self):
        ev=Evaluator();value=ev.run('error_group("x",[error(7)])')
        ev.env.bind('original',value)
        with self.assertRaises(GroupedError) as caught:ev.run('try{throw original}catch* {value:9,.._} as g{0}')
        self.assertIs(caught.exception,value.exception)

    def test_explicit_subgroup_throw_reconstructs_tree(self):
        with self.assertRaises(GroupedError) as caught:self.run_code('try{throw '+TREE+'}catch* {value:1,.._} as g{throw g}')
        self.assertEqual([leaf.value for _,leaf in leaves(caught.exception)],[1,2,3])
        self.assertEqual([e.message for e in caught.exception.exceptions],['left','程序抛出错误'])

    def test_new_handler_failures_are_not_selected_by_later_cases(self):
        with self.assertRaises(GroupedError) as caught:self.run_code('try{throw '+TREE+'}catch* {value:1,.._} as g{throw 10}catch* {value:2,.._} as g{throw 20}catch* {value:10,.._} as g{0}')
        self.assertEqual([leaf.value for _,leaf in leaves(caught.exception)],[10,20,3])
        self.assertEqual(caught.exception.exceptions[-1].message,'root')

    def test_guard_failure_keeps_whole_uncommitted_partition(self):
        with self.assertRaises(GroupedError) as caught:self.run_code('try{throw '+TREE+'}catch* {value:n,.._} if do{if n==2{throw 99}else{true}} as g{0}catch* _ as rest{0}')
        self.assertEqual([leaf.value for _,leaf in leaves(caught.exception)],[99,1,2,3])
        self.assertEqual(caught.exception.exceptions[-1].message,'root')

    def test_guard_current_error_is_leaf_and_handler_current_error_is_group(self):
        self.assertEqual(self.run_code('try{throw error(42)}catch* _ if current_error().value==42 as g{[current_error()==g,current_error().code,current_error().exceptions[0].value]}'),[True,'exception_group',42])
        self.assertIsNone(self.run_code('try{throw 1}catch* _ as g{0};current_error()'))

    def test_result_else_and_finally(self):
        self.assertEqual(self.run_code('var seen=[];let value=try{throw error_group("batch",[error(1),error(2)])}catch* {value:1,.._} as g{20}catch* _ as g{42}else{0}finally{seen=[7]};[value,seen]'),[42,[7]])
        self.assertEqual(self.run_code('try{41}catch* _ as g{0}else{42}'),42)

    def test_async_handlers_keep_context_and_do_not_tail_escape(self):
        source='''async fn f(n){try{try{throw error_group("batch",[error(n),error(n+1)])}catch* {value:x,.._} if x==n as g{await sleep(0);throw}}catch* _ as g{await sleep(0);map(fn(e){e.value},g.exceptions)}};await gather([f(1),f(10)])'''
        self.assertEqual(self.run_code(source),[[1,2],[10,11]])

    def test_generator_handler_can_suspend_without_losing_remainder(self):
        self.assertEqual(self.run_code('fn* f(){try{throw error_group("batch",[error(1),error(2)])}catch* {value:1,.._} as g{yield g.exceptions[0].value}catch* _ as g{yield g.exceptions[0].value}};collect(f())'),[1,2])

    def test_escaping_controls_rejected_before_effects(self):
        invalid=['return 0','break','continue','fn nested(x=do{return 0}){x}']
        for body in invalid:
            calls=[]
            with self.subTest(body=body),self.assertRaises(LanguageError):
                self.run_code('fn f(){while true{tool("mark",0);try{throw 1}catch* _ as g{'+body+'}}};f()',tool_handler=lambda *args:calls.append(args))
            self.assertEqual(calls,[])
        with self.assertRaises(LanguageError):Parser('fn f(){try{0}catch* _ if do{return true} as g{0}}').program()

    def test_nested_function_returns_and_local_loop_control_are_allowed(self):
        self.assertEqual(self.run_code('try{throw 1}catch* _ as g{fn f(){return 42};for n in [1,2]{if n==1{continue}else{break}};f()}'),42)

    def test_mixed_handlers_and_forged_diagnostics_are_rejected(self):
        for source in ['try{0}catch e{0}catch* _ as g{0}','try{0}catch* _ as g{0}catch e{0}']:
            with self.assertRaises(LanguageError):Parser(source).program()
        self.assertEqual(self.run_code('try{error_group("x",[{"$error":{value:42}}])}catch e{e.code}'),'ownership_error')

    def test_bounded_construction_and_no_control_wrapping(self):
        for expression in ['error_group("x",[])','error_group("",[error(0)])','error_group(1,[error(0)])','error_group("x",error(0))']:
            self.assertEqual(self.run_code('try{'+expression+'}catch e{e.code}'),'argument_error')
        with self.assertRaises(ResourceLimit):self.run_code('var e=error(0);for n in range(34){e=error_group("x",[e])};e')
        for failure in (ResourceLimit('budget'),ExecutionControl('stop'),TaskCancelled('cancel')):
            with self.subTest(failure=failure),self.assertRaises(LanguageError):capture(Evaluator(),failure)

    def test_foreign_ownership_in_containers_class_and_throw(self):
        foreign=Evaluator().run('error(42)')
        for source in ['foreign.value','throw foreign','error_group("x",[foreign])','new_class("C",members={problem:foreign})','getitem([foreign],0)','tool("sink",[foreign])']:
            ev=Evaluator(tool_handler=lambda *args:0);ev.env.bind('foreign',foreign)
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:ev.run(source)
            self.assertEqual(caught.exception.code,'ownership_error')

    def test_serialization_contains_bounded_inert_tree(self):
        value=self.run_code(TREE);record=serializable(value)
        self.assertEqual(record['$error']['code'],'exception_group')
        self.assertEqual(record['$error']['exceptions'][0]['$error']['exceptions'][1]['$error']['value'],2)
        self.assertNotIn('owner',str(record));self.assertNotIn('traceback',str(record))

    def test_task_group_policy_default_and_singleton_group(self):
        self.assertEqual(self.run_code('try{async with task_group() as g{throw 42}}catch e{[e.code,e.value]}'),['user_throw',42])
        self.assertEqual(self.run_code('try{async with task_group(errors="group") as g{throw 42}}catch* _ as g{[g.code,g.exceptions[0].value]}'),['exception_group',42])
        for args in ['"group"','errors="unknown"','errors=null']:
            with self.assertRaises(LanguageError):self.run_code('task_group('+args+')')

    def test_parallel_failures_drain_and_deduplicate_await_forwarding(self):
        barrier=threading.Barrier(2);ended=[];lock=threading.Lock()
        def effect(kind,payload,cancel):
            barrier.wait(3)
            with lock:ended.append(payload['value'])
            raise LanguageError('failure',value=payload['value'])
        source='''async fn f(n){tool("fail",n)};try{async with task_group(errors="group") as g{let a=g.spawn(f(1));g.spawn(f(2));await a}}catch* _ as g{sort(map(fn(e){e.value},g.exceptions))}'''
        self.assertEqual(self.run_code(source,tool_handler=lambda *a:None,effect_handler=effect),[1,2])
        self.assertEqual(sorted(ended),[1,2])

    def test_nested_task_group_keeps_subgroup(self):
        self.assertEqual(self.run_code('async fn f(){async with task_group(errors="group") as inner{throw 42}};try{async with task_group(errors="group") as outer{await outer.spawn(f())}}catch* _ as g{[g.exceptions[0].code,g.exceptions[0].exceptions[0].value]}'),['exception_group',42])

    def test_internal_cancel_is_not_a_leaf(self):
        self.assertEqual(self.run_code('async fn busy(){while true{1}};async fn bad(){throw 42};try{async with task_group(errors="group") as g{g.spawn(busy());g.spawn(bad());while true{1}}}catch* _ as g{map(fn(e){e.value},g.exceptions)}'),[42])

    def test_hard_controls_bypass_group_handlers_and_cleanup(self):
        for failure in (ResourceLimit('limit'),ExecutionControl('host'),TaskCancelled('foreign'),KeyboardInterrupt(),SystemExit(7)):
            seen=[]
            def effect(kind,payload,cancel):
                if payload['name']=='stop':raise failure
                seen.append(payload['name'])
            with self.subTest(kind=type(failure).__name__),self.assertRaises(type(failure)) as caught:
                self.run_code('try{async with task_group(errors="group") as g{tool("stop",0)}}catch* _ as g{tool("caught",0)}finally{tool("cleanup",0)}',tool_handler=lambda *a:None,effect_handler=effect)
            self.assertIs(caught.exception,failure);self.assertEqual(seen,[])

    def test_late_hard_failure_overrides_collected_ordinary_errors(self):
        entered=threading.Event();hard=ResourceLimit('late budget')
        def effect(kind,payload,cancel):
            if payload['name']=='late':entered.set();self.assertTrue(cancel.wait(3));raise hard
            self.assertTrue(entered.wait(3));raise LanguageError('ordinary')
        with self.assertRaises(ResourceLimit) as caught:self.run_code('async fn bad(){tool("bad",0)};try{async with task_group(errors="group") as g{g.spawn(bad());tool("late",0)}}catch* _ as g{0}',tool_handler=lambda *a:None,effect_handler=effect)
        self.assertIs(caught.exception,hard)

    def test_actual_agent_functions_report_both_provider_failures(self):
        barrier=threading.Barrier(2);registry=Registry()
        def provider(request,context):barrier.wait(3);raise ValueError('provider-'+str(request.input))
        registry.register_provider(ProviderSpec('broken',provider))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('grouped-agents',source='agent a{provider:"broken",tools:[]};async fn work(n){a(n)};try{async with task_group(errors="group") as g{g.spawn(work(1));g.spawn(work(2))}}catch* {code:"provider_error",.._} as errors{length(errors.exceptions)}')
            self.assertEqual(app.start('grouped-agents',1,None,mode='source').result(8),2)

if __name__=='__main__':unittest.main()
