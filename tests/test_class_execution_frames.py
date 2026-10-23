"""Original21 class algorithms, real frame state, lifetime and exact scheduler parity."""
import copy
import unittest
from contextlib import ExitStack,nullcontext
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ResourceLimit,ExecutionControl,Native,TailCall,serializable
from chaos.execution_frames import ExecutionCursor,ensure_cursor
from chaos.execution_class_creation import CLASS_FRAMES,ClassFrameRequest
from _class_reference184 import reference,ORIGINALS,SEGMENTS
from _effect_schedule158 import effect_schedule
from test_operator_execution_frames import active

WITNESS='''var events=[];
class NS{var values={};fn keys(s){keys(s.values)}fn __getitem__(s,k){s.values[k]}
 fn __setitem__(s,k,v){s.values=set(s.values,k,v);null}}
class Meta(Type){fn __prepare__(m,n,b,**options){NS()}
 fn __new__(m,n,b,ns,**options){Type.__new__(m,n,b,ns,**options)}
 fn __init__(c,n,b,ns,**options){null}}
class Named{fn __set_name__(s,c,n){events=append(events,n);null}}
class Base{fn __init_subclass__(c,**options){null}}
class Proxy{fn __mro_entries__(s,original){(Base,)}}
fn identity(value){value}
@identity
class Example(Proxy(),metaclass=Meta){var count:Int=40;let value=do{count=count+1;count};
 let named=Named();fn own(s){Example};fn answer(s){s.count+1}}
let Dynamic=new_class("Dynamic",[Base],{answer:class_member(42)},{metaclass:Meta});
let Direct=Type.__new__(Meta,"Direct",[],{answer:class_member(42)});
@identity
fn wrapped(value){value+1}
@identity
agent unused{}
[Example().answer(),Example.value,Dynamic.answer,Direct.answer,isinstance(Example(),Example),
issubclass(Example,Base),Example().own()==Example,events,wrapped(41),type_of(unused)]'''
WITNESS_EXPECTED=[42,41,42,42,True,True,True,['named'],42,'Function']


def snapshot(source,legacy=False,limit=200000,cancel=None,setup=None,effect_policy='already_done',inputs=None):
    effects=[];trace=[];debug=[]
    def effect(kind,payload,event):
        effects.append((kind,copy.deepcopy(payload)))
        return payload['value']if kind=='tool'else payload.get('input')
    e=Evaluator(step_limit=limit,timeout=30,tool_handler=lambda n,v:v,effect_handler=effect,trace_handler=trace.append)
    e.cancelled=lambda:cancel is not None and e.steps>=cancel
    e.debug_hook=lambda node,env,frames:debug.append((node[0],dict(e.current_span),e.steps))
    if setup is not None:setup(e)
    with effect_schedule(effect_policy)as schedule,reference()if legacy else nullcontext():
        try:result=('value',serializable(e.run(source,inputs)))
        except LanguageError as error:result=('error',error.record())
        except ExecutionControl as error:result=('control',type(error).__name__,str(error))
    return result,e.steps,effects,trace,debug,e.depth,len(e.frames),dict(schedule)


class ClassExecutionFrames(unittest.TestCase):
    def check(self,source,expected,**options):
        modern=snapshot(source,**options);old=snapshot(source,legacy=True,**options)
        self.assertEqual(modern[0],('value',expected));self.assertEqual(modern,old)

    def test_all21_original_algorithms_have_actual_first_advance_witnesses(self):
        seen=set()
        with reference()as reached,ExitStack()as stack:
            for cls in CLASS_FRAMES:
                original=cls.step
                def step(frame,cursor,value,error,_cls=cls,_original=original):seen.add(_cls);return _original(frame,cursor,value,error)
                stack.enter_context(patch.object(cls,'step',step))
            self.assertEqual(snapshot(WITNESS)[0],('value',WITNESS_EXPECTED))
        self.assertEqual(seen,set(),'reference mode must execute original algorithms, never a new family frame')
        self.assertEqual(set(reached),set(ORIGINALS));self.assertEqual(len(reached),21)
        self.assertTrue(all(reached.values()),{k:v for k,v in reached.items()if not v})
        self.assertEqual(set(SEGMENTS),set(ORIGINALS))

    def test_all21_authoritative_step_classes_drive_real_source(self):
        seen=set();cursors=[];roots=[];runner_roots={}
        def lineage(cursor):
            runner=cursor.owner.suspension.current;root=runner.iterator
            previous=runner_roots.get(id(runner))
            if previous is not None:
                self.assertIs(previous[0],runner);self.assertIs(previous[1],root,'one actual Runner must retain its execution root')
            else:runner_roots[id(runner)]=(runner,root)
            self.assertIs(type(root),ExecutionCursor);self.assertIs(root.owner,cursor.owner)
            pending=[(root,())];visited=set()
            while pending:
                current,chain=pending.pop()
                self.assertNotIn(id(current),visited,'observer graph cannot cycle');visited.add(id(current))
                self.assertIs(current.owner,cursor.owner)
                if current is cursor:
                    for child,bridge in chain:self.assertIs(child.parent_bridge,bridge);self.assertIs(bridge.pending_cursor,child)
                    roots.append(root);return
                for frame in current.frames:
                    child=getattr(frame,'pending_cursor',None)
                    if child is not None:
                        self.assertIs(type(child),ExecutionCursor);pending.append((child,chain+((child,frame),)))
            self.fail('actual class cursor is detached from current Runner.iterator bridge lineage')
        with ExitStack()as stack:
            for cls in CLASS_FRAMES:
                original=cls.step
                def step(frame,cursor,value,error,_cls=cls,_original=original):seen.add(_cls);cursors.append(cursor);lineage(cursor);return _original(frame,cursor,value,error)
                stack.enter_context(patch.object(cls,'step',step))
            self.assertEqual(snapshot(WITNESS)[0],('value',WITNESS_EXPECTED))
        self.assertEqual(len(CLASS_FRAMES),21);self.assertEqual(seen,set(CLASS_FRAMES));self.assertTrue(cursors)
        # load_module drives each top-level node separately; public synchronous
        # evaluate/invoke/resolve can also create a new Runner. They must not be
        # conflated with normalized/JIT cursors within one Runner's bridge graph.
        self.assertEqual({id(root)for root in roots},{id(root)for runner,root in runner_roots.values()})
        self.assertEqual(len({id(root)for runner,root in runner_roots.values()}),len(runner_roots))
        self.assertEqual({id(cursor.owner)for cursor in cursors},{id(roots[0].owner)})
        for runner,root in runner_roots.values():self.assertIs(runner.iterator,root);self.assertIs(root.owner,roots[0].owner)

    def test_original_header_prepare_notification_init_and_decorator_order(self):
        source='''var events=[];class D{fn __set_name__(s,c,n){events=append(events,"named");null}}
class Base{fn __init_subclass__(c,*,tag){events=append(events,["subclass",tag]);null}}
class M(Type){fn __prepare__(m,n,b,*,tag){events=append(events,"prepare");{}}
 fn __new__(m,n,b,ns,*,tag){events=append(events,"new");Type.__new__(m,n,b,ns,tag=tag)}
 fn __init__(c,n,b,ns,*,tag){events=append(events,"init");null}}
fn decorate(c){events=append(events,"decorated");c}
@decorate
class C(Base,metaclass=M,tag=42){let d=D()}events'''
        self.check(source,['prepare','new','named',['subclass',42],'init','decorated'])

    def test_base_preflight_precedes_proxy_effects_and_keeps_original_tuple(self):
        self.check('''var seen=[];class B{}class Proxy{fn __mro_entries__(s,original){seen=append(seen,length(original));(B,)}}
let proxy=Proxy();class C(proxy){}[C.__orig_bases__[0]==proxy,issubclass(C,B),seen]''',[True,True,[1]])
        for tail in ('42','null','Proxy()'):
            source='class P{fn __mro_entries__(s,x){tool("must_not_run",1);()}}class Proxy{}class C(P(),'+tail+'){}'
            modern=snapshot(source);old=snapshot(source,legacy=True)
            self.assertEqual(modern,old);self.assertEqual(modern[0][0],'error');self.assertEqual(modern[2],[])

    def test_prepare_record_copy_and_native_mapping_identity(self):
        self.check('''let seed={a:class_member(40)};class M(Type){fn __prepare__(m,n,b){seed}}
class C(metaclass=M){let answer=a+2}[C.answer,keys(seed),keys(class_members(C))]''',[42,['a'],['a','answer']])
        self.check('''let ns=do{class NS{var values={};fn keys(s){keys(s.values)}fn __getitem__(s,k){s.values[k]}
fn __setitem__(s,k,v){s.values=set(s.values,k,v);null}};NS()};class M(Type){fn __prepare__(m,n,b){ns}}
class C(metaclass=M){let answer=42}[C.answer,ns.values.answer.value]''',[42,42])

    def test_mapping_cross_key_transform_and_setter_readback_are_live(self):
        self.check('''class NS{var values={x:class_member(1)};fn keys(s){keys(s.values)}fn __getitem__(s,k){s.values[k]}
fn __setitem__(s,k,v){s.values=set(s.values,k,v);if k=="y"{s.values=set(s.values,"x",class_member(2))}else{null};null}}
class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){let y=x;let z=x}[C.x,C.y,C.z]''',[2,1,2])
        self.check('''class NS{var values={};fn keys(s){keys(s.values)}fn __getitem__(s,k){s.values[k]}
fn __setitem__(s,k,v){let changed=if k=="a"{class_member(v.value+1,mutable=v.mutable,contract=v.contract)}else{v};s.values=set(s.values,k,changed);null}}
class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){var a:Int=40;let b=do{a=a+1;a}}[C.a,C.b]''',[43,43])

    def test_final_mutable_cell_and_class_reference_retain_exact_alias(self):
        source='''class M(Type){fn __new__(m,n,b,ns){Type.__new__(m,n,b,set(ns,"n",class_member("ready",mutable=true,contract="String")))}}
class C(metaclass=M){var n:Int=1;let read=fn(){n};fn own(s){C}}C'''
        outcomes=[]
        for legacy in (False,True):
            e=Evaluator()
            with reference()if legacy else nullcontext():klass=e.run(source)
            cell=klass.members['n'].value;reader=klass.members['read'].value;own=klass.members['own'].value
            self.assertIs(reader.env.values['n'],cell);self.assertIs(own.env.values['C'].value,klass)
            outcomes.append((cell.value,cell.contract,reader.env.values['n'] is cell,own.env.values['C'].value is klass))
        self.assertEqual(outcomes,[('ready','String',True,True)]*2)

    def test_factory_replacement_self_reference_and_nonclass_init_skip(self):
        self.check('fn factory(n,b,ns){Type(n+"Final",b,ns)}class C(metaclass=factory){fn own(s){C}}C().own()==C',True)
        self.check('var seen=0;class M(Type){fn __new__(m,n,b,ns){42}fn __init__(c,n,b,ns){seen+=1;null}}class C(metaclass=M){}[C,seen]',[42,0])

    def test_type_new_direct_calls_skip_prepare_and_choose_actual_metaclass(self):
        self.check('var seen=0;class M(Type){fn __prepare__(m,n,b){seen+=1;{}}}let A=M("A",[],{answer:class_member(42)});let B=Type.__new__(M,"B",[],{answer:class_member(41)});[seen,class_of(A)==M,B.answer]',[0,True,41])
        self.check('class M(Type){}class N(M){}class A(metaclass=M){}class B(metaclass=N){}class C(A,B,metaclass=M){}class_of(C)==N',True)

    def test_new_returned_subtype_controls_initializer_and_static_binding(self):
        self.check('''var target=null;class A{var n=0;fn __new__(cls,x){alloc_instance(target)}fn __init__(s,x){s.n=1;null}}
class B(A){fn __init__(s,x){s.n=x+1;null}}target=B;A(41).n''',42)
        self.check('class C{@classmethod\nfn __new__(cls,x){let v=alloc_instance(cls);v.n=x;v}var n=0}C(42).n',42)

    def test_type_predicates_custom_boolean_and_conflicts_preserve_errors(self):
        self.check('class M(Type){fn __instancecheck__(c,v){v==42}fn __subclasscheck__(c,v){true}}class C(metaclass=M){}[isinstance(42,C),issubclass(C,C)]',[True,True])
        for source in ['class M(Type){fn __instancecheck__(c,v){1}}class C(metaclass=M){}isinstance(42,C)','class A(Type){}class B(Type){}class X(metaclass=A){}class Y(metaclass=B){}class Z(X,Y){}']:
            self.assertEqual(snapshot(source),snapshot(source,legacy=True));self.assertEqual(snapshot(source)[0][0],'error')

    def test_decorator_expression_application_and_method_closure_order(self):
        self.check('''var seen=[];fn decorator(n){seen=append(seen,n);fn(f){seen=append(seen,n+10);f}}
@decorator(1)
@decorator(2)
fn answer(){42};[answer(),seen]''',[42,[1,2,12,11]])
        self.check('let n=40;class C{let n=1;let decorate=fn(f){f};@decorate\nfn f(self,x=n){n+x}}C().f()',41)

    def test_decorated_agent_and_all_cold_function_flavors(self):
        self.check('fn identity(f){f};@identity\nagent a{};type_of(a)','Function')
        self.check('fn identity(f){f};@identity\nasync fn a(){42};@identity\nfn* g(){yield 42};@identity\nasync fn* ag(){yield 42};[await a(),collect(g()),await acollect(ag())]',[42,[42],[42]])

    def test_prepare_new_and_init_reject_cold_results_without_body_effects(self):
        for name,params,result in [('__prepare__','m,n,b','{}'),('__new__','m,n,b,ns','42'),('__init__','c,n,b,ns','null')]:
            source='class M(Type){async fn '+name+'('+params+'){tool("must_not_run",1);'+result+'}}class C(metaclass=M){}'
            modern=snapshot(source);self.assertEqual(modern,snapshot(source,legacy=True));self.assertEqual(modern[0][0],'error');self.assertEqual(modern[2],[])

    def test_foreign_nested_factory_namespace_and_type_values_are_rejected(self):
        foreign=Evaluator().run('class F{};F')
        for source in ('class C(metaclass=input){}','new_class("C",[],{nested:[input]})','Type("C",[],{nested:[input]})','Type(input)'):
            modern=snapshot(source,inputs={'input':foreign});old=snapshot(source,legacy=True,inputs={'input':foreign})
            self.assertEqual(modern,old);self.assertEqual(modern[0][1]['code'],'ownership_error')

    def test_narrow_factory_and_namespace_do_not_widen_callbacks(self):
        def setup(e):e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        self.check('fn factory(n,b,ns){fn(x){tool("secret",x)}}let chosen=limited(fn(){factory});class C(metaclass=chosen){}try{C(42)}catch e{e.code}','capability_error',setup=setup)
        source='class C{fn secret(s){tool("secret",42)}}let chosen=limited(fn(){C});try{chosen().secret()}catch e{e.code}'
        modern=snapshot(source,setup=setup);old=snapshot(source,legacy=True,setup=setup)
        self.assertEqual(modern,old);self.assertEqual(modern[0][0],'error')
        self.assertEqual(modern[0][1]['code'],'capability_error');self.assertEqual(modern[2],[])

    def test_real_first_wait_prepare_and_class_body_share_original_schedule(self):
        self.check('class M(Type){fn __prepare__(m,n,b){tool("prepare",null);{seed:class_member(40)}}}async fn build(){class C(metaclass=M){let answer=seed+2};C.answer}await build()',42,effect_policy='first_wait')

    def test_real_first_wait_notifications_decorators_and_init_share_schedule(self):
        self.check('class D{fn __set_name__(s,c,n){tool("named",null)}}fn decorate(c){tool("decorator",null);c}async fn build(){@decorate\nclass C{let d=D();var n=0;fn __init__(s,x){s.n=tool("init",x);null}};C(42).n}await build()',42,effect_policy='first_wait')

    def test_small_step_and_cancel_thresholds_match_original_complete_observation(self):
        source='class M(Type){fn __prepare__(m,n,b){tool("prepare",{})}}class C(metaclass=M){let x=42}C.x'
        for limit in (1,63,127,255):
            with self.subTest(limit=limit):self.assertEqual(snapshot(source,limit=limit),snapshot(source,legacy=True,limit=limit))
        for cancel in (1,64,128,256):
            with self.subTest(cancel=cancel):self.assertEqual(snapshot(source,cancel=cancel),snapshot(source,legacy=True,cancel=cancel))

    def test_hard_budget_inside_naming_restores_depth_and_original_error(self):
        source='class D{fn __set_name__(s,c,n){while true{1}}}class C{let d=D()}'
        modern=snapshot(source,limit=400);old=snapshot(source,legacy=True,limit=400)
        self.assertEqual(modern,old);self.assertEqual(modern[0][1]['code'],'resource_limit');self.assertEqual(modern[5:7],(0,0))

    def test_mapping_errors_keep_original_source_location_and_no_stale_cache(self):
        self.check('class NS{fn keys(s){[]}fn __setitem__(s,k,v){null}fn __getitem__(s,k){throw "read"}}class M(Type){fn __prepare__(m,n,b){NS()}}try{class C(metaclass=M){let v=Type}}catch e{e.value}','read')
        for source in ('class M(Type){fn __prepare__(m,n,b){42}}class C(metaclass=M){}','class NS{fn keys(s){["x","x"]}fn __getitem__(s,k){1}fn __setitem__(s,k,v){null}}class M(Type){fn __prepare__(m,n,b){NS()}}class C(metaclass=M){}'):
            self.assertEqual(snapshot(source),snapshot(source,legacy=True));self.assertEqual(snapshot(source)[0][0],'error')

    def test_capture_request_roots_foreign_and_opaque_host_objects(self):
        from chaos.capture_graph import seal
        e=Evaluator();foreign=Evaluator().run('fn(){42}')
        with self.assertRaises(LanguageError)as caught:seal(e,(ClassFrameRequest('namespace_read',([foreign],'x')),),None)
        self.assertEqual(caught.exception.code,'ownership_error')
        class Opaque:
            @property
            def owner(self):raise AssertionError('host storage must remain opaque')
            def __iter__(self):raise AssertionError('host object must not be traversed')
        seal(e,(ClassFrameRequest('namespace_read',(Opaque(),'x')),),None)

    def test_prepared_namespace_env_is_an_actual_capture_root(self):
        from chaos.capture_graph import seal
        from chaos.language import Env
        e=Evaluator();env=Env();env._prepared_namespace={'hidden':Evaluator().run('fn(){42}')};env._class_name='C'
        with self.assertRaises(LanguageError)as caught:seal(e,(ClassFrameRequest('lookup_name',(env,'value')),),None)
        self.assertEqual(caught.exception.code,'ownership_error')
        absent=Env();empty=Env();empty._prepared_namespace=None
        before=e.steps;seal(e,(ClassFrameRequest('lookup_name',(absent,'value')),),None);absent_steps=e.steps-before
        before=e.steps;seal(e,(ClassFrameRequest('lookup_name',(empty,'value')),),None)
        self.assertEqual(e.steps-before,absent_steps,'explicit absent namespace preserves original charged Env walk')

    def test_atomic_capture_limits_do_not_partially_narrow_live_callbacks(self):
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        from chaos.tuple_values import TupleValue
        e=Evaluator();token=NextDescriptor(e,None);native=Native('next',lambda:None,e,generic=token,captures=())
        value=TupleValue((Evaluator().run('fn(){42}'),native))
        with self.assertRaises(LanguageError):seal(e,(ClassFrameRequest('namespace_write',({},'x',value)),),frozenset())
        self.assertIsNone(native.capabilities);self.assertIsNone(token.capabilities)
        value=TupleValue(([None]*10000,[None]*10000))
        with self.assertRaises(ResourceLimit):seal(e,(ClassFrameRequest('namespace_write',({},'x',value)),),None)

    def test_pending_write_key_controls_real_readback_after_callback_wait(self):
        from chaos import metaclasses
        from chaos.execution_class_protocols import NamespaceWriteFrame
        from chaos.suspension import CHECKPOINT,Effect
        e=Evaluator(tool_handler=lambda n,v:v)
        ns=e.run('class NS{var values={a:40,b:42};fn keys(s){keys(s.values)}fn __getitem__(s,k){s.values[k]}fn __setitem__(s,k,v){tool("wait",null);s.values=set(s.values,k,v);null}}NS()')
        with active(e,None):
            cursor=ensure_cursor(e,metaclasses.namespace_write(e,ns,'a',0));request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect)
            frame=next(f for f in cursor.frames if type(f)is NamespaceWriteFrame)
            self.assertEqual(frame.phase,'written');frame.key='b'
            self.assertEqual(e.suspension.drive(cursor),42)
            self.assertEqual(ns.fields['values'].value,{'a':0,'b':42})

    def test_pending_snapshot_key_changes_actual_accumulator_after_real_wait(self):
        from chaos import metaclasses
        from chaos.execution_class_protocols import NamespaceSnapshotFrame
        from chaos.suspension import CHECKPOINT,Effect
        e=Evaluator(tool_handler=lambda n,v:v)
        ns=e.run('class NS{fn keys(s){["left","right"]}fn __getitem__(s,k){tool("read",if k=="left"{40}else{42})}fn __setitem__(s,k,v){null}}NS()')
        with active(e,None):
            cursor=ensure_cursor(e,metaclasses.namespace_snapshot(e,ns));request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect)
            frame=next(f for f in cursor.frames if type(f)is NamespaceSnapshotFrame)
            self.assertEqual(frame.phase,'item');frame.key='renamed'
            # The pending external callback's reply is supplied once, never replayed.
            request=cursor.send(40)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect)
            try:
                request=cursor.send(42)
                while True:
                    self.assertIs(request,CHECKPOINT);request=cursor.send(None)
            except StopIteration as done:
                self.assertEqual(done.value,{'renamed':40,'right':42})

    def test_pending_naming_notification_snapshot_drives_next_real_callback(self):
        from chaos import class_creation
        from chaos.execution_class_creation import ClassFinalizeBodyFrame
        from chaos.suspension import CHECKPOINT,Effect
        e=Evaluator(tool_handler=lambda n,v:v)
        klass=e.run('class D{fn __set_name__(s,c,n){tool("name",n);null}}class C{let first=D();let second=D()}C')
        with active(e,None):
            cursor=ensure_cursor(e,class_creation._finalize(e,klass,{}));request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect)
            frame=next(f for f in cursor.frames if type(f)is ClassFinalizeBodyFrame)
            self.assertEqual(frame.phase,'notify');self.assertEqual(frame.notifications[1][0],'second')
            frame.notifications[1]=('changed',frame.notifications[1][1])
            request=cursor.send(None)
            while request is CHECKPOINT:request=cursor.send(None)
            self.assertIs(type(request),Effect);self.assertEqual(request.payload['value'],'changed')
            self.assertIs(e.suspension.drive(cursor),klass)

    def test_forceclose_pending_source_class_pops_only_own_build_and_unpublishes(self):
        from chaos.language import Parser
        from chaos.suspension import CHECKPOINT,Effect
        outcomes=[]
        for legacy in (False,True):
            e=Evaluator(tool_handler=lambda n,v:v);meta=e.run('class M(Type){}M');e.env.bind('M',meta)
            node=Parser('class Pending(metaclass=M){let value=tool("wait",42)}').program()[1][0]
            with active(e,frozenset({'outside','wait'}))as runner,reference()if legacy else nullcontext():
                previous=runner.capabilities;cursor=ensure_cursor(e,e._s_class(node,e.env));request=cursor.send(None)
                while request is CHECKPOINT:request=cursor.send(None)
                self.assertIs(type(request),Effect);stack=runner._class_build_stack
                self.assertEqual(len(stack),1);pending=stack[-1];self.assertTrue(pending['shell'].building)
                cursor.close()
                self.assertEqual(stack,[]);self.assertIs(runner.capabilities,previous)
                self.assertNotIn('Pending',e.env.values);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertEqual(runner.capture_frames,[])
                outcomes.append((e.steps,len(stack),e.depth,len(e.frames),len(runner.capture_frames),'Pending'in e.env.values))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_every_frame_declared_root_and_saved_authority_reject_foreign_state(self):
        from chaos.capture_graph import seal
        from chaos.execution_class_creation import (ClassResolveBasesFrame,ClassFinalizeFrame,ClassFinalizeBodyFrame,ClassConstructFrame,DecoratedDeclarationFrame,ClassDeclarationFrame,ClassSyntaxEvaluationFrame)
        from chaos.execution_class_protocols import (TypeCheckFrame,MappingMemberFrame,NamespaceSnapshotFrame,NamespaceReadFrame,ClassLookupNameFrame,ClassAssignNameFrame,NamespaceWriteFrame,OriginalBasesWriteFrame,ClassPrepareFrame,TypeNewFrame,MetaclassNewValueFrame,TypeDefaultCallFrame,ClassCallFrame,MetaclassCreateFrame)
        from chaos.class_creation import ClassRequest
        from chaos.metaclasses import TypeNewRequest,TypeCallRequest,TypeCheckRequest
        e=Evaluator();klass=e.native_type;env=e.env;foreign=Evaluator().run('fn(){42}')
        factories=[lambda:ClassResolveBasesFrame([]),lambda:ClassFinalizeFrame(klass,{}),lambda:ClassFinalizeBodyFrame(klass,{}),lambda:ClassConstructFrame(klass,[],{},None),lambda:DecoratedDeclarationFrame(('literal',None),env),lambda:ClassDeclarationFrame(('literal',None),env),lambda:ClassSyntaxEvaluationFrame(('literal',None),env),lambda:TypeCheckFrame(TypeCheckRequest('instance',None,klass)),lambda:MappingMemberFrame(None,'keys'),lambda:NamespaceSnapshotFrame({}),lambda:NamespaceReadFrame({},'a'),lambda:ClassLookupNameFrame(env,'a'),lambda:ClassAssignNameFrame(env,'a',None),lambda:NamespaceWriteFrame({},'a',None),lambda:OriginalBasesWriteFrame({},None),lambda:ClassPrepareFrame(klass,'C',[],{}),lambda:TypeNewFrame(TypeNewRequest(klass,'C',[],{},{})),lambda:MetaclassNewValueFrame(klass,[],{}),lambda:TypeDefaultCallFrame(TypeCallRequest(klass,[],{})),lambda:ClassCallFrame(klass,[],{}),lambda:MetaclassCreateFrame(ClassRequest('C',[],{},{}))]
        self.assertEqual({type(f())for f in factories},set(CLASS_FRAMES))
        for factory in factories:
            for field in factory().ROOTS+('result','previous_authority'):
                frame=factory();setattr(frame,field,foreign)
                with self.subTest(frame=type(frame).__name__,field=field),self.assertRaises(LanguageError)as caught:seal(e,(ExecutionCursor(e,frame),),None)
                self.assertEqual(caught.exception.code,'ownership_error')

    def test_restore_anchor_owner_epoch_and_exact_original_runner_are_checked(self):
        from chaos.capture_graph import seal
        from chaos.execution_class_creation import ClassFinalizeFrame,ClassSyntaxEvaluationFrame
        from chaos.suspension import Runner
        e=Evaluator();other=Evaluator();frame=ClassFinalizeFrame(None,{})
        frame.authority_runner=Runner(ExecutionCursor(other,ClassSyntaxEvaluationFrame(('literal',None),other.env)));frame.authority_epoch=e.suspension.epoch
        with self.assertRaises(LanguageError):seal(e,(ExecutionCursor(e,frame),),None)
        with active(e,frozenset({'outside','wait'}))as runner:
            frame=ClassFinalizeFrame(None,{});cursor=ExecutionCursor(e,frame);previous=runner.capabilities
            frame.enter_scope(e,frozenset({'wait'}));frame.authority_epoch-=1
            with self.assertRaises(LanguageError):seal(e,(cursor,),None)
            frame.authority_epoch+=1
            replacement=Runner(ExecutionCursor(e,ClassSyntaxEvaluationFrame(('literal',None),e.env)));replacement.capabilities=frozenset({'other'})
            e.suspension.current=replacement;frame.close(cursor)
            self.assertIs(runner.capabilities,previous);self.assertEqual(replacement.capabilities,frozenset({'other'}));e.suspension.current=runner

    def test_all64_real_pending_class_capture_charge_alignments_match_original(self):
        from chaos.capture_graph import seal
        from chaos.execution_class_creation import ClassDeclarationFrame
        from chaos.execution_class_protocols import ClassPrepareFrame
        from chaos.suspension import Task
        charges=[]
        for padding in range(64):
            witnesses=[]
            def setup(e):
                def capture(task):
                    self.assertIs(type(task),Task);self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                    seen=[];original=__import__('chaos.execution_frames',fromlist=['frame_roots']).frame_roots
                    def roots(frame):seen.append(type(frame));return original(frame)
                    before=e.steps
                    with patch('chaos.execution_frames.frame_roots',roots):seal(e,(task,),None)
                    witnesses.append((set(seen),e.steps-before));return None
                e.env.bind('capture_probe186',Native('capture_probe186',capture,e,captures=()))
            source='''let padding=range(PADDING);let ready=channel(1);
class M(Type){fn __prepare__(m,n,b){spawn(channel_send(ready,0));tool("class_hold",null);{seed:class_member(40)}}}
async fn work(){class C(metaclass=M){let answer=seed+2};C.answer}
let task=work();spawn(task);await channel_receive(ready);capture_probe186(task);await task'''.replace('PADDING',str(padding))
            with self.subTest(padding=padding):
                modern=snapshot(source,setup=setup,effect_policy='first_wait');old=snapshot(source,legacy=True,setup=setup,effect_policy='first_wait')
                self.assertEqual(modern[0],('value',42));self.assertEqual(modern,old)
                self.assertEqual(len(witnesses),2);self.assertEqual(witnesses[0][1],witnesses[1][1]);self.assertGreater(witnesses[0][1],0)
                self.assertIn(ClassDeclarationFrame,witnesses[0][0]);self.assertIn(ClassPrepareFrame,witnesses[0][0])
                self.assertTrue(set(CLASS_FRAMES).isdisjoint(witnesses[1][0]));charges.append(witnesses[0][1])
        self.assertGreater(len(set(charges)),1,'padding crosses real charged-node boundaries, not synthetic yields')

    def test_entered_host_scope_suppression_and_cold_throw_match_original(self):
        from chaos import agent_tools
        from chaos.language import Parser
        original_scope=agent_tools.scope
        for source,published in [('class Pending{let bad=do{throw "body"}}',False),('class Pending{let first=40;let bad=do{throw "body"}}',True)]:
            outcomes=[]
            for legacy in (False,True):
                e=Evaluator();node=Parser(source).program()[1][0];events=[];level=[0]
                class Scope:
                    def __init__(self,owner,caps):self.cm=original_scope(owner,caps)
                    def __enter__(self):
                        level[0]+=1;self.outer=level[0]==1;events.append(('enter',level[0]));return self.cm.__enter__()
                    def __exit__(self,kind,error,tb):
                        events.append(('exit',level[0],kind.__name__ if kind else None));level[0]-=1
                        result=self.cm.__exit__(kind,error,tb)
                        return bool(result or self.outer and isinstance(error,LanguageError))
                with active(e,frozenset({'outside'}))as runner,reference()if legacy else nullcontext(),patch.object(agent_tools,'scope',Scope):
                    before=e.steps;failure=ResourceLimit('cold class throw');cold=e._s_class(node,e.env)
                    with self.assertRaises(ResourceLimit)as caught:cold.throw(failure)
                    self.assertIs(caught.exception,failure);self.assertEqual(e.steps,before);self.assertEqual(events,[])
                    previous=runner.capabilities;cursor=ensure_cursor(e,e._s_class(node,e.env))
                    try:result=('value',e.suspension.drive(cursor))
                    except BaseException as error:result=('error',type(error).__name__,str(error))
                    self.assertIs(runner.capabilities,previous);self.assertEqual(level,[0]);self.assertEqual(getattr(runner,'_class_build_stack',[]),[])
                    self.assertEqual('Pending'in e.env.values,published)
                    if published:self.assertEqual(e.env.values['Pending'],40);self.assertEqual(result,('value',40))
                    else:self.assertEqual(result[0:2],('error','UnboundLocalError'))
                    self.assertTrue(any(row[0]=='exit'and row[-1]is not None for row in events))
                    outcomes.append((result,e.steps,events,e.depth,len(e.frames),len(runner.capture_frames),'Pending'in e.env.values))
            self.assertEqual(outcomes[0],outcomes[1])

    def test_generatorexit_suppression_keeps_original_publication_and_unbound_error(self):
        from chaos import agent_tools
        from chaos.language import Parser
        from chaos.suspension import CHECKPOINT,Effect
        original_scope=agent_tools.scope
        for prior,published in [('',False),('let first=40;',True)]:
            outcomes=[]
            for legacy in (False,True):
                e=Evaluator(tool_handler=lambda name,value:value);node=Parser('class Pending{'+prior+'let waiting=tool("wait",42)}').program()[1][0]
                events=[];level=[0]
                class Scope:
                    def __init__(self,owner,caps):self.cm=original_scope(owner,caps)
                    def __enter__(self):level[0]+=1;self.outer=level[0]==1;events.append(('enter',level[0]));return self.cm.__enter__()
                    def __exit__(self,kind,error,tb):
                        events.append(('exit',level[0],kind.__name__ if kind else None));level[0]-=1
                        result=self.cm.__exit__(kind,error,tb)
                        return bool(result or self.outer and isinstance(error,GeneratorExit))
                with active(e,frozenset({'outside','wait'}))as runner,reference()if legacy else nullcontext(),patch.object(agent_tools,'scope',Scope):
                    previous=runner.capabilities;cursor=ensure_cursor(e,e._s_class(node,e.env));request=cursor.send(None)
                    while request is CHECKPOINT:request=cursor.send(None)
                    self.assertIs(type(request),Effect);self.assertEqual(request.payload['name'],'wait')
                    self.assertEqual(len(runner._class_build_stack),1)
                    try:cursor.close();result=('closed',)
                    except BaseException as error:result=('error',type(error).__name__,str(error))
                    self.assertIs(runner.capabilities,previous);self.assertEqual(runner._class_build_stack,[]);self.assertEqual(level,[0])
                    self.assertEqual('Pending'in e.env.values,published)
                    if published:self.assertEqual(e.env.values['Pending'],40);self.assertEqual(result,('closed',))
                    else:self.assertEqual(result[0:2],('error','UnboundLocalError'))
                    self.assertIn(('exit',1,'GeneratorExit'),events)
                    self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertEqual(runner.capture_frames,[])
                    outcomes.append((result,e.steps,events,'Pending'in e.env.values,e.depth,len(e.frames),len(runner.capture_frames)))
            self.assertEqual(outcomes[0],outcomes[1])

    def test_wide_caller_alias_survives_narrow_meta_scope_and_real_first_wait(self):
        from chaos.capture_graph import seal
        from chaos.suspension import Task
        witnesses=[]
        def setup(e):
            e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset({'meta_hold'})))
            def capture(task,broad):
                self.assertIs(type(task),Task);self.assertNotIn(task.state,('completed','failed','cancelled','closed'))
                before=broad.capabilities;self.assertIsNone(before);seen=[]
                original=__import__('chaos.execution_frames',fromlist=['frame_roots']).frame_roots
                def roots(frame):seen.append(type(frame));return original(frame)
                with patch('chaos.execution_frames.frame_roots',roots):seal(e,(task,),None)
                self.assertIs(broad.capabilities,before);witnesses.append(set(seen));return None
            e.env.bind('capture_probe186',Native('capture_probe186',capture,e,captures=()))
        source='''let ready=channel(1);
let meta=limited(fn(){class M(Type){fn __prepare__(m,n,b){spawn(channel_send(ready,0));tool("meta_hold",null);{}}}M});
fn broad(){tool("secret",42)}
async fn work(){class C(metaclass=meta){};broad()}
let task=work();spawn(task);await channel_receive(ready);capture_probe186(task,broad);await task'''
        modern=snapshot(source,setup=setup,effect_policy='first_wait');old=snapshot(source,legacy=True,setup=setup,effect_policy='first_wait')
        self.assertEqual(modern[0],('value',42));self.assertEqual(modern,old);self.assertEqual(len(witnesses),2)
        self.assertTrue(set(CLASS_FRAMES)&witnesses[0]);self.assertTrue(set(CLASS_FRAMES).isdisjoint(witnesses[1]))
        self.assertEqual([payload['name']for kind,payload in modern[2]if kind=='tool'],['meta_hold','secret'])

    def test_std_swarm_generic_eql_pending_name_assignment_capture_matches_original(self):
        # Exact source from the unchanged legacy integration case that exposed
        # the real 20000-node regression; no weakened limit or mocked capture.
        source='import "std/swarm" as S;\nvar log=[];\nlet base=generic_method(generic_function("work"),["Int"],async fn(x){log=append(log,x);x+1});\nlet work=generic_method(base,[generic_eql(40)],async fn(next,x){let n=await next();n+1},with_next=true);\nlet old=S.create({a:40,b:41});let changed=await S.step(old,{a:work,b:work},prepare=fn(context){context.state});\n[old.states,changed.states,changed.tick,sort(log)]'
        expected=[{'a':40,'b':41},{'a':42,'b':42},1,[40,41]]
        # Preserve Framework.evaluate's actual uninstrumented optimized path;
        # debug/trace hooks would force portable JIT and could mask its graph.
        observations=[]
        for legacy in (False,True):
            e=Evaluator(step_limit=30000,timeout=20)
            with reference()if legacy else nullcontext():
                try:result=('value',serializable(e.run(source)))
                except LanguageError as error:result=('error',error.record())
            observations.append((result,e.steps,e.depth,len(e.frames)))
        self.assertEqual(observations[1][0],('value',expected));self.assertEqual(observations[0],observations[1])
        modern=snapshot(source,limit=30000);old=snapshot(source,legacy=True,limit=30000)
        self.assertEqual(old[0],('value',expected));self.assertEqual(modern,old)
