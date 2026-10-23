"""Operand children execute their real CFG on the original semantic frames."""
import unittest
from unittest.mock import patch
from chaos import suspension
from chaos.language import Evaluator, Native, LanguageError, serializable
from chaos.execution_frames import OperandFrame, ProtocolBridge, EvaluationBoundaryFrame
from chaos.jit_flow import FlowProgram
from chaos.native_jit import NativeKernel
from _effect_schedule158 import effect_schedule
from _capture_paths204 import observed_seal
from _protected_flow214 import record
import _generator_flow208 as streams
from test_native_jit import NATIVE


class OperandFlow243(unittest.TestCase):
    def same(self, source, expected, *, setup='', multiple=False, required=()):
        rows=[]
        for target in streams.targets():
            row,states,functions=record(source,target,setup=setup,multiple=multiple)
            self.assertEqual(row[0],('value',expected));rows.append(row)
            if target not in ('raw','interpreter'):
                self.assertTrue(states);self.assertTrue(all(s.closed and not s.frames and s.domain.closed for s in states))
                descriptors=[r for f in functions for r in f.compiled_plan.flow.instructions if r[0]=='operand']
                self.assertTrue(set(required)<={r[1][0] for r in descriptors})
        self.assertTrue(all(row==rows[0] for row in rows),rows)

    def test_all_nine_operand_kinds_recurse_into_nested_control(self):
        source='fn(){let x=if true{20}else{0};var y=do{if true{22}else{0}};fn add(a,b){a+b};[add(x,if true{y}else{0}),({answer:if true{x}else{0}},if true{y}else{0}),-(if false{1}else{2}),do{if true{x+y}else{0}}]}'
        self.same(source,[42,{'$tuple':[{'answer':20},22]},-2,42],required={'call','let','var','list','tuple','record','binary','unary','do'})

    def test_callee_argument_keyword_and_spread_order_are_original(self):
        source='fn(){fn target(a,b,*,c){[a,b,c]};(if true{do{tool("callee",0);target}}else{target})(tool("first",20),*do{tool("spread",0);[if true{22}else{0}]},**do{tool("keywords",0);{c:42}})}'
        self.same(source,[20,22,42],required={'call','do','list','record'})

    def test_short_circuit_strict_bool_and_non_tail_full_result_channel(self):
        self.same('fn(){[false&&do{tool("forbidden",0);true},true||do{tool("forbidden",0);false},true&&return_values(true,42),false||return_values(false,42)]}',[False,True,True,False],required={'binary','list'})
        for expression,expected in [('return_values()',[]),('return_values(null,42)',[None,42]),('return_values(40,2)',[40,2])]:
            self.same('fn(){do{'+expression+'}}',expected,multiple=True,required={'do','call'})

    def test_contracts_errors_and_loop_transfers_inside_initializers(self):
        self.same('fn(){var i=0;var seen=[];while i<4{i=i+1;let n=do{if i<2{continue}else{null};if i>2{break}else{null};i};seen=append(seen,n)};seen}',[2],required={'let','do','binary'})
        rows=[]
        source='fn(){var effects=[];fn mark(x){effects=append(effects,x);42};try{let bad:Missing=do{mark("let")}}catch e{null};try{var bad:Missing=do{mark("var")}}catch e{null};effects}'
        self.same(source,['let'],required={'let','var','do','call'})

    def successor(self,native,redirect):
        calls=[];changed=[];e=Evaluator(timeout=20,tool_handler=lambda n,v:calls.append((n,v)) or v)
        f=streams.compile_choice(e,e.run('fn(){let x=if tool("condition",true){tool("left",20)}else{tool("right",30)};[x+2]}'),'native' if native else 'portable')
        program=f.compiled_plan.flow;original=FlowProgram.portable
        def alter(pc,flags):
            if redirect and not changed and program.instructions[pc][0]=='branch' and pc>0 and program.instructions[pc-1][0]=='condition' and calls==[('condition',True)]:
                self.assertEqual(flags[program.instructions[pc][1]],1);flags[program.instructions[pc][1]]=0;changed.append(pc)
        def portable(current,pc,flags):
            if current is program:alter(pc,flags)
            return original(current,pc,flags)
        if native:
            resume=program.kernel.resume
            def actual(pc,flags):alter(pc,flags);return resume(pc,flags)
            observer=patch.object(program.kernel,'resume',actual)
        else:observer=patch.object(FlowProgram,'portable',portable)
        with observer:
            try:result=e.invoke(f,[],force=True)
            finally:e.suspension.finish()
        self.assertGreater(program.native_resumes if native else program.portable_resumes,0)
        return result,calls,changed

    def test_actual_portable_successor_changes_nested_initializer_branch(self):
        self.assertEqual(self.successor(False,False),([22],[('condition',True),('left',20)],[]))
        row=self.successor(False,True);self.assertEqual(row[:2],([32],[('condition',True),('right',30)]));self.assertEqual(len(row[2]),1)

    @unittest.skipUnless(NATIVE,'optional LLVM backend unavailable')
    def test_actual_native_successor_and_original_scalar_kernel_fast_path(self):
        row=self.successor(True,True);self.assertEqual(row[:2],([32],[('condition',True),('right',30)]));self.assertEqual(len(row[2]),1)
        counts=[];original=NativeKernel.invoke;e=Evaluator();f=streams.compile_choice(e,e.run('fn(x){let result=x+2;[result]}'),'native')
        def invoke(kernel,*args,**kwargs):counts.append(kernel);return original(kernel,*args,**kwargs)
        base=e.steps
        with patch.object(NativeKernel,'invoke',invoke):self.assertEqual(e.invoke(f,[40]),[42])
        self.assertTrue(counts);actual=e.steps-base
        plain=Evaluator();g=plain.run('fn(x){let result=x+2;[result]}');base=plain.steps;self.assertEqual(plain.invoke(g,[40]),[42]);self.assertEqual(actual,plain.steps-base)

    def test_saved_artifact_uses_decoded_operand_routes_without_relowering(self):
        from chaos.compilation_artifact import compile_artifact,load_artifact
        source='let raw=fn(flag){let x=if flag{40}else{0};[x+2]};let f=compile_function(raw,target="auto-flow");f(true)'
        bundle=load_artifact(compile_artifact(source))
        with patch('chaos.jit_flow.lower',side_effect=AssertionError('Saved flow was relowered')):
            self.assertEqual(serializable(Evaluator().run(bundle)),[42])

    @unittest.skipUnless(NATIVE,'optional LLVM backend unavailable')
    def test_shared_AST_never_regrants_regions_inside_disabled_argument_descendants(self):
        source='fn(x){let permitted=x+2;fn identity(value){value};[identity(do{if true{let selected=x+2;tool("audit",selected);selected}else{0}}),permitted]}'
        rows=[]
        for target in ('raw','native'):
            e=Evaluator(timeout=20);plain=e.run(source);found={}
            def find(value):
                if isinstance(value,tuple):
                    if value and value[0]=='let' and value[1] in ('permitted','selected'):found[value[1]]=value
                    for child in value:find(child)
                elif type(value) is list:
                    for child in value:find(child)
            find(plain.body);shared=found['permitted'][3];selected=found['selected']
            from chaos.language import Node
            replacement=Node((*selected[:3],shared));replacement.__dict__.update(vars(selected))
            def replace(value):
                if type(value) is list:
                    for index,child in enumerate(value):
                        if child is selected:value[index]=replacement
                        else:replace(child)
                elif isinstance(value,tuple):
                    for child in value:replace(child)
            replace(plain.body);f=streams.compile_choice(e,plain,target);eligible=[];calls=[];captures=[];seal=observed_seal()
            step=EvaluationBoundaryFrame.step;invoke=NativeKernel.invoke
            def observed(boundary,cursor,value,error):
                if boundary.phase=='enter' and boundary.node is shared:eligible.append(boundary.options.get('_compiled_regions') is not None)
                return step(boundary,cursor,value,error)
            def kernel(current,*args,**kwargs):calls.append(current);return invoke(current,*args,**kwargs)
            def audit(name,value):
                self.assertEqual(name,'audit');_,delta,paths=seal(e,(e.suspension.current,),None);captures.append((delta,paths));return value
            e.tool_handler=audit;base=e.steps
            with patch.object(EvaluationBoundaryFrame,'step',observed),patch.object(NativeKernel,'invoke',kernel):result=e.invoke(f,[40])
            rows.append((serializable(result),e.steps-base,captures));self.assertEqual(eligible,[False,False] if target=='raw' else [True,False])
            if target=='native':self.assertEqual(len(calls),1)
        self.assertEqual(rows[0],rows[1])

    def pending(self,padding,target):
        e=Evaluator(timeout=20,tool_handler=lambda n,v:v);held=[];observations=[];effects=[];seen=set();seal=observed_seal()
        def choose(f):return streams.compile_choice(e,f,target)
        def remember(task,pad):held.append((task,pad))
        for name,fn in [('choose',choose),('remember',remember)]:e.env.bind(name,Native(name,fn,e,captures=()))
        e.trace_handler=lambda row:None
        def waiting(futures,*args,**kwargs):
            task,pad=held[0];future=task.runner.future
            if future is not None and future in futures and id(future) not in seen:
                seen.add(id(future));self.assertFalse(future.done());todo=[task.runner.iterator];visited=set();matches=[]
                while todo:
                    cursor=todo.pop()
                    if id(cursor) in visited:continue
                    visited.add(id(cursor))
                    for frame in cursor.frames:
                        if type(frame) is OperandFrame and frame.node[0]=='call' and frame.node[1][0]=='name' and frame.node[1][1]=='sink':matches.append(frame)
                        if type(frame) is ProtocolBridge:
                            todo.extend(child for child in (getattr(frame,'pending_cursor',None),getattr(frame,'dispatch_cursor',None),getattr(frame,'mode_cursor',None)) if child is not None)
                self.assertEqual(len(matches),1);frame=matches[0];self.assertEqual(frame.phase,'argument_value');self.assertIs(frame.env.get('pad'),pad)
                if target=='portable':
                    state=frame.boundary.flow_state;state.validate(full=True);self.assertEqual(frame._flow_operand_segment,('argument_value',frame.index))
                    for field,bad in [('phase','invalid'),('index',999)]:
                        previous=getattr(frame,field);setattr(frame,field,bad)
                        try:
                            with self.assertRaises(LanguageError) as caught:state.validate(full=True)
                            self.assertEqual(caught.exception.code,'ownership_error')
                        finally:setattr(frame,field,previous)
                _,delta,paths=seal(e,(task,),None);observations.append((frame.index,delta,paths))
            return wait(futures,*args,**kwargs)
        def effect(kind,payload,event):effects.append(payload['name']);return payload['value']
        e.effect_handler=effect
        source='let pad=range('+str(padding)+');let work=choose(async fn(pad){fn sink(a,b){a+b};sink(tool("first",40),if true{tool("second",2)}else{0})});let task=work(pad);remember(task,pad);spawn(task);await task'
        with effect_schedule('first_wait'):
            wait=suspension.wait
            with patch.object(suspension,'wait',waiting):result=serializable(e.run(source))
        self.assertEqual(result,42);self.assertEqual(effects,['first','second']);self.assertEqual([x[0] for x in observations],[0,1]);return observations

    def test_actual_pending_operands_preserve_all_65_charged_offsets_and_metadata_guards(self):
        for offset in range(65):
            with self.subTest(offset=offset):self.assertEqual(self.pending(offset,'raw'),self.pending(offset,'portable'))
