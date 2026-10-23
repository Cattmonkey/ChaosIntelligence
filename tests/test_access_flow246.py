"""Shared access algorithms consume genuine sealed CFG children."""
import copy
import unittest
from unittest.mock import patch

from chaos import suspension
from chaos.language import Evaluator,Native,LanguageError,serializable
from chaos.execution_access import ObjectAccessEvaluationFrame,ItemKeyFrame
from chaos.execution_class_creation import ClassSyntaxEvaluationFrame
from chaos.jit_flow import FlowProgram
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
from _protected_flow214 import record
import _generator_flow208 as streams
from test_native_jit import NATIVE


BOX='class Box{var value=0;fn __getitem__(self,key){tool("get",key);self.value}fn __setitem__(self,key,item){tool("set",item);self.value=item;null}}'


class AccessFlow246(unittest.TestCase):
    def same(self,body,expected,*,setup=BOX,required=()):
        rows=[]
        for target in streams.targets():
            row,states,functions=record(body,target,setup=setup)
            self.assertEqual(row[0],('value',expected));rows.append(row)
            if target not in ('raw','interpreter'):
                self.assertTrue(states)
                self.assertTrue(all(s.closed and s.domain.closed and not s.frames for s in states))
                kinds={r[1][0] for f in functions for r in f.compiled_plan.flow.instructions if r[0]=='access'}
                self.assertTrue(set(required)<=kinds,(required,kinds))
        self.assertTrue(all(row==rows[0] for row in rows),rows)
        return rows[0]

    def test_complete_six_syntax_family_routes_nested_control_and_commit(self):
        self.same('fn(){var n=0;let box=Box();n=if true{40}else{0};box.value=if true{1}else{0};box[if true{0}else{1}]=if true{n}else{0};box.value+=if true{2}else{0};[n,box.value,box[if true{0}else{1}]]}',[40,42,42],required={'assign','index','subscript','set_member','set_item','aug_assign'})

    def test_slice_multi_index_components_none_holes_and_original_protocol_order(self):
        setup='class Keys{fn __getitem__(self,key){tool("get",0);[key[0].start,key[0].stop,key[0].step,key[1],key[2].start,key[2].stop,key[2].step]}}'
        row=self.same('fn(){Keys()[do{tool("start",1);if true{1}else{0}}:do{tool("stop",5);if true{5}else{0}}:do{tool("step",2);if true{2}else{0}},do{tool("scalar",7);if true{7}else{0}},:if true{9}else{0}:]}',[1,5,2,7,None,9,None],setup=setup,required={'subscript'})
        self.assertEqual([name for name,value in row[2]],['start','stop','step','scalar','get'])

    def test_target_key_rhs_order_and_failed_rhs_do_not_commit(self):
        row=self.same('fn(){let box=Box();try{do{tool("target",0);box}[do{tool("key",0);if true{0}else{1}}]=do{tool("rhs",0);throw 99}}catch e{null};box.value}',0,required={'set_item'})
        self.assertEqual([name for name,value in row[2]],['target','key','rhs'])
        row=self.same('fn(){var value:Int=1;try{value=do{tool("rhs",0);true}}catch e{null};value}',1,setup='',required={'assign'})
        self.assertEqual(row[2],[('rhs',0)])

    def test_augmented_name_and_item_read_old_value_before_rhs_and_write_once(self):
        row=self.same('fn(){var total=40;let box=Box();box.value=40;total+=if true{2}else{0};box[tool("key",0)]+=do{tool("rhs",2);if true{2}else{0}};[total,box.value]}',[42,42],required={'aug_assign'})
        self.assertEqual([name for name,value in row[2]],['key','get','rhs','set'])
        # The original name target is resolved before RHS for augmented writes.
        row=self.same('fn(){try{missing+=do{tool("forbidden",0);42}}catch e{e.code}}','runtime_error',setup='',required={'aug_assign'})
        self.assertEqual(row[2],[])

    def successor(self,native,redirect):
        calls=[];changed=[];e=Evaluator(timeout=20,tool_handler=lambda n,v:calls.append((n,v))or v)
        plain=e.run(BOX+';fn(){let box=Box();box[tool("key",0)]=if tool("condition",true){tool("selected",42)}else{tool("alternate",41)};box.value}')
        f=streams.compile_choice(e,plain,'native' if native else 'portable');program=f.compiled_plan.flow;original=FlowProgram.portable
        def alter(pc,flags):
            row=program.instructions[pc]
            if redirect and not changed and row[0]=='branch' and calls==[('key',0),('condition',True)]:
                self.assertEqual(flags[row[1]],1);flags[row[1]]=0;changed.append(pc)
        def portable(current,pc,flags):
            if current is program:alter(pc,flags)
            return original(current,pc,flags)
        if native:
            resume=program.kernel.resume
            def actual(pc,flags):alter(pc,flags);return resume(pc,flags)
            observer=patch.object(program.kernel,'resume',actual)
        else:observer=patch.object(FlowProgram,'portable',portable)
        with streams.observe_states() as states,observer,effect_schedule('already_done'):
            try:result=e.invoke(f,[],force=True)
            finally:e.suspension.finish()
        self.assertGreater(program.native_resumes if native else program.portable_resumes,0)
        self.assertTrue(states);self.assertTrue(all(s.closed and s.domain.closed for s in states))
        return result,calls,changed

    def test_actual_portable_packet_successor_controls_write_rhs(self):
        self.assertEqual(self.successor(False,False),(42,[('key',0),('condition',True),('selected',42),('set',42)],[]))
        row=self.successor(False,True);self.assertEqual(row[:2],(41,[('key',0),('condition',True),('alternate',41),('set',41)]));self.assertEqual(len(row[2]),1)

    @unittest.skipUnless(NATIVE,'optional LLVM backend unavailable')
    def test_actual_native_packet_successor_controls_write_rhs(self):
        row=self.successor(True,True);self.assertEqual(row[:2],(41,[('key',0),('condition',True),('alternate',41),('set',41)]));self.assertEqual(len(row[2]),1)

    def pending(self,padding,target):
        held=[];seen=set();observed=[];effects=[];seal=observed_seal();e=Evaluator(timeout=20)
        def choose(f):return streams.compile_choice(e,f,target)
        def remember(task):held.append(task)
        for name,fn in [('choose',choose),('remember',remember)]:e.env.bind(name,Native(name,fn,e,captures=()))
        def effect(kind,payload,event):effects.append(payload['name']);return payload['value']
        e.effect_handler=effect;e.tool_handler=lambda n,v:v;e.trace_handler=lambda row:None
        source='class Keys{fn __getitem__(self,key){key.stop}};let work=choose(async fn(pad){Keys()[:if true{tool("held",42)}else{0}]});let task=work(range('+str(padding)+'));remember(task);spawn(task);await task'
        with streams.observe_states() as states,effect_schedule('first_wait'):
            wait=suspension.wait
            def waiting(futures,*args,**kwargs):
                task=held[0];future=task.runner.future
                if future is not None and future in futures and id(future)not in seen:
                    seen.add(id(future));self.assertFalse(future.done())
                    frames=task.runner.iterator.frames
                    keys=[f for f in frames if type(f)is ItemKeyFrame and f.phase=='part']
                    self.assertEqual(len(keys),1);key=keys[0];self.assertEqual(key.index,1);self.assertEqual(key.parts,[None])
                    if target=='portable':
                        parent=key._flow_access_parent;state=parent._flow_boundary.flow_state;state.validate(full=True)
                        self.assertEqual(parent._flow_access_segment,('key',(1,)))
                        self.assertIs(parent._flow_access_child,key)
                        for field,bad in [('index',999),('node',('slice',None,None,None)),('_flow_access_path',(9,))]:
                            previous=getattr(key,field);setattr(key,field,bad)
                            try:
                                with self.assertRaises(LanguageError)as caught:state.validate(full=True)
                                self.assertEqual(caught.exception.code,'ownership_error')
                            finally:setattr(key,field,previous)
                        previous=key.parts[0];key.parts[0]=1
                        try:
                            with self.assertRaises(LanguageError)as caught:state.validate(full=True)
                            self.assertEqual(caught.exception.code,'ownership_error')
                        finally:key.parts[0]=previous
                        previous=parent._flow_access_child
                        fake=ItemKeyFrame(key.node,key.env,parent,key._flow_access_path);fake.owner=e;fake.phase=key.phase;fake.parts=list(key.parts);fake.index=key.index;parent._flow_access_child=fake
                        try:
                            with self.assertRaises(LanguageError)as caught:state.validate(full=True)
                            self.assertEqual(caught.exception.code,'ownership_error')
                        finally:parent._flow_access_child=previous
                    _,delta,paths=seal(e,(task,),None);observed.append((delta,paths))
                return wait(futures,*args,**kwargs)
            with patch.object(suspension,'wait',waiting):result=e.run(source)
        self.assertEqual(result,42);self.assertEqual(effects,['held']);self.assertEqual(len(observed),1)
        self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
        return observed

    def test_all65_capture_offsets_and_actual_key_metadata_are_validated(self):
        for padding in range(65):
            with self.subTest(padding=padding):self.assertEqual(self.pending(padding,'portable'),self.pending(padding,'raw'))

    def test_original_stream_close_during_key_or_rhs_does_not_commit_or_replay(self):
        for flavor,nextop,closeop in [('fn*','next(stream)','close(stream)'),('async fn*','await anext(stream)','await aclose(stream)')]:
            for location in ('key','rhs'):
                operand='do{let feedback=yield 7;tool("after",feedback);if true{feedback}else{0}}'
                key=operand if location=='key' else '0'
                rhs=operand if location=='rhs' else '42'
                source=BOX+';let box=Box();let work=choose('+flavor+'(){try{box['+key+']='+rhs+';yield box.value}finally{tool("cleanup",1)}});let stream=work();mark(stream);let first='+nextop+';'+closeop+';'+closeop+';[first,'+nextop+',box.value]'
                raw,_,_,_=streams.run(source,'raw')
                self.assertEqual(raw[0],('value',[{'done':False,'value':7},{'done':True,'value':None},0]))
                self.assertEqual(raw[2],[('cleanup',1)])
                for target in streams.targets()[1:]:
                    with self.subTest(flavor=flavor,location=location,target=target):
                        actual,states,functions,_=streams.run(source,target)
                        self.assertEqual(actual,raw);streams.assert_compiled(self,target,states,functions)
