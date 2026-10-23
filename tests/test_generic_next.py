"""Primary continuations reuse the original generic invocation and authority."""
import unittest
from chaos.language import Evaluator, LanguageError, Native, TailCall, ResourceLimit, ExecutionControl


class GenericNextTests(unittest.TestCase):
    def code(self,source,**options): return Evaluator(**options).run(source)

    def test_primary_chain_and_legacy_records(self):
        self.assertEqual(self.code('''let a=generic_method(generic_function("f"),["Any"],fn(x){10});
        let b=generic_method(a,["Number"],fn(next,x){next()+20},with_next=true);
        let c=generic_method(b,["Int"],fn(next,x){next()+12},with_next=true);
        [c(1),b(1),a(1),generic_methods(a)[0],generic_methods(c)[2].withNext]''')[:3],[42,30,10])
        self.assertEqual(self.code('''let a=generic_method(generic_function("f"),["Any"],fn(x){10});
        [keys(generic_methods(a)[0]),length(keys(generic_info(a)))]'''),[['specializers','implementation','qualifier'],6])

    def test_qualifier_and_flag_are_explicit_not_inferred_from_parameter_name(self):
        self.assertEqual(self.code('''let f=generic_method(generic_function("f"),["Int"],fn(next){next+1});f(41)'''),42)
        for expression in ['generic_method(generic_function("f"),["Int"],fn(x){x},with_next=1)',
                           'generic_method(generic_function("f"),["Int"],fn(x){x},"around",with_next=true)',
                           'generic_method(generic_function("f"),["Int"],fn(x){null},"before",with_next=true)',
                           'generic_method(generic_function("f"),["Int"],fn(x){null},"after",with_next=true)',
                           'generic_method(generic_function("f"),["Int"],fn(x){x},"primary",true)']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:self.code(expression)
            self.assertEqual(caught.exception.code,'argument_error')

    def test_original_keyword_and_extra_positional_arguments_are_captured(self):
        self.assertEqual(self.code('''let a=generic_method(generic_function("f"),["Any"],fn(x,y,*,z=0){x+y+z});
        let b=generic_method(a,["Int"],fn(next,x,y,*,z=0){next},with_next=true);
        let n=b(20,20,z=2);[n(),try{n(1)}catch e{e.code},generic_next_available(n)]'''),[42,'argument_error',True])

    def test_terminal_token_can_be_queried_and_has_clear_call_error(self):
        self.assertEqual(self.code('''let f=generic_method(generic_function("f"),["Int"],fn(next,x){next},with_next=true);
        let n=f(42);[generic_next_available(next=n),try{n()}catch e{e.code},generic_next_available(n)]'''),[False,'no_next_method',False])
        for expression in ['generic_next_available(fn(){42})','generic_next_available(42)','generic_next_available(generic_function("f"))']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:self.code(expression)
            self.assertEqual(caught.exception.code,'argument_error')

    def test_reachable_preflight_stops_after_ordinary_primary(self):
        self.assertEqual(self.code('''var f=generic_function("f");f=generic_method(f,["Number"],fn(x){1});
        f=generic_method(f,["Rational"],fn(x){2});f=generic_method(f,["Int"],fn(x){42});f(1)'''),42)
        self.assertEqual(self.code('''var log=[];var f=generic_function("f");f=generic_method(f,["Number"],fn(x){1});
        f=generic_method(f,["Rational"],fn(x){2});f=generic_method(f,["Int"],fn(next,x){log=append(log,"primary");42},with_next=true);
        f=generic_method(f,["Any"],fn(x){log=append(log,"before");null},"before");
        f=generic_method(f,["Any"],fn(next,x){log=append(log,"around");42},"around");
        [try{f(1)}catch e{e.code},log]'''),['ambiguous_method',[]])

    def test_combination_runs_auxiliaries_once_primary_next_only_replays_suffix(self):
        self.assertEqual(self.code('''var log=[];fn mark(x){log=append(log,x);null};
        var f=generic_method(generic_function("f"),["Any"],fn(x){mark("base");x});
        f=generic_method(f,["Int"],fn(next,x){mark("int");next()+next()},with_next=true);
        f=generic_method(f,["Any"],fn(x){mark("before")},"before");
        f=generic_method(f,["Any"],fn(x){mark("after")},"after");
        f=generic_method(f,["Any"],fn(next,x){mark("around");let y=next();mark("end");y},"around");
        [f(21),log]'''),[42,['around','before','int','base','base','after','end']])

    def test_saved_next_retains_plan_and_never_reruns_completed_after(self):
        self.assertEqual(self.code('''var log=[];let old=generic_method(generic_function("f"),["Any"],fn(x){log=append(log,"old");x});
        var f=generic_method(old,["Int"],fn(next,x){next},with_next=true);
        f=generic_method(f,["Any"],fn(x){log=append(log,"before");null},"before");
        f=generic_method(f,["Any"],fn(x){log=append(log,"after");null},"after");
        let saved=f(42);let changed=generic_method(f,["Any"],fn(x){0});[saved(),saved(),changed(1)(),log]'''),
        [42,42,0,['before','after','old','old','before','after']])

    def test_multidispatch_c3_primary_order_and_no_metaclass_hooks(self):
        self.assertEqual(self.code('''var effects=0;class Meta(Type){fn __instancecheck__(self,x){effects=effects+1;true}}
        class Base(metaclass=Meta){}class Right(Base){}class Left(Base){}class Both(Left,Right){}
        var f=generic_method(generic_function("f",2),[Base,"Any"],fn(x,y){"B"});
        f=generic_method(f,[Right,"Any"],fn(next,x,y){"R"+next()},with_next=true);
        f=generic_method(f,[Left,"Any"],fn(next,x,y){"L"+next()},with_next=true);
        f=generic_method(f,[Both,"Int"],fn(next,x,y){"D"+next()},with_next=true);
        [f(Both(),1),effects]'''),['DLRB',0])

    def test_async_call_layer_and_task_completion_remain_separate(self):
        self.assertEqual(self.code('''var log=[];
        var f=generic_method(generic_function("f"),["Any"],async fn(x){log=append(log,"base");x});
        f=generic_method(f,["Int"],async fn(next,x){log=append(log,"int");await next()},with_next=true);
        f=generic_method(f,["Any"],fn(x){log=append(log,"before");null},"before");
        f=generic_method(f,["Any"],fn(x){log=append(log,"after");null},"after");
        let t=f(42);let initial=log;[initial,task_info(t).state,await t,log]'''),
        [['before','after'],'created',42,['before','after','int','base']])

    def test_generator_next_remains_cold_and_returns_original_generator(self):
        self.assertEqual(self.code('''var count=0;let base=generic_method(generic_function("f"),["Any"],fn*(x){count=count+1;yield x});
        let f=generic_method(base,["Int"],fn(next,x){next()},with_next=true);let g=f(42);
        let before=count;[before,collect(g),count]'''),[0,[42],1])

    def test_primary_tail_recursion_uses_existing_trampoline_and_contracts(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");var f=generic_function("f");
        f=generic_method(f,["Any"],fn(n)->Numbers{if n==0{[42]}else{f(n-1)}});
        f=generic_method(f,["Int"],fn(next,n)->Numbers{next()},with_next=true);f(1500)''',step_limit=1000000),[42])
        self.assertEqual(self.code('''let Numbers=contract_list("Int");let base=generic_method(generic_function("f"),["Any"],fn(x){["bad"]});
        let f=generic_method(base,["Int"],fn(next,x)->Numbers{next()},with_next=true);try{f(1)}catch e{e.code}'''),'type_mismatch')

    def test_error_after_and_around_cleanup_follow_existing_rules(self):
        self.assertEqual(self.code('''var log=[];var f=generic_method(generic_function("f"),["Any"],fn(x){throw ValueError("fail")});
        f=generic_method(f,["Int"],fn(next,x){next()},with_next=true);
        f=generic_method(f,["Any"],fn(x){log=append(log,"after");null},"after");
        f=generic_method(f,["Any"],fn(next,x){try{next()}finally{log=append(log,"finally")}},"around");
        [try{f(1)}catch e{e.code},log]'''),['value_error',['finally']])

    def test_narrow_callback_receiving_primary_token_cannot_export_authority(self):
        for qualifier,flag in [('primary',',with_next=true'),('around','')]:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            source='''var saved=null;let body=limited(fn(){fn(next,x){saved=next;0}});
            let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
            let f=generic_method(base,["Int"],body,"'''+qualifier+'"'+flag+''');f(42);try{saved()}catch e{e.code}'''
            with self.subTest(qualifier=qualifier):self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_next_capsules_nested_in_public_data_carriers_are_sealed(self):
        cases=[('[next]','saved[0]()'),('{item:next}','saved.item()'),('(next,)','saved[0]()'),
               ('hash_map([("item",next)])','hash_get(saved,"item")()'),
               ('class_member(next)','saved.value()'),('error(next)','saved.value()')]
        for carrier,invoke in cases:
            for qualifier,flag in [('primary',',with_next=true'),('around','')]:
                calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
                ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
                source='''var saved=null;let sink=limited(fn(){fn(xs){saved=xs;0}});
                let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
                let f=generic_method(base,["Int"],fn(next,x){sink('''+carrier+''');0},"'''+qualifier+'"'+flag+''');f(42);try{'''+invoke+'''}catch e{e.code}'''
                with self.subTest(carrier=carrier,qualifier=qualifier):self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_class_and_instance_native_slots_are_sealed_without_getters(self):
        for setup in ['let box=Box();box.item=next;','let box=Box;box.item=next;']:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            source='''var touches=0;class Box{var item=null;let hidden=property(fn(self){touches=touches+1;0})};var saved=null;
            let sink=limited(fn(){fn(x){saved=x;0}});let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
            let f=generic_method(base,["Int"],fn(next,x){'''+setup+'''sink(box);0},with_next=true);
            f(42);[try{saved.item()}catch e{e.code},touches]'''
            with self.subTest(setup=setup):self.assertEqual(ev.run(source),['capability_error',0]);self.assertEqual(calls,[])

    def test_shared_dag_revisits_stricter_hashmap_paths_and_old_aliases(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''var saved=null;let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
        let f=generic_method(base,["Int"],fn(next,x){saved=next;let wide=hash_map([("x",[next])]);
        let narrow=limited(fn(){wide});fn sink(x){0};sink([narrow,wide]);0},with_next=true);
        f(42);try{saved()}catch e{e.code}'''),'capability_error')
        self.assertEqual(calls,[])

    def test_cross_evaluator_next_rejected_in_queries_and_nested_graphs(self):
        foreign=Evaluator().run('let f=generic_method(generic_function("f"),["Any"],fn(next,x){next},with_next=true);f(1)')
        for expression in ['generic_next_available(foreign)','fn f(xs){xs[0]()};f([foreign])']:
            ev=Evaluator();ev.env.bind('foreign',foreign)
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:ev.run(expression)
            self.assertEqual(caught.exception.code,'ownership_error')

    def test_replacement_flag_changes_same_primary_slot_without_mutating_old(self):
        self.assertEqual(self.code('''let a=generic_method(generic_function("f"),["Any"],fn(x){1});
        let old=generic_method(a,["Int"],fn(next,x){next()+1},with_next=true);
        let new=generic_method(old,["Int"],fn(x){42});[old(1),new(1),generic_info(new).methodCount,
        generic_remove(new,["Int"])(1)]'''),[2,42,2,1])

    def test_entry_graph_limits_and_cancellation_propagate(self):
        from chaos.capture_graph import seal
        ev=Evaluator(step_limit=1000000)
        nested=0
        for _ in range(102):nested=[nested]
        with self.assertRaises(ResourceLimit):seal(ev,[nested],None)
        with self.assertRaises(ResourceLimit):seal(ev,[[0]*10000,[0]*10000],None)
        def stop():raise ExecutionControl('stop')
        ev.tick=stop
        with self.assertRaises(ExecutionControl):seal(ev,[[0]],None)

    def test_function_and_declared_native_closure_captures_are_sealed(self):
        for carrier in ['fn(){next()}', 'partial(next)', 'compose(fn(x){x},next)']:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            source='''var saved=null;let sink=limited(fn(){fn(x){saved=x;0}});
            let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
            let f=generic_method(base,["Int"],fn(next,x){sink('''+carrier+''');0},with_next=true);
            f(42);try{saved()}catch e{e.code}'''
            with self.subTest(carrier=carrier):self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_weak_registry_does_not_keep_unreferenced_next_alive(self):
        import gc
        ev=Evaluator();ev.run('let f=generic_method(generic_function("f"),["Any"],fn(next,x){42},with_next=true);f(1)')
        gc.collect()
        self.assertFalse(ev._generic_next_capsules)

    def test_whole_env_capture_is_conservative_and_has_no_freevar_guess(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''var saved=null;let sink=limited(fn(){fn(f){0}});
        let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
        let f=generic_method(base,["Int"],fn(next,x){saved=next;sink(fn(){42});0},with_next=true);
        f(42);try{saved()}catch e{e.code}'''),'capability_error')
        self.assertEqual(calls,[])

    def test_unused_narrow_function_in_shared_env_conservatively_seals_next(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''var saved=null;let unused=limited(fn(){fn(){0}});
        let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
        let f=generic_method(base,["Int"],fn(next,x){saved=next;0},with_next=true);
        f(42);try{saved()}catch e{e.code}'''),'capability_error')
        self.assertEqual(calls,[])

    def test_graph_preflight_is_atomic_and_caps_merge_across_actual_dag_paths(self):
        from dataclasses import replace
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.hash_maps import make
        ev=Evaluator(step_limit=1000000)
        token=NextDescriptor(ev,None)
        next=Native('next',lambda:0,ev,generic=token,captures=())
        shared=[next]
        wide=make(ev,[("key",shared,0)])
        narrow=replace(wide,capabilities=frozenset())
        # Worklist reads wide first, then revisits the identical shared List.
        seal(ev,[[narrow,wide]],None)
        self.assertEqual(token.capabilities,frozenset())
        untouched=NextDescriptor(ev,None)
        other=Native('next',lambda:0,ev,generic=untouched,captures=())
        with self.assertRaises(ResourceLimit):seal(ev,[[0]*10001,other],frozenset())
        self.assertIsNone(untouched.capabilities)
        self.assertIsNone(other.capabilities)

    def test_declared_capture_walk_never_runs_host_objects_or_native_bodies(self):
        from chaos.capture_graph import seal
        class Host:
            def __iter__(self):raise AssertionError('host iterator')
            def __getattribute__(self,name):raise AssertionError('host getter')
        ev=Evaluator()
        native=Native('opaque',lambda:(_ for _ in ()).throw(AssertionError('native body')),ev,captures=(Host(),))
        seal(ev,[native],None)

    def test_context_default_and_iterator_carriers_have_defined_capture_boundaries(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''var saved=null;let sink=limited(fn(){fn(x){saved=x;0}});
        let base=generic_method(generic_function("f"),["Any"],fn(x){tool("secret",x)});
        let f=generic_method(base,["Int"],fn(next,x){sink(context_var("n",next));0},with_next=true);
        f(42);try{context_get(saved)()}catch e{e.code}'''),'capability_error')
        self.assertEqual(calls,[])

    def test_code_function_from_retains_explicit_next_contract(self):
        self.assertEqual(self.code('''let c=code_from("fn(next:Function,x:Int)->Int{next()+1}");let body=function_from(c);
        let base=generic_method(generic_function("f"),["Any"],fn(x){41});
        generic_method(base,["Int"],body,with_next=true)(1)'''),42)


    def test_waiting_parent_task_seals_anonymous_child_captured_next(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args) or 42)
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('let base=generic_method(generic_function("g"),["Any"],fn(x){tool("fixture",x)});\nlet g=generic_method(base,["Int"],fn(next,x){next},with_next=true);\nvar saved=null;let sink=limited(fn(){fn(item){saved=item;0}});\nasync fn outer(){await (async fn(resume){await sleep(0.05);resume})(g(42))}\nlet pending=outer();spawn(pending);await sleep(0.005);\nlet state=task_info(pending).state;sink(pending);let resume=await saved;\n[state,try{resume()}catch error{error.code}]'),['running','capability_error'])
        self.assertEqual(calls,[])

    def test_native_wait_carriers_follow_only_declared_storage(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.suspension import Task, Runner, Await, Drain, Join, Yield, Effect
        from chaos.channels import Channel, Message, Send, Receive
        from chaos.task_groups import TaskGroup, GroupRun, GroupAbort
        from chaos.task_waits import Wait
        ev=Evaluator();ev.run('0')
        class OpaqueIterator:
            def __iter__(self):raise AssertionError('host iterator reflection')
        for carrier in ('await','drain','join','yield','effect','send','receive','group','abort','wait','owned','error'):
            token=NextDescriptor(ev,None)
            next=Native('next',lambda:0,ev,generic=token,captures=())
            task=Task(ev.suspension,OpaqueIterator());task.capture_roots=(next,)
            channel=Channel(ev.suspension,1);message=Message(ev.suspension,next,1,0)
            group=TaskGroup(ev.suspension);group.body=task
            error=LanguageError('deferred',value=next)
            requests={'await':Await(task),'drain':Drain(task),'join':Join([task]),'yield':Yield(next),
                      'effect':Effect('fixture',{'next':next}),'send':Send(channel,message),
                      'receive':Receive(channel),'group':GroupRun(group,OpaqueIterator()),
                      'abort':GroupAbort(group,error),'wait':Wait((task,),'all',None)}
            runner=Runner(OpaqueIterator())
            if carrier=='owned':runner.await_owned=task
            elif carrier=='error':runner.deferred_error=error
            else:runner.waiting=requests[carrier]
            if carrier=='receive':channel.buffer.append(message)
            with self.subTest(carrier=carrier):
                seal(ev,[runner],frozenset())
                self.assertEqual(token.capabilities,frozenset())

if __name__=='__main__':unittest.main()
