"""Item access is distinct syntax with cooperative owned user protocols."""
import threading
import unittest
from dataclasses import FrozenInstanceError

from chaos.language import Evaluator, Parser, LanguageError, ResourceLimit, serializable
from chaos.indexing import Slice, slice_key, slice_indices, native_get


class IndexingProtocolTests(unittest.TestCase):
    def run_code(self, source, bindings=None, **kwargs): return Evaluator(**kwargs).run(source, bindings)

    def test_parser_distinguishes_dot_bracket_and_legacy(self):
        self.assertEqual([Parser(s).program()[1][0][0] for s in ['a.x','a.[key]','a[key]','a.x=1','a.[key]=1','a[key]=1']],['index','index','subscript','set_member','set_member','set_item'])
        self.assertEqual(Parser('a[::]').program()[1][0][2],('slice',None,None,None))
        self.assertEqual(Parser('a[1:]').program()[1][0][2][1],('literal',1))
        self.assertEqual(Parser('a[key]',legacy_items=True).program()[1][0][0],'index')
        self.assertEqual(Parser('a[key]=1',legacy_items=True).program()[1][0][0],'set_member')
        for source in ['a[]','a[1,,2]','a[1:2:3:4]','a.[1:2]','a[*xs]']:
            with self.subTest(source=source),self.assertRaises(LanguageError):Parser(source).program()
        with self.assertRaises(LanguageError):Parser('a[:]',legacy_items=True).program()
        with self.assertRaises(LanguageError):Parser('a[1,2]',legacy_items=True).program()

    def test_native_negative_indices_and_record_string_keys(self):
        self.assertEqual(self.run_code('[[1,2,3][-1],"😀x"[-2],{x:42}["x"],getitem(value=[40,42],key=-1)]'),[3,'😀',42,42])
        for source in ['[1][true]','[1][1.0]','{x:1}[0]','[1][-2]','[1].[(-1)]']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)

    def test_all_slice_shapes_and_unicode(self):
        self.assertEqual(self.run_code('let x=[0,1,2,3,4];[x[:],x[1:],x[:3],x[1:4],x[::2],x[::-1],x[4:0:-2],x[-4:-1],x[99:-99:-1],"😀ab"[::-1]]'),[[0,1,2,3,4],[1,2,3,4],[0,1,2],[1,2,3],[0,2,4],[4,3,2,1,0],[4,2],[1,2,3],[4,3,2,1,0],'ba😀'])
        for value in [[],[1],[1,2,3,4],'','😀abcd']:
            for start in [None,-8,-1,0,2,8]:
                for stop in [None,-8,-1,0,2,8]:
                    for step in [None,-3,-1,1,2,9]:
                        spec=Slice(start,stop,step)
                        self.assertEqual(native_get(value,spec),value[slice(start,stop,step)])

    def test_slice_constructor_contract_fields_equality_and_normalization(self):
        self.assertEqual(self.run_code('let s:Slice=slice_key(start=null,stop=null,step=-1);[type_of(s),s.start,s.stop,s.step,slice_indices(spec=s,length=5),s==slice_key(null,null,-1),s!=slice_key(),getattr(s,"step"),s.["step"]]'),['Slice',None,None,-1,[4,-1,-1],True,True,-1,-1])
        self.assertEqual(slice_indices(Slice(None,None,2),1<<100),[0,1<<100,2])
        for source in ['slice_key(true)','slice_key(1.0)','slice_key(null,null,0)','[1][::0]','[1][true:]','slice_indices(slice_key(),-1)','slice_indices(slice_key(),true)','slice_key().start=1','delattr(slice_key(),"step")','slice_key()["start"]']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)
        with self.assertRaises(ResourceLimit):slice_key(1<<14000)
        with self.assertRaises(ResourceLimit):slice_indices(Slice(),1<<14000)
        with self.assertRaises(FrozenInstanceError):Slice().start=1

    def test_slice_transport_is_inert_and_survives_epoch_as_data(self):
        seen=[]
        self.assertEqual(self.run_code('let s=slice_key(1,5,2);tool("save",s);[parse_json(json_stringify(s))==s,type_of(parse_json(json_stringify(s)))]',tool_handler=lambda n,v:seen.append(v)),[False,'Record'])
        self.assertEqual(seen,[{'$slice':{'start':1,'stop':5,'step':2}}])
        spec=Evaluator().run('slice_key(1,5,2)')
        self.assertEqual(Evaluator().run('[0,1,2,3,4][input]',{'input':spec}),[1,3])
        self.assertEqual(serializable([spec]),[{'$slice':{'start':1,'stop':5,'step':2}}])

    def test_property_item_and_legacy_are_distinct(self):
        source='class C{let x=40;let p=property(fn(self){41});fn __getitem__(self,key){["item",key]}}let c=C();[c.x,c.p,c["x"],c["p"],c.["x"],c.["p"],getitem(c,"x")]'
        self.assertEqual(self.run_code(source),[40,41,['item','x'],['item','p'],40,41,['item','x']])
        self.assertEqual(self.run_code('class C{let x=42}C()["x"]'),42)

    def test_item_assignment_and_legacy_fallback_return_values(self):
        source='class C{var x=0;fn __setitem__(self,key,item){self.x=item;null}}let c=C();let result=do{c["x"]=42};let value=c.x;c.["x"]=41;[result,value,c.x,setitem(c,"x",40),c.x]'
        self.assertEqual(self.run_code(source),[None,42,41,None,40])
        self.assertEqual(self.run_code('class C{var x=0}let c=C();let value=do{c["x"]=42};[value,c.x]'),[42,42])
        for source in ['class C{var x=0}setitem(C(),"x",42)','class C{var x=0}delitem(C(),"x")']:
            with self.assertRaises(LanguageError):self.run_code(source)

    def test_delete_is_composable_and_null_checked(self):
        source='class C{var contents={x:42};fn __getitem__(self,key){self.contents[key]}fn __delitem__(self,key){self.contents=remove(self.contents,key);null}}let c=C();let value=c["x"];let remove_x=partial(delitem,c,"x");[value,remove_x(),has(c.contents,"x")]'
        self.assertEqual(self.run_code(source),[42,None,False])
        with self.assertRaises(LanguageError):self.run_code('class C{fn __delitem__(self,key){42}}delitem(C(),"x")')

    def test_target_key_slice_rhs_evaluated_once_in_order(self):
        source='''var events=[];fn mark(name,value){events=append(events,name);value}class C{fn __setitem__(self,key,item){events=append(events,[key.start,key.stop,key.step,item]);null}}
mark("object",C())[mark("start",1):mark("stop",4):mark("step",2)]=mark("rhs",42);events'''
        self.assertEqual(self.run_code(source),['object','start','stop','step','rhs',[1,4,2,42]])
        self.assertEqual(self.run_code('var seen=[];fn key(){seen=append(seen,1);"x"}class C{fn __getitem__(self,k){k}}let result=C()[key()];[result,seen]'),['x',[1]])

    def test_user_slice_receives_unmodified_boundaries(self):
        self.assertEqual(self.run_code('class C{fn __getitem__(self,key:Slice){[key.start,key.stop,key.step,slice_indices(key,4)]}}C()[-9::-2]'),[-9,None,-2,[-1,-1,-2]])
        self.assertEqual(self.run_code('class C{var seen=null;fn __setitem__(self,key:Slice,item){self.seen=[key.start,key.stop,key.step,item];null}}let c=C();c[:3:2]=[1,2];c.seen'),[None,3,2,[1,2]])

    def test_noncallable_and_throwing_hooks_never_fall_back(self):
        for source in ['class C{let x=42;let __getitem__=null}C()["x"]','class C{var x=0;let __setitem__=42}C()["x"]=1','class C{let x=42;fn __getitem__(self,key){throw "hook"}}C()["x"]']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.run_code(source)
        self.assertEqual(self.run_code('class C{let x=42;fn __getitem__(self,key){NotImplemented}}C()["x"]==NotImplemented'),True)

    def test_descriptors_mro_dynamic_class_slots_and_instance_shadowing(self):
        source='class D{fn __get__(self,obj,owner){fn(key){[obj.x,key]}}}class A{let __getitem__=D();let x=42}class B{}class C(A,B){}C()["x"]'
        self.assertEqual(self.run_code(source),[42,'x'])
        self.assertEqual(self.run_code('class C{var __getitem__=fn(key){1}}let c=C();c.__getitem__=fn(key){0};let first=c[0];C.__getitem__=fn(key){2};[first,c[0]]'),[1,2])
        self.assertEqual(self.run_code('class C{let x=42;fn __getitem__(self,key){0}}class D(C){}[C["x"],C.["x"],super(D,D())["x"]]'),[42,42,42])

    def test_native_mutation_rejected_without_changing_values(self):
        self.assertEqual(self.run_code('let xs=[1,2];let record={x:1};try{xs[0]=42}catch e{null};try{record["x"]=42}catch e{null};[xs,record]'),[[1,2],{'x':1}])
        for source in ['setitem([1],0,42)','delitem({x:1},"x")','"abc"[0]="x"','[1,2][:]=[3]']:
            with self.assertRaises(LanguageError):self.run_code(source)
        self.assertEqual(self.run_code('[slice([1,2,3],1),slice("abc",0,2),set({x:1},"x",2),append([1],2)]'),[[2,3],'ab',{'x':2},[1,2]])

    def test_async_getter_and_streams_require_explicit_consumption(self):
        self.assertEqual(self.run_code('var calls=0;class C{async fn __getitem__(self,key)->Int{calls=calls+1;key}}let task=C()[42];let before=calls;[before,type_of(task),await task,calls]'),[0,'Task',42,1])
        self.assertEqual(self.run_code('class C{fn* __getitem__(self,key){yield key}}collect(C()[42])'),[42])
        self.assertEqual(self.run_code('class C{async fn* __getitem__(self,key){yield key}}await acollect(C()[42])'),[42])

    def test_async_and_generator_writes_rejected_before_body(self):
        for flavor in ['async fn','fn*','async fn*']:
            for method,params,call in [('__setitem__','self,key,item','setitem(C(),0,42)'),('__delitem__','self,key','delitem(C(),0)')]:
                source='var calls=0;class C{'+flavor+' '+method+'('+params+'){calls=calls+1;null}}try{'+call+'}catch e{null};calls'
                with self.subTest(flavor=flavor,method=method):self.assertEqual(self.run_code(source),0)
        self.assertEqual(self.run_code('var calls=0;async fn work(){calls=1;null}class C{fn __setitem__(self,k,v){work()}}try{setitem(C(),0,1)}catch e{null};calls'),0)

    def test_bound_writing_protocol_provides_descriptor_effects_once(self):
        source='var seen=[];class D{fn __get__(self,obj,owner){seen=append(seen,"bind");fn(key,item){seen=append(seen,[key,item]);null}}}class C{let __setitem__=D()}setitem(C(),"x",42);seen'
        self.assertEqual(self.run_code(source),['bind',['x',42]])

    def test_shared_native_dag_read_does_not_expand(self):
        source='fn nest(n){if n==0{[1]}else{let a=nest(n-1);[a,a]}}let a=nest(35);let result=[a][-1];result==a'
        self.assertEqual(self.run_code(source,step_limit=2000),True)
        self.assertEqual(self.run_code(source.replace('[a][-1]','getitem([a],-1)'),step_limit=2000),True)
        source='fn nest(n){if n==0{[1]}else{let a=nest(n-1);[a,a]}}let a=nest(35);let result=[a,a][1:];result[0]==a'
        self.assertEqual(self.run_code(source,step_limit=2000),True)

    def test_nested_foreign_keys_and_results_are_rejected(self):
        foreign=Evaluator().run('class C{}C()')
        for source,bindings in [('class C{fn __getitem__(self,key){42}}C()[input]',{'input':[foreign]}),('[input][0]',{'input':{'nested':foreign}}),('class C{fn __getitem__(self,key){input}}C()[0]',{'input':[foreign]}),('class C{let x=[input]}C["x"]',{'input':foreign})]:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:self.run_code(source,bindings)
            self.assertEqual(caught.exception.code,'ownership_error')
        task=Evaluator().run('async fn f(){42}f()')
        with self.assertRaises(LanguageError):self.run_code('class C{fn __getitem__(self,key){42}}C()[input]',{'input':{'task':task}})

    def test_protocol_recursion_is_bounded(self):
        with self.assertRaises(ResourceLimit):self.run_code('class C{fn __getitem__(self,key){self[key]}}C()[0]',step_limit=3000)

    def test_agent_protocols_share_current_task_and_permissions(self):
        seen=[];main=threading.get_ident()
        def agent(name,config,value):seen.append((value,threading.get_ident()));return value
        source='agent worker{};class C{fn __getitem__(self,key){worker(key)}fn __setitem__(self,key,item){worker(item);null}fn __delitem__(self,key){worker(key);null}}async fn work(){let c=C();let value=c[40];c[0]=41;delitem(c,42);value}await work()'
        self.assertEqual(self.run_code(source,agent_handler=agent),40);self.assertEqual([x for x,t in seen],[40,41,42]);self.assertTrue(all(t!=main for x,t in seen))
        with self.assertRaises(LanguageError):self.run_code('agent worker{};class C{fn __getitem__(self,key){worker(key)}}C()[42]')

    def test_independent_async_item_operations_really_overlap(self):
        barrier=threading.Barrier(2);seen=[]
        def effect(kind,payload,cancel):seen.append(threading.get_ident());barrier.wait(timeout=2);return payload['value']
        source='class C{async fn __getitem__(self,key){tool("read",key)}}let c=C();await gather([c[41],c[42]])'
        self.assertEqual(self.run_code(source,tool_handler=lambda *a:None,effect_handler=effect),[41,42]);self.assertEqual(len(set(seen)),2)

    def test_cancel_during_item_effect_drains_and_restores_frames(self):
        started=threading.Event();finished=[];ev=Evaluator(tool_handler=lambda *a:None);target=[None]
        def effect(kind,payload,cancel):started.set();cancel.wait(timeout=2);finished.append(True);return 42
        def trace(event):
            if event['event']=='task.started' and target[0] is None:target[0]=event['taskId']
        def hook(node,env,frames):
            if started.is_set():
                for task in ev.suspension.tasks:
                    if task.id==target[0]:task.cancel_event.set()
        ev.effect_handler=effect;ev.trace_handler=trace;ev.debug_hook=hook
        source='var cleaned=0;class C{fn __getitem__(self,key){try{tool("read",key)}finally{cleaned=1}}}async fn work(){C()[42]}spawn(work());async fn other(){var i=0;while i<100{i=i+1}}await other();cleaned'
        self.assertEqual(ev.run(source),0);self.assertEqual(finished,[True]);self.assertEqual(ev.frames,[]);self.assertEqual(ev.depth,0);self.assertIsNone(ev.suspension.pool)

    def test_native_slice_result_bound_before_allocation(self):
        with self.assertRaises(ResourceLimit):native_get([0]*10001,Slice())
        with self.assertRaises(ResourceLimit):native_get('x'*250001,Slice())
        self.assertEqual(native_get([0]*10001,Slice(0,1)),[0])
