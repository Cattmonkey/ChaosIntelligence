"""Admission frontiers reject before installing user-visible continuation effects."""
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError,ResourceLimit

class ContinuationAdmission226(unittest.TestCase):
    def evaluator(self,events):return Evaluator(timeout=20,step_limit=200000,tool_handler=lambda n,v:events.append((n,v))or v)

    def test_capture_frame_quota_raises_hard_limit_before_receiver_and_cleanup(self):
        events=[];e=self.evaluator(events)
        source='''let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){tool("before",0)},fn(){1+capture_continuation(p,fn(k){tool("receiver",0);2})},fn(){tool("after",0)})})'''
        with patch('chaos.execution_continuations.MAX_FRAMES',2),self.assertRaises(ResourceLimit):e.run(source)
        self.assertEqual(events,[('before',0)]);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.control_state.stack)

    def test_invocation_quota_rejects_before_another_wind_or_suffix_effect(self):
        events=[];e=self.evaluator(events)
        source='''var saved=null;let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){tool("before",0)},fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("body",x);x},fn(){tool("after",0)})});saved(2);saved(3)'''
        with patch('chaos.execution_continuations.MAX_INVOCATIONS',1),self.assertRaises(ResourceLimit):e.run(source)
        self.assertEqual(events,[('before',0),('body',1),('after',0),('before',0),('body',2),('after',0)]);self.assertEqual(e.depth,0);self.assertFalse(e.suspension.control_state.stack)

    def test_image_container_corruption_rejects_before_a_new_activation(self):
        for corruption in ('rows','containers'):
            with self.subTest(corruption=corruption):
                events=[];e=self.evaluator(events);held=[]
                def damage(k):held.append(k);setattr(k.image,corruption,object())
                e.env.bind('damage226',Native('damage226',damage,e,captures=()))
                source='''var saved=null;let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){tool("before",0)},fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("body",x);x},fn(){tool("after",0)})});try{damage226(saved);saved(3)}catch e{e.code}'''
                self.assertEqual(e.run(source),'ownership_error');self.assertEqual(len(held),1)
                self.assertEqual(events,[('before',0),('body',1),('after',0)]);self.assertEqual(e.depth,0);self.assertFalse(e.suspension.control_state.stack)

    def test_saved_function_definition_mutation_rejects_without_prefix_replay(self):
        events=[];e=self.evaluator(events);held=[]
        replacement=Evaluator().run('fn(){tool("forbidden",99);0}')
        def replace(fn):held.append(fn);fn.body=replacement.body
        e.env.bind('replace226',Native('replace226',replace,e,captures=()))
        source='''var saved=null;var prefix=0;let p=prompt_tag();fn work(){prefix+=1;let x=capture_continuation(p,fn(k){saved=k;1});tool("suffix",x);x};call_prompt(p,work);let error=try{replace226(work);saved(2)}catch e{e.code};[error,prefix]'''
        self.assertEqual(e.run(source),['definition_changed',1]);self.assertEqual(len(held),1);self.assertEqual(events,[('suffix',1)]);self.assertEqual(e.depth,0);self.assertFalse(e.suspension.control_state.stack)

    def test_nested_before_failure_rolls_back_only_already_entered_winds(self):
        events=[];e=self.evaluator(events)
        source='''var saved=null;var fail=false;let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){tool("before-A",0)},fn(){dynamic_wind(fn(){tool("before-B",0);if fail{throw "inner before failed"}else{null}},fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("body",x);x},fn(){tool("after-B",0)})},fn(){tool("after-A",0)})});fail=true;let error=try{saved(3)}catch e{e.value};fail=false;[error,saved(4)]'''
        self.assertEqual(e.run(source),['inner before failed',4])
        self.assertEqual(events,[('before-A',0),('before-B',0),('body',1),('after-B',0),('after-A',0),('before-A',0),('before-B',0),('after-A',0),('before-A',0),('before-B',0),('body',4),('after-B',0),('after-A',0)])
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.control_state.stack)
