"""Inert lexical integer facts respect scopes, metadata and structural bounds."""
import unittest

from chaos.language import Node, Parser, LanguageError
from chaos.modules import ProgramBundle
from chaos.portable_optimizer import build_plan, MAX_NODES


def plan_source(source,files=None):
    bundle=ProgramBundle(source,files=files)
    return build_plan(bundle.expanded_trees),bundle.expanded_trees


def arithmetic(trees,right=6):
    pending=[trees];seen=set();found=[]
    while pending:
        item=pending.pop()
        if type(item)is dict:pending.extend(item.values());continue
        if type(item)not in(Node,list,tuple)or id(item)in seen:continue
        seen.add(id(item))
        if (type(item)is Node and len(item)==4 and item[0]=='binary'and item[1]=='*'and
            type(item[3])is Node and tuple(item[3])==('literal',right)):found.append(item)
        pending.extend(child for child in item if type(child)in(Node,list,tuple))
    return found


def literal(value):return Node(('literal',value))
def name(value):return Node(('name',value))
def multiply(left,right=6):return Node(('binary','*',left,literal(right)))
def binding(key,value,kind='let'):return Node((kind,key,'Any',value))
def block(*items):return Node(('block',list(items)))


class LexicalOptimizer(unittest.TestCase):
    def test_real_let_alias_closure_builds_guarded_physical_operation_site(self):
        plan,trees=plan_source('let k=7;let alias=k;fn f(){alias*6};f();f()')
        op=arithmetic(trees)[0];site=plan.site(op)
        self.assertIs(site.node,op);self.assertEqual(site.operands,(7,6));self.assertEqual(site.result,42)
        self.assertEqual(site.match('*',(7,6)),(True,42))
        for values in ((8,6),(True,6),(7.0,6)):
            self.assertEqual(site.match('*',values),(False,None))
        self.assertEqual(plan.info()['algorithm'],'guarded-total-int/2')
        self.assertEqual(plan.info()['candidateSites'],1)

    def test_prior_let_arithmetic_aliases_and_nested_local_scopes(self):
        plan,trees=plan_source('let k=2+5;let alias=k;fn outer(){let k=8;fn inner(){k*6};inner()};fn f(){alias*6};[outer(),f()]')
        sites=[plan.site(op)for op in arithmetic(trees)]
        self.assertEqual(sorted(site.operands for site in sites),[(7,6),(8,6)])
        self.assertEqual(plan.info()['candidateSites'],3)

    def test_parameters_shadow_captured_facts_but_defaults_use_declaration_scope(self):
        plan,trees=plan_source('let k=7;fn f(k=k*2){k*6};f(8)')
        self.assertIsNone(plan.site(arithmetic(trees)[0]))
        default=plan.site(arithmetic(trees,2)[0]);self.assertEqual(default.operands,(7,2))
        self.assertEqual(default.result,14)

    def test_mutable_cells_duplicate_bindings_and_future_local_masks_are_unknown(self):
        for source in ('var k=7;fn f(){k*6};k=8;f()',
                       'let k=7;fn outer(){let g=fn(){k*6};let k=8;g()};outer()'):
            with self.subTest(source=source):
                plan,trees=plan_source(source)
                self.assertTrue(all(plan.site(op)is None for op in arithmetic(trees)))
        duplicate='let k=7;fn f(){k*6};let k=8;f()'
        raw=Parser(duplicate,ranges=True).program();plan=build_plan(raw)
        self.assertTrue(all(plan.site(op)is None for op in arithmetic(raw)))
        with self.assertRaises(LanguageError):ProgramBundle(duplicate)
        # Conservative future-local masking is not a claim that later let
        # changes an already-created closure's actual env.snapshot semantics.

    def test_var_initializer_can_use_immutable_alias_but_var_reads_remain_unknown(self):
        plan,trees=plan_source('let alias=7;var product=alias*6;product=8;product*6')
        ops=arithmetic(trees)
        sites=[plan.site(op)for op in ops]
        self.assertEqual(sum(site is not None for site in sites),1)
        self.assertEqual(next(site for site in sites if site is not None).operands,(7,6))

    def test_unknown_loop_and_pattern_scope_keep_literal_folding_only(self):
        op=multiply(name('k'));literal_op=multiply(literal(2))
        loop=Node(('for','k',Node(('list',[literal(8)])),block(op,literal_op)))
        tree=block(binding('k',literal(7)),loop)
        plan=build_plan(tree);self.assertIsNone(plan.site(op));self.assertEqual(plan.site(literal_op).result,12)
        unknown=Node(('let_pattern',Node(('p_name','k')),'Any',literal(9)))
        plan=build_plan(block(binding('outer',literal(7)),unknown,multiply(name('outer'))))
        self.assertEqual(plan.info()['candidateSites'],0)

    def test_class_field_namespace_is_not_treated_as_method_capture(self):
        plan,trees=plan_source('let x=9;class C{let x=7;fn read(self){x*6}};null')
        self.assertTrue(arithmetic(trees));self.assertTrue(all(plan.site(op)is None for op in arithmetic(trees)))

    def test_real_modules_have_separate_binding_facts(self):
        main='import "other.chaos" as other;let k=7;fn f(){k*6};[f(),other.f()]'
        plan,trees=plan_source(main,{'main.chaos':main,'other.chaos':'let k=8;fn f(){k*6};export {f}'})
        self.assertEqual(sorted(plan.site(op).operands for op in arithmetic(trees)),[(7,6),(8,6)])

    def test_real_macro_internal_parameter_alias_masks_outer_binding(self):
        source='let value=7;macro make(x){quote{fn(value){value*unquote(x)}}};let f=make!(6);f(8)'
        plan,trees=plan_source(source)
        self.assertTrue(arithmetic(trees));self.assertTrue(all(plan.site(op)is None for op in arithmetic(trees)))
        plan,trees=plan_source('macro op(x){quote{unquote(x)*6}};let k=7;op!(k)')
        self.assertEqual(plan.site(arithmetic(trees)[0]).result,42)

    def test_shared_syntax_in_different_scopes_never_uses_first_context_fact(self):
        shared=multiply(name('k'));raw=multiply(literal(2))
        tree=block(block(binding('k',literal(7)),shared,raw),block(binding('k',literal(8)),shared,raw))
        plan=build_plan(tree)
        self.assertIsNone(plan.site(shared));self.assertEqual(plan.site(raw).result,12)
        self.assertEqual(plan.info()['candidateSites'],1)

    def test_hostile_tags_attribute_keys_and_mapping_values_cannot_run_hooks(self):
        class Hostile:
            def __hash__(self):return hash('macro_bindings')
            def __eq__(self,other):raise AssertionError('host equality hook executed')
        class Mapping(dict):
            def items(self):raise AssertionError('mapping hook executed')
        def function():return Node(('fn',[],'Any',block(multiply(name('k'))),'<lambda>'))
        fn=function();fn.__dict__[Hostile()]='bad'
        tree=block(binding('k',literal(7)),fn,Node((Hostile(),literal(1))))
        plan=build_plan(tree);self.assertEqual(plan.info()['candidateSites'],0)
        fn=function();fn.macro_bindings=Mapping({'public':'internal'})
        plan=build_plan(block(binding('k',literal(7)),fn))
        self.assertEqual(plan.info()['candidateSites'],0)

    def test_cycles_deep_scopes_and_wide_input_share_the_original_hard_budget(self):
        cycle=[];cycle.append(cycle)
        self.assertTrue(build_plan(block(cycle)).info()['truncated'])
        tree=multiply(name('k'))
        for _ in range(90):tree=block(tree)
        self.assertTrue(build_plan(block(binding('k',literal(7)),tree)).info()['truncated'])
        wide=block(*(binding('v'+str(index),literal(index))for index in range(5000)))
        calls=[];plan=build_plan(wide,checkpoint=lambda:calls.append(None))
        self.assertTrue(plan.info()['truncated']);self.assertLessEqual(len(calls),MAX_NODES)
        self.assertLessEqual(plan.info()['scannedNodes'],MAX_NODES)
        class Stop(Exception):pass
        def stop():raise Stop()
        with self.assertRaises(Stop):build_plan(block(binding('k',literal(7)),multiply(name('k'))),checkpoint=stop)


if __name__=='__main__':unittest.main()
