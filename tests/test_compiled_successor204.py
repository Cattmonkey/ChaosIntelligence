"""The CFG backend must choose the actual child, not only increment counters.

Trusted-backend fault injection deliberately flips one conditional flag before
calling the real backend. The opposite effect proves actual successor control.
"""
import unittest
from unittest.mock import patch
from chaos.language import Evaluator
from chaos.jit import compile_function
from chaos.jit_flow import FlowProgram
from chaos.native_jit import BackendUnavailable
from _effect_schedule158 import effect_schedule

class CompiledSuccessor204(unittest.TestCase):
    def execute(self,flavor,native,redirect):
        effects=[];changed=[]
        e=Evaluator(tool_handler=lambda name,value:effects.append((name,value))or value,step_limit=10000,timeout=15)
        fn=e.run(flavor+'(flag){if flag{tool("yes",11)}else{tool("no",22)}}')
        if native:fn=compile_function(e,fn,'native-flow')
        else:
            with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('portable-test')):
                fn=compile_function(e,fn,'auto-flow')
        program=fn.compiled_plan.flow
        def change(pc,flags):
            instruction=program.instructions[pc]
            if redirect and instruction[0]=='branch':
                flags[instruction[1]]=1-flags[instruction[1]];changed.append(pc)
        original=FlowProgram.portable
        def portable(current,pc,flags):
            if current is program:change(pc,flags)
            return original(current,pc,flags)
        if native:
            kernel=program.kernel;resume=kernel.resume
            def routed(pc,flags):change(pc,flags);return resume(pc,flags)
            patcher=patch.object(kernel,'resume',routed)
        else:patcher=patch.object(FlowProgram,'portable',portable)
        with patcher,effect_schedule('already_done'):
            try:result=e.invoke(fn,[False],force=True)
            finally:e.suspension.finish()
        self.assertGreater(program.native_resumes if native else program.portable_resumes,0)
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
        return result,effects,changed

    def test_portable_cfg_successor_selects_real_effect_for_sync_and_async(self):
        for flavor in('fn','async fn'):
            with self.subTest(flavor=flavor):
                normal=self.execute(flavor,False,False)
                forced=self.execute(flavor,False,True)
                self.assertEqual(normal,(22,[('no',22)],[]))
                self.assertEqual(forced[:2],(11,[('yes',11)]));self.assertEqual(len(forced[2]),1)

    def test_native_cfg_successor_selects_real_effect_for_sync_and_async(self):
        from test_native_jit import NATIVE
        if not NATIVE:self.skipTest('Optional LLVM backend unavailable')
        for flavor in('fn','async fn'):
            with self.subTest(flavor=flavor):
                normal=self.execute(flavor,True,False)
                forced=self.execute(flavor,True,True)
                self.assertEqual(normal,(22,[('no',22)],[]))
                self.assertEqual(forced[:2],(11,[('yes',11)]));self.assertEqual(len(forced[2]),1)
