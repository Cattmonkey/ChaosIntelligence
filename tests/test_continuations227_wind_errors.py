"""Fresh wind obligations preserve original causal context on replacement failure."""
import unittest
from chaos.language import Evaluator

class ContinuationWindErrors227(unittest.TestCase):
    def test_nested_before_failure_then_outer_after_replaces_error_context(self):
        events=[];e=Evaluator(timeout=20,step_limit=200000,tool_handler=lambda name,value:events.append((name,value)) or value)
        source='''var saved=null;var fail=false;let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){tool("before-A",0)},fn(){dynamic_wind(fn(){tool("before-B",0);if fail{throw "inner-before"}else{null}},fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("body",x);x},fn(){tool("after-B",0)})},fn(){let pending=current_error();tool("after-A",if pending==null{null}else{pending.value});if fail{throw "outer-after"}else{null}})});fail=true;let error=try{saved(2)}catch error{[error.value,current_error().context.value,current_error().context.context]};fail=false;[error,saved(3)]'''
        self.assertEqual(e.run(source),[['outer-after','inner-before',None],3])
        self.assertEqual(events,[('before-A',0),('before-B',0),('body',1),('after-B',0),('after-A',None),('before-A',0),('before-B',0),('after-A','inner-before'),('before-A',0),('before-B',0),('body',3),('after-B',0),('after-A',None)])
        self.assertIsNone(e.error_context);self.assertIsNone(e.handled_error)
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.control_state.stack)

    def test_suffix_body_error_and_after_replacement_preserve_exact_chain_each_time(self):
        events=[];e=Evaluator(timeout=20,step_limit=200000,tool_handler=lambda name,value:events.append((name,value)) or value)
        source='''var saved=null;var fail=false;let p=prompt_tag();let initial=call_prompt(p,fn(){dynamic_wind(fn(){tool("before",0)},fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("body",x);if x<0{throw "body"}else{x}},fn(){let pending=current_error();tool("after",if pending==null{null}else{pending.value});if fail{throw "after"}else{null}})});fail=true;let error=try{saved(-1)}catch error{[error.value,current_error().context.value,current_error().context.context]};fail=false;[initial,error,saved(3)]'''
        self.assertEqual(e.run(source),[1,['after','body',None],3])
        self.assertEqual(events,[('before',0),('body',1),('after',None),('before',0),('body',-1),('after','body'),('before',0),('body',3),('after',None)])
        self.assertIsNone(e.error_context);self.assertIsNone(e.handled_error);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.control_state.stack)

    def test_three_wind_replacement_updates_remaining_outer_cleanup_context(self):
        events=[];e=Evaluator(timeout=20,step_limit=200000,tool_handler=lambda name,value:events.append((name,value)) or value)
        source='''var saved=null;var fail=false;let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){tool("before-A",0)},fn(){dynamic_wind(fn(){tool("before-B",0)},fn(){dynamic_wind(fn(){tool("before-C",0);if fail{throw "C-before"}else{null}},fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("body",x);x},fn(){tool("after-C",0)})},fn(){let pending=current_error();tool("after-B",if pending==null{null}else{pending.value});if fail{throw "B-after"}else{null}})},fn(){let pending=current_error();tool("after-A",if pending==null{null}else{[pending.value,pending.context.value]})})});fail=true;let error=try{saved(2)}catch error{[error.value,current_error().context.value]};fail=false;[error,saved(3)]'''
        self.assertEqual(e.run(source),[['B-after','C-before'],3])
        self.assertEqual(events,[('before-A',0),('before-B',0),('before-C',0),('body',1),('after-C',0),('after-B',None),('after-A',None),('before-A',0),('before-B',0),('before-C',0),('after-B','C-before'),('after-A',['B-after','C-before']),('before-A',0),('before-B',0),('before-C',0),('body',3),('after-C',0),('after-B',None),('after-A',None)])
        self.assertIsNone(e.error_context);self.assertIsNone(e.handled_error);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[]);self.assertFalse(e.suspension.control_state.stack)
