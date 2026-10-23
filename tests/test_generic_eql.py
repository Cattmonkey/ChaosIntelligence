"""Actual-value method keys share dispatch, authority and native value boundaries."""
import unittest
from dataclasses import replace
from chaos.language import Evaluator, LanguageError, ResourceLimit, Native, ExecutionControl, serializable
from chaos.eql_specializers import create, info, project, EqlSpecializer, equal
from chaos.generic_functions import create as generic, extend, remove, methods, NextDescriptor
from chaos.numeric_values import Fraction, Complex
from chaos.decimal_values import make as decimal
from chaos.agent_tools import scope
from chaos.class_creation import validate_value, project_value
from chaos.indexing import validate_owned
from chaos.capture_graph import seal
from chaos.debugger import preview


class GenericEqlTests(unittest.TestCase):
    def run_code(self,source,**kwargs):return Evaluator(**kwargs).run(source)

    def test_scalar_type_sensitive_dispatch_and_numeric_normalization(self):
        source='''let base=generic_method(generic_function("g"),["Any"],fn(x){"base"});
        let ints=generic_method(base,[generic_eql(1)],fn(x){"int"});
        let bools=generic_method(ints,[generic_eql(true)],fn(x){"bool"});
        let nums=generic_method(bools,[generic_eql(1.0)],fn(x){"number"});
        let strings=generic_method(nums,[generic_eql("a"+"b")],fn(x){"string"});
        [strings(1),strings(true),strings(1.0),strings("ab"),strings(null)]'''
        self.assertEqual(self.run_code(source),['int','bool','number','string','base'])
        pairs=[(0.0,-0.0),(Fraction(2,4),Fraction(1,2)),(decimal('1.0'),decimal('1.00')),(decimal('-0.0'),decimal('0')),(Complex(1,2),Complex(1,2))]
        for left,right in pairs:self.assertTrue(equal(left,right))
        for left,right in [(1,1.0),(False,0),(Fraction(1),1),(Complex(1,0),1),(Complex(1,0),Complex(1.0,0.0))]:self.assertFalse(equal(left,right))

    def test_eql_precedes_class_and_builtin_primary_next(self):
        self.assertEqual(self.run_code('''class C{};let c=C();let other=C();
        let a=generic_method(generic_function("f"),["Any"],fn(x){"any"});
        let b=generic_method(a,[C],fn(next,x){"class/"+next()},with_next=true);
        let d=generic_method(b,[generic_eql(c)],fn(next,x){"exact/"+next()},with_next=true);
        [d(c),d(other)]'''),['exact/class/any','class/any'])

    def test_left_to_right_precedence_is_unchanged(self):
        self.assertEqual(self.run_code('''let a=generic_method(generic_function("f",2),["Int",generic_eql(2)],fn(a,b){"second"});
        let b=generic_method(a,[generic_eql(1),"Any"],fn(a,b){"first"});b(1,2)'''),'first')

    def test_persistent_equivalent_replacement_and_removal(self):
        self.assertEqual(self.run_code('''let a=generic_method(generic_function("f"),[generic_eql(decimal("1.00"))],fn(x){1});
        let b=generic_method(a,[generic_eql(decimal("1"))],fn(x){2});
        let c=generic_remove(b,[generic_eql(decimal("1.0"))]);
        [a(decimal("1")),b(decimal("1")),generic_info(b).methodCount,generic_info(c).methodCount]'''),[1,2,1,0])

    def test_containers_tuple_set_and_function_use_identity(self):
        self.assertEqual(self.run_code('''let list=[1];let tup=(1,);let set=set_of([1]);let fun=fn(){1};
        let a=generic_method(generic_function("f"),["Any"],fn(x){false});
        let b=generic_method(a,[generic_eql(list)],fn(x){true});let c=generic_method(b,[generic_eql(tup)],fn(x){true});
        let d=generic_method(c,[generic_eql(set)],fn(x){true});let e=generic_method(d,[generic_eql(fun)],fn(x){true});
        [e(list),e([1]),e(tup),e((1,)),e(set),e(set_of([1])),e(fun),e(fn(){1})]'''),[True,False,True,False,True,False,True,False])

    def test_mutable_instance_identity_no_hooks(self):
        effects=[]
        result=self.run_code('''class C{var value=0;fn __eq__(self,other){tool("trap",null);true};fn __hash__(self){tool("trap",null);0};fn __getattr__(self,name){tool("trap",null);null}};
        let c=C();let a=generic_method(generic_function("f"),["Any"],fn(x){0});
        let b=generic_method(a,[generic_eql(c)],fn(x){x.value});c.value=42;
        [b(c),b(C()),generic_eql_info(generic_eql(c)).comparison]''',tool_handler=lambda *args:effects.append(args))
        self.assertEqual(result,[42,0,'identity']);self.assertEqual(effects,[])

    def test_auxiliary_order_and_cold_returns_keep_original_semantics(self):
        self.assertEqual(self.run_code('''var log=[];fn note(x){log=append(log,x);null};
        let a=generic_method(generic_function("f"),["Int"],async fn(x){note("body");x});
        let b=generic_method(a,[generic_eql(42)],fn(x){note("before")},"before");
        let c=generic_method(b,[generic_eql(42)],fn(x){note("after")},"after");
        let task=c(42);let first=log;let result=await task;[first,log,result]'''),[['before','after'],['before','after','body'],42])

    def test_generator_and_async_generator_returns_are_not_consumed(self):
        self.assertEqual(self.run_code('''let a=generic_method(generic_function("f"),[generic_eql(1)],fn*(x){yield x});
        let b=generic_method(a,[generic_eql(2)],async fn*(x){yield x});[collect(a(1)),await acollect(b(2))]'''),[[1],[2]])

    def test_actual_descriptor_type_annotations_and_composite_contracts(self):
        self.assertEqual(self.run_code('''fn use(x:EqlSpecializer)->EqlSpecializer{x};let s=use(generic_eql(42));
        [type_of(s),contract_accepts(contract_list("EqlSpecializer"),[s]),generic_eql_info(s)]'''),['EqlSpecializer',True,{'kind':'eql','comparison':'value','valueType':'Int'}])

    def test_opaque_fields_json_and_debug_preview(self):
        e=Evaluator();s=create(e,{'secret':'do not expose'})
        self.assertEqual(serializable(s),{'$eqlSpecializer':{'kind':'eql','comparison':'identity','valueType':'Record'}})
        self.assertNotIn('secret',str(preview(s)));self.assertNotIn('payload',str(preview(s)))
        for source in ['let s=generic_eql(42);s.payload','generic_eql_info(parse_json(json_stringify(generic_eql(42))))']:
            with self.assertRaises(LanguageError):self.run_code(source)
        self.assertEqual(info(e,s)['valueType'],'Record')

    def test_host_and_subclasses_are_rejected_without_protocols(self):
        class Host:
            def __eq__(self,other):raise AssertionError('host eq')
            def __hash__(self):raise AssertionError('host hash')
        class Int(int):pass
        for value in [Host(),Int(42)]:
            with self.assertRaises(LanguageError):create(Evaluator(),value)

    def test_foreign_ownership_in_payload_and_method_apis(self):
        e=Evaluator();foreign=create(Evaluator(),42)
        with self.assertRaises(LanguageError):create(e,[foreign])
        with self.assertRaises(LanguageError):info(e,foreign)
        with self.assertRaises(LanguageError):validate_value(e,{'s':foreign})
        from chaos.tuple_values import TupleValue
        with self.assertRaises(LanguageError):validate_owned(e,TupleValue((foreign,)))

    def test_resource_epoch_rechecked_by_info_dispatch_remove_and_methods(self):
        e=Evaluator();s,f=e.run('let s=generic_eql((async fn(){42})());let f=generic_method(generic_function("f"),[s],fn(x){42});[s,f]')
        e.run('null')
        for action in [lambda:info(e,s),lambda:remove(e,f,[create(e,42)]),lambda:methods(e,f),lambda:e.invoke(f,[42])]:
            with self.assertRaises(LanguageError) as caught:action()
            self.assertIn('Task/Generator',str(caught.exception))

    def test_plain_data_survives_same_evaluator_epoch(self):
        e=Evaluator();s=e.run('generic_eql(42)');e.run('null');self.assertEqual(info(e,s)['valueType'],'Int')

    def test_projection_hides_value_and_preserves_match_identity(self):
        e=Evaluator();value=e.run('class C{};C()');s=create(e,value)
        limited=Native('limited',lambda value:project_value(e,value),e,capabilities=frozenset())
        narrow=e.run('limited(s)',{'limited':limited,'s':s})
        self.assertIs(narrow.payload,value);self.assertEqual(narrow.capabilities,frozenset());self.assertIsNone(s.capabilities)
        f=extend(e,generic(e,'f'),[narrow],Native('answer',lambda x:42,e));self.assertEqual(e.invoke(f,[value]),42)

    def test_isolated_hidden_next_capture_seals_original_token(self):
        e=Evaluator();token=NextDescriptor(e,None);n=Native('next',lambda:None,e,generic=token)
        s=create(e,n);seal(e,[s],frozenset());self.assertEqual(token.capabilities,frozenset())

    def test_nested_capture_paths_merge_caps_and_failure_is_atomic(self):
        e=Evaluator();token=NextDescriptor(e,None);n=Native('next',lambda:None,e,generic=token);s=create(e,n)
        narrow=replace(s,capabilities=frozenset());seal(e,[s,narrow],None);self.assertEqual(token.capabilities,frozenset())
        other=NextDescriptor(e,None);wrapped=create(e,Native('next',lambda:None,e,generic=other));foreign=create(Evaluator(),42)
        with self.assertRaises(LanguageError):seal(e,[foreign,wrapped],frozenset())
        self.assertIsNone(other.capabilities)

    def test_payload_budgets_cancellation_and_nonfinite(self):
        e=Evaluator();s=create(e,[]);s.payload.extend(range(10001))
        with self.assertRaises(ResourceLimit):info(e,s)
        s=create(e,[]);s.payload.append('x'*250001)
        with self.assertRaises(ResourceLimit):info(e,s)
        with self.assertRaises(LanguageError):create(e,float('nan'))
        e.tick=lambda:(_ for _ in ()).throw(ExecutionControl('cancel'))
        with self.assertRaises(ExecutionControl):info(e,s)

    def test_selector_wrappers_are_not_implicitly_structural_or_hashable(self):
        self.assertEqual(self.run_code('let a=generic_eql(42);[a==a,a==generic_eql(42)]'),[True,False])
        with self.assertRaises(LanguageError) as caught:self.run_code('hash(generic_eql(42))')
        self.assertEqual(caught.exception.code,'unhashable_type')

    def test_std_generic_exports_and_method_records_keep_actual_values(self):
        self.assertEqual(self.run_code('''import "std/generic" as G;let s=G.generic_eql(42);
        let g=G.generic_method(G.generic_function("f"),[s],fn(x){42});
        let actual=G.generic_methods(g)[0].specializers[0];[actual==s,G.generic_eql_info(actual).kind]'''),[True,'eql'])

if __name__=='__main__':unittest.main()
