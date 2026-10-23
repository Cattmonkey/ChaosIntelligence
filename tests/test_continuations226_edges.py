"""Literal source oracles for composition boundaries; no product execution by author."""
import unittest
from chaos.language import Evaluator,Native,TailCall,LanguageError

class ContinuationCompositionEdges226(unittest.TestCase):
    def value(self,source,events=None):
        options={} if events is None else {'tool_handler':lambda name,value:events.append((name,value)) or value}
        e=Evaluator(timeout=20,step_limit=200000,**options)
        result=e.run(source)
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
        self.assertFalse(e.suspension.control_state.stack)
        return result

    def test_original_snapshot_forward_name_rejection_and_post_capture_local_closures(self):
        source='''var k=null;let p=prompt_tag();let initial=call_prompt(p,fn(){fn read(){later};let x=capture_continuation(p,fn(c){k=c;1});let later=x;read()});[initial,k(2),k(3)]'''
        with self.assertRaises(LanguageError) as caught:self.value(source)
        self.assertEqual(caught.exception.code,'runtime_error')
        self.assertIn('未定义名称 later',caught.exception.message)
        source='''var k=null;let p=prompt_tag();let first=call_prompt(p,fn(){let x=capture_continuation(p,fn(c){k=c;1});let later=x;fn(){later}});let second=k(2);let third=k(3);[first(),second(),third(),first()]'''
        self.assertEqual(self.value(source),[1,2,3,1])

    def test_nested_capture_inside_saved_suffix_keeps_independent_images(self):
        source='''var outer=null;var inner=null;var prefix=0;let p=prompt_tag();let initial=call_prompt(p,fn(){prefix+=1;let x=capture_continuation(p,fn(k){outer=k;1});let y=capture_continuation(p,fn(k){inner=k;2});[x,y]});let first_inner=inner;let again=outer(10);let second_inner=inner;[initial,again,first_inner(30),second_inner(40),prefix]'''
        self.assertEqual(self.value(source),[[1,2],[10,2],[1,30],[10,40],1])

    def test_abort_targets_fresh_composition_delimiter_after_saved_wind_cleanup(self):
        events=[]
        source='''var saved=null;let p=prompt_tag();let initial=call_prompt(p,fn(){dynamic_wind(fn(){tool("before",0)},fn(){let x=capture_continuation(p,fn(k){saved=k;1});if x<0{abort_prompt(p,99)}else{tool("body",x);x}},fn(){tool("after",0)})});[initial,saved(-1),saved(2)]'''
        self.assertEqual(self.value(source,events),[1,99,2])
        self.assertEqual(events,[('before',0),('body',1),('after',0),('before',0),('after',0),('before',0),('body',2),('after',0)])

    def test_capture_during_saved_wind_before_rejects_without_partial_activation(self):
        events=[]
        source='''var saved=null;var retry=false;var receivers=0;let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){tool("before",0);if retry{capture_continuation(p,fn(k){receivers+=1;0})}else{null}},fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("body",x);x},fn(){tool("after",0)})});retry=true;let error=try{saved(2)}catch e{e.code};retry=false;[error,receivers,saved(3)]'''
        self.assertEqual(self.value(source,events),['continuation_barrier_error',0,3])
        self.assertEqual(events,[('before',0),('body',1),('after',0),('before',0),('before',0),('body',3),('after',0)])

    def test_capability_projected_view_and_current_caller_cannot_widen_saved_authority(self):
        effects=[]
        e=Evaluator(timeout=20,step_limit=200000,tool_handler=lambda name,value:effects.append((name,value)) or value)
        e.env.bind('restricted226',Native('restricted226',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        source='''var saved=null;let p=prompt_tag();call_prompt(p,fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("secret",x);x});let limited=restricted226(fn(){saved});let view_error=try{limited(2)}catch e{e.code};let caller_error=try{restricted226(fn(){saved(4)})}catch e{e.code};[view_error,caller_error,saved(3),limited==saved]'''
        self.assertEqual(e.run(source),['capability_error','capability_error',3,True])
        self.assertEqual(effects,[('secret',1),('secret',3)])
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.control_state.stack)
