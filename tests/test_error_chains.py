"""Native causal graphs belong to logical execution, not host tracebacks."""
import unittest
import threading
from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, Native, TailCall, serializable
from chaos.error_values import capture
from chaos.errors import graph
from chaos.suspension import TaskCancelled, TERMINAL

class ErrorChains(unittest.TestCase):
    def run_code(self, source, **options): return Evaluator(**options).run(source)

    def test_explicit_cause_and_null_are_distinct_from_omission(self):
        self.assertEqual(self.run_code('let a=error(1);let b=error(2,cause=a);let c=error(3,cause=null);[a.cause,a.context,a.suppress_context,b.cause==a,b.context,b.suppress_context,c.cause,c.context,c.suppress_context]'),[None,None,False,True,None,True,None,None,True])

    def test_created_value_captures_context_only_on_first_propagation(self):
        self.assertEqual(self.run_code('let e=try{throw 1}catch x{error(2)};try{try{throw 3}catch x{throw e}}catch x{[e.context.value,current_error()==e]}'),[3,True])
        self.assertEqual(self.run_code('let e=error(2);try{throw e}catch x{0};try{try{throw 3}catch x{throw e}}catch x{e.context}'),None)

    def test_runtime_failures_and_guard_failures_link_active_error(self):
        for body in ('1/0','unknown','length(1)','throw 2','error(0,cause=7)'):
            with self.subTest(body=body):self.assertEqual(self.run_code('try{try{throw 42}catch x{'+body+'}}catch x{current_error().context.value}'),42)
        self.assertEqual(self.run_code('try{try{throw 42}catch x if 7{0}}catch x{current_error().context.value}'),42)

    def test_explicit_cause_retains_implicit_context_without_legacy_record_change(self):
        self.assertEqual(self.run_code('let cause=error(20);try{try{throw 21}catch x{throw error(22,cause=cause)}}catch x{let e=current_error();[e.cause.value,e.context.value,e.suppress_context,keys(x)]}'),[20,21,True,['code','message','value','location']])
        self.assertEqual(self.run_code('try{try{throw 1}catch x{throw error(2,cause=null)}}catch x{[current_error().cause,current_error().context.value,current_error().suppress_context]}'),[None,1,True])

    def test_reraise_is_identity_preserving_and_does_not_create_cycles(self):
        for statement in ('throw','throw current_error()'):
            self.assertEqual(self.run_code('let a=error(42);try{try{throw a}catch x{'+statement+'}}catch x{[current_error()==a,a.context]}'),[True,None])
        self.assertEqual(self.run_code('let a=error(42);let g=error_group("g",[a]);try{try{throw g}catch x{throw a}}catch x{[current_error()==a,a.context]}'),[True,None])

    def test_nested_handlers_restore_context_and_tailcalls(self):
        self.assertEqual(self.run_code('fn fail(n){if n==0{throw 9}else{fail(n-1)}};try{try{throw 42}catch outer{try{throw 7}catch inner{0};fail(300)}}catch x{current_error().context.value}'),42)
        self.assertEqual(self.run_code('fn fail(){throw 9};fn keep(x){x};try{try{throw 42}catch outer{compose(keep,fail)()}}catch x{current_error().context.value}'),42)

    def test_task_context_isolation_and_interleaving(self):
        self.assertEqual(self.run_code('async fn f(n){try{try{throw n}catch x{await sleep(0);throw n+1}}catch x{[current_error().value,current_error().context.value]}};await gather([f(20),f(40)])'),[[21,20],[41,40]])
        self.assertEqual(self.run_code('async fn f(){throw 2};let task=try{throw 1}catch x{f()};try{try{throw 3}catch x{await task}}catch x{current_error().context}'),None)

    def test_generators_keep_own_context_and_ignore_consumer(self):
        for flavor, step in [('fn*','next'),('async fn*','await anext')]:
            self.assertEqual(self.run_code(flavor+' f(){try{throw 42}catch x{yield 0;throw 2}};let g=f();'+step+'(g);try{try{throw 3}catch x{'+step+'(g)}}catch x{current_error().context.value}'),42)
            self.assertEqual(self.run_code(flavor+' f(){throw 2;yield 0};let g=f();try{try{throw 3}catch x{'+step+'(g)}}catch x{current_error().context}'),None)

    def test_group_partition_preserves_cause_and_context(self):
        self.assertEqual(self.run_code('let why=error(40);var out=[];try{try{throw 41}catch x{throw error_group("g",[error(1),error(2)],cause=why)}}catch* {value:1,.._} as selected{out=append(out,[selected.cause==why,selected.context.value,selected.suppress_context])}catch* _ as selected{out=append(out,[selected.cause==why,selected.context.value,selected.suppress_context])};out'),[[True,41,True],[True,41,True]])

    def test_group_handler_failure_links_subgroup_not_sibling(self):
        self.assertEqual(self.run_code('try{try{throw error_group("g",[error(1),error(2)])}catch* {value:1,.._} as selected{throw 3}catch* _ as selected{null}}catch x{[current_error().value,current_error().context.exceptions[0].value]}'),[3,1])

    def test_resources_chain_without_enabling_bare_rethrow(self):
        self.assertEqual(self.run_code('class R{fn __enter__(self){self}fn __exit__(self,e){throw 2}};try{with R() as r{throw 42}}catch x{current_error().context.value}'),42)
        self.assertEqual(self.run_code('class R{fn __enter__(self){self}fn __exit__(self,e){throw}};try{with R() as r{throw 42}}catch x{[x.code,current_error().context.value]}'),['rethrow_error',42])
        self.assertEqual(self.run_code('class R{async fn __aenter__(self){self}async fn __aexit__(self,e){await sleep(0);throw 2}};try{async with R() as r{throw 42}}catch x{current_error().context.value}'),42)

    def test_exit_stack_keeps_lifo_failure_chain(self):
        self.assertEqual(self.run_code('fn fail(n){throw n};try{with exit_stack() as s{s.callback(fail,3);s.callback(fail,2);throw 1}}catch x{let e=current_error();[e.value,e.context.value,e.context.context.value]}'),[3,2,1])

    def test_permissions_survive_cause_and_context_escape(self):
        for field in ('cause','context'):
            seen=[];ev=Evaluator(tool_handler=lambda *args:seen.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            made='error(2,cause=why)' if field=='cause' else 'try{throw why}catch x{try{throw 2}catch x{current_error()}}'
            source='fn secret(){tool("secret",0)};let why=error(secret);let e=limited(fn(){'+made+'});try{e.'+field+'.value()}catch x{x.code}'
            self.assertEqual(ev.run(source),'capability_error');self.assertEqual(seen,[])

    def test_foreign_causes_and_forged_records_rejected(self):
        other=Evaluator().run('error(42)')
        for source in ('error(2,cause=foreign)','error_group("g",[error(2)],cause=foreign)'):
            ev=Evaluator();ev.env.bind('foreign',other)
            with self.assertRaises(LanguageError) as caught:ev.run(source)
            self.assertEqual(caught.exception.code,'ownership_error')
        self.assertEqual(self.run_code('try{error(2,cause={"$error":{value:1}})}catch x{x.code}'),'ownership_error')

    def test_graph_budget_and_host_cycles_fail_closed(self):
        self.assertEqual(self.run_code('var e=error(0);for n in range(40){e=error(n,cause=e)};e.cause.value'),38)
        root=LanguageError('root');tail=root
        for _ in range(10000):
            tail._error_cause=LanguageError('next');tail=tail._error_cause
        with self.assertRaises(ResourceLimit):graph(root)
        a=LanguageError('a');b=LanguageError('b');a._error_cause=b;b._error_context=a
        with self.assertRaises(LanguageError) as caught:graph(a)
        self.assertEqual(caught.exception.code,'value_error')
        for failure in (ResourceLimit('limit'),ExecutionControl('stop'),TaskCancelled('cancel')):
            with self.assertRaises(LanguageError):capture(Evaluator(),failure)

    def test_diagnostics_use_local_graph_references_and_no_host_tracebacks(self):
        value=self.run_code('try{try{throw 42}catch x{throw error(2,cause=current_error())}}catch x{current_error()}')
        record=serializable(value)['$error']
        self.assertEqual(record['cause']['$error']['value'],42)
        self.assertEqual(record['context'],{'$errorRef':record['cause']['$error']['id']})
        self.assertNotIn('traceback',str(record));self.assertNotIn('owner',str(record))

    def test_hard_control_never_becomes_a_chain_and_state_restores(self):
        for failure in (ResourceLimit('budget'),ExecutionControl('host'),TaskCancelled('cancel'),KeyboardInterrupt(),SystemExit(7)):
            ev=Evaluator(tool_handler=lambda *a:None,effect_handler=lambda *a: (_ for _ in ()).throw(failure))
            with self.subTest(failure=type(failure).__name__),self.assertRaises(type(failure)) as caught:ev.run('async fn f(){try{throw 42}catch x{tool("fail",0)}};await f()')
            self.assertIs(caught.exception,failure);self.assertIsNone(ev.error_context)
            self.assertTrue(all(t.state in TERMINAL for t in ev.suspension.tasks))


    def test_design_boundary_cases(self):
        cases = [{'name': 'construction_is_not_propagation', 'source': 'var saved=null;try{throw 1}catch e{saved=error(2)};try{throw saved}catch e{current_error().context}', 'expected': None}, {'name': 'explicit_null_suppresses_without_erasing_context', 'source': 'try{try{throw 1}catch e{throw error(2,cause=null)}}catch e{[current_error().cause,current_error().context.value,current_error().suppress_context]}', 'expected': [None, 1, True]}, {'name': 'first_propagation_is_frozen', 'source': 'let shared=error(2);try{throw 1}catch e{try{throw shared}catch e{null}};try{throw 3}catch e{try{throw shared}catch e{current_error().context.value}}', 'expected': 1}, {'name': 'exit_chain_preserves_bare_throw_context_rule', 'source': 'var seen=null;class C{fn __enter__(self){null}fn __exit__(self,e){seen=current_error();throw "cleanup"}};try{with C() as c{throw "body"}}catch e{[seen,current_error().value,current_error().context.value]}', 'expected': [None, 'cleanup', 'body']}, {'name': 'cleanup_context_is_nearest_and_separate_from_handled_error', 'source': 'var seen=null;class C{fn __enter__(self){null}fn __exit__(self,e){seen=current_error();throw "cleanup"}};try{throw "outer"}catch e{try{with C() as c{throw "body"}}catch e{[seen.value,current_error().value,current_error().context.value,current_error().context.context.value]}}', 'expected': ['outer', 'cleanup', 'body', 'outer']}, {'name': 'exit_stack_replacement_retains_lifo_chain', 'source': 'try{with exit_stack() as s{s.push(fn(e){throw "outer"});s.callback(fn(){throw "inner"});throw "body"}}catch e{[current_error().value,current_error().context.value,current_error().context.context.value]}', 'expected': ['outer', 'inner', 'body']}, {'name': 'suppressed_stack_error_not_leaked_into_later_exit', 'source': 'try{with exit_stack() as s{s.callback(fn(){throw "outer"});s.push(fn(e){true});throw "body"}}catch e{[current_error().value,current_error().context]}', 'expected': ['outer', None]}, {'name': 'async_exit_inherits_only_causal_context', 'source': 'var seen=null;class C{async fn __aenter__(self){null}async fn __aexit__(self,e){await sleep(0);seen=current_error();throw "cleanup"}};try{async with C() as c{throw "body"}}catch e{[seen,current_error().value,current_error().context.value]}', 'expected': [None, 'cleanup', 'body']}, {'name': 'independent_task_does_not_inherit_consumer_context', 'source': 'async fn child(){throw 2};try{throw 1}catch e{try{await child()}catch e{current_error().context}}', 'expected': None}, {'name': 'generator_does_not_inherit_consumer_context', 'source': 'fn* child(){throw 2;yield 0};let g=child();try{throw 1}catch e{try{next(g)}catch e{current_error().context}}', 'expected': None}, {'name': 'parallel_catches_restore_their_own_context', 'source': 'async fn f(n){try{try{throw n}catch e{await sleep(0);throw n+1}}catch e{current_error().context.value}};await gather([f(10),f(20)])', 'expected': [10, 20]}, {'name': 'causal_cycle_does_not_mutate_old_graph', 'source': 'let a=error(1);let b=error(2,cause=a);try{throw b}catch e{try{throw a}catch e{[current_error().context,b.cause==a]}}', 'expected': [None, True]}]
        for case in cases:
            with self.subTest(name=case['name']):self.assertEqual(self.run_code(case['source']),case['expected'])

    def test_restricted_group_ancestors_apply_to_legacy_records(self):
        from chaos.errors import GroupedError
        from chaos.error_values import Error
        seen=[];ev=Evaluator(tool_handler=lambda *a:seen.append(a))
        function=ev.run('fn secret(){tool("secret",0)};secret')
        child=GroupedError('restricted',[LanguageError('leaf',value=function)])
        child._error_capabilities=frozenset();child._context_seen=True
        outer=GroupedError('outer',[child])
        ev.env.bind('problem',Error(outer,ev))
        self.assertEqual(ev.run('try{throw problem}catch x{let callback=x.value.exceptions[0].value.exceptions[0].value;try{callback()}catch x{x.code}}'),'capability_error')
        self.assertEqual(seen,[])

    def test_late_failures_retain_worker_context_while_cancelled(self):
        barrier=threading.Barrier(2)
        def effect(kind,payload,cancel):
            barrier.wait(3)
            raise LanguageError('failed',code='worker_error')
        ev=Evaluator(tool_handler=lambda *a:None,effect_handler=effect)
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset({'tool:fail'})))
        source='async fn f(n){try{throw n}catch x{tool("fail",0)}};try{async with task_group(errors="group") as g{g.spawn(f(1));g.spawn(f(2))}}catch* _ as errors{sort(map(fn(e){e.context.value},errors.exceptions))}'
        self.assertEqual(ev.run(source),[1,2])
        self.assertTrue(all(t.state in TERMINAL for t in ev.suspension.tasks))

    def test_real_agent_failure_can_be_wrapped_with_a_cause(self):
        from chaos import Framework, Registry, Settings, ProviderSpec
        registry=Registry()
        def provider(request,context):raise ValueError('upstream unavailable')
        registry.register_provider(ProviderSpec('broken',provider))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('causal-agent',source='agent worker{provider:"broken",tools:[]};try{try{worker(1)}catch x{throw error({stage:"planning"},cause=current_error())}}catch x{[current_error().value.stage,current_error().cause.code,current_error().suppress_context]}')
            self.assertEqual(app.start('causal-agent',1,None,mode='source').result(8),['planning','provider_error',True])


    def test_future_context_cannot_expand_a_projected_cause(self):
        seen=[];ev=Evaluator(tool_handler=lambda *args:seen.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        source='fn secret(){tool("secret",0)};let a=error(1);let b=limited(fn(){error(2,cause=a)});var escaped=null;try{throw b}catch e{escaped=current_error()};try{throw secret}catch e{try{throw a}catch e{null}};if escaped.cause.context==null{42}else{try{escaped.cause.context.value()}catch e{e.code}}'
        self.assertIn(ev.run(source),(42,'capability_error'));self.assertEqual(seen,[])

if __name__=='__main__':unittest.main()
