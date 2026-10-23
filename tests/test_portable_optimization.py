"""Portable execution specialization: real work elimination and exact observables."""
import copy
import unittest
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError, Node, Parser, ResourceLimit
from chaos.modules import ProgramBundle
from chaos.portable_optimizer import build_plan
from chaos.program import plain
from chaos.suspension import CHECKPOINT


def nodes(value):
    if isinstance(value, Node):
        yield value
        for item in value[1:]:
            yield from nodes(item)
    elif type(value) in (list, tuple):
        for item in value:
            yield from nodes(item)
    elif type(value) is dict:
        for item in value.values():
            yield from nodes(item)


def outcome(source, enabled, *, limit=100000, cancel=None, files=None):
    trace=[]
    evaluator=Evaluator(optimize_literals=enabled, step_limit=limit, timeout=10,
                        trace_handler=trace.append)
    evaluator.cancelled=lambda: cancel is not None and evaluator.steps >= cancel
    try:
        result=('value', evaluator.run(source, files=files))
    except LanguageError as error:
        result=('error', error.record())
    return result, evaluator.steps, trace


class PortableOptimization(unittest.TestCase):
    def test_plan_is_pure_and_does_not_mutate_original_nodes(self):
        tree=Parser('(2+3)*(4-1)',ranges=True).program(); before=copy.deepcopy(tree)
        with patch.object(Evaluator, 'run', side_effect=AssertionError('planner executed language')):
            plan=build_plan({'main.chaos':tree})
        self.assertEqual(plain(tree), plain(before))
        self.assertEqual(plan.info()['candidateSites'], 3)
        root=tree[1][0]
        self.assertEqual(plan.site(root).match('*',(5,3)), (True,15))
        self.assertIsNone(plan.site(tree))

    def test_plan_info_is_closed_aggregate_and_detached(self):
        plan=build_plan({'main.chaos':Parser('123+456',ranges=True).program()})
        info=plan.info()
        self.assertEqual(set(info), {'schema','algorithm','candidateSites','scannedNodes','truncated','limits','checkpointPolicy'})
        self.assertEqual(info['schema'], 'chaos.literal-optimization/1')
        self.assertEqual(info['algorithm'], 'guarded-total-int/2')
        self.assertEqual(info['checkpointPolicy'], 'original-node-and-operation-entry')
        self.assertEqual(info['limits'], {'maxNodes':16384,'maxSites':4096,'maxBits':256})
        info['limits']['maxBits']=1
        self.assertEqual(plan.info()['limits']['maxBits'],256)
        self.assertNotIn('123',repr(plan.info()))

    def test_site_guards_exact_types_operator_and_actual_values_without_hooks(self):
        tree=Parser('2+3',ranges=True).program(); site=build_plan({'main.chaos':tree}).site(tree[1][0])
        class HostInt(int):
            def __eq__(self,other): raise AssertionError('host equality executed')
        for op,args in [('-', (2,3)),('+',(2,4)),('+',(2.0,3)),('+',(True,3)),('+',(HostInt(2),3)),('+',[2,3]),('+',(2,3,4))]:
            with self.subTest(op=op,args=type(args).__name__):
                self.assertEqual(site.match(op,args),(False,None))
        self.assertEqual(site.match('+',(2,3)),(True,5))

    def test_unary_and_nested_constants_have_real_sites(self):
        tree=Parser('-(2+3)+(+4)',ranges=True).program(); plan=build_plan({'main.chaos':tree})
        sites=[(node,plan.site(node)) for node in nodes(tree) if plan.site(node) is not None]
        self.assertEqual(len(sites),4)
        for node,site in sites:
            if node[0]=='unary':
                operand=5 if node[1]=='-' else 4
                self.assertEqual(site.match('neg' if node[1]=='-' else 'pos',(operand,)),(True,-5 if operand==5 else 4))

    def test_unsupported_and_oversized_values_never_become_compile_errors(self):
        for source in ('1.5+2.5','true+1','1/0','2**10000','(2**255)*(2**255)','0*(1/0)'):
            with self.subTest(source=source):
                tree=Parser(source,ranges=True).program(); plan=build_plan({'main.chaos':tree})
                self.assertIsNone(plan.site(tree[1][0]))
                self.assertEqual(outcome(source,True),outcome(source,False))

    def test_plan_limits_truncate_optimization_without_rejecting_valid_program(self):
        tree=Node(('block',[Node(('binary','+',Node(('literal',1)),Node(('literal',2)))) for _ in range(4200)]))
        plan=build_plan({'main.chaos':tree})
        self.assertTrue(plan.info()['truncated'])
        self.assertLessEqual(plan.info()['candidateSites'],4096)
        self.assertLessEqual(plan.info()['scannedNodes'],16384)

    def test_checkpoint_callback_cancellation_is_not_swallowed(self):
        def stop(): raise ResourceLimit('planner cancelled')
        with self.assertRaisesRegex(ResourceLimit,'planner cancelled'):
            build_plan({'main.chaos':Parser('2+3',ranges=True).program()},checkpoint=stop)

    def test_actual_runtime_eliminates_arithmetic_and_reference_switch_keeps_it(self):
        import chaos.operators as operators
        original=operators.primitive; counts=[]
        for enabled in (False,True):
            with patch('chaos.operators.primitive',wraps=original) as primitive:
                e=Evaluator(optimize_literals=enabled)
                self.assertEqual(e.run('fn work(){(2+3)*(4-1)};[work(),work(),work()]'),[15,15,15])
                counts.append(primitive.call_count)
                info=e.optimization_info()
                self.assertEqual(info['enabled'],enabled)
                self.assertEqual(info['schema'],'chaos.literal-runtime/1')
                self.assertEqual(set(info),{'schema','enabled','candidateSites','appliedOperations','guardMisses'})
                if enabled:self.assertGreater(info['candidateSites'],0)
                self.assertEqual(info['appliedOperations'],9 if enabled else 0)
        self.assertEqual(counts,[9,0])

    def test_every_budget_threshold_keeps_value_error_location_steps_and_trace(self):
        source='fn work(){(2+3)*(4-1)};work()'
        total=outcome(source,False)[1]
        for limit in range(total+2):
            with self.subTest(limit=limit):
                self.assertEqual(outcome(source,True,limit=limit),outcome(source,False,limit=limit))

    def test_every_cancel_threshold_keeps_value_error_location_steps_and_trace(self):
        source='fn work(){(2+3)*(4-1)};work()'
        total=outcome(source,False)[1]
        for threshold in range(total+2):
            with self.subTest(threshold=threshold):
                self.assertEqual(outcome(source,True,cancel=threshold),outcome(source,False,cancel=threshold))

    def test_loop_scheduler_checkpoints_and_literal_activations_match(self):
        observations=[]
        for enabled in (False,True):
            e=Evaluator(optimize_literals=enabled)
            f=e.run('fn(n){var i=0;var sum=0;while i<n{sum=sum+(6*7);i=i+1};sum}')
            e.steps=0; yielded=[]; iterator=e._s_invoke(f,[3])
            while True:
                try:
                    self.assertIs(next(iterator),CHECKPOINT)
                    yielded.append((e.steps,dict(e.current_span)))
                except StopIteration as done:
                    observations.append((done.value,e.steps,yielded));break
        self.assertEqual(observations[0],observations[1])
        self.assertEqual(observations[0][0],126)

    def test_debug_and_trace_keep_all_original_nodes_while_optimization_applies(self):
        observations=[]
        for enabled in (False,True):
            debug=[];trace=[];e=Evaluator(optimize_literals=enabled,trace_handler=trace.append)
            e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
            result=e.run('fn work(){(2+3)*(4-1)};work()')
            observations.append((result,e.steps,debug,trace))
            if enabled:self.assertGreater(e.optimization_info()['appliedOperations'],0)
        self.assertEqual(observations[0],observations[1])

    def test_replaced_public_ast_does_not_reuse_stale_constant(self):
        bundle=ProgramBundle('(2+3)*4')
        replacement=Parser('(9+1)*4',ranges=True).program()[1][0]
        bundle.expanded_trees['main.chaos'][1][0]=replacement
        self.assertEqual(Evaluator(optimize_literals=True).run(bundle),40)
        self.assertEqual(Evaluator(optimize_literals=False).run(bundle),40)

    def test_short_circuit_unreachable_errors_and_finally_order_are_unchanged(self):
        source='var events=[];fn mark(x){events=append(events,x);x};fn work(){try{mark("start");if false{1/0}else{(6*7)}}finally{mark("cleanup")}};let result=work();[result,events]'
        self.assertEqual(outcome(source,True),outcome(source,False))
        self.assertEqual(outcome(source,True)[0],('value',[42,['start','cleanup']]))

    def test_class_user_operations_are_never_replaced_by_constant_heuristics(self):
        source='var calls=0;class Box{fn __add__(self,other){calls=calls+1;6*7}};[Box()+2,calls]'
        self.assertEqual(outcome(source,True),outcome(source,False))
        self.assertEqual(outcome(source,True)[0],('value',[42,1]))

    def test_reference_flag_is_exact_bool_and_runtime_query_is_detached(self):
        for invalid in (0,1,None,"yes"):
            with self.subTest(value=invalid),self.assertRaises((ValueError,TypeError)):
                Evaluator(optimize_literals=invalid)
        e=Evaluator();self.assertEqual(e.run('6*7'),42)
        first=e.optimization_info();self.assertEqual(first['appliedOperations'],1)
        first['appliedOperations']=999
        self.assertEqual(e.optimization_info()['appliedOperations'],1)
        self.assertEqual(e.run('42'),42)
        self.assertEqual(e.optimization_info()['appliedOperations'],0)

    def test_malformed_host_tags_and_foreign_children_never_execute_hooks(self):
        class Hostile:
            def __eq__(self, other): raise AssertionError('host equality executed')
            def __hash__(self): raise AssertionError('host hashing executed')
            def __iter__(self): raise AssertionError('host iteration executed')
            def __len__(self): raise AssertionError('host length executed')
        foreign=Hostile()
        roots={'bad-tag.chaos':Node((Hostile(),1)),
               'foreign-child.chaos':Node(('block',[
                   Node(('binary','+',foreign,Node(('literal',1)))),
                   Node(('binary','+',Node(('literal',foreign)),Node(('literal',1))))]))}
        plan=build_plan(roots)
        self.assertEqual(plan.info()['candidateSites'],0)
        self.assertIsNone(plan.site(roots['bad-tag.chaos']))
