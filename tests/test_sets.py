"""Native immutable Set is a strict pure value, independent of Record updates."""
import unittest
from chaos.language import Evaluator, LanguageError, Parser, serializable
from chaos.set_values import SetValue, SetBuilder, set_equal
from chaos.tuple_values import TupleValue


class SetTests(unittest.TestCase):
    def run_code(self, source, **options): return Evaluator(**options).run(source)

    def test_literal_empty_constructor_and_native_type(self):
        self.assertEqual(self.run_code('[type_of({1}),type_of({}),type_of({*[]}),type_of(set_of())]'), ['Set','Record','Set','Set'])
        self.assertEqual(self.run_code('[collect({3,1,3,2}),collect(set_of("aba"))]'), [[3,1,2],['a','b']])
        self.assertEqual(Parser('{}').program()[1][0][0], 'record')
        self.assertEqual(Parser('{*[]}').program()[1][0][0], 'set')

    def test_strict_scalar_keys_and_first_representative(self):
        self.assertEqual(self.run_code('length({true,1,1.0,false,0,0.0,null,"1"})'), 8)
        value=self.run_code('collect({-0.0,0.0})')
        self.assertEqual(len(value),1);self.assertEqual(value[0].hex(),'-0x0.0p+0')
        self.assertEqual(self.run_code('collect({"你好","é","é","你好"})'), ['你好','é','é'])

    def test_tuple_order_and_nested_set_unordered_identity(self):
        self.assertEqual(self.run_code('[length({(1,2),(2,1),(1,2)}),length({{1,2},{2,1}}),{(1,{2,3})}=={(1,{3,2})}]'), [2,1,True])
        self.assertFalse(self.run_code('{1,2}==[1,2]'))
        self.assertTrue(self.run_code('{1,2}=={2,1}'))

    def test_set_algebra_order_and_subset_comparisons(self):
        source='let a={3,1,2};let b={2,4,3};[collect(a|b),collect(a&b),collect(a-b),collect(a^b),{1}<{1,2},{1}<={1},{1,2}>{2},{1}>={1}]'
        self.assertEqual(self.run_code(source), [[3,1,2,4],[3,2],[1],[1,4],True,True,True,True])
        self.assertFalse(self.run_code('{1}<{1}'))
        self.assertFalse(self.run_code('{1,2}<={2,3}'))

    def test_functional_helpers_leave_previous_value_unchanged(self):
        source='let a={1,2};let b=set_add(a,3);let c=set_discard(b,2);[collect(a),collect(b),collect(c),set_is_disjoint(a,{3}),set_is_disjoint(a,{2})]'
        self.assertEqual(self.run_code(source), [[1,2],[1,2,3],[1,3],True,False])
        self.assertEqual(self.run_code('collect(set_discard({1,2},9))'), [1,2])

    def test_length_bool_membership_and_no_sequence_index(self):
        self.assertEqual(self.run_code('[length({1,2}),bool({*[]}),bool({1}),contains({1,2},2),contains({1},true)]'), [2,False,True,True,False])
        for source in ('{1}[0]','slice({1},0,1)','contains({1},[])','set_add([],1)'):
            with self.subTest(source=source),self.assertRaises(LanguageError): self.run_code(source)

    def test_typed_parameters_defaults_and_augmented_assignment(self):
        source='fn update(value:Set={1},/){value|{2}};var s:Set=update();s|={3};s-={1};collect(s)'
        self.assertEqual(self.run_code(source), [2,3])
        with self.assertRaises(LanguageError): self.run_code('var s:Set={1};s=[]')

    def test_set_iterates_in_functions_loops_and_delegation(self):
        source='fn sum3(a,b,c){a+b+c};var n=0;for x in {1,2,3}{n+=x};fn* stream(){yield from {3,1,2}};[sum3(*{1,2,3}),n,map(fn(x){x*2},{1,2}),collect(stream()),tuple({2,1})]'
        value=self.run_code(source)
        self.assertEqual(value[:4],[6,6,[2,4],[3,1,2]]);self.assertEqual(list(value[4]),[2,1])

    def test_literal_spread_evaluates_in_order_and_deduplicates(self):
        source='var calls=[];fn mark(n){calls=append(calls,n);n};let s={mark(1),*[mark(2),mark(1)],mark(3)};[collect(s),calls]'
        self.assertEqual(self.run_code(source), [[1,2,3],[1,2,1,3]])
        self.assertEqual(self.run_code('collect({*"aba",*(x for x in ["c","a"])})'), ['a','b','c'])

    def test_comprehensions_reuse_patterns_guards_scope_and_async(self):
        source='let x=99;let a={n%2 for [n] in [[1],[2],[3]] if n>0};async fn* rows(){yield 2;yield 3};let b={v*2 async for v in rows()};[collect(a),collect(b),x]'
        self.assertEqual(self.run_code(source), [[1,0],[4,6],99])
        self.assertEqual(self.run_code('class C{let x=42};collect({x for C::{x:x} in [C()]})'), [42])

    def test_record_syntax_old_set_and_reserved_labels_remain_unchanged(self):
        self.assertEqual(self.run_code('set({if:1,true:2},"added",42)'), {'if':1,'true':2,'added':42})
        self.assertEqual(self.run_code('{str(x):x for x in [1,2]}'), {'1':1,'2':2})
        self.assertEqual(self.run_code('try{throw 1}catch e{} {value:42}'), {'value':42})

    def test_invalid_spread_and_mixed_record_set_fail_before_effects(self):
        for expression in ('{**{}}','{*[] for x in []}','{1,a:2}','{a:1,2}','[*[]]','{*}'): 
            calls=[]
            with self.subTest(expression=expression),self.assertRaises(LanguageError):
                self.run_code('tool("before",0);'+expression,tool_handler=lambda *a:calls.append(a))
            self.assertEqual(calls,[])

    def test_invalid_elements_reject_without_user_protocols(self):
        source='var calls=0;class C{fn __eq__(s,x){calls+=1;true}fn __hash__(s){calls+=1;1}};let value={C()};[length(value),calls]'
        # Instances now use their explicit synchronous hash protocol; rejected containers remain hook-free.
        self.assertEqual(self.run_code(source), [1,1])
        for expression in ('{[]}','{{x:1}}','{([1],)}','{fn(){1}}','{slice_key()}'):
            with self.subTest(expression=expression),self.assertRaises(LanguageError): self.run_code(expression)

    def test_external_instance_comparison_protocol_is_preserved(self):
        source='var calls=0;class C{fn __eq__(s,x){calls+=1;true}};let c=C();let result=[{1}==c,c=={1},(1,)==c];let before=calls;let error=try{contains({1},c)}catch e{e.code};[result,before,calls,error]'
        self.assertEqual(self.run_code(source),[[True,True,True],3,3,'unhashable_type'])

    def test_set_spread_source_ranges_cover_star_and_operand(self):
        node=Parser('let values={1,*[2,3]}','set.chaos',ranges=True).program()[1][0][3][1][1]
        self.assertEqual(node[0],'spread');self.assertEqual(node.span['column'],15)
        self.assertEqual(node.span['endColumn'],21)

    def test_json_projection_is_inert_and_copy_keeps_native_type(self):
        source='let s={(1,2),{3,4}};[parse_json(json_stringify(s)),type_of(parse_json(json_stringify(s))),type_of(s)]'
        self.assertEqual(self.run_code(source), [{'$set':[{'$tuple':[1,2]},{'$set':[3,4]}]},'Record','Set'])
        value=SetValue([TupleValue([42])]);self.assertIs(Evaluator.copy_value(value),value)
        self.assertEqual(serializable(value),{'$set':[{'$tuple':[42]}]})

    def test_host_construction_is_deeply_immutable_and_validated(self):
        original=[1,2];value=SetValue(original);original.append(3)
        self.assertEqual(list(value),[1,2])
        with self.assertRaises(AttributeError):value.items=(9,)
        with self.assertRaises(TypeError):value._buckets[b'bad']=()
        with self.assertRaises(LanguageError):SetValue([[1]])
        with self.assertRaises(LanguageError):SetValue(iter([1]))
        self.assertFalse(value==SetValue([1,2]));self.assertTrue(set_equal(value,SetValue([2,1])))


if __name__=='__main__':unittest.main()
