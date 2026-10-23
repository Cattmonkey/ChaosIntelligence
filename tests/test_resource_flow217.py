"""Original resource contracts, all flavors and genuine selected successors."""
import unittest
from unittest.mock import patch
from chaos.language import Evaluator
from chaos.jit_flow import FlowProgram
from _effect_schedule158 import effect_schedule
import _generator_flow208 as streams
import _resource_flow217 as fixture

class ResourceFlow217(unittest.TestCase):
    def same(self,body,expected,**options):
        rows=[]
        for target in fixture.TARGETS():
            with self.subTest(target=target):
                row,states,functions=fixture.record(body,target,**options)
                self.assertEqual(row[0],('value',expected));fixture.compiled(self,target,states,functions);rows.append(row)
        self.assertEqual(len(rows),len(fixture.TARGETS()));self.assertTrue(all(row==rows[0]for row in rows),rows)
        return rows[0]

    def test_four_resource_forms_preserve_manager_once_local_binding_and_body_value(self):
        for flavor,keyword,manager in [('fn','with','C'),('async fn','with','C'),('async fn','async with','A')]:
            for target,binding,expression in [('simple','value','value[0]+value[1]'),('pattern','[a,b]','a+b')]:
                with self.subTest(flavor=flavor,form=target,keyword=keyword):
                    self.same(flavor+'(){var count=0;var result=null;fn manager(){count=count+1;'+manager+'([20,22])};'+keyword+' manager() as '+binding+'{result='+expression+'};[result,count,events]}',[42,1,[['enter','r'],['exit','r',None]]])

    def test_streams_are_cold_and_resource_lease_survives_yield_until_explicit_close(self):
        for flavor,nextop,closeop,keyword,manager in [('fn*','next(s)','close(s)','with','C'),('async fn*','await anext(s)','await aclose(s)','with','C'),('async fn*','await anext(s)','await aclose(s)','async with','A')]:
            for binding,value in [('value','value[0]+value[1]'),('[a,b]','a+b')]:
                source=fixture.SETUP+'let g=choose('+flavor+'(){'+keyword+' '+manager+'([20,22]) as '+binding+'{yield '+value+';yield 99}});let s=g();let cold=events;mark(s);let first='+nextop+';inspect_stream(s);let held=events;'+closeop+';let again='+closeop+';[cold,first,held,events,again,'+nextop+']'
                rows=[]
                for target in fixture.TARGETS():
                    def inspect(e,stream,states,functions):
                        self.assertEqual(stream.state,'suspended')
                        if target in ('raw','interpreter'):self.assertEqual(states,[]);return
                        live=[s for s in states if not s.closed];self.assertEqual(len(live),1)
                        self.assertIs(live[0].pending_cursor,stream.runner.iterator);self.assertEqual(live[0].pending_kind,'yield')
                        self.assertTrue(fixture.structural(live[0].program))
                        frames=[f for f in stream.runner.iterator.frames if type(f)is fixture.ContextFrame];self.assertEqual(len(frames),1)
                        self.assertIsNotNone(frames[0].entry);self.assertFalse(frames[0].entry.released)
                    with self.subTest(flavor=flavor,keyword=keyword,binding=binding,target=target):
                        row,states,functions,held=streams.run(source,target,inspect=inspect)
                        self.assertEqual(row[0],('value',[[],{'done':False,'value':42},[['enter','r']],[['enter','r'],['exit','r',None]],None,{'done':True,'value':None}]))
                        fixture.compiled(self,target,states,functions);rows.append(row)
                self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_multiple_resources_exit_in_reverse_order_and_pattern_failure_is_atomic(self):
        self.same('fn(){var result=null;with C([20,22],"one") as [a,b],C({sum:a+b},"two") as {sum:n}{result=n};[result,events]}',[42,[['enter','one'],['enter','two'],['exit','two',None],['exit','one',None]]])
        self.same('async fn(){var result=null;async with A([20,22],"one") as [a,b],A({sum:a+b},"two") as {sum:n}{result=n};[result,events]}',[42,[['enter','one'],['enter','two'],['exit','two',None],['exit','one',None]]])
        self.same('fn(){var code=null;try{with C([1],"outer") as [a],C([2],"inner") as [b,c]{events=append(events,"forbidden")}}catch e{code=e.code};[code,events]}',['match_error',[['enter','outer'],['enter','inner'],['exit','inner','match_error'],['exit','outer','match_error']]])
        self.same('async fn(){var value=99;async with A([1],"bad",true) as [a,b]{value=0};[value,events]}',[99,[['enter','bad'],['exit','bad','match_error']]])

    def test_failed_acquire_never_exits_and_invalid_exit_never_suppresses(self):
        bad='class Bad{fn __enter__(s){throw "acquire"}fn __exit__(s,e){events=append(events,"forbidden");null}};'
        self.same('fn(){var failure=null;try{with C(1,"outer") as a,Bad() as b{0}}catch e{failure=e.value};[failure,events]}',['acquire',[['enter','outer'],['exit','outer','user_throw']]],setup=bad)
        self.same('fn(){var result=7;with C(42,"r",true) as value{throw 99};[result,events]}',[7,[['enter','r'],['exit','r','user_throw']]])
        for badexit in ('1','[]','async fn(){true}()'):
            rows=[]
            for target in fixture.TARGETS():
                row,states,functions=fixture.record('fn(){with Wrong() as value{42}}',target,setup='class Wrong{fn __enter__(s){42}fn __exit__(s,e){'+badexit+'}};')
                self.assertEqual(row[0][0],'error');fixture.compiled(self,target,states,functions);rows.append(row)
            self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_tail_calls_zero_and_secondary_values_are_resolved_before_exit(self):
        row=self.same('fn(){with Tail() as value{return read()}}',1,setup='var n=1;fn read(){tool("read",n)};class Tail{fn __enter__(s){0}fn __exit__(s,e){n=2;tool("exit",n);null}};')
        self.assertEqual(row[2],[('read',1),('exit',2)])
        for expression,expected in [('return_values()',[]),('return_values(null,42)',[None,42]),('return_values(40,2)',[40,2])]:
            self.same('fn(){with C(0) as value{'+expression+'}}',expected,multiple=True)

    def test_loop_transfers_and_exit_replacement_preserve_original_lexical_route(self):
        self.same('fn(){var seen=[];for n in [1,2,3]{with C(n) as value{seen=append(seen,value);if value==1{continue}else{break}}};[seen,events]}',[[1,2],[['enter','r'],['exit','r',None],['enter','r'],['exit','r',None]]])
        self.same('fn(){with C(1) as value{return 42}}',42)
        self.same('fn(){var result=null;try{with Broken() as x{return 1}}catch e{result=e.value};result}',42,setup='class Broken{fn __enter__(s){0}fn __exit__(s,e){throw 42}};')
        self.same('fn(){let p=prompt_tag();with prompt_scope(p) as scope{with C(0) as value{abort_prompt(p,42)}}}',42)

    def test_prompt_shield_and_task_group_special_scopes_keep_original_ownership(self):
        self.same('fn(){let p=prompt_tag();var result=null;with prompt_scope(p,fn(x){[x,prompt_available(p)]}) as scope{abort_prompt(p,42)}}',[42,False])
        self.same('async fn(){var answer=null;async with task_group() as group{let child=group.spawn((async fn(){40})());answer=await child+2};answer}',42)

    def successor(self,native,redirect):
        effects=[];changed=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:effects.append((n,v))or v)
        source='class Lease{fn __enter__(s){tool("enter",1);42}fn __exit__(s,error){tool("exit",1);null}};fn manager(){tool("manager",1);Lease()};fn(){with manager() as value{if tool("condition",true){let answer=tool("selected",value);answer}else{let answer=tool("fallback",0);answer}}}'
        f=streams.compile_choice(e,e.run(source),'native'if native else'portable');program=f.compiled_plan.flow
        self.assertTrue(fixture.structural(program));self.assertTrue(any(row[0]=='resource_return'for row in program.instructions))
        original=FlowProgram.portable
        def alter(pc,flags):
            row=program.instructions[pc]
            if redirect and not changed and row[0]=='branch'and effects==[('manager',1),('enter',1),('condition',True)]:
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
            try:result=e.invoke(f,[],force=True)
            finally:e.suspension.finish()
        self.assertGreater(program.native_resumes if native else program.portable_resumes,0)
        self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
        return result,effects,changed

    def test_portable_successor_controls_actual_resource_body_without_reacquire_or_replay(self):
        self.assertEqual(self.successor(False,False),(42,[('manager',1),('enter',1),('condition',True),('selected',42),('exit',1)],[]))
        row=self.successor(False,True);self.assertEqual(row[:2],(0,[('manager',1),('enter',1),('condition',True),('fallback',0),('exit',1)]));self.assertEqual(len(row[2]),1)

    def test_native_successor_controls_actual_resource_body_without_reacquire_or_replay(self):
        from test_native_jit import NATIVE
        if not NATIVE:self.skipTest('Optional LLVM backend unavailable')
        self.assertEqual(self.successor(True,False),(42,[('manager',1),('enter',1),('condition',True),('selected',42),('exit',1)],[]))
        row=self.successor(True,True);self.assertEqual(row[:2],(0,[('manager',1),('enter',1),('condition',True),('fallback',0),('exit',1)]));self.assertEqual(len(row[2]),1)

    def test_cancel_shield_body_uses_real_active_task_and_keeps_original_holder(self):
        rows=[]
        for target in fixture.TARGETS():
            row,states,functions=fixture.pending(self,2,target,shield=True)
            self.assertEqual(row[0],('value',42));fixture.compiled(self,target,states,functions);rows.append(row)
        self.assertTrue(all(row==rows[0]for row in rows),rows)
