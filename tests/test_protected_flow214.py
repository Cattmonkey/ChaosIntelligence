"""Independent values, transfers and real protected backend successors."""
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError
from chaos.jit_flow import FlowProgram
from _effect_schedule158 import effect_schedule
import _generator_flow208 as streams
import _protected_flow214 as fixture

class ProtectedFlow214(unittest.TestCase):
    def same(self,body,expected,**options):
        rows=[]
        for target in fixture.TARGETS():
            with self.subTest(target=target):
                row,states,functions=fixture.record(body,target,**options)
                self.assertEqual(row[0],('value',expected));fixture.compiled(self,target,states,functions);rows.append(row)
        self.assertEqual(len(rows),len(fixture.TARGETS()));self.assertTrue(all(row==rows[0]for row in rows),rows)
        return rows[0]

    def test_normal_and_handled_completion_select_else_once_and_keep_finally_value(self):
        self.same('fn(){var seen=[];var result=null;try{seen=append(seen,"body");result=20}catch e{result=999}else{seen=append(seen,"else");result=42}finally{seen=append(seen,"cleanup");99};[result,seen]}',[42,['body','else','cleanup']])
        self.same('fn(){var seen=[];var result=null;try{throw 40}catch {value:n,.._} if do{seen=append(seen,"false-guard");false}{result=999}catch {value:n,.._} if do{seen=append(seen,"true-guard");true}{seen=append(seen,"handler");result=n+2}else{seen=append(seen,"forbidden-else");result=-1}finally{seen=append(seen,"cleanup");99};[result,seen]}',[42,['false-guard','true-guard','handler','cleanup']])
        self.same('fn(){try{false}finally{99}}',False)
        self.same('fn(){try{}finally{99}}',None)

    def test_protected_tail_return_and_handler_calls_finish_before_cleanup(self):
        row=self.same('fn(){try{return read()}finally{n=2;tool("cleanup",n)}}',1,setup='var n=1;fn read(){tool("read",n)}')
        self.assertEqual(row[2],[('read',1),('cleanup',2)])
        row=self.same('fn(){try{throw 40}catch e{return read(e.value)}finally{tool("cleanup",2)}}',42,setup='fn read(n){tool("handler-return",n+2)}')
        self.assertEqual(row[2],[('handler-return',42),('cleanup',2)])

    def test_zero_and_secondary_values_survive_body_handler_else_and_cleanup(self):
        for expression,expected in [('return_values()',[]),('return_values(null,42)',[None,42]),('return_values(40,2)',[40,2])]:
            for body in ['fn(){try{'+expression+'}finally{return_values(99,100)}}','fn(){try{throw 7}catch e{'+expression+'}}','fn(){try{20}catch e{0}else{'+expression+'}}']:
                with self.subTest(expression=expression,body=body):self.same(body,expected,multiple=True)

    def test_cleanup_inner_loop_preserves_outer_pending_transfer_and_overrides_are_original(self):
        self.same('fn(){var seen=[];for n in [1,2,3]{try{if n==1{continue}else{break}}finally{for inner in [0]{break};seen=append(seen,n)}};seen}',[1,2])
        self.same('fn(){var n=0;while n<3{try{n=n+1;break}finally{if n<3{continue}else{null}}};n}',3)
        self.same('fn(){for n in [42]{try{return n}finally{for inner in [0]{continue};tool("cleanup",n)}};0}',42)
        self.same('fn(){try{throw 1}finally{return 42}}',42)
        self.same('fn(){try{try{return 1}finally{throw 42}}catch e{e.value}}',42)

    def test_guard_boolean_and_new_cleanup_error_bypass_the_associated_catch(self):
        self.same('fn(){var seen=[];var value=null;try{try{throw 7}catch e if 1{seen=append(seen,"forbidden");0}finally{seen=append(seen,"cleanup")}}catch e{value=e.code};[value,seen]}',['runtime_error',['cleanup']])
        self.same('fn(){var count=0;var result=null;try{try{1}catch e{count=count+1}finally{throw 42}}catch e{result=e.value};[result,count]}',[42,0])
        self.same('fn(){try{try{throw 7}catch e if do{throw}{0}catch later{99}}catch outer{outer.value}}',7)

    def test_handled_exception_context_restores_across_real_async_await(self):
        row=self.same('async fn(){try{throw 40}catch e{let before=try{throw}catch inner{inner.value};await sleep(0);let after=try{throw}catch inner{inner.value};[before,after]}finally{tool("cleanup",2)}}',[40,40])
        self.assertEqual(row[2],[('cleanup',2)])

    def successor(self,native,redirect):
        effects=[];changed=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:effects.append((n,v))or v)
        f=streams.compile_choice(e,e.run('fn(){try{throw 40}catch e if do{tool("guard",e.value);true}{let selected=tool("selected",e.value+2);selected}catch e{let fallback=tool("fallback",0);fallback}finally{tool("cleanup",1)}}'),'native'if native else'portable')
        program=f.compiled_plan.flow;self.assertIsNotNone(program)
        self.assertTrue(any(row[0]=='protect'for row in program.instructions));self.assertTrue(any(row[0]=='protect_return'for row in program.instructions))
        original=FlowProgram.portable
        def alter(pc,flags):
            row=program.instructions[pc]
            if redirect and not changed and row[0]=='branch'and pc>0 and program.instructions[pc-1][0]=='protect_return'and program.instructions[pc-1][2]==('guard',0)and effects==[('guard',40)]:
                self.assertEqual(flags[row[1]],1);flags[row[1]]=0;changed.append(pc)
        def portable(current,pc,flags):
            if current is program:alter(pc,flags)
            return original(current,pc,flags)
        if native:
            resume=program.kernel.resume
            def actual(pc,flags):alter(pc,flags);return resume(pc,flags)
            observer=patch.object(program.kernel,'resume',actual)
        else:observer=patch.object(FlowProgram,'portable',portable)
        with observer,effect_schedule('already_done'):
            try:value=e.invoke(f,[],force=True)
            finally:e.suspension.finish()
        self.assertGreater(program.native_resumes if native else program.portable_resumes,0)
        return value,effects,changed

    def test_portable_guard_successor_selects_actual_later_handler_without_guard_replay(self):
        self.assertEqual(self.successor(False,False),(42,[('guard',40),('selected',42),('cleanup',1)],[]))
        row=self.successor(False,True);self.assertEqual(row[:2],(0,[('guard',40),('fallback',0),('cleanup',1)]));self.assertEqual(len(row[2]),1)

    def test_native_guard_successor_selects_actual_later_handler_without_guard_replay(self):
        from test_native_jit import NATIVE
        if not NATIVE:self.skipTest('Optional LLVM backend unavailable')
        self.assertEqual(self.successor(True,False),(42,[('guard',40),('selected',42),('cleanup',1)],[]))
        row=self.successor(True,True);self.assertEqual(row[:2],(0,[('guard',40),('fallback',0),('cleanup',1)]));self.assertEqual(len(row[2]),1)

    def test_error_group_occurrence_partition_guards_and_handler_context_follow_original_algorithm(self):
        self.same('fn(){var seen=[];try{throw error_group("root",[error(1),error(2),error(3)])}catch* {value:n,.._} if n!=2 as g{seen=append(seen,[g.exceptions[0].value,g.exceptions[1].value])}catch* _ as g{seen=append(seen,[g.exceptions[0].value])};seen}',[[1,3],[2]])
        self.same('async fn(){try{throw error(42)}catch* _ if current_error().value==42 as g{await sleep(0);[current_error()==g,current_error().code,current_error().exceptions[0].value]}}',[True,"exception_group",42])
