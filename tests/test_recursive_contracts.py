"""Closed symbolic recursion uses the original pure Contract boundaries."""
import unittest
from dataclasses import FrozenInstanceError
from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, Native, TailCall, serializable
from chaos.contracts import (Contract, ref_contract, recursive_contract, list_contract, tuple_contract,
                             record_contract, union_contract, map_contract, accepts, require, info, validate_closed)
from chaos.session import Session

TREE='let Tree=contract_recursive({Node:contract_record({value:"Int",children:contract_list(contract_ref("Node"))})},"Node");'
VALID='{value:42,children:[{value:1,children:[]}]}'


class RecursiveContractTests(unittest.TestCase):
    def code(self,source,**options):return Evaluator(**options).run(source)

    def test_tree_accepts_and_reports_nested_shape(self):
        self.assertEqual(self.code(TREE+'[contract_accepts(Tree,'+VALID+'),contract_accepts(Tree,{value:42,children:[{value:"bad",children:[]}]}),contract_accepts(Tree,{value:42,children:[]})]'),[True,False,True])
        self.assertEqual(self.code(TREE+'try{let x:Tree={value:42,children:[{value:"bad",children:[]}]}}catch e{[e.code,contains(e.message,"children")]}'),['type_mismatch',True])

    def test_mutual_recursion_with_optional_and_union(self):
        self.assertEqual(self.code('''let Pair=contract_recursive({A:contract_record({b:contract_optional(contract_ref("B"))}),B:contract_record({a:contract_optional(contract_ref("A"))})},"A");
        [contract_accepts(Pair,{b:{a:{b:null}}}),contract_accepts(Pair,{a:null}),contract_accepts(Pair,{b:{a:42}})]'''),[True,False,False])

    def test_all_structural_guards_and_map_no_protocol_execution(self):
        self.assertEqual(self.code('''let L=contract_recursive({A:contract_list(contract_ref("A"))},"A");
        let T=contract_recursive({A:contract_union(["Null",contract_tuple([contract_ref("A")])])},"A");
        let R=contract_recursive({A:contract_record({},optional={next:contract_ref("A")})},"A");
        let M=contract_recursive({A:contract_map("String",contract_ref("A"))},"A");
        [contract_accepts(L,[[],[[]]]),contract_accepts(T,((null,),)),contract_accepts(R,{next:{}}),contract_accepts(M,hash_map([("next",hash_map())]))]'''),[True]*4)
        self.assertEqual(self.code('''var hashes=0;var equals=0;class Key{fn __hash__(s){hashes=hashes+1;1}fn __eq__(s,x){equals=equals+1;false}}
        let item=hash_map([(Key(),hash_map())]);let M=contract_recursive({Node:contract_map(Key,contract_ref("Node"))},"Node");
        let before=[hashes,equals];[contract_accepts(M,item),before,[hashes,equals]]'''),[True,[1,0],[1,0]])

    def test_open_templates_are_values_and_diagnostics_are_symbolic(self):
        self.assertEqual(self.code('''fn keep(x:Contract)->Contract{x};let ref=contract_ref(name="Node");let template=contract_list(ref);
        [type_of(keep(ref)),contract_info(ref),contract_info(template),keep(ref)==ref]'''),
        ['Contract',{'kind':'ref','name':'Node'},{'kind':'list','element':{'kind':'ref','name':'Node'}},True])
        result=self.code(TREE+'contract_info(Tree)')
        self.assertEqual(result['kind'],'recursive');self.assertEqual(result['root'],'Node')
        self.assertEqual(result['definitions']['Node']['required']['children']['element'],{'kind':'ref','name':'Node'})

    def test_open_references_rejected_before_any_matching(self):
        for expression in ['contract_accepts(R,42)','contract_accepts(contract_union(["Any",R]),42)',
                           'contract_accepts(contract_list(R),[])','contract_accepts(contract_record({},optional={x:R}),{})',
                           'contract_accepts(contract_map("String",R),hash_map())']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:self.code('let R=contract_ref("R");'+expression)
            self.assertEqual(caught.exception.code,'argument_error')

    def test_open_annotations_rejected_eagerly_at_declaration(self):
        declarations=['fn f(x:R){42};0','fn f()->R{42};0','async fn f()->R{42};0','fn* f()->R{yield 42};0',
                      'class C{var x:R=42};0','class_member(42,contract=R)','var x:R=42']
        for declaration in declarations:
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError) as caught:self.code('let R=contract_union(["Any",contract_ref("A")]);'+declaration)
            self.assertEqual(caught.exception.code,'argument_error')

    def test_unguarded_cycles_and_unused_invalid_definitions_rejected(self):
        definitions=['{A:contract_ref("A")}','{A:contract_ref("B"),B:contract_ref("A")}',
                     '{A:contract_union(["Int",contract_ref("A")])}',
                     '{A:contract_union([contract_list(contract_ref("A")),contract_ref("A")])}',
                     '{A:"Int",Unused:contract_ref("Missing")}',
                     '{A:"Int",Unused:contract_union(["Any",contract_ref("Unused")])}']
        for definitions in definitions:
            with self.subTest(definitions=definitions),self.assertRaises(LanguageError) as caught:self.code('contract_recursive('+definitions+',"A")')
            self.assertEqual(caught.exception.code,'argument_error')

    def test_alias_chain_into_a_guarded_cycle_is_valid(self):
        self.assertEqual(self.code('''let C=contract_recursive({A:contract_ref("B"),B:contract_list(contract_ref("A"))},"A");
        [contract_accepts(C,[[],[[]]]),contract_accepts(C,[42])]'''),[True,False])

    def test_same_template_bound_in_two_groups_has_separate_match_memo(self):
        self.assertEqual(self.code('''let shared=contract_list(contract_ref("Leaf"));
        let Ints=contract_recursive({Root:shared,Leaf:"Int"},"Root");
        let Strings=contract_recursive({Root:shared,Leaf:"String"},"Root");
        [contract_accepts(contract_union([Ints,Strings]),["ok"]),contract_accepts(Ints,["ok"]),contract_info(shared)]'''),
        [True,False,{'kind':'list','element':{'kind':'ref','name':'Leaf'}}])

    def test_nested_groups_keep_own_binding_and_do_not_capture_outer(self):
        self.assertEqual(self.code('''let Inner=contract_recursive({Node:"String"},"Node");
        let Outer=contract_recursive({Node:contract_record({item:Inner,children:contract_list(contract_ref("Node"))})},"Node");
        [contract_accepts(Outer,{item:"ok",children:[]}),contract_accepts(Outer,{item:42,children:[]})]'''),[True,False])
        with self.assertRaises(LanguageError):self.code('contract_recursive({Node:"Int",Inner:contract_recursive({Other:contract_ref("Node")},"Other")},"Node")')

    def test_nominal_class_and_data_leaves_remain_actual(self):
        self.assertEqual(self.code('''var effects=0;class Meta(Type){fn __instancecheck__(s,x){effects=effects+1;true}}
        class Base(metaclass=Meta){}class Child(Base){}let Other=new_class("Base",[],{});data Maybe{Yes(x),No};
        let C=contract_recursive({Node:contract_union([Base,Maybe,contract_list(contract_ref("Node"))])},"Node");
        [contract_accepts(C,[Child(),[Yes(42),No()]]),contract_accepts(C,Other()),effects]'''),[True,False,0])

    def test_parameter_default_return_and_original_value_identity(self):
        result=self.code(TREE+'fn keep(x:Tree='+VALID+')->Tree{x};let x='+VALID+';[x,keep(x),keep()]')
        self.assertIs(result[0],result[1]);self.assertEqual(result[2],result[0])
        for expression in ['fn f(x:Tree={value:"bad",children:[]}){x};0','fn f()->Tree{{value:"bad",children:[]}};f()','fn f(x:Tree){x};f({value:1})']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:self.code(TREE+expression)
            self.assertEqual(caught.exception.code,'type_mismatch')

    def test_atomic_cells_destructuring_and_class_fields(self):
        self.assertEqual(self.code(TREE+'var a:Tree={value:1,children:[]};var b:Tree={value:2,children:[]};let failed=try{(a,b)=({value:3,children:[]},{value:"bad",children:[]})}catch e{e.code};class C{var tree:Tree={value:42,children:[]}};let c=C();let field=try{c.tree={value:0}}catch e{e.code};[a.value,b.value,c.tree.value,failed,field]'),[1,2,42,'type_mismatch','type_mismatch'])

    def test_tail_calls_keep_recursive_contract_identity_and_validation(self):
        self.assertEqual(self.code(TREE+'fn loop(n,x:Tree)->Tree{if n==0{x}else{loop(n-1,x)}};loop(1500,{value:42,children:[]}).value',step_limit=1500000),42)

    def test_async_and_generator_check_timing_preserved(self):
        self.assertEqual(self.code(TREE+'''var calls=0;async fn work(x:Tree)->Tree{calls=calls+1;x};
        let failure=try{work({value:"bad",children:[]})}catch e{e.code};let t=work({value:42,children:[]});let before=calls;
        fn* stream()->Tree{yield "unconstrained";return {value:42,children:[]}};let g=stream();
        [failure,before,(await t).value,calls,next(g).value,next(g).value.value]'''),['type_mismatch',0,42,1,'unconstrained',42])
        self.assertEqual(self.code(TREE+'async fn bad()->Tree{{value:"bad",children:[]}};let t=bad();[task_info(t).state,try{await t}catch e{e.code}]'),['created','type_mismatch'])

    def test_code_module_contract_bindings_remain_native(self):
        self.assertEqual(self.code(TREE+'let f=function_from(code_from("fn(x:Tree)->Tree{x}"),{Tree:Tree});[f('+VALID+').value,try{f({value:"bad",children:[]})}catch e{e.code}]'),[42,'type_mismatch'])

    def test_ref_names_and_definition_domains_are_exact_and_bounded(self):
        ev=Evaluator()
        for name in ['',129*'x',1,True,None]:
            with self.subTest(name=type(name).__name__),self.assertRaises(LanguageError):ref_contract(ev,name)
        for definitions,root in [({},'A'),([], 'A'),({'A':'Int'},'B'),({i:'Int' for i in range(129)},'A'),({'':'Int'},''),({'A':1},'A')]:
            with self.subTest(root=root),self.assertRaises(LanguageError):recursive_contract(ev,definitions,root)
        self.assertEqual(info(ev,ref_contract(ev,'space and λ'))['name'],'space and λ')

    def test_group_snapshot_immutable_diagnostics_never_expose_nominal(self):
        ev=Evaluator();definitions={'A':'Int'};group=recursive_contract(ev,definitions,'A');definitions['A']='String'
        self.assertTrue(accepts(ev,group,42));self.assertFalse(accepts(ev,group,'x'))
        with self.assertRaises(FrozenInstanceError):group.arguments=()
        self.assertEqual(serializable(group),{'$contract':{'kind':'recursive','definitions':{'A':{'kind':'builtin','name':'Int'}},'root':'A'}})
        self.assertEqual(self.code(TREE+'try{contract_accepts(parse_json(json_stringify(Tree)),'+VALID+')}catch e{e.code}'),'argument_error')

    def test_foreign_group_refs_and_unused_nominal_leaves_rejected(self):
        ev=Evaluator();other=Evaluator();ref=ref_contract(other,'A');group=recursive_contract(other,{'A':'Int'},'A');cls=other.run('class Foreign{};Foreign')
        for definitions in [{'A':ref},{'A':group},{'A':'Int','Unused':cls}]:
            with self.subTest(definitions=list(definitions)),self.assertRaises(LanguageError) as caught:recursive_contract(ev,definitions,'A')
            self.assertEqual(caught.exception.code,'ownership_error')
        local=recursive_contract(ev,{'A':'Any'},'A')
        with self.assertRaises(LanguageError):accepts(ev,local,[cls])

    def test_cyclic_data_fails_and_shared_dag_succeeds(self):
        ev=Evaluator(step_limit=1000000);group=recursive_contract(ev,{'A':list_contract(ev,ref_contract(ev,'A'))},'A')
        child=[];self.assertTrue(accepts(ev,group,[child,child]))
        cycle=[];cycle.append(cycle)
        with self.assertRaises(LanguageError) as caught:accepts(ev,group,cycle)
        self.assertEqual(caught.exception.code,'argument_error')
        any_group=recursive_contract(ev,{'A':'Any'},'A')
        with self.assertRaises(LanguageError) as caught:accepts(ev,any_group,cycle)
        self.assertEqual(caught.exception.code,'argument_error')

    def test_opaque_map_cycles_are_rejected_before_any_branch(self):
        from chaos.hash_maps import make
        ev=Evaluator();group=recursive_contract(ev,{'A':'Any'},'A')
        mapping=make(ev);object.__setattr__(mapping,'entries',(('self',mapping,0),))
        with self.assertRaises(LanguageError) as caught:accepts(ev,group,mapping)
        self.assertEqual(caught.exception.code,'argument_error')
        mapping=make(ev);items=[mapping];object.__setattr__(mapping,'entries',(('self',items,0),))
        with self.assertRaises(LanguageError) as caught:accepts(ev,group,mapping)
        self.assertEqual(caught.exception.code,'argument_error')
        shared=make(ev);self.assertTrue(accepts(ev,group,[shared,shared]))

    def test_record_tuple_cycles_and_nominal_opaque_boundary(self):
        from chaos.tuple_values import TupleValue
        ev=Evaluator();group=recursive_contract(ev,{'A':'Any'},'A')
        record={};record['self']=record
        items=[];pair=TupleValue((items,));items.append(pair)
        for value in (record,pair):
            with self.subTest(kind=type(value).__name__),self.assertRaises(LanguageError) as caught:accepts(ev,group,value)
            self.assertEqual(caught.exception.code,'argument_error')
        self.assertTrue(self.code('class C{}let c=C();c.self=c;contract_accepts(contract_recursive({Root:C},"Root"),c)'))

    def test_malformed_host_graphs_fail_without_string_or_equality_hooks(self):
        class Host(str):
            def __hash__(self):raise AssertionError('host hash')
            def __eq__(self,other):raise AssertionError('host equality')
            def __str__(self):raise AssertionError('host string')
        ev=Evaluator()
        bad=[Contract(ev,'ref',(Host('A'),)),Contract(ev,'recursive',(((Host('A'),'Int'),),'A')),
             Contract(ev,'recursive',((('A','Int'),('A','String')),'A')),Contract(ev,'recursive',([], 'A'))]
        for value in bad:
            with self.subTest(kind=value.kind),self.assertRaises(LanguageError):info(ev,value)
        cyclic=Contract(ev,'list',());object.__setattr__(cyclic,'arguments',(cyclic,))
        with self.assertRaises(LanguageError):recursive_contract(ev,{'A':cyclic},'A')

    def test_shared_budgets_cover_aliases_branches_and_diagnostics(self):
        ev=Evaluator(step_limit=1000000)
        definitions={'A'+str(i):ref_contract(ev,'A'+str(i+1)) for i in range(127)};definitions['A127']='Int'
        long=recursive_contract(ev,definitions,'A0')
        with self.assertRaises(ResourceLimit):accepts(ev,long,42)
        body=record_contract(ev,{'x'*2000:'Int'});group=recursive_contract(ev,{'A'+str(i):body for i in range(128)},'A0')
        with self.assertRaises(ResourceLimit):info(ev,group)

    def test_ownership_of_epoch_values_precedes_any_union_success(self):
        session=Session();session.evaluate('let C=contract_recursive({A:contract_union(["Any",contract_list(contract_ref("A"))])},"A");let old=(async fn(){42})()')
        with self.assertRaises(LanguageError):session.evaluate('contract_accepts(C,[old])')

    def test_cancel_does_not_become_false_or_invalid_contract(self):
        ev=Evaluator();group=recursive_contract(ev,{'A':list_contract(ev,ref_contract(ev,'A'))},'A')
        ev.tick=lambda:(_ for _ in ()).throw(ExecutionControl('cancel'))
        for action in [lambda:accepts(ev,group,[]),lambda:info(ev,group),lambda:validate_closed(ev,group)]:
            with self.assertRaises(ExecutionControl):action()

    def test_recursive_nominal_capture_edges_seal_hidden_next(self):
        from chaos.objects import ChaosClass,Member
        from chaos.generic_functions import NextDescriptor
        from chaos.capture_graph import seal
        ev=Evaluator();token=NextDescriptor(ev,None);native=Native('next',lambda:None,ev,generic=token)
        cls=ChaosClass('Carrier',(),ev,building=False);cls.mro=(cls,);cls.members['next']=Member(native,'Any',True,False,cls)
        group=recursive_contract(ev,{'Root':'Int','Unused':cls},'Root')
        seal(ev,[group],frozenset());self.assertEqual(token.capabilities,frozenset())

    def test_generic_specificity_still_rejects_composite_contracts(self):
        self.assertEqual(self.code(TREE+'let f=generic_function("f");try{generic_method(f,[Tree],fn(x){42})}catch e{e.code}'),'argument_error')


if __name__=='__main__':unittest.main()
