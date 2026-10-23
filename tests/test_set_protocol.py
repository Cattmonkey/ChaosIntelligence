"""The existing Set integrates owned keys without weakening its pure subset."""
import unittest
from dataclasses import replace
from chaos.language import Evaluator,Native,LanguageError,ResourceLimit,serializable
from chaos.set_values import SetValue,SetBuilder,set_equal
from chaos.set_runtime import Builder,project,items
from chaos.class_creation import project_value
from chaos.capture_graph import seal
from chaos.generic_functions import NextDescriptor
from chaos.objects import Member

KEY='''class Key{let n=0;fn __init__(self,n){self.n=n;null};fn __hash__(self){hash(self.n)};fn __eq__(self,other){if isinstance(other,Key){self.n==other.n}else{self.n==other}}};'''
# Mutable slot is required for constructor assignment.
KEY=KEY.replace('let n=0','var n=0')

class SetProtocolTests(unittest.TestCase):
    def run_code(self,source,**kw):return Evaluator(**kw).run(source)

    def test_literal_constructor_spread_and_comprehension_share_protocol(self):
        self.assertEqual(self.run_code(KEY+'''let a=Key(1);let b=Key(1);let c=Key(2);
        [length({a,b,c}),length(set_of([a,b,c])),length({*{a,b},c}),length({x for x in [a,b,c]})]'''),[2,2,2,2])

    def test_sync_generator_and_async_comprehension(self):
        self.assertEqual(self.run_code(KEY+'''fn* source(){yield Key(1);yield Key(1);yield Key(2)};
        async fn* asource(){yield Key(1);yield Key(1);yield Key(2)};
        [length(set_of(source())),length({await (async fn(){x})() async for x in asource()})]'''),[2,2])

    def test_mixed_scalar_equivalence_preserves_first_identity(self):
        self.assertEqual(self.run_code(KEY+'''let a=Key(1);let s={a,1,Key(1)};
        [length(s),type_of(collect(s)[0]),contains(s,1),contains({1},a),s=={1},{1}==s]'''),[1,'Instance',True,True,True,True])

    def test_scalar_types_remain_strict(self):
        self.assertEqual(self.run_code('length({true,1,1.0,fraction(1),decimal("1"),complex(1,0)})'),6)

    def test_full_algebra_order_and_subsets(self):
        self.assertEqual(self.run_code(KEY+'''let a={Key(1),Key(2)};let b={Key(2),3};
        [collect(a|b).length,length(a&b),length(a-b),length(a^b),a<=a,a<a,a> (a&b),set_is_disjoint(a,{4}),set_is_disjoint(a,b)]'''.replace('collect(a|b).length','length(a|b)')),[3,1,1,2,True,False,True,True,False])

    def test_persistent_add_discard_no_demotion(self):
        e=Evaluator();a,b,c=e.run(KEY+'let a={Key(1)};let b=set_add(a,2);let c=set_discard(b,Key(1));[a,b,c]')
        self.assertEqual([len(v) for v in (a,b,c)],[1,2,1]);self.assertIs(c.owner,e)
        self.assertEqual(c.items,(2,));self.assertEqual(serializable(c),{'$set':[2]})

    def test_nested_tuple_and_set_hash_congruence(self):
        self.assertEqual(self.run_code(KEY+'''let a={Key(1),(Key(2),),{Key(3)}};let b={1,(2,),{3}};
        let m=hash_map([[a,42]]);[a==b,hash(a)==hash(b),m[b],length({a,b})]'''),[True,True,42,1])

    def test_public_tuple_hash_still_shared_for_instance_equality(self):
        self.assertEqual(self.run_code('''class T{fn __hash__(self){hash((1,2))};fn __eq__(self,x){x==(1,2)}};
        let a={T()};let b={(1,2)};[a==b,hash(a)==hash(b),hash_map([[a,42]])[b]]'''),[True,True,42])

    def test_no_implicit_rehash_for_existing_keys(self):
        self.assertEqual(self.run_code('''var calls=0;class C{fn __hash__(s){calls+=1;7};fn __eq__(s,o){false}};
        let c=C();let s={c};let initial=calls;let h=hash(s);let a=s|{1};let b=s& s;let d=set_discard(s,2);
        [initial,calls,length(a),length(b),length(d)]'''),[1,1,2,1,1])

    def test_plain_instance_identity_hash_and_alias(self):
        self.assertEqual(self.run_code('class C{};let c=C();[length({c,c,C()}),contains({c},c)]'),[2,True])

    def test_unhashable_eq_override_null_hash_and_bad_return(self):
        for body,code in [('fn __eq__(s,o){true}','unhashable_type'),('let __hash__=null','unhashable_type'),('fn __hash__(s){1.0}','argument_error')]:
            with self.subTest(body=body),self.assertRaises(LanguageError) as caught:self.run_code('class C{'+body+'};{C()}')
            self.assertEqual(caught.exception.code,code)

    def test_async_and_generator_hash_cannot_run(self):
        for declaration in ['async fn __hash__(s){1}','fn* __hash__(s){yield 1}']:
            with self.subTest(declaration=declaration),self.assertRaises(LanguageError):self.run_code('class C{'+declaration+'};{C()}')

    def test_collision_comparison_failure_is_not_swallowed(self):
        with self.assertRaises(LanguageError) as caught:self.run_code('class C{fn __hash__(s){1};fn __eq__(s,o){throw "failed"}};{C(),C()}')
        self.assertEqual(caught.exception.value,'failed')

    def test_collision_and_input_limits(self):
        with self.assertRaises(ResourceLimit):self.run_code(KEY+'set_of((Key(x) for x in range(10001)))',step_limit=1000000)
        with self.assertRaises(ResourceLimit):self.run_code('class C{fn __hash__(s){1};fn __eq__(s,o){false}};{C() for x in range(1000)}',step_limit=30000)

    def test_foreign_owned_set_rejects_all_boundaries(self):
        s=Evaluator().run('class C{};{C()}')
        for source in ['length(s)','collect(s)','contains(s,1)','hash(s)','s==s','set_add(s,1)','generic_eql(s)']:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:Evaluator().run(source,{'s':s})
            self.assertEqual(caught.exception.code,'ownership_error')

    def test_narrow_projection_preserves_hidden_identity_and_rejects_extraction(self):
        e=Evaluator();s=e.run(KEY+'{Key(1)}')
        limited=Native('limited',lambda value:project_value(e,value),e,capabilities=frozenset())
        narrow=e.run('limited(s)',{'s':s,'limited':limited})
        self.assertEqual(narrow.capabilities,frozenset());self.assertIs(narrow.items[0],s.items[0])
        for source in ['collect(s)','contains(s,c)']:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:e.run(source,{'s':narrow,'c':e.run('K(1)',{'K':s.items[0].klass})})
            self.assertEqual(caught.exception.code,'capability_error')

    def test_capture_graph_sees_hidden_instance_token_and_builder(self):
        e=Evaluator();c=e.run('class C{};C()');token=NextDescriptor(e,None)
        n=Native('next',lambda:None,e,generic=token);c.fields['resume']=Member(n)
        s=e.run('{c}',{'c':c});seal(e,[s],frozenset());self.assertEqual(token.capabilities,frozenset())
        token.capabilities=None;draft=Builder(e);draft.items.append(c)
        seal(e,[draft],frozenset());self.assertEqual(token.capabilities,frozenset())

    def test_pure_pattern_equality_is_identity_for_owned_sets(self):
        e=Evaluator();a,b=e.run(KEY+'[{Key(1)},{Key(1)}]')
        self.assertFalse(e.equal(a,b));self.assertTrue(e.equal(a,a));self.assertTrue(e.run('a==b',{'a':a,'b':b}))
        self.assertTrue(set_equal(SetValue([1,2]),SetValue([2,1])))

    def test_condition_cannot_strip_owned_storage(self):
        with self.assertRaises(LanguageError):self.run_code(KEY+'condition("event","message",set_discard({Key(1)},Key(1)))')

    def test_serialization_is_inert_not_a_native_set(self):
        self.assertEqual(self.run_code(KEY+'let s={Key(1)};[parse_json(json_stringify(s)),type_of(parse_json(json_stringify(s)))]'),[{'$set':[{'$instance':'Key'}]},'Record'])

    def test_promotion_keeps_aggregate_budget_before_later_effects(self):
        effects=[];e=Evaluator(step_limit=1000000,tool_handler=lambda *args:effects.append(args))
        with self.assertRaises(ResourceLimit):e.run('class C{};{C(),a,b,tool("late",null)}',{'a':'a'*150000,'b':'b'*150000})
        self.assertEqual(effects,[])

    def test_repeated_container_occurrences_repeat_protocol_equality(self):
        self.assertEqual(self.run_code('var calls=0;class C{fn __hash__(s){1};fn __eq__(s,o){calls+=1;true}};let a={C()};let b={C()};let x=[a];let y=[b];[(x,x)==(y,y),calls]'),[True,2])

    def test_literal_and_async_comprehension_temporaries_seal_next(self):
        from chaos.language import TailCall
        prefix='''class Box{var resume=null};fn box(value){let b=Box();b.resume=value;b};
        let base=generic_method(generic_function("g"),["Any"],fn(x){tool("fixture",x)});
        let g=generic_method(base,["Int"],fn(next,x){next},with_next=true);
        let sink=limited(fn(){fn(item){0}});'''
        bodies=[
          'async fn outer(){{box(remember(g(42))),do{await sleep(0.02);Box()}}};let pending=outer();spawn(pending);await sleep(0.001);sink(pending);await pending;42',
          'async fn* source(){yield 0;await sleep(0.02);yield 1};async fn outer(){{if i==0{box(remember(g(42)))}else{Box()} async for i in source()}};let pending=outer();spawn(pending);await sleep(0.001);sink(pending);await pending;42']
        for body in bodies:
            calls=[];aliases=[];e=Evaluator(step_limit=1000000,tool_handler=lambda *args:calls.append(args))
            e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
            e.env.bind('remember',Native('remember',lambda value:aliases.append(value) or value,e))
            self.assertEqual(e.run(prefix+body),42);self.assertEqual(len(aliases),1)
            with self.assertRaises(LanguageError) as caught:e.invoke(aliases[0],[])
            self.assertEqual(caught.exception.code,'capability_error');self.assertEqual(calls,[])

    def test_compiler_marks_set_literal_protocol(self):
        from chaos.compiler import _EFFECTS
        self.assertIn('protocol',_EFFECTS['set'])

if __name__=='__main__':unittest.main()
