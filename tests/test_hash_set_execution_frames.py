"""Complete original hashing/Set oracle, real execution state and capture safety."""
import copy
import unittest
from contextlib import ExitStack,nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native,TailCall,serializable
from chaos.execution_frames import ExecutionCursor,ensure_cursor
from chaos.execution_hash_collections import (HASH_COLLECTION_FRAMES,HashCollectionFrameRequest,
    HashKeyFrame,InstanceHashFrame,HashValueFrame,HashFindFrame,HashStoreFrame,HashBuilderAddFrame,
    HashConstructFrame,HashEqualFrame,HashResolveFrame,SetBuilderAddFrame,SetMappingFrame,
    SetContainsFrame,SetAlgebraFrame,SetUpdateFrame,SetResolveFrame,SetLiteralEvaluationFrame)
from _hash_set_reference175 import reference
from _effect_schedule158 import effect_schedule
from test_operator_execution_frames import active

KEY='''class K{var n=0;fn __init__(s,n){s.n=n;null}fn __hash__(s){hash(s.n)}
fn __eq__(s,o){if isinstance(o,K){s.n==o.n}else{s.n==o}}}'''
COLLISION=KEY.replace('hash(s.n)','7')
WITNESS=COLLISION+'''let a=K(1);let b=K(1);let c=K(2);
let m=hash_map([(a,40),(c,2),(b,42)]);let changed=hash_set(m,b,42);
let removed=hash_remove(changed,c);let same=m==hash_map([(c,2),(a,42)]);
let values={a,b,c};let promoted=set_add({1,2},a);let less=values&{c};
[hash((a,{1,2})),hash_get(removed,a),hash_has(m,b),same,length(values),
 contains(values,b),length(values|{K(3)}),length(less),length(values-{c}),
 length(values^{c}),values>=less,values>less,values<=values,values==values,
 set_is_disjoint(values,{K(4)}),length(set_discard(values,c)),length(promoted)]'''


def snapshot(source,legacy=False,limit=200000,cancel=None,setup=None,effect_policy='already_done',inputs=None):
    effects=[];trace=[];debug=[]
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)))
        return payload['value'] if kind=='tool' else payload.get('input')
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:v,effect_handler=effect,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup is not None:setup(e)
    with effect_schedule(effect_policy)as schedule,reference()if legacy else nullcontext():
        try:result=('value',serializable(e.run(source,inputs)))
        except LanguageError as error:result=('error',error.record())
        except ExecutionControl as error:result=('control',type(error).__name__,str(error))
    return result,e.steps,effects,trace,debug,e.depth,len(e.frames),dict(schedule)


class HashSetExecutionFrames(unittest.TestCase):
    def check(self,source,expected,**options):
        current=snapshot(source,**options);original=snapshot(source,legacy=True,**options)
        self.assertEqual(current[0],('value',expected));self.assertEqual(current,original)

    def test_all15_original_algorithms_and_literal_have_actual_oracle_witnesses(self):
        seen=set()
        with reference()as reached,ExitStack()as stack:
            for kind in HASH_COLLECTION_FRAMES:
                original=kind.step
                def step(frame,cursor,value,error,_kind=kind,_original=original):
                    seen.add(_kind);return _original(frame,cursor,value,error)
                stack.enter_context(patch.object(kind,'step',step))
            result=snapshot(WITNESS)[0]
        self.assertEqual(seen,set(),'the original oracle must own every migrated algorithm')
        self.assertEqual(result[0],'value');self.assertEqual(len(reached),16)
        self.assertTrue(all(reached.values()),{name:count for name,count in reached.items()if not count})

    def test_all16_authoritative_step_classes_execute(self):
        seen=set()
        with ExitStack()as stack:
            for kind in HASH_COLLECTION_FRAMES:
                original=kind.step
                def step(frame,cursor,value,error,_kind=kind,_original=original):
                    seen.add(_kind);return _original(frame,cursor,value,error)
                stack.enter_context(patch.object(kind,'step',step))
            self.assertEqual(snapshot(WITNESS)[0][0],'value')
        self.assertEqual(len(HASH_COLLECTION_FRAMES),16);self.assertEqual(seen,set(HASH_COLLECTION_FRAMES))

    def test_native_hash_tuple_set_equivalence_and_strict_numeric_keys(self):
        self.check('let m=hash_map([(1,40),(1.0,41),(true,42),(null,43)]);[length(m),m[true],m[null],hash((1,{2,3}))==hash((1,{3,2})),hash(1)!=hash(true)]',[4,42,43,True,True])

    def test_instance_hash_mro_null_override_and_exact_integer(self):
        for definition,expected in [('class K{let __hash__=null}','unhashable_type'),('class K{fn __eq__(s,o){true}}','unhashable_type'),('class B{fn __hash__(s){1}}class K(B){fn __eq__(s,o){true}}','unhashable_type'),('class K{fn __hash__(s){true}}','argument_error')]:
            self.check(definition+';try{hash(K())}catch e{e.code}',expected)
        self.check('class B{fn __hash__(s){42}}class K(B){fn __eq__(s,o){true}fn __hash__(s){super(K,s).__hash__()}}hash(K())',42)
        self.check('class K{}let a=K();let b=K();[hash(a)==hash(a),hash(a)!=hash(b)]',[True,True])

    def test_collision_update_retains_first_key_identity_and_insertion_hash(self):
        self.check('''var calls=0;class K{var n=1;fn __hash__(s){calls+=1;s.n}fn __eq__(s,o){true}}
let first=K();let second=K();let before=hash_map([(first,40)]);let after=hash_set(before,second,42);
first.n=2;let same=after==after;first.n=1;[before[first],after[first],hash_keys(after)[0]==first,calls,same]''',[40,42,True,4,True])

    def test_hash_constructor_borrowed_pairs_duplicate_order_and_error_resume(self):
        self.check('fn* pairs(){yield (1,40);yield (1,42);yield (2,43)}let it=pairs();let m=hash_map(it);[hash_values(m),next(it).done]',[[42,43],True])
        self.check('fn* pairs(){yield (1,42);yield (2,);yield (3,44)}let it=pairs();[try{hash_map(it)}catch e{e.code},next(it).value[0]]',['argument_error',3])

    def test_persistent_map_get_remove_and_missing_index_contract(self):
        self.check('let a=hash_map([(1,40)]);let b=hash_set(a,1,42);let c=hash_remove(b,1);[a[1],b[1],hash_get(c,1,99),hash_has(c,1),try{c[1]}catch e{e.code}]',[40,42,99,False,'key_error'])
        def setup(e):
            from chaos.hash_maps import Request
            e.env.bind('unknown_map',Native('unknown_map',lambda m,k:Request('unknown',m,k),e,captures=()))
        self.check('class K{fn __hash__(s){tool("hash",7)}fn __eq__(s,o){tool("equal",true)}}let m=hash_map([(K(),42)]);try{unknown_map(m,K())}catch e{e.code}','argument_error',setup=setup)


    def test_map_equality_order_value_protocol_and_short_circuit(self):
        self.check(COLLISION+'let a=hash_map([(K(1),[40]),(K(2),[42])]);let b=hash_map([(K(2),[42]),(K(1),[40])]);[a==b,a==hash_set(b,K(1),[0]),a==hash_map(),a==a]',[True,False,False,True])
        # _find binds operators.equal before its first comparison; changing the
        # module during that actual effect must not rebind the second slot.
        from chaos import hash_maps,operators
        from chaos.suspension import CHECKPOINT,Effect
        for legacy in (False,True):
            e=Evaluator(tool_handler=lambda n,v:False)
            table,query=e.run('class K{fn __hash__(s){7}fn __eq__(s,o){tool("swap",false)}}[hash_map([(K(),40),(K(),42)]),K()]')
            with reference()if legacy else nullcontext(),active(e,None):
                cursor=ensure_cursor(e,hash_maps._find(e,table,query,7));request=cursor.send(None)
                while request is CHECKPOINT:request=cursor.send(None)
                self.assertIs(type(request),Effect)
                with patch.object(operators,'equal',side_effect=AssertionError('rebound imported alias')):
                    request=cursor.send(False)
                    while request is CHECKPOINT:request=cursor.send(None)
                    self.assertIs(type(request),Effect)
                    reply=True
                    while True:
                        try:request=cursor.send(reply)
                        except StopIteration as returned:
                            self.assertEqual(returned.value,1);break
                        self.assertIs(request,CHECKPOINT);reply=None


    def test_set_pure_promotion_preserves_first_identity_and_native_types(self):
        self.check(KEY+'let first=K(1);let s={first,1,K(1)};[length(s),type_of(collect(s)[0]),contains({1},first),s=={1},length({true,1,1.0})]',[1,'Instance',True,True,3])

    def test_owned_set_every_algebra_operation_and_iteration_order(self):
        self.check(KEY+'let a={K(1),K(2)};let b={K(2),3};[length(a|b),length(a&b),length(a-b),length(a^b),a<=a,a<a,a>(a&b),a>=(a&b),set_is_disjoint(a,{4}),set_is_disjoint(a,b)]',[3,1,1,2,True,False,True,True,True,False])

    def test_nested_tuple_and_set_hash_congruence(self):
        self.check(KEY+'let a={K(1),(K(2),),{K(3)}};let b={1,(2,),{3}};let m=hash_map([(a,42)]);[a==b,hash(a)==hash(b),m[b],length({a,b})]',[True,True,42,1])

    def test_set_update_and_algebra_never_implicitly_rehash_stored_keys(self):
        self.check('var calls=0;class K{fn __hash__(s){calls+=1;7}fn __eq__(s,o){false}}let key=K();let a={key};let h=hash(a);let b=a|{1};let c=a&a;let d=set_discard(a,2);[calls,length(a),length(b),length(c),length(d)]',[1,1,2,1,1])

    def test_foreign_owned_map_set_and_nested_keys_rejected(self):
        from chaos.capture_graph import seal
        from chaos.tuple_values import TupleValue
        foreign=Evaluator().run('class K{};[hash_map([(1,42)]),{K()},K()]')
        for value in foreign:
            e=Evaluator()
            # Native language Tuple is traversable; arbitrary host tuples
            # remain deliberately opaque outside declared protocol vectors.
            for wrapped in (value,[value],TupleValue((value,)),TupleValue((TupleValue((value,)),))):
                with self.subTest(kind=type(value).__name__,wrapper=type(wrapped).__name__):
                    with self.assertRaises(LanguageError)as caught:
                        seal(e,(HashCollectionFrameRequest('hash_value',(wrapped,)),),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
        from chaos.hash_maps import _HashBudget,_Builder
        from chaos.set_runtime import Builder
        e=Evaluator();foreign_value=Evaluator().run('fn(){42}')
        budget=_HashBudget(e);budget.memo[1]=(foreign_value,(0,False,0))
        builder=_Builder(e);builder.entries.append((foreign_value,None,0))
        set_builder=Builder(e);set_builder.items.append(foreign_value)
        for frame in (HashKeyFrame(budget,None),HashBuilderAddFrame(builder,None,None),SetBuilderAddFrame(set_builder,None)):
            with self.subTest(carrier=type(frame).__name__),self.assertRaises(LanguageError)as caught:seal(e,(ExecutionCursor(e,frame),),None)
            self.assertEqual(caught.exception.code,'ownership_error')
        stale=e.run('async fn f(){42}f()');e.suspension.begin()
        with self.assertRaises(LanguageError)as caught:seal(e,(HashCollectionFrameRequest('hash_value',(stale,)),),None)
        self.assertEqual(caught.exception.code,'ownership_error')
        # Metadata slots are a strict private schema, never an opaque escape
        # hatch for a foreign value or an Int-keyed language Record.
        foreign_class=Evaluator().run('class Foreign{};Foreign')
        from chaos.set_values import _Key
        metadata=[]
        def map_case(change):
            value=_Builder(e);change(value);metadata.append(HashBuilderAddFrame(value,None,None))
        map_case(lambda value:value.entries.append((None,None,foreign_value)))
        map_case(lambda value:value.buckets.update({foreign_class:[0]}))
        map_case(lambda value:value.buckets.update({7:[foreign_value]}))
        map_case(lambda value:value.buckets.update({7:[True]}))
        def budget_case(change):
            value=_HashBudget(e);change(value);metadata.append(HashKeyFrame(value,None))
        budget_case(lambda value:value.memo.update({foreign_class:(None,(0,True,0))}))
        for triple in ((foreign_value,True,0),(0,foreign_value,0),(0,True,foreign_value)):
            budget_case(lambda value,triple=triple:value.memo.update({1:(None,triple)}))
        budget_case(lambda value:setattr(value,'nodes',foreign_value))
        budget_case(lambda value:value.active.add(foreign_class))
        budget_case(lambda value:setattr(value.pure,'nodes',foreign_value))
        budget_case(lambda value:setattr(value.pure,'chars',foreign_value))
        budget_case(lambda value:value.pure.active.add(foreign_class))
        budget_case(lambda value:value.pure.memo.update({foreign_class:(None,_Key('null',None,b'x'*32))}))
        budget_case(lambda value:value.pure.comparisons.update({(foreign_class,1):True}))
        budget_case(lambda value:value.pure.comparisons.update({(1,2):foreign_value}))
        for frame in metadata:
            with self.subTest(metadata=type(frame).__name__),self.assertRaises(LanguageError)as caught:seal(e,(ExecutionCursor(e,frame),),None)
            self.assertEqual(caught.exception.code,'ownership_error')

    def factories(self,e):
        from chaos.hash_maps import _HashBudget,_Builder,make,Request
        from chaos.set_runtime import Builder,Request as SetRequest
        budget=_HashBudget(e);table=make(e);builder=_Builder(e);set_builder=Builder(e)
        return [lambda:HashKeyFrame(budget,None),lambda:InstanceHashFrame(budget,None),lambda:HashValueFrame(None),lambda:HashFindFrame(table,None,0),lambda:HashStoreFrame(table,None,None),lambda:HashBuilderAddFrame(builder,None,None),lambda:HashConstructFrame([]),lambda:HashEqualFrame(table,table),lambda:HashResolveFrame(Request('hash',key=None)),lambda:SetBuilderAddFrame(set_builder,None),lambda:SetMappingFrame(None),lambda:SetContainsFrame(None,None),lambda:SetAlgebraFrame('|',None,None),lambda:SetUpdateFrame('add',None,None),lambda:SetResolveFrame(SetRequest('add',None,None)),lambda:SetLiteralEvaluationFrame(('set',()),e.env)]

    def test_every_declared_root_and_saved_authority_rejects_foreign_values(self):
        from chaos.capture_graph import seal
        e=Evaluator();foreign=Evaluator().run('fn(){42}');factories=self.factories(e)
        self.assertEqual(len(factories),16)
        for factory in factories:
            for field in factory().ROOTS+('result','previous_authority'):
                frame=factory();setattr(frame,field,foreign)
                with self.subTest(frame=type(frame).__name__,field=field),self.assertRaises(LanguageError)as caught:
                    seal(e,(ExecutionCursor(e,frame),),None)
                self.assertEqual(caught.exception.code,'ownership_error')

    def test_capture_bounds_and_capability_narrowing_are_atomic(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.tuple_values import TupleValue
        e=Evaluator();frame=HashValueFrame(None);frame.value=TupleValue(([None]*10000,[None]*10000))
        with self.assertRaises(ResourceLimit):seal(e,(ExecutionCursor(e,frame),),None)
        deep=None
        for unused in range(102):deep=[deep]
        frame.value=TupleValue((deep,))
        with self.assertRaises(ResourceLimit):seal(e,(ExecutionCursor(e,frame),),None)
        token=NextDescriptor(e,None);native=Native('next',lambda:None,e,generic=token,captures=())
        frame.value=TupleValue((Evaluator().run('fn(){42}'),native))
        with self.assertRaises(LanguageError):seal(e,(ExecutionCursor(e,frame),),frozenset())
        self.assertIsNone(token.capabilities);self.assertIsNone(native.capabilities)

    def test_restoration_anchors_validate_owner_epoch_and_restore_exact_runner(self):
        from chaos.capture_graph import seal
        from chaos.suspension import Runner
        e=Evaluator();foreign=Evaluator();foreign_runner=Runner(ExecutionCursor(foreign,HashValueFrame(None)))
        frame=HashValueFrame(None);frame.authority_runner=foreign_runner;frame.authority_epoch=e.suspension.epoch
        with self.assertRaises(LanguageError):seal(e,(ExecutionCursor(e,frame),),None)
        with active(e,frozenset({'outside','wait'}))as runner:
            frame=HashResolveFrame(None);cursor=ExecutionCursor(e,frame);previous=runner.capabilities
            frame.enter_scope(e,frozenset({'wait'}));frame.authority_epoch-=1
            with self.assertRaises(LanguageError):seal(e,(cursor,),None)
            frame.authority_epoch+=1;frame.close(cursor);self.assertIs(runner.capabilities,previous)

    def test_narrow_map_and_set_cannot_expose_wide_hidden_keys_or_values(self):
        def setup(e):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        for expression in ('hash_keys(saved)','hash_items(saved)','hash_has(saved,K())'):
            self.check('class K{fn __hash__(s){1}}let original=hash_map([(K(),42)]);let saved=limited(fn(){original});try{'+expression+'}catch e{e.code}','capability_error',setup=setup)
        self.check('class K{}let original={K()};let saved=limited(fn(){original});try{collect(saved)}catch e{e.code}','capability_error',setup=setup)

    def test_close_pending_hash_constructor_unlocks_borrowed_cursor(self):
        from chaos.hash_maps import construct
        from chaos.suspension import CHECKPOINT,Effect
        from chaos.iteration import Iterator
        outcomes=[]
        for legacy in (False,True):
            e=Evaluator(tool_handler=lambda n,v:v)
            source=e.run('class I{fn __iter__(s){s}fn __next__(s){tool("wait",1);{done:false,value:(1,42)}}fn __close__(s){tool("unexpected_close",null)}}I()')
            with active(e,None)as runner,reference()if legacy else nullcontext():
                cursor=ensure_cursor(e,construct(e,source));request=cursor.send(None)
                while request is CHECKPOINT:request=cursor.send(None)
                self.assertIs(type(request),Effect)
                borrowed=next(handle for handle in e.suspension.iteration.handles if type(handle)is Iterator and handle.target is source)
                self.assertTrue(borrowed.busy)
                if not legacy:
                    frame=next(f for f in cursor.frames if type(f)is HashConstructFrame)
                    self.assertIs(frame.cursor,borrowed);self.assertTrue(frame.locked)
                close_calls=[]
                from chaos.execution_iteration import IterationCloseFrame,IterationInvokeFrame
                close_step=IterationCloseFrame.step;invoke_step=IterationInvokeFrame.step
                def closing(frame,cursor,value,error):
                    close_calls.append('close');return close_step(frame,cursor,value,error)
                def invoking(frame,cursor,value,error):
                    if frame.name in ('__close__','__aclose__'):close_calls.append(frame.name)
                    return invoke_step(frame,cursor,value,error)
                with patch.object(IterationCloseFrame,'step',closing),patch.object(IterationInvokeFrame,'step',invoking):cursor.close()
                self.assertEqual(close_calls,[],'borrowed handles never receive an implicit close protocol')
                self.assertFalse(borrowed.busy);self.assertNotEqual(borrowed.state,'closed')
                if not legacy:self.assertFalse(frame.locked)
                self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertEqual(runner.capture_frames,[])
                # The pending iteration child is cancelled on close in the
                # original runtime too; this flag does not mean __close__ ran.
                outcomes.append((borrowed.state,borrowed.close_attempted,borrowed.busy,e.steps,e.depth,len(e.frames),len(runner.capture_frames),close_calls))
        self.assertEqual(outcomes[0],outcomes[1]);self.assertEqual(outcomes[0][:3],('cancelled',True,False))

    def test_close_pending_tuple_hash_releases_original_active_budget(self):
        from chaos.hash_maps import hash_value
        from chaos.tuple_values import TupleValue
        from chaos.suspension import CHECKPOINT,Effect
        e=Evaluator(tool_handler=lambda n,v:v);key=e.run('class K{fn __hash__(s){tool("wait",1)}}K()')
        with active(e,None):
            cursor=ensure_cursor(e,hash_value(e,TupleValue((key,))));request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect);budgets={id(f.budget):f.budget for f in cursor.frames if type(f)is HashKeyFrame}
            self.assertTrue(any(b.active for b in budgets.values()));cursor.close()
            self.assertTrue(all(not b.active for b in budgets.values()));self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])

    def test_unstarted_throw_preserves_exception_identity_and_zero_algorithm_ticks(self):
        from chaos import hash_maps,set_runtime
        for legacy in (False,True):
            e=Evaluator();failure=ResourceLimit('exact failure')
            with reference()if legacy else nullcontext():
                iterators=[hash_maps.hash_value(e,1),hash_maps.construct(e,[]),hash_maps.resolve(e,hash_maps.Request('hash',key=1)),set_runtime.mapping(e,None),set_runtime.contains(e,None,None),set_runtime.resolve(e,set_runtime.Request('add',None,None))]
                for iterator in iterators:
                    before=e.steps
                    with self.assertRaises(ResourceLimit)as caught:iterator.throw(failure)
                    self.assertIs(caught.exception,failure);self.assertEqual(e.steps,before)
                before=e.steps
                with self.assertRaises(RuntimeError):e.suspension.drive(ensure_cursor(e,set_runtime.resolve(e,set_runtime.Request('unknown',None))))
                self.assertEqual(e.steps,before)


    def test_all_small_step_and_cancel_thresholds_match_the_original(self):
        for source in ('hash_get(hash_map([(1,42)]),1)',KEY+'contains({K(1)},K(1))','length({1,*[2,3]})'):
            total=snapshot(source,legacy=True)[1]
            for limit in range(total+2):
                with self.subTest(source=source,limit=limit):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
            for cancel in range(total+2):
                with self.subTest(source=source,cancel=cancel):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))

    def test_real_first_wait_hash_effect_matches_value_steps_trace_debug_and_cleanup(self):
        self.check('class K{fn __hash__(s){tool("hash",42)}}async fn f(){hash(K())}await f()',42,effect_policy='first_wait')

    def test_real_first_wait_collision_equality_effect_matches_original(self):
        self.check('class K{fn __hash__(s){1}fn __eq__(s,o){tool("eq",true)}}let m=hash_map([(K(),42)]);async fn f(){hash_get(m,K())}await f()',42,effect_policy='first_wait')

    def test_hash_value_pending_result_controls_the_actual_returned_hash(self):
        original=HashValueFrame.advance;seen=[]
        def advance(frame,cursor,value):
            if frame.phase=='hash':value=(42,True,0);seen.append(True)
            return original(frame,cursor,value)
        with patch.object(HashValueFrame,'advance',advance):self.assertEqual(snapshot('hash(0)')[0],('value',42))
        self.assertEqual(seen,[True])

    def test_find_pending_index_controls_actual_returned_bucket_position(self):
        from chaos.hash_maps import make,_find
        from chaos.tuple_values import TupleValue
        original=HashFindFrame.advance;seen=[]
        def advance(frame,cursor,value):
            if frame.phase=='compare' and value:frame.index=1;seen.append(True)
            return original(frame,cursor,value)
        e=Evaluator();table=make(e,[(TupleValue((1,)),40,7),(TupleValue((2,)),42,7)])
        with patch.object(HashFindFrame,'advance',advance):self.assertEqual(e.suspension.drive(ensure_cursor(e,_find(e,table,TupleValue((1,)),7))),1)
        self.assertEqual(seen,[True])

    def test_store_pending_value_is_the_value_written_to_persistent_result(self):
        original=HashStoreFrame.advance;seen=[]
        def advance(frame,cursor,value):
            if frame.phase=='find':frame.value=42;seen.append(True)
            return original(frame,cursor,value)
        with patch.object(HashStoreFrame,'advance',advance):self.assertEqual(snapshot('hash_set(hash_map(),"x",1)["x"]')[0],('value',42))
        self.assertEqual(seen,[True])

    def test_set_update_pending_member_controls_the_actual_persistent_set(self):
        original=SetUpdateFrame.advance;seen=[]
        def advance(frame,cursor,value):
            if frame.phase=='mapping':frame.item=42;seen.append(True)
            return original(frame,cursor,value)
        with patch.object(SetUpdateFrame,'advance',advance):self.assertEqual(snapshot(KEY+'contains(set_add({K(1)},2),42)')[0],('value',True))
        self.assertEqual(seen,[True])

    def test_set_literal_pending_index_controls_next_member_evaluation(self):
        original=SetLiteralEvaluationFrame.advance;seen=[]
        def advance(frame,cursor,value):
            if frame.phase=='added' and frame.index==0:frame.index=1;seen.append(True)
            return original(frame,cursor,value)
        with patch.object(SetLiteralEvaluationFrame,'advance',advance):self.assertEqual(snapshot('collect({1,2,3})')[0],('value',[1,3]))
        self.assertEqual(seen,[True])

    def capture_alignment(self,literal):
        from chaos.capture_graph import seal
        from chaos.suspension import Task
        for padding in range(64):
            witnesses=[]
            def setup(e):
                def capture(task):
                    self.assertIs(type(task),Task);self.assertNotIn(task.state,('completed','failed','closed','cancelled'))
                    seen=[];from chaos.execution_frames import frame_roots as original
                    def roots(frame):seen.append(type(frame));return original(frame)
                    with patch('chaos.execution_frames.frame_roots',roots):seal(e,(task,),None)
                    witnesses.append(set(seen));return None
                e.env.bind('capture_probe175',Native('capture_probe175',capture,e,captures=()))
            expression='length({K()})' if literal else 'hash_get(hash_map([(K(),42)]),key)'
            source='''let padding=range(PADDING);let ready=channel(1);class K{fn __hash__(s){spawn(channel_send(ready,0));tool("hash_hold",1)}fn __eq__(s,o){true}}
let key=K();async fn work(){EXPR}let task=work();spawn(task);await channel_receive(ready);capture_probe175(task);await task'''.replace('PADDING',str(padding)).replace('EXPR',expression)
            with self.subTest(literal=literal,padding=padding):
                a=snapshot(source,setup=setup,effect_policy='first_wait');b=snapshot(source,legacy=True,setup=setup,effect_policy='first_wait')
                self.assertEqual(a[0],('value',1 if literal else 42));self.assertEqual(a,b)
                self.assertTrue(set(HASH_COLLECTION_FRAMES)&witnesses[0]);self.assertFalse(set(HASH_COLLECTION_FRAMES)&witnesses[1])

    def test_all64_pending_hash_task_capture_alignments_match_original(self):self.capture_alignment(False)
    def test_all64_pending_set_literal_task_capture_alignments_match_original(self):self.capture_alignment(True)

    def test_hash_depth_nodes_shared_subtrees_and_pure_budget_limits(self):
        self.check('var key=1;for n in range(18){key=(key,key)}type_of(hash(key))','Int')
        for source,expected_code in (('var key=null;for n in range(110){key=(key,)}hash(key)','resource_limit'),('hash("a"*300000)','argument_error')):
            a=snapshot(source,limit=1000000);b=snapshot(source,legacy=True,limit=1000000)
            self.assertEqual(a,b);self.assertEqual(a[0][0],'error');self.assertEqual(a[0][1]['code'],expected_code)

        # Public depth reaches the existing hash guard; the oversized string
        # is rejected by multiplication first. Direct hashing also isolates the
        # exact hash guards from earlier language-value validation.
        from chaos.hash_maps import _HashBudget
        from chaos.tuple_values import TupleValue
        deep=None
        for unused in range(102):deep=TupleValue((deep,))
        for key,expected in ((deep,'error'),('a'*250000,'value'),('a'*250001,'error')):
            outcomes=[]
            for legacy in (False,True):
                e=Evaluator(step_limit=2000000,timeout=30);budget=_HashBudget(e)
                with reference()if legacy else nullcontext():
                    try:result=('value',e.suspension.drive(ensure_cursor(e,budget.hash(key))))
                    except ResourceLimit as caught:result=('error',caught.record())
                self.assertEqual(result[0],expected)
                if expected=='error':self.assertEqual(result[1]['code'],'resource_limit')
                outcomes.append((result,e.steps,budget.nodes,budget.pure.nodes,budget.pure.chars,len(budget.active),e.depth,len(e.frames)))
            self.assertEqual(outcomes[0],outcomes[1]);self.assertEqual(outcomes[0][5:],(0,0,0))

        # Distinct bounded tuples force HashBudget.nodes beyond 20000 while
        # two memoized scalars keep the separate portable key budget small.
        from chaos.hash_maps import _HashBudget
        from chaos.tuple_values import TupleValue
        key=TupleValue(tuple(TupleValue((1,2))for unused in range(10000)))
        outcomes=[]
        for legacy in (False,True):
            e=Evaluator(step_limit=2000000,timeout=30);budget=_HashBudget(e)
            with reference()if legacy else nullcontext():
                with self.assertRaises(ResourceLimit)as caught:e.suspension.drive(ensure_cursor(e,budget.hash(key)))
            self.assertEqual(budget.nodes,20001);self.assertLess(budget.pure.nodes,20000)
            outcomes.append((caught.exception.record(),e.steps,budget.nodes,budget.pure.nodes,len(budget.active),e.depth,len(e.frames)))
        self.assertEqual(outcomes[0],outcomes[1])
        self.assertEqual(outcomes[0][4:],(0,0,0))

    def test_map_constructor_and_set_input_limits_keep_original_eof_probes(self):
        for source in ('fn* pairs(){for n in range(10000){yield (1,n)}yield (1,0)}hash_map(pairs())','set_of(range(10001))'):
            a=snapshot(source,limit=2000000);b=snapshot(source,legacy=True,limit=2000000)
            self.assertEqual(a,b);self.assertEqual(a[0][0],'error');self.assertEqual(a[0][1]['code'],'resource_limit')

    def test_cold_async_generator_hash_and_equality_are_never_started(self):
        for method in ('async fn __hash__(s){calls+=1;1}','fn* __hash__(s){calls+=1;yield 1}'):
            self.check('var calls=0;class K{'+method+'}let code=try{hash(K())}catch e{e.code};[code,calls]',['argument_error',0])
        self.check('var calls=0;class K{fn __hash__(s){1}async fn __eq__(s,o){calls+=1;true}}let a={K()};[try{contains(a,K())}catch e{e.code},calls]',['argument_error',0])

    def test_declared_roots_do_not_invoke_host_container_or_storage_hooks(self):
        from chaos.capture_graph import seal
        class Bomb:
            def __iter__(self):raise AssertionError('host iterate')
            def __getattr__(self,name):raise AssertionError('host getattr')
            def __hash__(self):raise AssertionError('host hash')
        e=Evaluator();frame=HashValueFrame(Bomb());seal(e,(ExecutionCursor(e,frame),HashCollectionFrameRequest('hash_value',(Bomb(),))),None)

    def test_portable_member_budget_survives_promotion_before_later_effects(self):
        source='class K{};{K(),a,b,tool("late",null)}'
        for legacy in (False,True):
            result=snapshot(source,legacy=legacy,limit=1000000,inputs={'a':'a'*150000,'b':'b'*150000})
            self.assertEqual(result[0][0],'error');self.assertEqual(result[0][1]['code'],'resource_limit');self.assertEqual(result[2],[])


if __name__=='__main__':unittest.main()
