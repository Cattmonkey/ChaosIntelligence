"""Independent bare/interpreter/portable/native controls for async loop CFG."""
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError
from chaos.jit_flow import FlowProgram
from _effect_schedule158 import effect_schedule
import _generator_flow208 as streams
import _async_iteration_flow210 as fixture

class AsyncIterationFlow210(unittest.TestCase):
    def same(self,body,expected,args=(),**options):
        rows=[]
        for target in fixture.TARGETS():
            with self.subTest(target=target):
                row,states,functions=fixture.record(body,args,target,**options)
                self.assertEqual(row[0],('value',expected));fixture.retired(self,target,states,functions);rows.append(row)
        self.assertEqual(len(rows),len(fixture.TARGETS()))
        self.assertTrue(all(row==rows[0]for row in rows),rows)
        return rows[0]

    def test_cold_consumer_source_acquisition_and_each_next_are_once_only(self):
        setup='''var calls=[];class Cursor{var n=0;fn __aiter__(self){calls=append(calls,"acquire");self}
async fn __anext__(self){calls=append(calls,"next");self.n=self.n+1;{done:self.n>2,value:self.n}}}'''
        self.same('''async fn(){var out=[];async for x in do{calls=append(calls,"source");Cursor()}{out=append(out,x)}else{calls=append(calls,"else")};[out,calls]}''',[[1,2],['source','acquire','next','next','next','else']],setup=setup)
        for target in fixture.TARGETS():
            e=Evaluator();f=streams.compile_choice(e,e.run('async fn(){async for x in source{null}}'),target)
            with streams.observe_states()as states:
                task=e.invoke(f,[])
                self.assertEqual(task.state,'created');self.assertEqual(states,[])
                self.assertEqual(0 if target=='raw'else f.compiled_plan.flow.resume_calls if f.compiled_plan.flow else 0,0)
            e.suspension.finish()

    def test_pattern_continue_break_return_and_else_match_original_control(self):
        prefix='async fn* items(){yield ([1,20]);yield ([2,22]);yield ([3,99])};'
        self.same('''async fn(){let s=items();var out=[];async for [key,value] in s{if key==1{continue}else{null};out=append(out,value);if key==2{break}else{null}}else{out=append(out,0)};[out,await anext(s)]}''',[[22],{'done':False,'value':[3,99]}],setup=prefix)
        self.same('async fn(){async for [key,value] in items(){return value}else{return -1}}',20,setup=prefix)
        self.same('async fn(){var out=[];async for x in (async fn*(){return 9})(){out=append(out,x)}else{out=append(out,42)};out}',[42])
        self.same('''async fn(){var outer=0;while true{outer=outer+1;async for x in do{break;(async fn*(){yield 1})()}{outer=99}};outer}''',1)

    def test_pattern_failure_is_atomic_and_does_not_enter_body_or_else(self):
        setup='async fn* bad(){yield ([1,"bad"])};'
        rows=[]
        for target in fixture.TARGETS():
            row,states,functions=fixture.record('async fn(){async for [a,0] in bad(){tool("body",a)}else{tool("else",100)}}',target=target,setup=setup)
            self.assertEqual(row[0][0],'error');self.assertEqual(row[0][1]['code'],'match_error');self.assertEqual(row[2],[])
            fixture.retired(self,target,states,functions);rows.append(row)
        self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_early_break_borrows_iterator_but_explicit_aclosing_owns_it(self):
        setup='''var closed=0;async fn* values(){try{yield 1;yield 2}finally{closed=closed+1}};'''
        self.same('''async fn(){let s=values();async for x in s{break};let before=closed;let next=await anext(s);await aclose(s);[before,next,closed]}''',[0,{'done':False,'value':2},1],setup=setup)
        rows=[]
        for target in fixture.TARGETS():
            e=Evaluator(timeout=30);functions=[]
            def choose(f):answer=streams.compile_choice(e,f,target);functions.append(answer);return answer
            e.env.bind('choose',Native('choose',choose,e,captures=()))
            source='import "std/context" as ctx;'+setup+'let consume=choose(async fn(s){async for x in s{break}});async with ctx.aclosing(values()) as s{await consume(s)};closed'
            with streams.observe_states()as states,effect_schedule('already_done'):value=e.run(source)
            self.assertEqual(value,1)
            if target in ('raw','interpreter'):self.assertEqual(states,[])
            else:
                self.assertTrue(states);self.assertTrue(all(s.closed for s in states))
                self.assertTrue(any(row[0]=='enter'and row[1][0]=='async_for'for row in functions[0].compiled_plan.flow.instructions))
            rows.append(value)
        self.assertEqual(rows,[1]*len(rows))

    def test_host_error_identity_from_source_acquire_and_next_is_not_replayed(self):
        for stage in ('source','acquire','next'):
            rows=[]
            for target in fixture.TARGETS():
                fault=LanguageError('async-loop210',code='fixture210');cause=ValueError('cause');fault.__cause__=cause;calls=[]
                e=Evaluator(timeout=30)
                def fail():calls.append(stage);raise fault
                e.env.bind('fail',Native('fail',fail,e,captures=()))
                setup='class Cursor{fn __aiter__(self){'+('fail();'if stage=='acquire'else'')+'self}async fn __anext__(self){'+('fail();'if stage=='next'else'')+'{done:true,value:null}}}'
                body='async fn(){async for x in '+('do{fail();Cursor()}'if stage=='source'else'Cursor()')+'{null}else{fail()}}'
                f=streams.compile_choice(e,e.run(setup+';'+body),target);base=e.steps
                with effect_schedule('already_done'):
                    try:
                        with self.assertRaises(LanguageError)as caught:e.invoke(f,[],force=True)
                        self.assertIs(caught.exception,fault);self.assertIs(caught.exception.__cause__,cause)
                    finally:e.suspension.finish(fault)
                rows.append((calls,e.steps-base,e.depth,len(e.frames)))
            self.assertTrue(all(row==rows[0]for row in rows));self.assertEqual(rows[0][0],[stage])

    def successor(self,native,redirect):
        e=Evaluator(timeout=30);effects=[];changed=[]
        e.tool_handler=lambda n,v:effects.append((n,v))or v
        f=streams.compile_choice(e,e.run('async fn* values(){yield 42};async fn(){var out=0;async for x in values(){out=tool("body",x)}else{out=out+1};out}'),'native'if native else'portable')
        program=f.compiled_plan.flow;original=FlowProgram.portable
        step_flags={row[2]for row in program.instructions if row[0]=='step'}
        self.assertEqual(len(step_flags),1,'must contain the actual async loop step')
        def change(pc,flags):
            row=program.instructions[pc]
            if redirect and not changed and row[0]=='branch'and row[1]in step_flags:
                self.assertEqual(flags[row[1]],1);flags[row[1]]=0;changed.append(pc)
        def portable(current,pc,flags):
            if current is program:change(pc,flags)
            return original(current,pc,flags)
        if native:
            resume=program.kernel.resume
            def actual(pc,flags):change(pc,flags);return resume(pc,flags)
            observer=patch.object(program.kernel,'resume',actual)
        else:observer=patch.object(FlowProgram,'portable',portable)
        with observer,effect_schedule('already_done'):
            try:value=e.invoke(f,[],force=True)
            finally:e.suspension.finish()
        self.assertGreater(program.native_resumes if native else program.portable_resumes,0)
        return value,effects,changed

    def test_portable_backend_successor_really_skips_async_body(self):
        self.assertEqual(self.successor(False,False),(43,[('body',42)],[]))
        forced=self.successor(False,True);self.assertEqual(forced[:2],(1,[]));self.assertEqual(len(forced[2]),1)

    def test_native_backend_successor_really_skips_async_body(self):
        from test_native_jit import NATIVE
        if not NATIVE:self.skipTest('Optional LLVM backend unavailable')
        self.assertEqual(self.successor(True,False),(43,[('body',42)],[]))
        forced=self.successor(True,True);self.assertEqual(forced[:2],(1,[]));self.assertEqual(len(forced[2]),1)
