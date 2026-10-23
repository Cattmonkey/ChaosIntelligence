"""Behavioral contracts for genuine compositional multi-shot continuations.
No replay interpreter or replacement semantic oracle: literal outcomes below follow
ordinary source control and explicit call-with-composable-continuation semantics.
"""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,compile_artifact,load_artifact,parse_program,format_program
from chaos.language import Evaluator,LanguageError,Native,ResourceLimit
import test_robot_values as robots

ROOT=Path(__file__).resolve().parents[1]
SOURCE="var saved=null;var prefix=0;var suffix=0;let tag=prompt_tag(\"saved\");\nlet initial=call_prompt(tag,fn(){prefix+=1;let value=10+capture_continuation(tag,fn(k){saved=k;2});suffix+=1;value});\nlet first=saved(30);let second=saved(40);[initial,first,second,prefix,suffix,type_of(saved),continuation_info(saved).invocations]"
EXPECTED=[12,40,50,1,3,"Continuation",2]

class ContinuationContracts226(unittest.TestCase):
    def value(self,source,**options):
        e=Evaluator(step_limit=200000,timeout=20,**options);value=e.run(source)
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
        self.assertFalse(e.suspension.control_state.stack)
        return value

    def test_capture_once_in_place_receiver_then_two_composed_suffixes(self):
        self.assertEqual(self.value(SOURCE),EXPECTED)
        self.assertEqual(self.value('''var k=null;var events=[];let p=prompt_tag();let initial=call_prompt(p,fn(){events=append(events,"prefix");let n=capture_continuation(p,fn(saved){k=saved;events=append(events,prompt_available(p));2});events=append(events,n);n+10});let a=k(3);let b=k(4);[initial,a,b,events,prompt_available(p)]'''),[12,13,14,['prefix',True,2,3,4],False])

    def test_explicit_receiver_abort_leaves_reusable_suffix_after_prompt_closed(self):
        source='''var saved=null;var events=[];let p=prompt_tag();let initial=call_prompt(p,fn(){events=append(events,"prefix");let x=capture_continuation(p,fn(k){saved=k;abort_prompt(p,"shifted")});events=append(events,x);x+1});let outside=prompt_available(p);[initial,outside,saved(20),saved(40),events]'''
        self.assertEqual(self.value(source),['shifted',False,21,41,['prefix',20,40]])

    def test_nested_calls_pending_operands_and_shared_cells_are_not_replayed(self):
        source='''var saved=null;var prefix=0;var cell=1;let p=prompt_tag();fn inner(n){n+capture_continuation(p,fn(k){saved=k;2})+cell};fn outer(){prefix+=1;100+inner(10)};let initial=call_prompt(p,outer);cell=5;let a=saved(3);cell=7;let b=saved(4);[initial,a,b,prefix,cell]'''
        self.assertEqual(self.value(source),[113,118,121,1,7])

    def test_while_position_is_saved_but_lexical_cells_stay_shared(self):
        source='''var saved=null;var prefix=0;var i=0;var total=0;let p=prompt_tag();let initial=call_prompt(p,fn(){prefix+=1;while i<2{let x=if i==0{capture_continuation(p,fn(k){saved=k;2})}else{3};total+=x;i+=1};total});i=0;total=0;let a=saved(4);i=0;total=100;let b=saved(5);[initial,a,b,prefix,i,total]'''
        self.assertEqual(self.value(source),[5,7,108,1,2,108])

    def test_zero_null_list_and_multiple_results_cross_original_hole(self):
        source='''var saved=null;let p=prompt_tag();let initial=multiple_value_list(fn(){call_prompt(p,fn(){capture_continuation(p,fn(k){saved=k;return_values(1,2)})})});[initial,multiple_value_list(fn(){saved()}),multiple_value_list(fn(){saved(null)}),multiple_value_list(fn(){saved([3,4])}),multiple_value_list(fn(){saved(20,22)})]'''
        self.assertEqual(self.value(source),[[1,2],[],[None],[[3,4]],[20,22]])

    def test_same_image_reentrant_invocation_has_fresh_operand_drafts(self):
        source='''var saved=null;var events=[];let p=prompt_tag();let initial=call_prompt(p,fn(){let x=capture_continuation(p,fn(k){saved=k;k(7)});events=append(events,x);x*2});[initial,saved(3),events,continuation_info(saved).invocations]'''
        self.assertEqual(self.value(source),[28,6,[7,14,3],2])

    def test_protected_body_handler_and_finally_run_per_real_activation(self):
        source='''var saved=null;var events=[];let p=prompt_tag();let initial=call_prompt(p,fn(){try{let x=capture_continuation(p,fn(k){saved=k;2});if x<0{throw "negative"}else{x+1}}catch e{events=append(events,e.value);99}finally{events=append(events,"finally")}});[initial,saved(-1),saved(4),events]'''
        self.assertEqual(self.value(source),[3,99,5,['finally','negative','finally','finally']])

    def test_wind_fresh_reentry_preserves_callers_distinct_prefix(self):
        source='''var saved=null;var events=[];let p=prompt_tag();let initial=call_prompt(p,fn(){dynamic_wind(fn(){events=append(events,"before-A")},fn(){let x=capture_continuation(p,fn(k){saved=k;2});events=append(events,x);x+1},fn(){events=append(events,"after-A")})});let again=dynamic_wind(fn(){events=append(events,"before-B")},fn(){saved(10)},fn(){events=append(events,"after-B")});[initial,again,events]'''
        self.assertEqual(self.value(source),[3,11,['before-A',2,'after-A','before-B','before-A',10,'after-A','after-B']])

    def test_wind_composition_keeps_caller_activation_and_enters_fresh_saved_wind(self):
        source='''var events=[];let p=prompt_tag();let result=call_prompt(p,fn(){dynamic_wind(fn(){events=append(events,"before")},fn(){let x=capture_continuation(p,fn(k){k(7)});events=append(events,x);x*2},fn(){events=append(events,"after")})});[result,events]'''
        self.assertEqual(self.value(source),[28,['before','before',7,'after',14,'after']])

    def test_nested_winds_reverse_exit_and_fresh_enter_order(self):
        source='''var saved=null;var events=[];let p=prompt_tag();let initial=call_prompt(p,fn(){dynamic_wind(fn(){events=append(events,"in-A")},fn(){dynamic_wind(fn(){events=append(events,"in-B")},fn(){capture_continuation(p,fn(k){saved=k;1})},fn(){events=append(events,"out-B")})},fn(){events=append(events,"out-A")})});let next=saved(2);[initial,next,events]'''
        self.assertEqual(self.value(source),[1,2,['in-A','in-B','out-B','out-A','in-A','in-B','out-B','out-A']])

    def test_failed_reentry_before_installs_no_partial_activation(self):
        source='''var saved=null;var fail=false;var events=[];let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){events=append(events,"before");if fail{throw "before failed"}else{null}},fn(){let x=capture_continuation(p,fn(k){saved=k;2});events=append(events,x);x},fn(){events=append(events,"after")})});fail=true;let error=try{saved(3)}catch e{e.value};fail=false;let result=saved(4);[error,result,events]'''
        self.assertEqual(self.value(source),['before failed',4,['before',2,'after','before','before',4,'after']])

    def test_after_failure_retires_once_and_next_composition_still_works(self):
        source='''var saved=null;var fail=false;var events=[];let p=prompt_tag();call_prompt(p,fn(){dynamic_wind(fn(){events=append(events,"before")},fn(){let x=capture_continuation(p,fn(k){saved=k;2});events=append(events,x);x},fn(){events=append(events,"after");if fail{throw "after failed"}else{null}})});fail=true;let error=try{saved(3)}catch e{e.value};fail=false;let result=saved(4);[error,result,events]'''
        self.assertEqual(self.value(source),['after failed',4,['before',2,'after','before',3,'after','before',4,'after']])

    def test_resource_capture_barrier_rejects_before_original_exit_or_receiver(self):
        source='''var events=[];var receivers=0;let p=prompt_tag();class C{fn __enter__(self){events=append(events,"enter");self}fn __exit__(self,error){events=append(events,"exit");false}};let result=call_prompt(p,fn(){with C() as resource{try{capture_continuation(p,fn(k){receivers+=1;0})}catch e{[e.code,prompt_available(p),events]}}});[result,events,receivers]'''
        self.assertEqual(self.value(source),[['continuation_barrier_error',True,['enter']],['enter','exit'],0])

    def test_compiled_and_unknown_native_capture_are_atomic_explicit_barriers(self):
        for body in ('map(fn(x){capture_continuation(p,fn(k){receivers+=1;0})},[1])','let f=compile_function(fn(){1+capture_continuation(p,fn(k){receivers+=1;0})},target="auto-flow");f()'):
            with self.subTest(body=body):
                source='var receivers=0;let p=prompt_tag();let error=call_prompt(p,fn(){try{'+body+'}catch e{[e.code,prompt_available(p)]}});[error,receivers]'
                from chaos.compiled_flow_state import FlowState
                states=[];original=FlowState.__init__
                def entered(state,*args,**kwargs):original(state,*args,**kwargs);states.append(state)
                with patch.object(FlowState,'__init__',entered):result=self.value(source)
                self.assertEqual(result,[['continuation_barrier_error',True],0])
                if 'compile_function' in body:
                    self.assertTrue(states,'non-tail capture must reach an actual compiled invocation')
                    self.assertTrue(all(s.program.resume_calls>0 and s.closed and not s.frames and not s._owned_activations and s.pending_request is None for s in states))

    def test_foreign_evaluator_expired_epoch_and_foreign_Task_holder_reject(self):
        captured=[];e=Evaluator();e.env.bind('retain226',Native('retain226',lambda k:captured.append(k)or 0,e))
        e.run('let p=prompt_tag();call_prompt(p,fn(){capture_continuation(p,retain226)})');self.assertEqual(len(captured),1)
        for owner in (e,Evaluator()):
            owner.env.bind('old226',captured[0])
            with self.subTest(sameOwner=owner is e),self.assertRaises(LanguageError)as caught:owner.run('old226(42)')
            self.assertEqual(caught.exception.code,'ownership_error')
        source='''var saved=null;let p=prompt_tag();call_prompt(p,fn(){capture_continuation(p,fn(k){saved=k;0})});async fn other(){try{saved(2)}catch e{e.code}};[saved(1),await other()]'''
        self.assertEqual(self.value(source),[1,'ownership_error'])

    def test_info_is_readonly_and_reports_only_actual_explicit_invocations(self):
        source='''var saved=null;let p=prompt_tag();call_prompt(p,fn(){capture_continuation(p,fn(k){saved=k;0})});let before=continuation_info(saved);let copy=continuation_info(saved);let value=saved(42);let after=continuation_info(saved);[type_of(saved),sort(keys(before)),before.invocations,copy.invocations,before.frames>0,before.available,value,after.invocations,after.id==before.id]'''
        self.assertEqual(self.value(source),['Continuation',['available','frames','id','invocations','state'],0,0,True,True,42,1,True])

    def test_completed_Agent_prefix_is_not_replayed_but_suffix_effect_is_explicit(self):
        calls=[];effects=[]
        source='''var saved=null;let p=prompt_tag();agent worker{provider:"fixture",tools:[]};let prefix=worker(40);let initial=call_prompt(p,fn(){let x=capture_continuation(p,fn(k){saved=k;1});tool("suffix",x);x+prefix});[initial,saved(2),saved(3)]'''
        result=self.value(source,agent_handler=lambda n,c,v:calls.append((n,v))or v,tool_handler=lambda n,v:effects.append((n,v))or v)
        self.assertEqual(result,[41,42,43]);self.assertEqual(calls,[('worker',40)]);self.assertEqual(effects,[('suffix',1),('suffix',2),('suffix',3)])

    def test_completed_software_Robot_prefix_is_not_replayed(self):
        log=[];source='''async fn prefix(){async with await robot_open("rig") as r{let observed=await robot_observe(r);let command=robot_command(observed,{pan:0,tilt:1});let receipt=await r(command);receipt.status}};let status=await prefix();var saved=null;let p=prompt_tag();let initial=call_prompt(p,fn(){let x=capture_continuation(p,fn(k){saved=k;1});[status,x]});[initial,saved(2),saved(3)]'''
        with robots.host(log)as app:result=robots.run(app,source)
        self.assertEqual(result,[['sent',1],['sent',2],['sent',3]]);self.assertEqual([row[0]for row in log],['open','observe','act','close'])

    def test_source_IR_Code_and_loaded_artifact_share_real_in_process_control(self):
        source=SOURCE;ir=parse_program(source);pretty=format_program(ir)
        self.assertEqual(parse_program(pretty)['semanticHash'],ir['semanticHash'])
        for mode,value in [('source',source),('ir',pretty),('artifact',load_artifact(json.loads(json.dumps(compile_artifact(source)))) )]:
            with self.subTest(mode=mode):self.assertEqual(Framework.evaluate(value),EXPECTED)
        code='''var saved=null;let p=prompt_tag();let f=function_from(code_from("fn(tag,receiver){10+capture_continuation(tag,receiver)}"));let initial=call_prompt(p,fn(){f(p,fn(k){saved=k;2})});[initial,saved(3)]'''
        self.assertEqual(Framework.evaluate(code),[12,13])

    def test_outside_isolated_SDK_and_CLI_run_the_same_epoch_local_source(self):
        child="import io,json,socket,sys\nfrom pathlib import Path\nfrom contextlib import redirect_stdout\nsys.path.insert(0,sys.argv[1])\ndef denied(*a,**k):raise AssertionError('Continuation oracle has no network authority')\nsocket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied\nimport chaos\nfrom chaos import Framework\nfrom chaos.cli import main\nroot=Path(sys.argv[1]);source=Path(sys.argv[3])\nassert Path(chaos.__file__).resolve()==root/'chaos/__init__.py'and sys.flags.isolated and sys.flags.no_site\nif sys.argv[2]=='sdk':result=Framework.evaluate(source.read_text())\nelse:\n sys.argv=['chaos','run',str(source),'--db',':memory:'];stream=io.StringIO()\n with redirect_stdout(stream):status=main()\n assert status==0;result=json.loads(stream.getvalue())\nprint(json.dumps(result))\n"
        with tempfile.TemporaryDirectory(prefix='continuation226-')as directory:
            work=Path(directory);source=work/'main.chaos';source.write_text(SOURCE,encoding='utf-8')
            env={k:v for k,v in os.environ.items()if k in ('PATH','LANG','LC_ALL','TMP','TEMP','TMPDIR','SYSTEMROOT','WINDIR','HOME','USERPROFILE')}
            env.update(CHAOS_CONFIG=str(work/'settings.json'),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
            for entry in ('sdk','cli'):
                with self.subTest(entry=entry):
                    actual=subprocess.run([sys.executable,'-I','-S','-B','-c',child,str(ROOT),entry,str(source)],cwd=work,env=env,capture_output=True,text=True,timeout=30)
                    self.assertEqual(actual.returncode,0,(actual.stdout,actual.stderr));self.assertEqual(json.loads(actual.stdout),EXPECTED)
