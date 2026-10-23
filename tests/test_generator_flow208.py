"""Compiled streams must preserve the original uncompiled public protocol."""
import copy
import unittest
from unittest.mock import patch
from chaos.language import Evaluator,Native,LanguageError,ResourceLimit
from chaos.jit import compile_function,compile_info
from chaos.jit_flow import FlowProgram,info
from chaos.capture_graph import seal
from chaos.native_jit import BackendUnavailable
import _generator_flow208 as fixture

class GeneratorFlow208(unittest.TestCase):
    def same(self,source,expected=None,**options):
        old,_,_,_=fixture.run(source,'raw',**options)
        if expected is not None:self.assertEqual(old[0],('value',expected))
        for target in fixture.targets()[1:]:
            with self.subTest(target=target):
                result,states,functions,streams=fixture.run(source,target,**options)
                self.assertEqual(result,old);fixture.assert_compiled(self,target,states,functions)
                self.assertEqual(result[-3:],(0,0,tuple(False for _ in streams)))
        return old

    def test_both_flavors_compile_and_call_are_cold_and_coldclose_is_inert(self):
        for flavor,close in [('fn*','close(s)'),('async fn*','await aclose(s)')]:
            for target in fixture.targets():
                with self.subTest(flavor=flavor,target=target):
                    checked=[]
                    def inspect(e,s,states,functions):
                        self.assertEqual(states,[]);self.assertEqual(s.state,'created');checked.append(s)
                        if target not in('raw','interpreter'):self.assertIsNotNone(functions[0].compiled_plan.flow)
                    source='var calls=0;let g=choose('+flavor+'(){calls=calls+1;yield 42});let s=g();mark(s);inspect_stream(s);'+close+';[calls,type_of(s)]'
                    result,states,functions,streams=fixture.run(source,target,inspect=inspect)
                    self.assertEqual(result[0],('value',[0,'Generator'if flavor=='fn*'else'AsyncGenerator']))
                    self.assertEqual(states,[]);self.assertEqual(len(checked),1);self.assertEqual(streams[0].state,'closed')

    def test_next_send_and_all_final_return_values_match_raw(self):
        self.same('let g=choose(fn*(){let feedback=yield 1;yield feedback;return_values(9,10)});let s=g();mark(s);[next(s),send(s,42),next(s),final_parts(s),next(s)]',
                  [{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':9},[9,10],{'done':True,'value':9}])
        self.same('let g=choose(async fn*(){let feedback=yield 1;await sleep(0);yield feedback;return_values(9,10)});let s=g();mark(s);[await anext(s),await asend(s,42),await anext(s),final_parts(s)]',
                  [{'done':False,'value':1},{'done':False,'value':42},{'done':True,'value':9},[9,10]])

    def test_throw_and_bidirectional_delegation_match_raw(self):
        for flavor,nextop,sendop,throwop in [('fn*','next(s)','send(s,42)','throw_into(s,43)'),('async fn*','await anext(s)','await asend(s,42)','await athrow(s,43)')]:
            for inner in ('fn*','async fn*')if flavor=='async fn*'else('fn*',):
                source='let inner=choose('+inner+'(){try{let x=yield 1;yield x}catch e{yield e.value};return 9});let outer=choose('+flavor+'(){return yield from inner()});let s=outer();mark(s);['+nextop+','+sendop+','+throwop+','+nextop+']'
                with self.subTest(flavor=flavor,inner=inner):self.same(source,[{'done':False,'value':1},{'done':False,'value':42},{'done':False,'value':43},{'done':True,'value':9}])

    def test_sequence_delegate_rejections_and_finally_order_match_raw(self):
        for flavor,nextop,sendop,closeop in [('fn*','next(s)','send(s,2)','close(s)'),('async fn*','await anext(s)','await asend(s,2)','await aclose(s)')]:
            for sequence in ('[1,2]','"ab"'):
                source='let g=choose('+flavor+'(){try{yield from '+sequence+'}finally{tool("finally1",1);tool("finally2",2)}});let s=g();mark(s);let first='+nextop+';let error=try{'+sendop+'}catch e{e.message};'+closeop+';[first,error]'
                with self.subTest(flavor=flavor,sequence=sequence):
                    row=self.same(source);self.assertEqual(row[2],[('finally1',1),('finally2',2)])
                    self.assertEqual(row[0][1][1],'List/String 委托只接受 null')

    def test_two_cleanup_effects_and_illegal_yield_close_match_raw(self):
        for flavor,nextop,closeop in [('fn*','next(s)','close(s)'),('async fn*','await anext(s)','await aclose(s)')]:
            for illegal in(False,True):
                body='yield 2;'if illegal else''
                source='let g=choose('+flavor+'(){try{yield 1}finally{tool("close1",1);'+body+'tool("close2",2)}});let s=g();mark(s);'+nextop+';let result=try{'+closeop+';"closed"}catch e{e.message};'+closeop+';[result,'+nextop+']'
                with self.subTest(flavor=flavor,illegal=illegal):
                    row=self.same(source);self.assertEqual(row[2][0],('close1',1));self.assertEqual(sum(n=='close1'for n,v in row[2]),1)
                    self.assertEqual(sum(n=='close2'for n,v in row[2]),0 if illegal else 1)

    def test_loop_transfer_and_stream_backpressure_have_identical_effects(self):
        for flavor,consume,closeop in [('fn*','next(s)','close(s)'),('async fn*','await anext(s)','await aclose(s)')]:
            source='let g=choose('+flavor+'(){var i=0;while i<5{i=i+1;if i==1{continue}else{null};if i==4{break}else{null};yield tool("item",i)}else{tool("else",9)};return 7});let s=g();mark(s);let first='+consume+';inspect_stream(s);let second='+consume+';let done='+consume+';'+closeop+';[first,second,done]'
            calls=[]
            def inspect(e,s,states,functions):self.assertEqual(s.state,'suspended');calls.append(True)
            with self.subTest(flavor=flavor):
                row=self.same(source,[{'done':False,'value':2},{'done':False,'value':3},{'done':True,'value':7}],inspect=inspect)
                self.assertEqual(row[2],[('item',2),('item',3)]);self.assertTrue(calls)

    def test_language_and_plain_host_errors_keep_identity_and_cause(self):
        for flavor,nextop in [('fn*','next(s)'),('async fn*','await anext(s)')]:
            for failure in('language','stop'):
                observations=[]
                for target in fixture.targets():
                    e=Evaluator();cause=ValueError('original-cause');fault=LanguageError('identity',code='identity')if failure=='language'else StopIteration('original-stop');fault.__cause__=cause
                    def fail():raise fault
                    e.env.bind('fail',Native('fail',fail,e,captures=()))
                    e.env.bind('choose',Native('choose',lambda f:fixture.compile_choice(e,f,target),e,captures=()))
                    with fixture.observe_states()as states:
                        try:e.run('let g=choose('+flavor+'(){yield 1;fail()});let s=g();'+nextop+';'+nextop)
                        except BaseException as error:
                            observations.append((type(error).__name__,str(error),error is fault,error.__cause__ is fault,error.__cause__ is cause))
                        else:self.fail('stream error was mistaken for normal completion')
                    self.assertTrue(all(s.closed for s in states));self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
                self.assertTrue(all(row==observations[0]for row in observations),observations)

    def test_definition_change_at_yield_rejects_next_send_throw_close_without_replay(self):
        for flavor,nextop,operations in [('fn*','next(s)',('next(s)','send(s,42)','throw_into(s,43)','close(s)')),('async fn*','await anext(s)',('await anext(s)','await asend(s,42)','await athrow(s,43)','await aclose(s)'))]:
            for op in operations:
                checked=[]
                def inspect(e,s,states,functions):
                    state=next(x for x in states if not x.closed);self.assertEqual(state.pending_kind,'yield')
                    self.assertIs(state.pending_cursor,s.runner.iterator);functions[0].body[1][-1]=copy.deepcopy(functions[0].body[1][-1]);checked.append(True)
                source='let g=choose('+flavor+'(){tool("prefix",1);try{yield 1;tool("suffix",2)}finally{tool("cleanup",3)}});let s=g();mark(s);'+nextop+';inspect_stream(s);'+op
                with self.subTest(flavor=flavor,operation=op):
                    row,states,functions,streams=fixture.run(source,'portable',inspect=inspect)
                    self.assertEqual(row[0][0],'error');self.assertEqual(row[0][1]['code'],'definition_changed')
                    self.assertEqual(sum(n=='prefix'for n,v in row[2]),1);self.assertFalse(any(n=='suffix'for n,v in row[2]));self.assertTrue(checked)
                    self.assertTrue(all(s.closed for s in states));self.assertEqual(row[-3:],(0,0,(False,)))

    def test_yielded_state_metadata_and_foreign_roots_are_checked_atomically(self):
        foreign=Evaluator().run('fn foreign(){1}')
        def inspect(e,s,states,functions):
            state=next(x for x in states if not x.closed);state.validate(full=True)
            self.assertIs(state.pending_cursor,s.runner.iterator);self.assertEqual(state.pending_kind,'yield')
            for field,bad in [('flavor',foreign),('generating',1),('asynchronous',1),('epoch',True),('pc',True),('pending_kind',foreign),('pending_cursor',foreign),('frames',[foreign])]:
                old=getattr(state,field);setattr(state,field,bad)
                try:
                    with self.subTest(field=field),self.assertRaises(LanguageError)as caught:seal(e,(s,),None)
                    self.assertEqual(caught.exception.code,'ownership_error')
                finally:setattr(state,field,old)
            native=e.env.get('tool');caps=native.capabilities;old=state.result;state.result=foreign
            try:
                with self.assertRaises(LanguageError):seal(e,(s,native),frozenset())
                self.assertIs(native.capabilities,caps)
            finally:state.result=old
        source='let g=choose(fn*(){yield 1;return 42});let s=g();mark(s);next(s);inspect_stream(s);next(s).value'
        row,states,functions,_=fixture.run(source,'portable',inspect=inspect);self.assertEqual(row[0],('value',42));fixture.assert_compiled(self,'portable',states,functions)

    def test_hard_budget_abort_never_replays_prefix_or_runs_forbidden_cleanup(self):
        for flavor,nextop in [('fn*','next(s)'),('async fn*','await anext(s)')]:
            source='let g=choose('+flavor+'(){tool("prefix",1);try{yield 1;while true{1}}finally{tool("cleanup",2)}});let s=g();mark(s);'+nextop+';'+nextop
            compared=[]
            for target in fixture.targets():
                details=[];row,states,functions,streams=fixture.run(source,target,limit=180,budget_details=details)
                self.assertEqual(len(details),1);setup,absolute_limit=details[0];self.assertEqual(absolute_limit,setup+180)
                self.assertEqual(row[0][0],'error');error=row[0][1];self.assertEqual(error['code'],'resource_limit')
                self.assertEqual(error['message'],'超过计算步数限制 '+str(absolute_limit))
                self.assertEqual(row[2],[('prefix',1)]);fixture.assert_compiled(self,target,states,functions)
                # The absolute error names this actual budget. All body evidence,
                # including the source span, is still compared without offsets.
                compared.append(((row[0][0],{k:v for k,v in error.items()if k!='message'}),)+row[1:])
            self.assertTrue(all(row==compared[0]for row in compared),compared)

    def successor(self,flavor,native,redirect):
        calls=[];changed=[];e=Evaluator(tool_handler=lambda n,v:calls.append((n,v))or v)
        f=e.run(flavor+'(flag){if flag{yield tool("yes",11)}else{yield tool("no",22)};return 9}')
        f=fixture.compile_choice(e,f,'native'if native else'portable');program=f.compiled_plan.flow
        e.env.bind('g',f)
        def route(pc,flags):
            row=program.instructions[pc]
            if redirect and not changed and row[0]=='branch':flags[row[1]]=1-flags[row[1]];changed.append(pc)
        if native:
            resume=program.kernel.resume
            def routed(pc,flags):route(pc,flags);return resume(pc,flags)
            context=patch.object(program.kernel,'resume',routed)
        else:
            resume=FlowProgram.portable
            def routed(current,pc,flags):
                if current is program:route(pc,flags)
                return resume(current,pc,flags)
            context=patch.object(FlowProgram,'portable',routed)
        nextop='await anext(s)'if flavor.startswith('async')else'next(s)'
        with context:result=e.run('let s=g(false);['+nextop+','+nextop+']')
        self.assertGreater(program.native_resumes if native else program.portable_resumes,0)
        return result,calls,changed

    def test_portable_successor_controls_actual_yielded_effect(self):
        for flavor in('fn*','async fn*'):
            with self.subTest(flavor=flavor):
                normal=self.successor(flavor,False,False);forced=self.successor(flavor,False,True)
                self.assertEqual(normal[:2],([{'done':False,'value':22},{'done':True,'value':9}],[('no',22)]))
                self.assertEqual(forced[:2],([{'done':False,'value':11},{'done':True,'value':9}],[('yes',11)]));self.assertEqual(len(forced[2]),1)

    def test_native_successor_controls_actual_yielded_effect(self):
        from test_native_jit import NATIVE
        if not NATIVE:self.skipTest('Optional LLVM backend unavailable')
        for flavor in('fn*','async fn*'):
            with self.subTest(flavor=flavor):
                normal=self.successor(flavor,True,False);forced=self.successor(flavor,True,True)
                self.assertEqual(normal[0][0]['value'],22);self.assertEqual(forced[0][0]['value'],11);self.assertEqual(len(forced[2]),1)
                self.assertEqual(normal[1],[('no',22)]);self.assertEqual(forced[1],[('yes',11)])
