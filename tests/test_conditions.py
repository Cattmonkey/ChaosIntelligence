import json,unittest
from chaos.language import Evaluator,LanguageError,ResourceLimit
from chaos.condition_values import Condition,make,info


class ConditionValuesTests(unittest.TestCase):
    def value(self,source):return Evaluator(step_limit=200000).run(source)
    def test_native_members_and_types(self):
        self.assertEqual(self.value('let c:Condition=condition("missing","need value",{fallback:42});[type_of(c),c.kind,c.message,c.["data"].fallback,dir(c)]'),['Condition','missing','need value',42,['data','kind','message']])
    def test_immutable_input_and_projection(self):
        source='var payload={n:[1]};let c=condition("x",data=payload);payload=set(payload,"n",[2]);var out=c.["data"];out=set(out,"n",[3]);c.["data"]'
        self.assertEqual(self.value(source),{'n':[1]})
    def test_value_equality_is_typed(self):
        self.assertEqual(self.value('[condition("x",data=[1])==condition("x",data=[1]),condition("x",data=[1])==condition("x",data=[true]),condition("x") == {kind:"x",message:"",data:null}]'),[True,False,False])
    def test_pure_numeric_tuple_set_nested_payload(self):
        result=self.value('let c=condition("x",data=(fraction(1,3),decimal("-0.00"),{2,3},condition("inner")));[c.["data"][0].denominator,c.["data"][1].sign,c.["data"][1].exponent,type_of(c.["data"][2]),c.["data"][3].kind]')
        self.assertEqual(result,[3,1,-2,'Set','inner'])
    def test_shape_and_live_payload_rejected(self):
        for source in ('condition(1)','condition("")','condition("x",message=3)','condition("x",data=fn(){1})','condition("x",data=sleep(0))','condition("x",data=decimal_context())','condition("x",data=handler_bind([]))'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.value(source)
    def test_members_readonly(self):
        for source in ('let c=condition("x");c.kind="y"','let c=condition("x");setattr(c,"data",1)','condition("x").owner'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.value(source)
    def test_condition_metadata_budget_and_nested_budget(self):
        with self.assertRaises(LanguageError):make('x'*161)
        with self.assertRaises(LanguageError):make('x','m'*2001)
        with self.assertRaises(ResourceLimit):make('x',data=[make('x','m'*2000)]*126)
        with self.assertRaises(ResourceLimit):make('x',data='m'*250001)
    def test_cycle_depth_and_host_values_rejected(self):
        value=[];value.append(value)
        with self.assertRaises(LanguageError):make('x',data=value)
        value=None
        for _ in range(66):value=[value]
        with self.assertRaises(ResourceLimit):make('x',data=value)
        with self.assertRaises(LanguageError):make('x',data=object())
    def test_json_describes_but_does_not_restore(self):
        self.assertEqual(self.value('let c=condition("x",data=42);let d=parse_json(json_stringify(c));[type_of(d),d["$condition"].["data"],type_of(parse_json(str(c)))]'),['Record',42,'Record'])
    def test_signal_and_error_are_distinct(self):
        self.assertEqual(self.value('[signal_condition(condition("x")),try{error_condition(condition("x","missing",{n:42}))}catch e{[e.code,e.message,e.value.kind,e.value.["data"].n]}]'),[None,['condition_error','missing','x',42]])
    def test_legacy_errors_are_not_conditions(self):
        self.assertEqual(self.value('var hit=false;with handler_bind([{predicate:fn(c){true},handler:fn(c){hit=true}}]) as h {try{throw 1}catch e{e.code}};hit'),False)
    def test_immutable_conditions_cross_evaluator_as_pure_values(self):
        condition=make('x',data=[1])
        self.assertEqual(Evaluator().run('input.["data"]',{'input':condition}),[1])


class ConditionControlTests(unittest.TestCase):
    def value(self,source,**options):return Evaluator(step_limit=200000,**options).run(source)
    def test_handler_group_order_and_decline(self):
        source='var seen=[];with handler_bind([{predicate:fn(c){true},handler:fn(c){seen=append(seen,"outer")}}]) as outer {with handler_bind([{predicate:fn(c){false},handler:fn(c){seen=append(seen,"no")}}, {predicate:fn(c){true},handler:fn(c){seen=append(seen,"inner");42}}, {predicate:fn(c){true},handler:fn(c){seen=append(seen,"later")}}]) as inner {signal_condition(condition("x"))}};seen'
        self.assertEqual(self.value(source),['inner','outer'])
    def test_masking_and_new_handler_inside_callback(self):
        source='var seen=[];with handler_bind([{predicate:fn(c){true},handler:fn(c){seen=append(seen,c.kind)}}]) as outer {with handler_bind([{predicate:fn(c){true},handler:fn(c){with handler_bind([{predicate:fn(c){true},handler:fn(c){seen=append(seen,"new")}}]) as newer {signal_condition(condition("nested"))};seen=append(seen,"inner")}}]) as inner {signal_condition(condition("root"))}};seen'
        self.assertEqual(self.value(source),['new','nested','inner','root'])
    def test_predicate_strict_bool_and_error_propagation(self):
        for callback in ('fn(c){1}','async fn(c){await sleep(0);1}'):
            with self.subTest(callback=callback),self.assertRaises(LanguageError):self.value('with handler_bind([{predicate:'+callback+',handler:fn(c){null}}]) as h {signal_condition(condition("x"))}')
    def test_inline_returns_at_invocation_site(self):
        self.assertEqual(self.value('with restart_bind([{name:"use",function:fn(x){x+1}}]) as r {[invoke_restart("use",[41]),invoke_restart(find_restart("use"),[9])]}'),[42,10])
    def test_nonlocal_unwind_not_caught_and_clause_inactive(self):
        source='var seen=[];let value=restart_case(fn(){with handler_bind([{predicate:fn(c){true},handler:fn(c){invoke_restart("use",[41])}}]) as h {try{error_condition(condition("x"));seen=append(seen,"after")}catch e{seen=append(seen,"catch")}finally{seen=append(seen,"cleanup")}}},[{name:"use",function:fn(x){seen=append(seen,"restart");[x+1,find_restart("use")]}}]);[value,seen]'
        self.assertEqual(self.value(source),[[42,None],['cleanup','restart']])
    def test_outer_case_identity_skips_inner_case(self):
        source='restart_case(fn(){let outer=find_restart("same");restart_case(fn(){invoke_restart(outer,[42])},[{name:"same",function:fn(x){0}}])},[{name:"same",function:fn(x){x}}])'
        self.assertEqual(self.value(source),42)
    def test_duplicate_names_and_unnamed_handles(self):
        self.assertEqual(self.value('with restart_bind([{name:"same",function:fn(){1}},{name:"same",function:fn(){2}},{name:null,function:fn(){3}}]) as r {let all=compute_restarts();[invoke_restart("same"),invoke_restart(all[1]),invoke_restart(all[2])]}'),[1,2,3])
    def test_test_functions_and_inert_info(self):
        source='var count=0;with restart_bind([{name:"use",function:fn(){42},test:fn(c){count=count+1;c!=null&&c.kind=="x"}}]) as r {let absent=find_restart("use");let handle=find_restart("use",condition("x"));let text=restart_info(handle);[absent,text.name,text.mode,count,invoke_restart(handle)]}'
        self.assertEqual(self.value(source),[None,'use','inline',2,42])
    def test_original_keyword_default_and_positional_contracts(self):
        self.assertEqual(self.value('with restart_bind([{name:"use",function:fn(x:Int,/,*,bonus:Int=2)->Int{x+bonus}}]) as r {invoke_restart("use",[40],{bonus:2})}'),42)
        with self.assertRaises(LanguageError):self.value('with restart_bind([{name:"use",function:fn(x:Int){x}}]) as r {invoke_restart("use",[true])}')
    def test_stale_restart_scope_one_shot(self):
        for source in ('let h=with restart_bind([{name:"x",function:fn(){42}}]) as r {find_restart("x")};invoke_restart(h)','let r=restart_bind([]);with r as a {null};with r as b {null}'):
            with self.subTest(source=source),self.assertRaises(LanguageError):self.value(source)
    def test_cleanup_failure_replaces_transfer(self):
        self.assertEqual(self.value('try{restart_case(fn(){try{invoke_restart("x")}finally{throw "cleanup"}},[{name:"x",function:fn(){42}}])}catch e{e.value}'),'cleanup')
    def test_tail_calls_retain_dynamic_extent(self):
        self.assertEqual(self.value('fn deeper(n){if n>0{deeper(n-1)}else{invoke_restart("x",[42])}};restart_case(fn(){deeper(500)},[{name:"x",function:fn(x){x}}])'),42)
