"""Composite contracts share existing annotated language value boundaries."""
import unittest
from dataclasses import FrozenInstanceError

from chaos.language import Evaluator, LanguageError, ResourceLimit, ExecutionControl, Native, TailCall, check_type, serializable
from chaos.session import Session
from chaos.tuple_values import TupleValue


class ContractTests(unittest.TestCase):
    def code(self, source, **options): return Evaluator(**options).run(source)

    def test_constructor_shapes_and_explicit_keyword_api(self):
        self.assertEqual(self.code('''let ints=contract_list(element="Int");let pair=contract_tuple(elements=["String",ints]);
        let item=contract_record(required={name:"String"},optional={value:contract_optional(contract="Int")},allow_extra=false);
        let options=contract_union(alternatives=[pair,item]);let table=contract_map(key="String",value=options);
        [type_of(ints),contract_info(contract=ints),contract_accepts(contract=pair,value=("a",[1,2])),
         contract_accepts(item,{name:"a",value:null}),contract_accepts(table,hash_map([("x",{name:"a"})]))]'''),
        ['Contract',{'kind':'list','element':{'kind':'builtin','name':'Int'}},True,True,True])

    def test_lists_are_not_tuples_and_fixed_tuple_arity_is_enforced(self):
        self.assertEqual(self.code('''let nums=contract_list("Int");let pair=contract_tuple(["Int","String"]);
        [contract_accepts(nums,[]),contract_accepts(nums,[1,2]),contract_accepts(nums,[true]),
         contract_accepts(nums,(1,2)),contract_accepts(pair,(1,"x")),contract_accepts(pair,[1,"x"]),
         contract_accepts(pair,(1,)),contract_accepts(contract_tuple([]),())]'''),[True,True,False,False,True,False,False,True])

    def test_record_required_optional_and_extra_fields_are_explicit(self):
        self.assertEqual(self.code('''let row=contract_record({name:"String"},{age:"Int"});let open=contract_record({name:"String"},allow_extra=true);
        [contract_accepts(row,{name:"a"}),contract_accepts(row,{name:"a",age:2}),contract_accepts(row,{age:2}),
         contract_accepts(row,{name:"a",age:null}),contract_accepts(row,{name:"a",extra:1}),
         contract_accepts(open,{name:"a",extra:1}),contract_accepts(contract_optional(row),null)]'''),
        [True,True,False,False,False,True,True])

    def test_union_retries_only_mismatches_and_optional_is_union(self):
        self.assertEqual(self.code('''let row=contract_record({n:"Int"});let both=contract_union([contract_list("Int"),row]);
        [contract_accepts(both,[1]),contract_accepts(both,{n:2}),contract_accepts(both,{n:"bad"}),
         contract_info(contract_optional("Int"))]'''),[True,True,False,{'kind':'union','alternatives':[{'kind':'builtin','name':'Int'},{'kind':'builtin','name':'Null'}]}])

    def test_existing_leaf_designators_are_actual_values(self):
        self.assertEqual(self.code('''class Base{}class Child(Base){};let alias=Base;let other=new_class("Base",[],{});
        data Choice{Yes(value),No};let choices=contract_list(Choice);
        [contract_accepts("Int",1),contract_accepts("Int",true),contract_accepts(alias,Child()),
         contract_accepts(alias,other()),contract_accepts(choices,[Yes(1),No()]),contract_info(alias)]'''),
        [True,False,True,False,True,{'kind':'class','name':'Base'}])

    def test_parameters_returns_and_defaults_preserve_original_values(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");fn keep(xs:Numbers=[42])->Numbers{xs};
        let xs=[1,2];[keep(),keep(xs),try{keep(["bad"])}catch TypeError::{code:c}{c}]'''),[[42],[1,2],'type_mismatch'])
        for source in ['let Numbers=contract_list("Int");fn f(xs:Numbers=["bad"]){xs};0',
                       'let Numbers=contract_list("Int");fn f()->Numbers{["bad"]};f()']:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:self.code(source)
            self.assertEqual(caught.exception.code,'type_mismatch')
        from chaos.contracts import list_contract,require
        ev=Evaluator();spec=list_contract(ev,'Int');value=[1,2]
        self.assertIs(require(ev,spec,value),value)

    def test_cells_and_destructuring_preflight_are_atomic(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");var a:Numbers=[1];var b:Numbers=[2];
        let code=try{(a,b)=([3],["bad"])}catch e{e.code};[a,b,code]'''),[[1],[2],'type_mismatch'])
        self.assertEqual(self.code('''let Numbers=contract_list("Int");var xs:Numbers=[1];let code=try{xs=["bad"]}catch e{e.code};[xs,code]'''),[[1],'type_mismatch'])

    def test_class_fields_and_dynamic_class_members_use_same_contract(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");class Box{var values:Numbers=[1]};let box=Box();
        let failure=try{box.values=["bad"]}catch e{e.code};let C=new_class("C",[],{values:class_member([42],mutable=true,contract=Numbers)});
        [box.values,failure,C().values,class_members(C).values.contract==Numbers,contract_info(class_members(C).values.contract).kind]'''),[[1],'type_mismatch',[42],True,'list'])

    def test_tail_calls_keep_identity_hashable_required_contracts(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");fn loop(n:Int,xs:Numbers)->Numbers{if n==0{xs}else{loop(n-1,xs)}};loop(1500,[42])''',step_limit=500000),[42])
        self.assertEqual(self.code('''let Numbers=contract_list("Int");fn bad(){["bad"]};fn good()->Numbers{bad()};try{good()}catch e{e.code}'''),'type_mismatch')

    def test_async_parameters_check_eagerly_returns_check_on_await(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");var ran=0;async fn task(xs:Numbers)->Numbers{ran=ran+1;xs};
        let code=try{task(["bad"])}catch e{e.code};let t=task([42]);let before=ran;
        [code,before,task_info(t).state,await t,ran]'''),['type_mismatch',0,'created',[42],1])
        self.assertEqual(self.code('''let Numbers=contract_list("Int");async fn bad()->Numbers{["bad"]};let t=bad();
        [task_info(t).state,try{await t}catch e{e.code}]'''),['created','type_mismatch'])

    def test_generator_return_contracts_do_not_constrain_yields(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");fn* f()->Numbers{yield "yielded";return [42]};
        let g=f();[next(g).value,next(g).value]'''),['yielded',[42]])
        self.assertEqual(self.code('''let Numbers=contract_list("Int");async fn* f()->Numbers{yield "yielded";return ["bad"]};
        let g=f();let first=await anext(g);[first.value,try{await anext(g)}catch e{e.code}]'''),['yielded','type_mismatch'])

    def test_source_generated_function_binding_preserves_contract_identity(self):
        self.assertEqual(self.code('''let Numbers=contract_list("Int");let body=code_from("fn(xs:Numbers)->Numbers{xs}");
        let f=function_from(body,{Numbers:Numbers});[f([42]),try{f([\"bad\"])}catch e{e.code}]'''),[[42],'type_mismatch'])

    def test_map_checking_never_hashes_compares_or_projects_keys(self):
        self.assertEqual(self.code('''var hashes=0;var equals=0;class Key{fn __hash__(self){hashes=hashes+1;1}fn __eq__(self,x){equals=equals+1;false}}
        let key=Key();let m=hash_map([(key,[42])]);let before=[hashes,equals];let Shape=contract_map(Key,contract_list("Int"));
        [contract_accepts(Shape,m),before,[hashes,equals],contract_accepts(Shape,hash_map()),contract_accepts(Shape,{x:[42]})]'''),
        [True,[1,0],[1,0],True,False])

    def test_class_and_record_checks_do_not_execute_metaclasses_descriptors(self):
        self.assertEqual(self.code('''var effects=0;class Meta(Type){fn __instancecheck__(self,x){effects=effects+1;true}}
        class Base(metaclass=Meta){}class Child(Base){};class Obj{fn __getitem__(self,k){effects=effects+1;42}
        fn __iter__(self){effects=effects+1;self}let value=property(fn(self){effects=effects+1;42})};
        let list=contract_list(Base);let record=contract_record({value:"Int"});
        [contract_accepts(list,[Child()]),contract_accepts(record,Obj()),effects]'''),[True,False,0])

    def test_descriptor_is_frozen_identity_value_and_info_is_inert(self):
        ev=Evaluator();spec=ev.run('contract_record({values:contract_list("Int")})')
        from chaos.contracts import Contract
        self.assertIs(type(spec),Contract)
        with self.assertRaises(FrozenInstanceError):spec.kind='list'
        self.assertEqual(serializable(spec),{'$contract':{'kind':'record','required':{'values':{'kind':'list','element':{'kind':'builtin','name':'Int'}}},'optional':{},'allowExtra':False}})
        self.assertEqual(self.code('let c=contract_list("Int");[c==c,c==contract_list("Int"),type_of(c)]'),[True,False,'Contract'])
        for field in ['owner','arguments','kind']:
            with self.subTest(field=field),self.assertRaises(LanguageError):self.code('let c=contract_list("Int");getattr(c,"'+field+'")')
        self.assertEqual(self.code('try{contract_accepts(parse_json(json_stringify(contract_list("Int"))),[1])}catch e{e.code}'),'argument_error')

    def test_info_cannot_expose_nominal_classes_through_narrow_scope(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''class Secret{fn __init__(self){tool("secret",0)}};let c=contract_list(Secret);
        let saved=limited(fn(){c});[contract_info(saved),limited(fn(){contract_accepts(saved,[])})]'''),
        [{'kind':'list','element':{'kind':'class','name':'Secret'}},True])
        self.assertEqual(calls,[])

    def test_foreign_contracts_and_nested_values_are_never_mismatch_false(self):
        foreign=Evaluator().run('contract_list("Int")')
        for expression in ['contract_list(foreign)','contract_accepts(foreign,[1])','contract_info(foreign)','let x:foreign=[1]']:
            ev=Evaluator();ev.env.bind('foreign',foreign)
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:ev.run(expression)
            self.assertEqual(caught.exception.code,'ownership_error')
        foreign_value=Evaluator().run('class C{};C()')
        for expression in ['contract_accepts("Any",[foreign])','contract_accepts(contract_optional("Any"),foreign)',
                           'contract_accepts(contract_union(["Any",contract_list("Int")]),[foreign])']:
            ev=Evaluator();ev.env.bind('foreign',foreign_value)
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:ev.run(expression)
            self.assertEqual(caught.exception.code,'ownership_error')
        from chaos.hash_maps import make
        ev=Evaluator();wrapped=make(ev,((1,foreign_value,0),));ev.env.bind('wrapped',wrapped)
        with self.assertRaises(LanguageError) as caught:ev.run('contract_accepts(contract_optional("Any"),wrapped)')
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_epoch_handles_validate_before_any_or_optional(self):
        session=Session();session.evaluate('let AnyValue=contract_optional("Any");async fn f(){42};let t=f()')
        for source in ['contract_accepts(AnyValue,t)','contract_accepts("Any",[t])']:
            with self.subTest(source=source),self.assertRaises(LanguageError):session.evaluate(source)

    def test_constructor_validation_is_explicit_and_bounded(self):
        for expression in ['contract_list("Missing")','contract_tuple(("Int",))','contract_tuple(1)',
                           'contract_union([])','contract_union(("Int",))','contract_record({x:"Int"},{x:"String"})',
                           'contract_record([],null)','contract_record({},allow_extra=1)','contract_map("Int",42)',
                           'contract_accepts(42,1)','contract_info({kind:"list",element:"Int"})']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError):self.code(expression)
        self.assertEqual(self.code('''let inputs=["Int"];let c=contract_tuple(inputs);let changed=append(inputs,"String");
        [contract_accepts(c,(1,)),contract_accepts(c,(1,"x"))]'''),[True,False])

    def test_generic_dispatch_rejects_composite_specificity_but_types_descriptor(self):
        self.assertEqual(self.code('''let c=contract_list("Int");let f=generic_function("g");
        let code=try{generic_method(f,[c],fn(x){42})}catch e{e.code};let g=generic_method(f,["Contract"],fn(x){42});
        [code,g(c)]'''),['argument_error',42])

    def test_resource_limits_and_host_cyclic_descriptors_are_not_swallowed(self):
        from chaos.contracts import Contract,accepts,union_contract,list_contract
        ev=Evaluator(step_limit=1000000)
        cyclic=Contract(ev,'list',())
        object.__setattr__(cyclic,'arguments',(cyclic,))
        with self.assertRaises(LanguageError) as caught:accepts(ev,cyclic,[])
        self.assertEqual(caught.exception.code,'argument_error')
        nested='Int'
        with self.assertRaises(ResourceLimit):
            for _ in range(102):nested=list_contract(ev,nested)
        # Every failed branch consumes the same matching budget; it is not reset.
        alternatives=[]
        for n in range(128):
            from chaos.contracts import record_contract
            alternatives.append(record_contract(ev,{'match':list_contract(ev,'Int'),'missing'+str(n):'Int'}))
        spec=union_contract(ev,alternatives)
        with self.assertRaises(ResourceLimit):accepts(ev,spec,{'match':list(range(300))})
        # Opaque descriptors inside checked values also share the same budget.
        descriptors=[Contract(ev,'tuple',tuple(['Int']*10000)) for _ in range(2)]
        with self.assertRaises(ResourceLimit):accepts(ev,'Any',descriptors)

    def test_diagnostics_bound_repeated_dag_names_and_error_paths(self):
        from chaos.contracts import record_contract,tuple_contract,info,require
        ev=Evaluator(step_limit=1000000)
        leaf=record_contract(ev,{'x'*2000:'Int'})
        shared=tuple_contract(ev,[leaf]*128)
        with self.assertRaises(ResourceLimit) as caught:info(ev,shared)
        self.assertIn('diagnostic output',caught.exception.message)
        long_name='x'*150000
        spec=record_contract(ev,{long_name:'Int'})
        with self.assertRaises(LanguageError) as caught:require(ev,spec,{long_name:'bad'})
        self.assertEqual(caught.exception.code,'type_mismatch')
        self.assertLess(len(caught.exception.message),1100)

    def test_malformed_host_type_metadata_never_runs_host_hooks(self):
        from chaos.contracts import Contract,accepts,list_contract
        from chaos.language import DataType
        class Host:
            def __eq__(self,other):raise AssertionError('host equality')
            def __str__(self):raise AssertionError('host stringification')
            def __repr__(self):raise AssertionError('host repr')
        ev=Evaluator()
        for malformed in [DataType(Host(),()),DataType('Bad',((Host(),1),)),DataType('Bad',(('Tag',True),)),Contract(ev,Host(),())]:
            with self.subTest(kind=type(malformed).__name__),self.assertRaises(LanguageError):
                accepts(ev,malformed,{'$type':'Bad','$tag':'Tag','$values':[1]})
        cls=ev.run('class C{};C');cls.name=Host()
        with self.assertRaises(LanguageError):list_contract(ev,cls)

    def test_host_cancellation_propagates_instead_of_false(self):
        from chaos.contracts import accepts,list_contract
        ev=Evaluator();spec=list_contract(ev,'Int')
        def stopped():raise ExecutionControl('stop')
        ev.tick=stopped
        with self.assertRaises(ExecutionControl):accepts(ev,spec,[1])

    def test_accepts_does_not_invoke_host_subclass_protocols(self):
        class HostList(list):
            def __iter__(self):raise AssertionError('host iteration')
        ev=Evaluator();ev.env.bind('host',HostList([1]))
        with self.assertRaises(LanguageError):ev.run('contract_accepts(contract_list("Int"),host)')


if __name__ == '__main__':unittest.main()
