"""General-key maps use language hashing, equality, ownership and effects."""
import unittest
from dataclasses import FrozenInstanceError

from chaos.language import Evaluator, LanguageError, ResourceLimit, Native, TailCall, serializable
from chaos.tuple_values import TupleValue
from chaos.session import Session


KEY = '''class Key{var n=0;fn __init__(self,n){self.n=n;null}
fn __hash__(self){7}fn __eq__(self,other){if isinstance(other,Key){self.n==other.n}else{NotImplemented}}}'''


class HashMapTests(unittest.TestCase):
    def code(self, source, **options): return Evaluator(**options).run(source)

    def test_persistent_general_keys_order_and_keyword_api(self):
        result=self.code('''let empty=hash_map();let first=hash_set(mapping=empty,key=1,value="one");
        let second=hash_set(first,(2,"x"),"two");let changed=hash_set(second,1,"changed");
        let removed=hash_remove(mapping=changed,key=1);
        [hash_get(mapping=first,key=1),changed[1],hash_get(removed,1,default="missing"),
         hash_keys(changed),hash_values(changed),hash_info(empty).size,hash_has(mapping=removed,key=1),hash_keys(hash_remove(removed,99))]''')
        self.assertEqual(result[:3],['one','changed','missing'])
        self.assertEqual(result[3][0],1);self.assertEqual(result[3][1].items,(2,'x'))
        self.assertEqual(result[4:7],[['changed','two'],0,False])
        self.assertEqual(result[7][0].items,(2,'x'))

    def test_collisions_replace_first_key_identity_and_position(self):
        result=self.code(KEY+'''let first=Key(1);let duplicate=Key(1);let other=Key(2);
        let m=hash_map([(first,"a"),(other,"b"),(duplicate,"c")]);
        [m[Key(1)],m[Key(2)],length(m),hash_keys(m)[0]==first,hash_items(m)[0][1],hash_get(hash_remove(m,Key(1)),Key(1),42)]''')
        self.assertEqual(result,['c','b',2,True,'c',42])
        ev=Evaluator();value=ev.run(KEY+'let original=Key(1);let duplicate=Key(1);let m=hash_map([(original,1),(duplicate,2)]);[original,hash_keys(m)[0]]')
        self.assertIs(value[0],value[1])

    def test_native_hashes_respect_strict_numeric_equality_and_normalization(self):
        self.assertEqual(self.code('''let m=hash_map([(1,"int"),(1.0,"number"),(true,"bool"),(decimal("1.00"),"decimal"),
        (fraction(1,2),"fraction"),(complex(1,2),"complex")]);
        [length(m),m[1],m[1.0],m[true],m[decimal("1.0")],m[fraction(2,4)],m[complex(1,2)],
         hash(decimal("1.00"))==hash(decimal("1.0")),hash(0.0)==hash(-0.0),hash(1)!=hash(1.0),hash(1)!=hash(true)]'''),
        [6,'int','number','bool','decimal','fraction','complex',True,True,True,True])
        self.assertEqual(self.code('hash(decimal("0"))==hash(decimal("-0.00"))'),True)

    def test_tuples_sets_and_null_keys(self):
        self.assertEqual(self.code(KEY+'''let m=hash_map([((Key(1),"a"),42),(set_of([2,1]),41),(null,40)]);
        [m[(Key(1),"a")],m[set_of([1,2])],m[null],hash(set_of([1,2]))==hash(set_of([2,1]))]'''),[42,41,40,True])

    def test_instance_hash_runs_per_tuple_occurrence(self):
        self.assertEqual(self.code('''var calls=0;class K{fn __hash__(self){calls=calls+1;42}};let k=K();let pair=(k,k);
        hash((pair,pair));calls'''),4)

    def test_default_instance_identity_and_mutated_key_contract(self):
        self.assertEqual(self.code('''class K{};let a=K();let b=K();let m=hash_map([(a,42)]);
        [hash(a)==hash(a),hash(a)!=hash(b),m[a],hash_has(m,b)]'''),[True,True,42,False])
        self.assertEqual(self.code('''class K{var n=1;fn __hash__(self){self.n}};let key=K();let m=hash_map([(key,42)]);
        key.n=2;let missing=hash_has(m,key);key.n=1;[missing,m[key]]'''),[False,42])

    def test_same_key_identity_shortcut_does_not_call_nonreflexive_equal(self):
        self.assertEqual(self.code('''var calls=0;class K{fn __hash__(self){1}fn __eq__(self,other){calls=calls+1;false}}
        let k=K();let m=hash_map([(k,42)]);[m[k],calls,hash_has(m,K()),calls]'''),[42,0,False,1])

    def test_eq_override_and_explicit_null_disable_hash(self):
        for definition in ['class K{fn __eq__(self,x){true}}', 'class K{let __hash__=null}',
                           'class Base{fn __hash__(self){1}}class K(Base){fn __eq__(self,x){true}}']:
            with self.subTest(definition=definition):
                self.assertEqual(self.code(definition+';try{hash(K())}catch e{e.code}'),'unhashable_type')
        self.assertEqual(self.code('''class Base{fn __hash__(self){42}}class K(Base){fn __eq__(self,x){true}fn __hash__(self){super(K,self).__hash__()}};hash(K())'''),42)

    def test_protocols_are_resolved_each_use_and_never_by_name(self):
        self.assertEqual(self.code('''class K{var __hash__=fn(){1}};let k=K();let one=hash(k);K.__hash__=fn(){2};
        let two=hash(k);K.__hash__=null;[one,two,try{hash(k)}catch e{e.code}]'''),[1,2,'unhashable_type'])
        for expression in ['[]','{}','hash_map()','fn(x){x}','do{class K{};K}','async fn(){1}()', 'slice_key(1,2)']:
            with self.subTest(expression=expression):
                self.assertEqual(self.code('try{hash('+expression+')}catch e{e.code}'),'unhashable_type')

    def test_hash_protocol_exact_int_and_no_implicit_await(self):
        for body in ['true','1.0','"1"','null','[]']:
            with self.subTest(body=body):
                self.assertEqual(self.code('class K{fn __hash__(self){'+body+'}};try{hash(K())}catch e{e.code}'),'argument_error')
        for flavor in ['async fn','fn*','async fn*']:
            body='yield 1' if '*' in flavor else '1'
            self.assertEqual(self.code('class K{'+flavor+' __hash__(self){'+body+'}};try{hash(K())}catch e{e.code}'),'argument_error')
        self.assertEqual(self.code('''var seen=0;async fn cold(){seen=seen+1;42};class K{fn __hash__(self){cold()}}
        try{hash(K())}catch e{let state=task_info(e.value).state;cancel(e.value);[e.code,state,seen]}'''),['argument_error','created',0])

    def test_equality_protocol_bool_and_no_implicit_await(self):
        for method in ['async fn __eq__(self,x){true}','fn* __eq__(self,x){yield true}','fn __eq__(self,x){42}']:
            with self.subTest(method=method),self.assertRaises(LanguageError):
                self.code('class K{fn __hash__(self){1}'+method+'};hash_has(hash_map([(K(),42)]),K())')
        self.assertEqual(self.code('''var seen=0;class K{fn __hash__(self){1}async fn __eq__(self,x){seen=seen+1;true}}
        let m=hash_map([(K(),42)]);let code=try{hash_has(m,K())}catch e{e.code};[code,seen]'''),['argument_error',0])

    def test_cross_type_user_equality_can_share_native_bucket(self):
        self.assertEqual(self.code('''class K{fn __hash__(self){hash(42)}fn __eq__(self,other){other==42}}
        let m=hash_map([(42,"a"),(K(),"b")]);[length(m),m[42],m[K()],hash_keys(m)[0]]'''),[1,'b','b',42])

    def test_constructor_borrows_iterators_and_validates_pairs(self):
        self.assertEqual(self.code('''var seen=[];fn* pairs(){yield (1,41);yield ([1,42]);yield (2,43);seen=append(seen,"done")}
        let stream=pairs();let m=hash_map(entries=stream);[m[1],m[2],seen,next(stream).done]'''),[42,43,['done'],True])
        self.assertEqual(self.code('''fn* pairs(){yield (1,42);yield ([2]);yield (3,44)};let stream=pairs();let code=try{hash_map(stream)}catch e{e.code};
        [code,next(stream).value[0]]'''),['argument_error',3])
        for pairs in ['[1]','[[1]]','[(1,2,3)]','[set_of([1,2])]','{}']:
            with self.subTest(pairs=pairs),self.assertRaises(LanguageError):self.code('hash_map('+pairs+')')

    def test_map_readonly_index_length_contains_bool_and_iteration(self):
        self.assertEqual(self.code('''let m:HashMap=hash_map([(1,40),(2,42)]);
        [m[2],length(m),contains(m,1),bool(m),bool(hash_map()),collect(m),map(fn(k){m[k]},m),
         try{m[3]}catch KeyError::{code:c}{c},try{m[1]=99}catch e{e.code}]'''),
        [42,2,True,True,False,[1,2],[40,42],'key_error','argument_error'])

    def test_structural_map_equality_and_pure_match_identity(self):
        self.assertEqual(self.code(KEY+'''let a=hash_map([(Key(1),[41]),(Key(2),[42])]);
        let b=hash_map([(Key(2),[42]),(Key(1),[41])]);let c=hash_set(a,Key(1),[0]);
        [a==b,a!=b,a==c,a==a]'''),[True,False,False,True])
        from chaos.hash_maps import make
        ev=Evaluator();a=make(ev);b=make(ev)
        self.assertTrue(ev.equal(a,a));self.assertFalse(ev.equal(a,b))

    def test_metadata_does_not_execute_hash_or_equality(self):
        self.assertEqual(self.code('''var seen=0;class K{fn __hash__(self){seen=seen+1;1}fn __eq__(self,x){seen=seen+100;false}}
        let m=hash_map([(K(),42)]);let before=seen;let info=hash_info(m);let wire=json_stringify(m);
        [before,seen,info,parse_json(wire)]'''),[1,1,{'size':1,'ordered':True,'persistent':True,'maxEntries':10000,'keyPolicy':'native-hash-equality'},
        {'$hashMap':{'size':1,'ordered':True,'persistent':True}}])
        self.assertEqual(self.code('try{hash_get(parse_json("{\\\"$hashMap\\\":{\\\"size\\\":1}}"),1)}catch e{e.code}'),'argument_error')

    def test_returned_functions_keep_map_and_current_capability_ceilings(self):
        for expression in ['hash_get(saved,"secret")','saved["secret"]','hash_values(saved)[0]','hash_items(saved)[0][1]']:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            source='fn secret(){tool("secret",0)};let m=hash_map([("secret",secret)]);let saved=limited(fn(){m});try{'+expression+'()}catch e{e.code}'
            with self.subTest(expression=expression):
                self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_lookup_cannot_pass_hidden_wide_keys_into_equality(self):
        for action in ['hash_has(narrow,query)','hash_get(narrow,query)','hash_set(narrow,query,0)','hash_remove(narrow,query)']:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            source='''var stolen=null;class Secret{fn __hash__(self){7}fn secret(self){tool("secret",0)}}
            let m=hash_map([(Secret(),42)]);let narrow=limited(fn(){m});class Query{fn __hash__(self){7}
            fn __eq__(self,other){stolen=other;true}};let query=Query();let code=try{'''+action+'''}catch e{e.code};[code,stolen]'''
            with self.subTest(action=action):
                self.assertEqual(ev.run(source),['capability_error',None]);self.assertEqual(calls,[])

    def test_key_extraction_preserves_identity_or_rejects_unsafe_projection(self):
        ev=Evaluator();value=ev.run('class K{};let key=(K(),1);let m=hash_map([(key,42)]);[key,hash_keys(m)[0]]')
        self.assertIs(value[0],value[1])
        for expression in ['hash_keys(saved)','hash_items(saved)','collect(saved)']:
            ev=Evaluator();ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            with self.subTest(expression=expression):
                self.assertEqual(ev.run('class K{};let m=hash_map([(K(),42)]);let saved=limited(fn(){m});try{'+expression+'}catch e{e.code}'),'capability_error')

    def test_map_updates_do_not_widen_caps_and_nested_maps_keep_them(self):
        calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        self.assertEqual(ev.run('''fn secret(){tool("secret",0)};let base=limited(fn(){hash_map()});
        let m=hash_set(base,"value",hash_map([("call",secret)]));let saved=hash_get(m,"value");try{saved["call"]()}catch e{e.code}'''),'capability_error')
        self.assertEqual(calls,[])

    def test_error_payload_and_class_projection_preserve_nested_map_authority(self):
        for source in [
            'fn secret(){tool("secret",0)};let m=hash_map([("secret",secret)]);let e=limited(fn(){error(m)});try{e.value["secret"]()}catch e{e.code}',
            'fn secret(){tool("secret",0)};let m=hash_map([("secret",secret)]);let outer=hash_map([("inner",m)]);let e=limited(fn(){error(outer)});try{e.value["inner"]["secret"]()}catch e{e.code}',
            'fn secret(){tool("secret",0)};let m=hash_map([("secret",secret)]);class Box{let value=m};let b=limited(fn(){class Narrow(Box){};Narrow()});try{b.value["secret"]()}catch e{e.code}'
        ]:
            calls=[];ev=Evaluator(tool_handler=lambda *args:calls.append(args))
            ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
            with self.subTest(source=source):
                self.assertEqual(ev.run(source),'capability_error');self.assertEqual(calls,[])

    def test_foreign_values_rejected_and_nested_task_epoch_validated(self):
        foreign=Evaluator().run('hash_map([(1,42)])')
        ev=Evaluator();ev.env.bind('foreign',foreign)
        for expression in ['hash_info(foreign)','hash_get(foreign,1)','hash_map([(1,foreign)])','hash(foreign)']:
            with self.subTest(expression=expression),self.assertRaises(LanguageError) as caught:ev.run(expression)
            self.assertEqual(caught.exception.code,'ownership_error')
        foreignkey=Evaluator().run('class K{};K()')
        ev=Evaluator();ev.env.bind('foreign',foreignkey)
        with self.assertRaises(LanguageError):ev.run('hash_map([(foreign,42)])')
        session=Session();session.evaluate('async fn f(){42};let m=hash_map([(1,f())])')
        with self.assertRaises(LanguageError):session.evaluate('m[1]')

    def test_maps_are_frozen_and_copy_safe_without_host_hash_callbacks(self):
        m=self.code('hash_map([(1,42)])')
        with self.assertRaises(FrozenInstanceError):m.entries=()
        with self.assertRaises(TypeError):m.buckets[0]=()
        self.assertEqual(serializable(m),{'$hashMap':{'size':1,'ordered':True,'persistent':True}})
        class Host:
            def __hash__(self):self.fail=True;raise AssertionError('host hash invoked')
        value=Host();ev=Evaluator();ev.env.bind('host',value)
        with self.assertRaises(LanguageError):ev.run('hash(host)')
        self.assertFalse(hasattr(value,'fail'))

    def test_bulk_constructor_budget_and_duplicate_input_limit(self):
        # A private construction draft avoids quadratic persistent-copy work.
        self.assertEqual(self.code('fn* pairs(){for n in range(300){yield (n,n*2)}};let m=hash_map(pairs());[length(m),m[299]]',step_limit=60000),[300,598])
        with self.assertRaises(ResourceLimit) as caught:
            self.code('fn* pairs(){for n in range(10000){yield (1,n)};yield (1,0)};hash_map(pairs())',step_limit=2000000)
        self.assertEqual(caught.exception.message,'hash_map input exceeds 10000 pairs')
        with self.assertRaises(ResourceLimit) as caught:
            self.code('fn* pairs(){for n in range(10000){yield (n,n)}};let m=hash_map(pairs());hash_set(m,10001,0)',step_limit=2000000)
        self.assertEqual(caught.exception.message,'HashMap exceeds 10000 entries')

    def test_hash_resource_limits_are_hard_and_do_not_use_host_recursion(self):
        with self.assertRaises(ResourceLimit):
            self.code('var key=null;for n in range(110){key=(key,)};hash(key)',step_limit=100000)
        self.assertEqual(self.code('var key=1;for n in range(18){key=(key,key)};type_of(hash(key))',step_limit=100000),'Int')


if __name__ == '__main__': unittest.main()
