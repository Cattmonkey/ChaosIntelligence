"""Direct pattern CFG values, atomic commits and actual selected successors."""
import copy,unittest
from unittest.mock import patch
from chaos.language import Evaluator,LanguageError,ExecutionControl,serializable
from chaos.jit_flow import FlowProgram
from _effect_schedule158 import effect_schedule
import _generator_flow208 as streams
import _pattern_flow218 as fixture

class PatternFlow218(unittest.TestCase):
    def same(self,body,expected,required=None,**options):
        rows=[]
        for target in fixture.TARGETS():
            with self.subTest(target=target):
                row,states,functions=fixture.record(body,target,**options)
                self.assertEqual(row[0],('value',expected));fixture.compiled(self,target,states,functions,required);rows.append(row)
        self.assertEqual(len(rows),len(fixture.TARGETS()));self.assertTrue(all(row==rows[0]for row in rows),rows)
        return rows[0]

    def test_ordered_cases_subject_once_false_guard_continues_without_scope_leak(self):
        body='fn(){var seen=[];var answer=null;fn subject(){seen=append(seen,"subject");{value:[20,22]}};match subject(){{value:[a,b]} if do{seen=append(seen,"false");false}=>0;{value:[a,b]} if do{seen=append(seen,"true");true}=>do{seen=append(seen,"body");answer=a+b};_=>-1};[answer,seen]}'
        self.same(body,[42,['subject','false','true','body']],required={'match'})
        self.same('fn(){let value=99;match 42{value if false=>0;value=>value};value}',99,required={'match'})
        self.same('fn(){match true{1=>0;true=>42}}',42,required={'match'})

    def test_declarations_rhs_once_return_whole_values_and_mutable_leaves_remain_cells(self):
        self.same('fn(){var calls=0;fn pair(){calls+=1;[20,22]};let [a,b]=pair();var {x:left,y:right}={x:a,y:b};left="changed";[a,b,left,right,calls]}',[20,22,'changed',22,1],required={'let_pattern','var_pattern'})
        self.same('fn(){let [a,b]=[20,22]}',[20,22],required={'let_pattern'})
        self.same('fn(){var {x:a}={x:42}}',{'x':42},required={'var_pattern'})

    def test_nested_tuple_record_rest_constructor_and_class_fields_use_original_pure_matcher(self):
        self.same('fn(){let [first,{key:second,..rest},..tail]=[1,{key:2,extra:3},4,5];match (first,second){(a,b)=>[a,b,rest,tail]}}',[1,2,{'extra':3},[4,5]],required={'let_pattern','match'})
        self.same('fn(){let [Ctor,Ctor(x)]=[99,Some(42)];[Ctor,x]}',[99,42],setup='data D{Some(value)};let Ctor=Some;',required={'let_pattern'})
        self.same('fn(){match Box(42){Box::{value:n}=>n;_=>0}}',42,setup='class Box{var value=0;fn __init__(s,n){s.value=n;null}};',required={'match'})

    def test_assign_pattern_swap_rhs_once_and_cell_contract_failure_does_not_partially_commit(self):
        self.same('fn(){var a:Int=1;var b:Int=2;var calls=0;fn pair(){calls+=1;[b,a]};[a,b]=pair();[a,b,calls]}',[2,1,1],required={'assign_pattern'})
        self.same('fn(){var a:Int=1;var b:Int=2;var calls=0;try{[a,b]=do{calls+=1;[20,"bad"]}}catch e{null};[a,b,calls]}',[1,2,1],required={'assign_pattern'})
        self.same('fn(){var a=1;let b=2;var calls=0;try{[a,b]=do{calls+=1;a=9;[20,22]}}catch e{null};[a,b,calls]}',[9,2,1],required={'assign_pattern'})

    def test_failed_declaration_contract_or_duplicate_never_publishes_partial_names(self):
        rows=[]
        for target in fixture.TARGETS():
            snapshots=[]
            def bind(e,retained):
                original=e.bind_pattern
                def observed(pattern,value,env,**options):
                    try:return original(pattern,value,env,**options)
                    finally:snapshots.append((set(env.values),env.get('kept')))
                e.bind_pattern=observed
            row,states,functions=fixture.record('fn(){let kept=42;let [fresh,kept]=do{tool("rhs",1);[1,2]};0}',target,bind=bind)
            self.assertEqual(row[0][0],'error');self.assertEqual(row[2],[('rhs',1)]);self.assertEqual(len(snapshots),1);self.assertNotIn('fresh',snapshots[0][0]);self.assertEqual(snapshots[0][1],42)
            fixture.compiled(self,target,states,functions,{'let_pattern'});rows.append(row)
        self.assertTrue(all(row==rows[0]for row in rows),rows)
        self.same('fn(){var effects=0;var answer=null;try{let [a,b]:Record=do{effects+=1;[1,2]}}catch e{answer=e.code};[effects,answer]}',[1,'runtime_error'],required={'let_pattern'})
        self.same('fn(){var before=7;try{var [before,0]=[1,2]}catch e{null};before}',7,required={'var_pattern'})

    def test_strict_bool_guard_unmatched_and_matcher_protocols_keep_original_errors(self):
        rows=[]
        for target in fixture.TARGETS():
            row,states,functions=fixture.record('fn(){match 42{x if 1=>x;_=>0}}',target);self.assertEqual(row[0][0],'error');fixture.compiled(self,target,states,functions,{'match'});rows.append(row)
        self.assertTrue(all(row==rows[0]for row in rows),rows)
        self.same('fn(){var calls=0;class Box{fn __iter__(s){calls+=1;iter([42])}fn __getitem__(s,k){calls+=1;42}};var result=null;try{match Box(){[x]=>x}}catch e{result=e.code};[result,calls]}',['match_error',0],required={'match'})

    def test_match_tail_calls_zero_and_secondary_values_use_original_all_results(self):
        self.same('fn(){match 40{x=>read(x)}}',42,setup='fn read(n){tool("read",n+2)};',required={'match'})
        for expression,expected in [('return_values()',[]),('return_values(null,42)',[None,42]),('return_values(40,2)',[40,2])]:
            self.same('fn(){match 0{_=>'+expression+'}}',expected,multiple=True,required={'match'})

    def test_match_return_break_continue_are_routed_by_original_lexical_loop(self):
        self.same('fn(){var seen=[];for n in [1,2,3]{match n{1=>continue;2=>do{seen=append(seen,n);break};_=>0}};seen}',[2],required={'match'})
        self.same('fn(){match 40{x=>do{return x+2}};0}',42,required={'match'})

    def test_every_execution_budget_and_cancel_boundary_keeps_assignment_atomic(self):
        source='var left:Int=1;var right:Int=2;var calls=0;fn(){[left,right]=do{calls+=1;[20,22]};[left,right,calls]}'
        def run(target,limit=None,cancel=None):
            e=Evaluator(timeout=30,step_limit=100000);f=streams.compile_choice(e,e.run(source),target);base=e.steps;e.step_limit=e.steps+(100000 if limit is None else limit)
            e.cancelled=lambda:cancel is not None and e.steps-base>=cancel
            if target not in ('raw','interpreter'):self.assertTrue(fixture.structural(f.compiled_plan.flow))
            failure=None
            with streams.observe_states()as states:
                try:outcome=('value',serializable(e.invoke(f,[],force=True)))
                except LanguageError as error:
                    failure=error;record=error.record()
                    if limit is not None and error.code=='resource_limit':
                        self.assertEqual(e.step_limit,base+limit);self.assertEqual(record['message'],'超过计算步数限制 '+str(e.step_limit))
                        record={k:v for k,v in record.items()if k!='message'}
                    outcome=('error',record)
                except ExecutionControl as error:failure=error;outcome=('control',type(error).__name__,str(error))
                finally:e.suspension.finish(failure)
            cells=[f.env.get('left'),f.env.get('right')];self.assertIn(cells,([1,2],[20,22]))
            return outcome,e.steps-base,cells,f.env.get('calls')
        total=run('raw')[1];self.assertGreater(total,5)
        for at in range(total+2):
            for kind in ('budget','cancel'):
                kwargs={'limit':at}if kind=='budget'else{'cancel':at};rows=[run(target,**kwargs)for target in fixture.TARGETS()]
                self.assertTrue(all(row==rows[0]for row in rows),(at,kind,rows))

    def successor(self,native,redirect):
        effects=[];changed=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:effects.append((n,v))or v)
        body='fn(){match tool("subject",40){x if do{tool("guard",true);true}=>do{let value=tool("selected",x+2);value};x=>do{let value=tool("fallback",0);value}}}'
        f=streams.compile_choice(e,e.run(body),'native'if native else'portable');program=f.compiled_plan.flow;self.assertTrue(fixture.structural(program))
        original=FlowProgram.portable
        def alter(pc,flags):
            row=program.instructions[pc]
            if redirect and not changed and row[0]=='branch'and pc>0 and program.instructions[pc-1][0]=='pattern_return'and program.instructions[pc-1][2]==('guard',0)and effects==[('subject',40),('guard',True)]:
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
        return result,effects,changed

    def test_portable_guard_successor_selects_actual_next_case_without_replaying_subject(self):
        self.assertEqual(self.successor(False,False),(42,[('subject',40),('guard',True),('selected',42)],[]))
        row=self.successor(False,True);self.assertEqual(row[:2],(0,[('subject',40),('guard',True),('fallback',0)]));self.assertEqual(len(row[2]),1)

    def test_native_guard_successor_selects_actual_next_case_without_replaying_subject(self):
        from test_native_jit import NATIVE
        if not NATIVE:self.skipTest('Optional LLVM backend unavailable')
        self.assertEqual(self.successor(True,False),(42,[('subject',40),('guard',True),('selected',42)],[]))
        row=self.successor(True,True);self.assertEqual(row[:2],(0,[('subject',40),('guard',True),('fallback',0)]));self.assertEqual(len(row[2]),1)

    def test_both_stream_flavors_resume_rhs_before_commit_and_match_yield_send_close_once(self):
        from test_callable_execution_frames import owned_frame
        from chaos.execution_patterns import PatternSyntaxFrame
        from chaos import suspension
        for flavor,nextop,sendop,closeop in [('fn*','next(s)','send(s,VALUE)','close(s)'),('async fn*','await anext(s)','await asend(s,VALUE)','await aclose(s)')]:
            for closing in (False,True):
                source='let g=choose('+flavor+'(){try{var [a,b]=[1,2];[a,b]=yield ([20,22]);match [a,b]{[x,y] if x+y==42=>do{let feedback=yield (x+y);yield feedback};_=>yield 0};return 9}finally{tool("cleanup1",1);tool("cleanup2",2)}});let s=g();mark(s);let first='+nextop+';let second='+sendop.replace('VALUE','[40,2]')+';inspect_stream(s);'
                source+=closeop+';'+closeop+';[first,second,'+nextop+']'if closing else'[first,second,'+sendop.replace('VALUE','44')+','+nextop+']'
                rows=[]
                for target in fixture.TARGETS():
                    def inspect(e,stream,states,functions):
                        frame=owned_frame(self,stream.runner.iterator,PatternSyntaxFrame);self.assertEqual(frame.phase,'body');self.assertEqual(frame.scope.get('x'),40);self.assertEqual(frame.scope.get('y'),2)
                        if target not in ('raw','interpreter'):
                            live=[s for s in states if not s.closed];self.assertEqual(len(live),1);self.assertIs(live[0].pending_cursor,stream.runner.iterator);self.assertIs(type(live[0].pending_request),suspension.Yield)
                    with self.subTest(flavor=flavor,target=target,closing=closing):
                        row,states,functions,held=streams.run(source,target,inspect=inspect)
                        expected=[{'done':False,'value':[20,22]},{'done':False,'value':42},{'done':True,'value':None}]if closing else[{'done':False,'value':[20,22]},{'done':False,'value':42},{'done':False,'value':44},{'done':True,'value':9}]
                        self.assertEqual(row[0],('value',expected));self.assertEqual(row[2],[('cleanup1',1),('cleanup2',2)]);fixture.compiled(self,target,states,functions,{'var_pattern','assign_pattern','match'});rows.append(row)
                self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_large_direct_match_keeps_existing_quota_fallback_and_strict_native_contract(self):
        from chaos.jit import compile_function,compile_info
        body='fn(){match 999{'+''.join(str(n)+'=>'+str(n)+';'for n in range(100))+'_=>tool("once",42)}}'
        rows=[]
        for target in ('raw','auto-flow'):
            calls=[];e=Evaluator(timeout=30,tool_handler=lambda n,v:calls.append((n,v))or v);plain=e.run(body)
            f=plain if target=='raw'else compile_function(e,plain,'auto-flow')
            self.assertEqual(calls,[],'compilation must not call any branch')
            if target!='raw':
                report=compile_info(e,f);self.assertEqual(report['status'],'unsupported');self.assertIn(report['fallbackReason'],('node_limit','instruction_limit','flag_limit'))
            rows.append((e.invoke(f,[],force=True),calls))
        self.assertEqual(rows,[(42,[('once',42)]),(42,[('once',42)])])
        e=Evaluator(timeout=30);plain=e.run(body)
        with self.assertRaises(LanguageError)as caught:compile_function(e,plain,'native-flow')
        self.assertEqual(caught.exception.code,'compiler_error')

    def test_resource_and_pattern_effect_metadata_is_conservative_sealed_and_does_not_execute_callbacks(self):
        from chaos.jit_flow import effect_tags
        from chaos.language import Native
        calls=[];e=Evaluator(timeout=30)
        source='class Gate{fn __enter__(s){[20,22]}fn __exit__(s,error){null}};fn(work){with Gate() as value{let [a,b]=work(value);match [a,b]{[x,y] if work(true)=>x+y;_=>0}}}'
        f=streams.compile_choice(e,e.run(source),'portable');program=f.compiled_plan.flow;self.assertIsNotNone(program)
        descriptors=[(index,row)for index,row in enumerate(program.instructions)if row[0]in('pattern','resource')]
        self.assertTrue(descriptors);self.assertEqual(calls,[])
        for index,row in descriptors:
            self.assertIs(type(program.effects[index]),frozenset);self.assertTrue(effect_tags(row[1])<=program.effects[index])
            self.assertIn('suspend',program.effects[index]);self.assertTrue({'call','protocol','unknown'}&program.effects[index])
        work=Native('abs',lambda value:calls.append(copy.deepcopy(value))or value,e,captures=())
        self.assertEqual(e.invoke(f,[work],force=True),42);self.assertEqual(calls,[[20,22],True])
        before=list(calls);program.effects=tuple(frozenset()for row in program.instructions)
        with self.assertRaises(LanguageError)as caught:e.invoke(f,[work],force=True)
        self.assertEqual(caught.exception.code,'ownership_error');self.assertEqual(calls,before)
